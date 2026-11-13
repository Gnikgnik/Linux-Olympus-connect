"""Finestra principale: rail, area di lavoro, ispettore, barra di stato.

I due pannelli laterali si ritirano perché lo schermo di riferimento è alto
768 px: a pannelli fissi il contenuto resterebbe una fascia. L'animazione
serve a dire dove sono andati, non a fare scena.
"""
from __future__ import annotations

import logging
import math
import os
import sys
import time
from importlib import resources
from pathlib import Path

from PyQt6.QtCore import (
    QEventLoop, QMetaObject, QSettings, QSize, Qt, QThread, QTimer, pyqtSignal, pyqtSlot,
)
from PyQt6 import sip
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from ..core.camprops import shot_busy_seconds
from ..core.downloader import JobState, plan_flat
from ..core.errors import NetworkUnavailable, OlympusError
from ..core.model import MediaKind
from ..core.network import CameraNetwork
from ..core.power import SleepInhibitor
from ..core.protocol import LIVE_QUALITY, OlympusClient
from ..core.sequence import (
    DEFAULT_DURATION, DEFAULT_INTERVAL, SNAPSHOT_SETTLE, SequencePlan, SequenceResult,
    folder_name, sequence_files,
)
from ..core.stats import format_duration, format_rate, format_size
from ..core.timelapse import (
    DEFAULT_FRAME_RATE, DEFAULT_RESOLUTION, FRAME_RATES, RESOLUTIONS, VIDEO_NAME, find_ffmpeg,
)
from . import icons, theme
from .grid import MediaFilter, MediaGrid, MediaListModel, MediaTable
from .panels import CollapsiblePanel, SectionLabel, Separator
from .liveview import LiveViewWorker
from .preview import PreviewView
from .remote import RemotePanel, RemoteView
from .sequence import SequenceController, SequenceEndDialog, SequenceJob
from .shot import ShotSection, label_column_width
from .workers import (
    DownloadWorker, ListWorker, PreviewWorker, RemoteWorker, ThumbWorker, TimelapseWorker,
    describe_error,
)

DEFAULT_DEST = Path.home() / "Immagini" / "Olympus"

VIEW_GRID = "griglia"
VIEW_TABLE = "elenco"
VIEW_LABELS = {VIEW_GRID: "Miniature", VIEW_TABLE: "Elenco dettagliato"}
# Il telecomando non è una vista dell'elenco: non si ricorda come vista
# d'avvio, perché all'avvio la fotocamera non è collegata.
VIEW_REMOTE = "telecomando"

# Thread del telecomando ancora dentro un comando alla scadenza della chiusura:
# non vanno distrutti con la finestra (Qt abortirebbe), si lasciano finire qui.
_DETACHED: list = []

KIND_ROWS = (
    (MediaKind.JPEG, "jpeg", "JPEG"),
    (MediaKind.RAW, "raw", "RAW"),
    (MediaKind.MOVIE, "film", "Video"),
)


def _label(text: str, role: str = "dim") -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(role)
    return widget


class MainWindow(QMainWindow):
    # La richiesta di anteprima viaggia come segnale, non come chiamata
    # diretta: il worker vive su un altro thread, e chiamarne un metodo
    # dalla GUI eseguirebbe la richiesta HTTP sul thread sbagliato.
    preview_needed = pyqtSignal(str)
    # Comandi verso il RemoteWorker, che vive su un altro thread.
    remote_enter = pyqtSignal(str, int)
    remote_focus = pyqtSignal(int, int)
    remote_shoot = pyqtSignal()
    remote_movie_start = pyqtSignal()
    remote_movie_stop = pyqtSignal()
    remote_leave = pyqtSignal()
    remote_read_props = pyqtSignal()
    remote_set_prop = pyqtSignal(str, str)
    remote_snapshot_names = pyqtSignal()
    remote_pause_live = pyqtSignal()
    remote_resume_live = pyqtSignal(int)

    # Letto subito dopo uno scatto, l'elenco della scheda non contiene ancora
    # quella foto (prova sul campo del 2026-11-11): l'istantanea aspetta.
    SNAPSHOT_SETTLE = SNAPSHOT_SETTLE

    def __init__(self, client_factory=OlympusClient,
                 network_factory=CameraNetwork,
                 settings: QSettings | None = None,
                 live_factory=LiveViewWorker,
                 inhibitor_factory=SleepInhibitor) -> None:
        super().__init__()
        self.setWindowTitle("Olympus Connect")
        self.resize(1180, 720)
        self.setMinimumSize(880, 560)

        self._settings = settings or QSettings("olympus-connect", "olympus-connect")
        self._client_factory = client_factory
        self._network_factory = network_factory
        self._threads: list[QThread] = []
        self._list_worker: ListWorker | None = None
        self._thumb_worker: ThumbWorker | None = None
        self._preview_worker: PreviewWorker | None = None
        self._download_worker: DownloadWorker | None = None
        self._preview_path: str | None = None
        self._connected_model: str = ""
        self._suspended = False
        self._live_factory = live_factory
        self._remote_active = False
        self._remote_leaving = False
        self._remote_worker: RemoteWorker | None = None
        self._live_worker = None
        self._remote_threads: list[QThread] = []
        self._remote_exit_view = VIEW_GRID
        self._live_port = 0
        self._downloading = False
        self._closing = False
        self._close_deadline = 0.0
        self._listing = False
        self._remote_entered = False
        self._resume_was_enabled = False
        # Pose lunghe: durante lo scatto il live si ferma di sicuro (la
        # fotocamera non manda pacchetti finché starttake non torna), quindi
        # l'avviso di live interrotto si rimanda alla fine dello scatto.
        self._shot_in_flight = False
        self._live_restart_pending = False
        self._mode_to_restore: str | None = None
        self._live_down: str | None = None     # avviso del live rimandato, se fermo
        self._exposure_shown = False
        self._exposure_left = 0
        self._exposure_timer = QTimer(self)
        self._exposure_timer.setInterval(1000)
        self._exposure_timer.timeout.connect(self._on_exposure_tick)

        # Sequenze di scatti a intervalli.
        self._remote_props: dict = {}              # l'ultimo elenco di impostazioni letto
        self._last_shot_end = 0.0                  # tempo monotonic della fine dell'ultimo scatto
        self._sequence_plan: SequencePlan | None = None
        self._sequence_preparing = False           # fra Avvia e l'istantanea dei nomi
        # Letture e modifiche delle impostazioni ancora in coda nel worker: finché
        # ce ne sono, il pannello non dice ancora il vero e la modalità scelta dal
        # PC può non essere stata rimessa. Una sequenza non scatta nel frattempo.
        self._props_pending = 0
        self._snapshot_timer = QTimer(self)
        self._snapshot_timer.setSingleShot(True)
        self._snapshot_timer.timeout.connect(self._sequence_snapshot)
        self._sequence_before: set[str] | None = None
        self._sequence_result: SequenceResult | None = None
        self._inhibitor_factory = inhibitor_factory
        self._sequence = SequenceController(self._sequence_ready,
                                            inhibitor=inhibitor_factory(), parent=self)
        self._sequence.shoot_requested.connect(self._sequence_shoot)
        self._sequence.ended.connect(self._on_sequence_ended)
        self._sequence.wake_requested.connect(self._wake_live)
        # Con intervalli lunghi il live si spegne fra uno scatto e l'altro.
        self._live_paused = False       # il live è spento di proposito fra due scatti
        self._live_waking = False       # è partita la riaccensione, si aspetta il primo fotogramma
        self._auto_reentries = 0        # rientri automatici di fila durante una sequenza
        self._pause_after_stall = False  # la pausa è cominciata con il live già fermo
        # Fine sequenza: finestra di scelta, scarico nella cartella dedicata, video.
        self._sequence_known: set[str] | None = None   # file noti entrando nel telecomando
        self._listed_ok = False                    # c'è stato almeno un elenco riuscito
        # Una sequenza finita aspetta l'elenco che la segue (anche se fallisce e
        # si rientra nel telecomando): risultato e insiemi di confronto insieme.
        self._sequence_end_before: set[str] | None = None
        self._sequence_end_known: set[str] | None = None
        self._sequence_missing = 0                 # foto della sequenza non scaricate
        # La modalità con cui la sequenza è partita. Ogni rientro nel live rimette
        # quella della ghiera: finché un elenco letto dopo il rientro non la
        # conferma, la sequenza non scatta.
        self._sequence_mode: str | None = None
        self._props_fresh = False                  # elenco letto dopo l'ultimo ingresso
        self._mode_restores = 0                    # tentativi di rimettere la modalità, di fila
        self._transfer_inhibitor = None            # PC sveglio durante scarico e video
        self._sequence_dialog: SequenceEndDialog | None = None
        self._sequence_offer: tuple | None = None  # (foto, risultato) mentre la finestra è aperta
        self._sequence_job: SequenceJob | None = None
        self._timelapse_worker: TimelapseWorker | None = None

        # Figlio della finestra: muore con lei, e con lui le animazioni delle
        # miniature. Senza genitore lo distruggerebbe la raccolta dei rifiuti di
        # Python in un momento qualunque, anche dentro un passo di animazione.
        self._model = MediaListModel(self)
        self._grid = MediaGrid(self._model)
        self._table = MediaTable(self._model)
        self._table.size_columns()

        self._build_ui()
        self.refresh_status()

    # ------------------------------------------------------------------
    # costruzione
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._rail = self._build_rail()
        self._inspector = self._build_inspector()

        centrale = QWidget()
        riga = QHBoxLayout(centrale)
        riga.setContentsMargins(0, 0, 0, 0)
        riga.setSpacing(0)
        riga.addWidget(self._rail)
        riga.addWidget(self._build_content(), 1)
        riga.addWidget(self._inspector)
        self.setCentralWidget(centrale)

        self.statusBar().setSizeGripEnabled(False)
        self._install_transfer_widgets()

        self.set_view(str(self._settings.value("view", VIEW_GRID)))
        self._view_grid.clicked.connect(lambda: self.choose_view(VIEW_GRID))
        self._view_table.clicked.connect(lambda: self.choose_view(VIEW_TABLE))
        self._view_remote.clicked.connect(self.enter_remote)

        self._remote_view.focus_requested.connect(self.on_remote_focus_requested)
        self._remote_view.retry_requested.connect(self.on_live_retry)
        self._remote_view.review_ended.connect(self._after_shot_review)
        self._remote_panel.shoot_requested.connect(self.on_remote_shoot)
        self._remote_panel.movie_toggle_requested.connect(self.on_remote_movie_toggle)
        self._remote_panel.props.prop_changed.connect(self.on_remote_prop_changed)
        sezione = self._remote_panel.sequence
        sezione.set_values(int(self._settings.value("sequence/interval", DEFAULT_INTERVAL)),
                           int(self._settings.value("sequence/duration", DEFAULT_DURATION)))
        sezione.start_requested.connect(self.on_sequence_start)
        sezione.stop_requested.connect(self.on_sequence_stop)
        sezione.changed.connect(self._on_sequence_changed)
        self._sequence.progress.connect(sezione.set_progress)
        self._update_sequence_summary()

        self._shortcut_shoot = QShortcut(QKeySequence("Space"), self)
        self._shortcut_shoot.activated.connect(self.on_remote_shoot)
        self._shortcut_movie = QShortcut(QKeySequence("R"), self)
        self._shortcut_movie.activated.connect(self.on_remote_movie_toggle)
        for scorciatoia in (self._shortcut_shoot, self._shortcut_movie):
            scorciatoia.setEnabled(False)
        for pulsante in self._filter_boxes.values():
            pulsante.toggled.connect(self.on_filter_changed)
        self.on_filter_changed()
        self._refresh_counts()
        self._update_selection_label()
        # Un reset del modello (filtri, Aggiorna) azzera la selezione senza
        # selectionChanged: pannelli e anteprima restavano su un file perso.
        self._model.modelReset.connect(self.on_selection_changed)

    # -- rail -----------------------------------------------------------

    def _build_rail(self) -> CollapsiblePanel:
        pannello = CollapsiblePanel("Fotocamera", "left", theme.RAIL_WIDTH)
        corpo = pannello.layout_body()

        stato = QHBoxLayout()
        stato.setSpacing(8)
        self._camera_icon = QLabel()
        self._camera_icon.setPixmap(
            icons.pixmap("camera", 20, theme.TEXT_FAINT, 2.0))
        self._camera_icon.setFixedWidth(20)
        testi = QVBoxLayout()
        testi.setSpacing(1)
        self._camera_model = _label("Non collegata", "value")
        self._camera_detail = _label("Nessuna sessione attiva", "faint")
        self._camera_detail.setWordWrap(True)
        testi.addWidget(self._camera_model)
        testi.addWidget(self._camera_detail)
        stato.addWidget(self._camera_icon, 0, Qt.AlignmentFlag.AlignTop)
        stato.addLayout(testi, 1)
        corpo.addLayout(stato)

        azioni = QHBoxLayout()
        azioni.setSpacing(6)
        self._connect_button = QPushButton(" Connetti")
        self._connect_button.setIcon(icons.icon("wifi", theme.TEXT, theme.TEXT_FAINT, 18))
        self._connect_button.setIconSize(QSize(18, 18))
        self._connect_button.clicked.connect(self.on_connect)
        self._refresh_button = QPushButton()
        self._refresh_button.setIcon(icons.icon("refresh", theme.TEXT, theme.TEXT_FAINT, 18))
        self._refresh_button.setToolTip("Aggiorna l'elenco")
        self._refresh_button.setIconSize(QSize(18, 18))
        self._refresh_button.setFixedWidth(34)
        self._refresh_button.clicked.connect(self.on_refresh)
        azioni.addWidget(self._connect_button, 1)
        azioni.addWidget(self._refresh_button)
        corpo.addLayout(azioni)

        corpo.addWidget(Separator())
        corpo.addWidget(SectionLabel("Contenuto della scheda"))

        self._filter_boxes: dict[str, QPushButton] = {}
        self._kind_counts: dict[MediaKind, QLabel] = {}
        for kind, icona, etichetta in KIND_ROWS:
            riga, conteggio = self._build_filter_row(kind, icona, etichetta)
            corpo.addWidget(riga)
            self._kind_counts[kind] = conteggio
        self._filter_jpeg = self._filter_boxes["jpeg"]
        self._filter_raw = self._filter_boxes["raw"]
        self._filter_movie = self._filter_boxes["movie"]

        self._card_total = _label("", "faint")
        corpo.addWidget(self._card_total)

        corpo.addWidget(Separator())
        corpo.addWidget(SectionLabel("Mostra"))
        self._only_new = QPushButton("  Solo non scaricati")
        self._only_new.setObjectName("filterRow")
        self._only_new.setCheckable(True)
        # Le quattro righe-filtro appartengono a una sola classe visiva:
        # una senza segno disegnato stonava con le tre sopra.
        self._only_new.setIcon(
            icons.icon("download", theme.TEXT_DIM, theme.TEXT_FAINT, 18))
        self._only_new.setIconSize(QSize(18, 18))
        self._only_new.setMinimumHeight(32)
        self._only_new.setChecked(
            self._settings.value("filter/only_new", False, type=bool))
        self._only_new.setCursor(Qt.CursorShape.PointingHandCursor)
        self._filter_boxes["only_new"] = self._only_new
        corpo.addWidget(self._only_new)

        corpo.addWidget(Separator())
        corpo.addWidget(SectionLabel("Destinazione"))
        self._dest_label = _label("", "value")
        self._dest_label.setWordWrap(True)
        corpo.addWidget(self._dest_label)
        cambia = QPushButton(" Cambia cartella")
        cambia.setObjectName("ghost")
        cambia.setIcon(icons.icon("folder", theme.TEXT_DIM, size=18))
        cambia.setIconSize(QSize(18, 18))
        cambia.clicked.connect(self.on_choose_destination)
        corpo.addWidget(cambia)
        self._refresh_destination()

        corpo.addStretch(1)
        return pannello

    def _build_filter_row(self, kind: MediaKind, icona: str,
                          etichetta: str) -> tuple[QWidget, QLabel]:
        """Una riga-filtro mostra anche quanto pesa: con 1141 RAW la domanda
        non è «ci sono RAW» ma «quanto spazio sono».

        Il conteggio vive DENTRO il pulsante, trasparente al mouse: messo
        accanto, lasciava un terzo della riga fuori dall'area cliccabile e
        la campitura di selezione si fermava a due terzi.
        """
        pulsante = QPushButton(f"  {etichetta}")
        pulsante.setObjectName("filterRow")
        pulsante.setCheckable(True)
        pulsante.setChecked(
            self._settings.value(f"filter/{kind.value}", True, type=bool))
        pulsante.setIcon(icons.icon(icona, theme.TEXT_DIM, theme.TEXT_FAINT, 18))
        pulsante.setIconSize(QSize(18, 18))
        pulsante.setCursor(Qt.CursorShape.PointingHandCursor)
        pulsante.setMinimumHeight(38)
        pulsante.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Fixed)

        conteggio = _label("—", "faint")
        conteggio.setAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)
        conteggio.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        conteggio.setStyleSheet("background: transparent;")

        dentro = QHBoxLayout(pulsante)
        dentro.setContentsMargins(0, 0, 10, 0)
        dentro.addStretch(1)
        dentro.addWidget(conteggio)

        chiave = {MediaKind.JPEG: "jpeg", MediaKind.RAW: "raw",
                  MediaKind.MOVIE: "movie"}[kind]
        self._filter_boxes[chiave] = pulsante
        return pulsante, conteggio

    # -- area di lavoro ---------------------------------------------------

    def _build_content(self) -> QWidget:
        contenitore = QWidget()
        contenitore.setObjectName("content")
        colonna = QVBoxLayout(contenitore)
        colonna.setContentsMargins(0, 0, 0, 0)
        colonna.setSpacing(0)

        colonna.addWidget(self._build_topbar())

        self._views = QStackedWidget()
        self._views.addWidget(self._grid)
        self._views.addWidget(self._table)
        self._views.addWidget(self._build_empty_state())
        self._remote_view = RemoteView()
        self._views.addWidget(self._remote_view)
        colonna.addWidget(self._views, 1)
        return contenitore

    def _build_topbar(self) -> QWidget:
        barra = QWidget()
        barra.setObjectName("topbar")
        barra.setFixedHeight(46)
        riga = QHBoxLayout(barra)
        riga.setContentsMargins(10, 6, 10, 6)
        riga.setSpacing(6)

        self._view_grid = QPushButton()
        self._view_grid.setObjectName("ghost")
        self._view_grid.setCheckable(True)
        self._view_grid.setIcon(icons.icon("grid", theme.TEXT_DIM, size=18))
        self._view_grid.setIconSize(QSize(18, 18))
        self._view_grid.setToolTip("Miniature")
        self._view_table = QPushButton()
        self._view_table.setObjectName("ghost")
        self._view_table.setCheckable(True)
        self._view_table.setIcon(icons.icon("list", theme.TEXT_DIM, size=18))
        self._view_table.setIconSize(QSize(18, 18))
        self._view_table.setToolTip("Elenco dettagliato")
        riga.addWidget(self._view_grid)
        riga.addWidget(self._view_table)
        self._view_remote = QPushButton()
        self._view_remote.setObjectName("ghost")
        self._view_remote.setCheckable(True)
        self._view_remote.setIcon(icons.icon("remote", theme.TEXT_DIM, size=18))
        self._view_remote.setIconSize(QSize(18, 18))
        self._view_remote.setToolTip("Telecomando: live, scatto e video")
        self._view_remote.setEnabled(False)
        riga.addWidget(self._view_remote)

        riga.addSpacing(10)
        self._selection_label = _label("", "dim")
        riga.addWidget(self._selection_label)
        riga.addStretch(1)

        self._download_button = QPushButton(" Scarica selezionati")
        self._download_button.setObjectName("primary")
        self._download_button.setIcon(
            icons.icon("download", theme.ACCENT_INK, theme.TEXT_FAINT, 18))
        self._download_button.setIconSize(QSize(18, 18))
        self._download_button.clicked.connect(self.on_download_selected)
        riga.addWidget(self._download_button)
        return barra

    def _build_empty_state(self) -> QWidget:
        """Uno stato vuoto che insegna, invece di dire «nessun file»."""
        vuoto = QWidget()
        vuoto.setObjectName("content")
        colonna = QVBoxLayout(vuoto)
        colonna.setAlignment(Qt.AlignmentFlag.AlignCenter)
        colonna.setSpacing(10)

        icona = QLabel()
        icona.setPixmap(icons.pixmap("camera", 52, theme.LINE_STRONG, 2.0))
        icona.setAlignment(Qt.AlignmentFlag.AlignCenter)

        titolo = QLabel("Nessun file da mostrare")
        titolo.setObjectName("panelTitle")
        titolo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._empty_hint = _label(
            "Attiva il Wi-Fi sulla fotocamera, collega il PC al suo\n"
            "access point, poi premi Connetti.", "faint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        colonna.addWidget(icona)
        colonna.addWidget(titolo)
        colonna.addWidget(self._empty_hint)
        return vuoto

    def _install_transfer_widgets(self) -> None:
        """I comandi di trasferimento vivono nella barra di stato.

        Una striscia separata sopra la barra di stato dava due righe di
        stato in fondo dove il contratto ne prometteva una, e a riposo
        mostrava una vaschetta vuota e un pulsante disattivato che non
        servivano a nessuno.
        """
        barra = self.statusBar()

        self._file_label = _label("", "dim")
        self._file_bar = QProgressBar()
        self._file_bar.setObjectName("file")
        self._file_bar.setTextVisible(False)
        self._file_bar.setFixedSize(116, 4)
        self._total_bar = QProgressBar()
        self._total_bar.setObjectName("total")
        self._total_bar.setTextVisible(False)
        self._total_bar.setFixedSize(210, 5)
        self._rate_label = _label("", "faint")

        # Una X senza etichetta nell'angolo in basso a destra di una
        # finestra si legge «chiudi», non «annulla»: su un trasferimento da
        # quasi due ore e' l'ambiguita' piu' cara dell'interfaccia.
        self._cancel_button = QPushButton("Annulla")
        self._cancel_button.setObjectName("ghost")
        self._cancel_button.setIcon(icons.icon("close", theme.TEXT_DIM,
                                               theme.LINE_STRONG, 18))
        self._cancel_button.setIconSize(QSize(18, 18))
        self._cancel_button.setToolTip("Annulla il trasferimento")
        self._cancel_button.clicked.connect(self.on_cancel)

        self._resume_button = QPushButton("Riprova")
        self._resume_button.setObjectName("ghost")
        self._resume_button.setIcon(icons.icon("retry", theme.WARN,
                                               theme.LINE_STRONG, 18))
        self._resume_button.setIconSize(QSize(18, 18))
        self._resume_button.clicked.connect(self.on_resume)

        self._transfer_widgets = (
            self._file_label, self._file_bar, self._total_bar,
            self._rate_label, self._cancel_button,
        )
        for widget in self._transfer_widgets:
            barra.addPermanentWidget(widget)
        barra.addPermanentWidget(self._resume_button)
        self._show_transfer(False)

    def _show_transfer(self, attivo: bool, con_riprova: bool = False) -> None:
        for widget in self._transfer_widgets:
            widget.setVisible(attivo)
        self._cancel_button.setEnabled(attivo)
        self._resume_button.setVisible(con_riprova)
        self._resume_button.setEnabled(con_riprova)

    # -- ispettore --------------------------------------------------------

    def _build_inspector(self) -> CollapsiblePanel:
        pannello = CollapsiblePanel("Anteprima", "right", theme.PANEL_WIDTH)
        esterno = pannello.layout_body()
        self._inspector_pages = QStackedWidget()
        self._inspector_pages.setObjectName("inspectorPages")
        pagina = QWidget()
        pagina.setObjectName("inspectorPage")
        corpo = QVBoxLayout(pagina)
        corpo.setContentsMargins(0, 0, 0, 0)
        corpo.setSpacing(10)      # come layout_body() di panels.py
        self._inspector_pages.addWidget(pagina)
        self._remote_panel = RemotePanel()
        self._inspector_pages.addWidget(self._remote_panel)
        esterno.addWidget(self._inspector_pages)

        self._preview = PreviewView()
        corpo.addWidget(self._preview)

        corpo.addWidget(SectionLabel("Dettagli"))
        self._details = QGridLayout()
        self._details.setContentsMargins(0, 0, 0, 0)
        self._details.setHorizontalSpacing(10)
        self._details.setVerticalSpacing(4)
        self._details.setColumnStretch(1, 1)
        self._details.setColumnMinimumWidth(0, label_column_width())
        self._detail_values: dict[str, QLabel] = {}
        for riga, nome in enumerate(
                ("Nome", "Scattato", "Tipo", "Dimensione", "Stato", "Destinazione")):
            self._details.addWidget(_label(nome, "faint"), riga, 0,
                                    Qt.AlignmentFlag.AlignTop)
            valore = _label("—", "value")
            valore.setWordWrap(True)
            self._details.addWidget(valore, riga, 1)
            self._detail_values[nome] = valore
        # Anteprima e dettagli formano un blocco unico in alto, e lo spazio
        # avanzato resta sotto. Dare altezza alla cornice non ingrandisce di
        # un pixel una foto limitata dalla larghezza: produce solo due
        # bande vuote dentro il bordo e un buco prima dei dettagli.
        corpo.addLayout(self._details)

        self._shot = ShotSection()
        corpo.addWidget(self._shot)
        corpo.addStretch(1)
        return pannello

    # ------------------------------------------------------------------
    # impostazioni
    # ------------------------------------------------------------------

    def dest_root(self) -> Path:
        stored = self._settings.value("dest_root", "")
        return Path(stored) if stored else DEFAULT_DEST

    def camera_ssid(self) -> str:
        return str(self._settings.value("ssid", "") or "")

    def _refresh_destination(self) -> None:
        percorso = str(self.dest_root())
        casa = str(Path.home())
        self._dest_label.setText(
            percorso.replace(casa, "~", 1) if percorso.startswith(casa) else percorso)
        self._dest_label.setToolTip(percorso)

    def on_choose_destination(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Cartella di destinazione", str(self.dest_root()))
        if chosen:
            self._settings.setValue("dest_root", chosen)
            self._refresh_destination()
            self.statusBar().showMessage(f"Destinazione: {chosen}", 5000)

    # ------------------------------------------------------------------
    # stato della rete
    # ------------------------------------------------------------------

    def refresh_status(self) -> None:
        try:
            diagnosis = self._network_factory(self.camera_ssid()).diagnose()
        except OlympusError as exc:
            self.statusBar().showMessage(describe_error(exc))
            self._set_camera_state("Non collegata", "Gestione della rete non disponibile")
            return

        if diagnosis.ok:
            # Raggiungibile non significa gia' collegata: riusare il titolo
            # precedente produceva «Non collegata / Raggiungibile», due
            # affermazioni che si contraddicono nella stessa riga.
            self.statusBar().showMessage("Fotocamera raggiungibile.")
            if self._connected_model:
                self._set_camera_state(f"Olympus {self._connected_model}",
                                       "Collegata", True)
            else:
                self._set_camera_state("Fotocamera pronta",
                                       "Raggiungibile · premi Connetti", True)
            return

        failing = next(r for r in diagnosis.rungs if not r.ok)
        self.statusBar().showMessage(f"{failing.name}: {failing.detail}")
        self._set_camera_state("Non collegata", failing.detail)

    def _set_camera_state(self, titolo: str, dettaglio: str,
                          connessa: bool = False) -> None:
        self._camera_model.setText(titolo or "Non collegata")
        self._camera_detail.setText(dettaglio)
        self._camera_icon.setPixmap(icons.pixmap(
            "camera", 20, theme.ACCENT if connessa else theme.TEXT_FAINT, 2.0))

    def ask_camera_ssid(self) -> str | None:
        """L'SSID della fotocamera è lungo e scomodo da digitare: si sceglie
        dalle reti visibili, con le fotocamere per prime. Resta modificabile
        per le reti non ancora visibili."""
        try:
            reti = self._network_factory(self.camera_ssid()).scan_ssids()
        except OlympusError:
            reti = []

        if reti:
            ssid, accepted = QInputDialog.getItem(
                self, "Rete della fotocamera",
                "Scegli la rete mostrata dalla fotocamera:", reti, 0, True)
        else:
            ssid, accepted = QInputDialog.getText(
                self, "Rete della fotocamera",
                "SSID mostrato dalla fotocamera:")
        return ssid.strip() if accepted and ssid.strip() else None

    def on_connect(self) -> None:
        ssid = self.camera_ssid()
        if not ssid:
            ssid = self.ask_camera_ssid()
            if not ssid:
                return
            self._settings.setValue("ssid", ssid)

        network = self._network_factory(ssid)
        try:
            if not network.profile_exists():
                password, accepted = QInputDialog.getText(
                    self, "Password Wi-Fi",
                    f"Password della rete «{ssid}»:",
                    echo=QLineEdit.EchoMode.Password)
                if not accepted or not password:
                    return
                network.ensure_profile(password=password)
            else:
                network.ensure_profile()
            network.connect()
        except OlympusError as exc:
            QMessageBox.warning(self, "Connessione", describe_error(exc))
            return

        self.refresh_status()
        self.on_refresh()

    # ------------------------------------------------------------------
    # elenco
    # ------------------------------------------------------------------

    def _start_on_thread(self, worker, slot=None) -> QThread:
        thread = QThread(self)
        worker.moveToThread(thread)
        if slot is not None:
            thread.started.connect(slot)
        thread.start()
        self._threads.append(thread)
        return thread

    def on_refresh(self) -> None:
        self._listing = True
        self._update_remote_button()
        # Un client per worker: requests.Session non è thread-safe.
        worker = ListWorker(self._client_factory(), self.dest_root())
        worker.connected.connect(self.on_connected)
        worker.listed.connect(self.on_listed)
        worker.failed.connect(self.on_failed)
        self._list_worker = worker
        self._start_on_thread(worker, worker.run)

    @pyqtSlot(str)
    def on_connected(self, model: str) -> None:
        self._connected_model = model
        self.statusBar().showMessage(f"Connesso a Olympus {model}.")
        self._set_camera_state(f"Olympus {model}", "Collegata", True)
        self._update_remote_button()

    @pyqtSlot(list)
    def on_listed(self, jobs: list) -> None:
        self._listing = False
        self._update_remote_button()
        if self._closing or self._remote_active:
            # Dopo la chiusura non si avviano thread; durante il telecomando non
            # si tocca la vista né si creano worker: l'elenco si rifà all'uscita.
            return
        self._model.set_jobs(jobs)
        self._listed_ok = True
        # I worker prima di tutto il resto: on_filter_changed passa da
        # set_view, che chiede le miniature visibili. Se il collegamento al
        # worker non esiste ancora, quella richiesta parte nel vuoto.
        self._ensure_thumb_worker()
        self._ensure_preview_worker()
        self.on_filter_changed()
        self._refresh_counts()

        new = sum(1 for j in jobs if j.state is not JobState.PRESENT)
        present = len(jobs) - new
        self.statusBar().showMessage(f"{new} da scaricare, {present} già presenti.")

        if self.current_view() == VIEW_GRID:
            self._grid.request_visible_thumbnails()

        self._offer_sequence(jobs)

    def _refresh_counts(self) -> None:
        conteggi = self._model.counts_by_kind()
        totale_file = totale_byte = 0
        for kind, _icona, _etichetta in KIND_ROWS:
            quanti, byte = conteggi.get(kind, (0, 0))
            totale_file += quanti
            totale_byte += byte
            etichetta = self._kind_counts[kind]
            etichetta.setText(f"{quanti}\n{format_size(byte)}" if quanti else "—")
        self._card_total.setText(
            f"{totale_file} file · {format_size(totale_byte)}" if totale_file
            else "Nessun file letto")

    def _ensure_thumb_worker(self) -> None:
        if self._thumb_worker is not None:
            return
        worker = ThumbWorker(self._client_factory())
        worker.ready.connect(self.on_thumbnail_ready)
        worker.failed.connect(self.on_thumbnail_failed)
        worker.skipped.connect(self._grid.forget_thumbnail)
        self._thumb_worker = worker
        self._start_on_thread(worker)
        # QueuedConnection: senza, la richiesta girerebbe sul thread della
        # GUI e la bloccherebbe per la durata della richiesta HTTP.
        self._grid.thumbnail_needed.connect(
            worker.request, Qt.ConnectionType.QueuedConnection)

    def _ensure_preview_worker(self) -> None:
        if self._preview_worker is not None:
            return
        worker = PreviewWorker(self._client_factory())
        worker.ready.connect(self.on_preview_ready)
        worker.failed.connect(self.on_preview_failed)
        worker.unavailable.connect(self.on_preview_unavailable)
        worker.shot_ready.connect(self.on_shot_ready)
        self._preview_worker = worker
        self._start_on_thread(worker)
        self.preview_needed.connect(worker.request,
                                    Qt.ConnectionType.QueuedConnection)
        for vista in (self._grid, self._table):
            vista.selectionModel().selectionChanged.connect(
                self.on_selection_changed)

    @pyqtSlot(str, bytes)
    def on_thumbnail_ready(self, camera_path: str, data: bytes) -> None:
        if not self._model.set_thumbnail(camera_path, data):
            self._grid.forget_thumbnail(camera_path)

    @pyqtSlot(str, str)
    def on_thumbnail_failed(self, camera_path: str, message: str) -> None:
        self._grid.forget_thumbnail(camera_path)

    def _suspend_thumbnails(self, suspended: bool) -> None:
        self._suspended = suspended
        if suspended:
            self._shot.clear()
        for worker in (self._thumb_worker, self._preview_worker):
            if worker is not None:
                worker.set_suspended(suspended)
        if not suspended and self.current_view() == VIEW_GRID:
            self._grid.request_visible_thumbnails()

    @pyqtSlot(str)
    def on_failed(self, message: str) -> None:
        self._listing = False
        self._update_remote_button()
        self.statusBar().showMessage(message)
        self._empty_hint.setText(message)

    # ------------------------------------------------------------------
    # selezione e anteprima
    # ------------------------------------------------------------------

    def on_selection_changed(self, *_args) -> None:
        self._update_selection_label()
        # I dati di scatto mostrati sono quelli del file precedente: via
        # subito, ricompaiono quando arrivano quelli nuovi.
        self._shot.clear()
        jobs = self.selected_jobs()
        if not jobs:
            self._preview.show_message("Nessun file selezionato")
            self._clear_details()
            self._preview_path = None
            return
        self._show_details(jobs[-1])
        if len(jobs) == 1 and self._preview_worker is not None:
            self._preview_path = jobs[0].media.camera_path
            self._preview.show_message("Caricamento anteprima…")
            self.preview_needed.emit(self._preview_path)

    def _update_selection_label(self) -> None:
        if self._remote_active:
            self._download_button.setEnabled(False)
            return
        jobs = self.selected_jobs()
        if not jobs:
            visibili = self._model.rowCount()
            self._selection_label.setText(
                f"{visibili} file" if visibili else "")
            self._download_button.setEnabled(False)
            return
        byte = sum(j.media.size for j in jobs)
        self._selection_label.setText(
            f"{len(jobs)} selezionati · {format_size(byte)}")
        self._download_button.setEnabled(True)

    def _clear_details(self) -> None:
        for valore in self._detail_values.values():
            valore.setText("—")

    def _show_details(self, job) -> None:
        quando = (job.media.shot_at.strftime("%d/%m/%Y  %H:%M:%S")
                  if job.media.shot_at else "data sconosciuta")
        stato = {JobState.NEW: "da scaricare",
                 JobState.PRESENT: "già presente",
                 JobState.INCOMPLETE: "da riscaricare"}[job.state]
        valori = {
            "Nome": job.media.name,
            "Scattato": quando,
            "Tipo": job.media.kind.value.upper(),
            "Dimensione": format_size(job.media.size),
            "Stato": stato,
            "Destinazione": str(job.dest).replace(str(Path.home()), "~", 1),
        }
        for nome, testo in valori.items():
            self._detail_values[nome].setText(testo)

    @pyqtSlot(str, bytes)
    def on_preview_ready(self, camera_path: str, data: bytes) -> None:
        if camera_path != self._preview_path:
            return      # la selezione è cambiata mentre arrivava
        if not self._preview.show_image(data):
            self._preview.show_message("Anteprima non leggibile")

    @pyqtSlot(str, str)
    def on_preview_failed(self, camera_path: str, message: str) -> None:
        if camera_path == self._preview_path:
            self._preview.show_message("Anteprima non disponibile")

    @pyqtSlot(str)
    def on_preview_unavailable(self, camera_path: str) -> None:
        self._preview.show_message(
            "Questa fotocamera non genera anteprime grandi")

    @pyqtSlot(str, object)
    def on_shot_ready(self, camera_path: str, info: object) -> None:
        if self._suspended:
            return      # download in corso: la sezione resta nascosta
        if camera_path != self._preview_path or len(self.selected_jobs()) != 1:
            return      # la selezione è cambiata mentre arrivava
        self._shot.show_shot(info)

    # ------------------------------------------------------------------
    # vista e filtri
    # ------------------------------------------------------------------

    def current_view(self) -> str:
        """La vista scelta, non il widget visibile.

        Con la scheda vuota a schermo c'è lo stato vuoto, ma la scelta
        dell'utente resta quella: perderla al primo elenco vuoto sarebbe
        un'impostazione che si dimentica da sola.
        """
        return getattr(self, "_requested_view", VIEW_GRID)

    def set_view(self, view: str) -> None:
        view = view if view in VIEW_LABELS else VIEW_GRID
        vuoto = self._model.rowCount() == 0
        if vuoto:
            self._views.setCurrentIndex(2)
        else:
            self._views.setCurrentWidget(
                self._table if view == VIEW_TABLE else self._grid)
        self._view_grid.setChecked(view == VIEW_GRID)
        self._view_table.setChecked(view == VIEW_TABLE)
        self._view_remote.setChecked(False)
        self._settings.setValue("view", view)
        self._requested_view = view
        if view == VIEW_GRID and not vuoto:
            self._grid.request_visible_thumbnails()

    def active_view(self):
        widget = self._views.currentWidget()
        return widget if widget in (self._grid, self._table) else self._grid

    def selected_jobs(self) -> list:
        return self.active_view().selected_jobs()

    def on_filter_changed(self) -> None:
        for chiave, pulsante in self._filter_boxes.items():
            self._settings.setValue(f"filter/{chiave}", pulsante.isChecked())

        kinds: set[MediaKind] = set()
        if self._filter_jpeg.isChecked():
            kinds.add(MediaKind.JPEG)
        if self._filter_raw.isChecked():
            kinds.add(MediaKind.RAW)
        if self._filter_movie.isChecked():
            kinds.add(MediaKind.MOVIE)
        self._grid.set_filter(
            MediaFilter(kinds=kinds, only_new=self._only_new.isChecked()))
        self.set_view(getattr(self, "_requested_view", VIEW_GRID))
        self._update_selection_label()

    # ------------------------------------------------------------------
    # download
    # ------------------------------------------------------------------

    def on_download_selected(self) -> None:
        jobs = self.selected_jobs()
        if not jobs:
            self.statusBar().showMessage("Nessun file selezionato.", 4000)
            return
        if self._timelapse_worker is not None:
            # La barra di stato e il suo Annulla servono al montaggio in corso.
            self.statusBar().showMessage("Aspetta che il video sia montato.", 4000)
            return
        self._start_download(jobs)

    def _start_download(self, jobs: list, mark_model: bool = True) -> None:
        """Avvia un trasferimento. `mark_model=False` per lo scarico di una
        sequenza: va in un'altra cartella, e i file dell'elenco non diventano
        «già scaricati» per questo."""
        if mark_model:
            self._sequence_job = None       # uno scarico ordinario: nessun video da montare dopo
        # Miniature e anteprima sospese: il server della fotocamera è un
        # microcontrollore e due trasferimenti in volo lo degradano.
        self._suspend_thumbnails(True)
        worker = DownloadWorker(self._client_factory(), jobs)
        worker.file_started.connect(self._file_label.setText)
        worker.file_progress.connect(self._update_file_bar)
        worker.total_progress.connect(self._update_total_bar)
        worker.stats_changed.connect(self.on_stats)
        if mark_model:
            worker.file_completed.connect(self.on_file_completed)
        worker.file_failed.connect(
            lambda name, msg: self.statusBar().showMessage(f"{name}: {msg}"))
        worker.paused.connect(self.on_paused)
        worker.finished.connect(self.on_download_finished)

        self._download_worker = worker
        self._show_transfer(True)
        self._downloading = True
        self._update_remote_button()
        self._start_on_thread(worker, worker.run)

    @pyqtSlot(str)
    def on_file_completed(self, camera_path: str) -> None:
        self._model.set_job_state(camera_path, JobState.PRESENT)
        self._refresh_counts()

    @pyqtSlot(int, int, object, object)
    def on_stats(self, written: int, total: int, speed, eta) -> None:
        pezzi = [f"{format_size(written)} / {format_size(total)}"]
        if speed:
            pezzi.append(format_rate(speed))
        pezzi.append(f"{format_duration(eta)} alla fine" if eta
                     else ("completato" if eta == 0 else "tempo sconosciuto"))
        self._rate_label.setText("   ·   ".join(pezzi))

    # QProgressBar lavora su interi a 32 bit: i 23,48 GB di RAW della scheda
    # di riferimento, in byte, sforano il limite. L'avanzamento viaggia
    # quindi in millesimi, non in byte.
    PROGRESS_SCALE = 1000

    def _set_progress(self, bar: QProgressBar, written: int, total: int) -> None:
        bar.setMaximum(self.PROGRESS_SCALE)
        if total <= 0:
            bar.setValue(0)
            return
        frazione = min(max(written / total, 0.0), 1.0)
        bar.setValue(round(frazione * self.PROGRESS_SCALE))

    def _update_file_bar(self, written: int, total: int) -> None:
        self._set_progress(self._file_bar, written, total)

    def _update_total_bar(self, written: int, total: int) -> None:
        self._set_progress(self._total_bar, written, total)

    def on_cancel(self) -> None:
        if self._timelapse_worker is not None:
            self._timelapse_worker.cancel()         # il resto lo fa on_timelapse_cancelled
            self._cancel_button.setEnabled(False)
            return
        if self._download_worker is not None:
            self._download_worker.cancel()
        self._keep_awake(False)                     # c'è qualcuno: ha appena premuto Annulla
        self._show_transfer(True, con_riprova=True)
        self._cancel_button.setEnabled(False)
        self._suspend_thumbnails(False)
        self._file_label.setText("Trasferimento annullato")
        self._downloading = False
        self._update_remote_button()

    def on_resume(self) -> None:
        if self._download_worker is None:
            return
        self._show_transfer(True)
        self._suspend_thumbnails(True)
        # Prima di resume(): finished e paused arrivano dentro la chiamata e
        # azzerano il flag; impostarlo dopo lo lascerebbe vero per sempre.
        self._downloading = True
        self._update_remote_button()
        if self._sequence_job is not None:
            self._keep_awake(True)
        # Sul thread del worker: chiamato da qui, il trasferimento girerebbe sul
        # thread della finestra e la bloccherebbe fino alla fine.
        QMetaObject.invokeMethod(self._download_worker, "resume",
                                 Qt.ConnectionType.QueuedConnection)

    @pyqtSlot(str)
    def on_paused(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self._show_transfer(True, con_riprova=True)
        self._cancel_button.setEnabled(False)
        self._file_label.setText("In pausa: la fotocamera si è disconnessa")
        self._suspend_thumbnails(False)
        self._downloading = False
        self._update_remote_button()

    @pyqtSlot()
    def on_download_finished(self) -> None:
        self._file_label.setText("")
        self._rate_label.setText("")
        self._file_bar.setValue(0)
        self._total_bar.setValue(0)
        self._show_transfer(False)
        self._suspend_thumbnails(False)
        self.statusBar().showMessage("Download completato.")
        self._downloading = False
        self._update_remote_button()
        lavoro, self._sequence_job = self._sequence_job, None
        if lavoro is not None:
            # Un'altra cartella: l'elenco non cambia, e il suo messaggio di stato
            # coprirebbe quello della sequenza.
            self._after_sequence_download(lavoro)
            return
        self.on_refresh()

    # ------------------------------------------------------------------
    # telecomando
    # ------------------------------------------------------------------

    def _update_remote_button(self) -> None:
        self._view_remote.setEnabled(
            self._remote_active or (bool(self._connected_model)
                                    and not self._downloading and not self._listing
                                    and self._timelapse_worker is None))

    def _set_library_controls(self, enabled: bool) -> None:
        for pulsante in self._filter_boxes.values():
            pulsante.setEnabled(enabled)
        self._refresh_button.setEnabled(enabled)
        self._connect_button.setEnabled(enabled)
        if enabled:
            self._resume_button.setEnabled(self._resume_was_enabled)
            self._update_selection_label()
        else:
            self._resume_was_enabled = self._resume_button.isEnabled()
            self._resume_button.setEnabled(False)
            self._download_button.setEnabled(False)

    def choose_view(self, view: str) -> None:
        if self._remote_active:
            self.exit_remote(view)
        else:
            self.set_view(view)

    def enter_remote(self) -> None:
        if self._remote_active:
            self._view_remote.setChecked(True)
            return
        if (not self._connected_model or self._downloading or self._listing
                or self._timelapse_worker is not None):
            self._view_remote.setChecked(False)
            return
        self._remote_active = True
        self._remote_entered = False
        self._shot_in_flight = False
        self._live_restart_pending = False
        self._mode_to_restore = None
        self._live_down = None
        self._remote_leaving = False       # mai ereditato da un'uscita precedente
        self._remote_props = {}
        self._sequence_known = self._model.camera_paths() if self._listed_ok else None
        self._props_pending = 0
        self._snapshot_timer.stop()
        self._sequence_preparing = False
        self._sequence_before = None
        self._live_paused = False
        self._live_waking = False
        self._auto_reentries = 0
        self._pause_after_stall = False
        self._remote_exit_view = self.current_view()
        # Miniature e anteprima lavorano in riproduzione: in modalità scatto
        # le loro richieste fallirebbero.
        self._suspend_thumbnails(True)
        self._set_library_controls(False)
        self._view_grid.setChecked(False)
        self._view_table.setChecked(False)
        self._view_remote.setChecked(True)
        self._views.setCurrentWidget(self._remote_view)
        self._inspector_pages.setCurrentWidget(self._remote_panel)
        self._inspector.set_title("Telecomando")
        self._remote_view.clear()
        self._remote_view.show_message("Collegamento al live…")
        self._remote_panel.props.clear()
        self._remote_panel.set_recording(False)
        self._remote_panel.set_busy(True)
        for scorciatoia in (self._shortcut_shoot, self._shortcut_movie):
            scorciatoia.setEnabled(True)
        self.statusBar().showMessage("Telecomando: passaggio in modalità scatto…")

        remote = RemoteWorker(self._client_factory(), LIVE_QUALITY)
        remote.entered.connect(self.on_remote_entered)
        remote.focused.connect(self.on_remote_focused)
        remote.shot.connect(self.on_remote_shot)
        remote.movie_started.connect(self.on_remote_movie_started)
        remote.movie_stopped.connect(self.on_remote_movie_stopped)
        remote.left.connect(self.on_remote_left)
        remote.failed.connect(self.on_remote_failed)
        remote.props_read.connect(self.on_remote_props)
        remote.props_failed.connect(self.on_remote_props_failed)
        remote.exposing.connect(self.on_remote_exposing)
        remote.names_read.connect(self.on_remote_names)
        remote.live_resumed.connect(self.on_remote_live_resumed)
        coda = Qt.ConnectionType.QueuedConnection
        self.remote_enter.connect(remote.enter, coda)
        self.remote_focus.connect(remote.focus, coda)
        self.remote_shoot.connect(remote.shoot, coda)
        self.remote_movie_start.connect(remote.start_movie, coda)
        self.remote_movie_stop.connect(remote.stop_movie, coda)
        self.remote_leave.connect(remote.leave, coda)
        self.remote_read_props.connect(remote.read_props, coda)
        self.remote_set_prop.connect(remote.set_prop, coda)
        self.remote_snapshot_names.connect(remote.snapshot_names, coda)
        self.remote_pause_live.connect(remote.pause_live, coda)
        self.remote_resume_live.connect(remote.resume_live, coda)

        live = self._live_factory()
        live.listening.connect(self.on_live_listening)
        live.frame_ready.connect(self.on_live_frame)
        live.stalled.connect(self.on_live_stalled)
        live.no_signal.connect(self.on_live_no_signal)
        live.resumed.connect(self.on_live_resumed)
        live.lens_changed.connect(self.on_lens_changed)

        self._remote_worker = remote
        self._live_worker = live
        self._remote_threads = [self._start_on_thread(remote),
                                self._start_on_thread(live, live.run)]

    @pyqtSlot(int)
    def on_live_listening(self, port: int) -> None:
        if self._remote_leaving or self._closing:
            return
        self._live_port = port
        if self._live_worker is not None:
            self._live_worker.arm()          # prima di startliveview: apre il passaggio
        self.remote_enter.emit(LIVE_QUALITY, port)

    @pyqtSlot(str, object)
    def on_remote_entered(self, mode: str, area: object) -> None:
        self._remote_entered = True
        self._props_fresh = False           # la modalità può essere tornata quella della ghiera
        if self._live_worker is not None and not (self._remote_leaving or self._closing):
            # arm() è partito prima che il rientro fosse eseguito: se il rientro ha
            # impiegato più di 3 s, la sentinella scatterebbe a vuoto. Si riparte da qui.
            self._live_worker.arm()
        logging.getLogger("olympus_connect.remote").debug("area di tocco ricevuta: %r", area)
        self._remote_panel.set_take_mode(mode)
        self._remote_panel.set_busy(False)
        self._remote_view.set_touch_area(area)
        self.statusBar().showMessage("Telecomando attivo.")
        if not (self._remote_leaving or self._closing):
            self._props_pending += 1
            self.remote_read_props.emit()

    @pyqtSlot()
    def on_live_frame(self) -> None:
        if self._live_worker is None:
            return
        if self._live_paused:
            if not self._live_waking:
                self._live_worker.take_frame()      # la coda del live appena spento
                return
            self._live_paused = False               # il live è tornato: si può scattare
            self._live_waking = False
        immagine = self._live_worker.take_frame()
        if immagine is not None:
            self._auto_reentries = 0                # il live c'è: i rientri hanno funzionato
            self._remote_view.set_frame(immagine)

    MAX_AUTO_REENTRIES = 2      # rientri automatici di fila, senza un fotogramma in mezzo
    _LIVE_STUCK = ("Il live non riparte. La sequenza è ferma finché non torna: premi Riprova, "
                   "o se non riparte fermala.")

    _LIVE_DOWN = {
        "stalled": "Live interrotto.",
        "no_signal": "Nessuna immagine dalla fotocamera. Se il firewall blocca il live, "
                     "consenti i pacchetti UDP da 192.168.0.10 (vedi il README, sezione Telecomando).",
    }

    @pyqtSlot()
    def on_live_stalled(self) -> None:
        self._live_lost("stalled")

    @pyqtSlot()
    def on_live_no_signal(self) -> None:
        self._live_lost("no_signal")

    def _live_lost(self, kind: str) -> None:
        """Il worker del live avvisa una volta sola per ogni interruzione:
        durante uno scatto l'avviso si ricorda e si mostra alla fine."""
        if self._live_paused:
            if self._live_waking:
                self._reenter_after_pause()         # riacceso, ma non è arrivato niente
            return
        self._live_down = kind
        if self._shot_in_flight:
            return
        if self._sequence.is_running():
            if not self._remote_entered:
                return                  # un rientro è già in corso: si aspetta il suo esito
            if self._auto_reentries < self.MAX_AUTO_REENTRIES:
                # Nessuno è lì a premere Riprova. Ma se il live non torna nemmeno
                # dopo un paio di rientri non si insiste ogni tre secondi: resta
                # l'avviso, e la sequenza si ferma da sola dopo tre scatti saltati.
                self._auto_reentries += 1
                self.on_live_retry()
                return
            self._remote_view.show_message(self._LIVE_STUCK, retry=True)
            return
        self._remote_view.show_message(self._LIVE_DOWN[kind], retry=True)

    @pyqtSlot()
    def on_live_resumed(self) -> None:
        self._live_down = None

    @pyqtSlot(float)
    def on_remote_exposing(self, seconds: float) -> None:
        """Posa lunga: conto alla rovescia sul live fermo, senza Riprova."""
        if (not self._remote_active or self._remote_leaving or self._closing
                or not self._shot_in_flight):
            return
        self._exposure_left = max(1, math.ceil(seconds))
        self._exposure_shown = True
        self._show_exposure()
        self._exposure_timer.start()

    def _on_exposure_tick(self) -> None:
        self._exposure_left -= 1
        if self._exposure_left <= 0:
            self._exposure_timer.stop()
        self._show_exposure()

    def _show_exposure(self) -> None:
        if self._exposure_left > 0:
            testo = f"Posa in corso: ancora circa {self._exposure_left} s"
        else:
            testo = "Quasi finito…"
        self._remote_view.show_message(testo)
        self.statusBar().showMessage(testo)

    def _stop_exposure(self) -> None:
        self._exposure_timer.stop()
        self._exposure_shown = False

    def _end_shot(self, riuscito: bool = False) -> None:
        """Fine dello scatto, riuscito o no: via il conto alla rovescia. Se il
        live è ancora fermo: dopo uno scatto riuscito riparte da solo alla fine
        dell'anteprima (dopo una posa da 60" la fotocamera non lo riprende,
        verificato sul campo il 2026-11-08); dopo un errore il solito Riprova."""
        mostrato = self._exposure_shown
        self._shot_in_flight = False
        self._stop_exposure()
        if self._remote_leaving or self._closing or not self._remote_active:
            return
        if self._live_down is not None and riuscito:
            self._live_restart_pending = True
            self._remote_view.show_message("Il live riparte…")
        elif self._live_down is not None:
            self._remote_view.show_message(self._LIVE_DOWN[self._live_down], retry=True)
        elif mostrato:
            self._remote_view.show_message("")

    REENTRY_RETRY_MS = 3000

    def _retry_reentry(self) -> None:
        if (not self._sequence.is_running() or self._remote_entered or not self._remote_active
                or self._remote_leaving or self._closing):
            return
        if self._auto_reentries >= self.MAX_AUTO_REENTRIES:
            self._remote_view.show_message(self._LIVE_STUCK, retry=True)
            return
        self._auto_reentries += 1
        self.on_live_retry()

    def _restart_live_after_shot(self) -> None:
        """Fine dell'anteprima dopo uno scatto col live fermo: lo fa ripartire
        come Riprova. Se nel frattempo il live è tornato da solo, niente."""
        if not self._live_restart_pending:
            return
        self._live_restart_pending = False
        if (not self._remote_active or self._remote_leaving or self._closing
                or self._shot_in_flight):
            return
        if self._live_down is None:
            if self._remote_view.message() == "Il live riparte…":
                self._remote_view.show_message("")
            return
        self.on_live_retry()

    def on_live_retry(self) -> None:
        if (not self._remote_active or self._live_worker is None
                or self._remote_leaving or self._closing):
            return
        if self._shot_in_flight:
            return      # rientrare in rec si accoderebbe allo scatto e romperebbe la sessione
        if self._remote_panel.is_recording():
            # rientrare in rec a video in corso lo interromperebbe
            self._remote_view.show_message(
                "Live interrotto durante la registrazione: ferma il video e riprova.", retry=True)
            return
        self._remote_entered = False
        self._live_down = None           # arm() fa ripartire la sentinella del live
        # Rientrare in rec rimette la modalità della ghiera fisica (verificato sul
        # campo il 2026-11-08): quella scelta dal PC si rimette dopo la rilettura.
        riga = self._remote_panel.props.row("takemode")
        self._mode_to_restore = riga.confirmed_value() if riga is not None else None
        self._remote_panel.set_busy(True)
        self._remote_view.show_message("Collegamento al live…")
        self._live_worker.arm()
        self.remote_enter.emit(LIVE_QUALITY, self._live_port)

    def on_remote_focus_requested(self, x: int, y: int) -> None:
        if (not self._remote_active or self._remote_leaving or self._closing
                or self._remote_panel.is_recording() or self._shot_in_flight
                or self._sequence.is_running() or self._sequence_preparing):
            return
        logging.getLogger("olympus_connect.remote").debug(
            "clic sul live: punto=(%d, %d) fotogramma=%r", x, y, self._remote_view.frame_size())
        self._remote_view.show_af(x, y)
        self.remote_focus.emit(x, y)

    @pyqtSlot(object)
    def on_remote_focused(self, result: object) -> None:
        self._remote_view.set_af_result(result)

    def on_remote_shoot(self) -> None:
        if (not self._remote_active or self._remote_leaving
                or not self._remote_panel._shoot.isEnabled()):
            return
        self._remote_panel.set_busy(True)
        self._shot_in_flight = True
        self.statusBar().showMessage("Scatto…")
        self.remote_shoot.emit()

    @pyqtSlot(bytes)
    def on_remote_shot(self, review: bytes) -> None:
        self._last_shot_end = time.monotonic()
        self._end_shot(riuscito=True)
        self._remote_panel.set_busy(False)
        self.statusBar().showMessage("Scatto eseguito.", 4000)
        durata = self._sequence_review_ms()
        self._sequence.shot_done(True)      # può chiudere la sequenza e uscire dal telecomando
        if self._remote_leaving or not self._remote_active:
            return
        if not self._remote_view.show_review(review, durata):
            self._after_shot_review()       # niente anteprima da aspettare

    def on_remote_movie_toggle(self) -> None:
        if (not self._remote_active or self._remote_leaving
                or not self._remote_panel._movie.isEnabled()):
            return
        self._remote_panel.set_busy(True)
        if self._remote_panel.is_recording():
            self.remote_movie_stop.emit()
        else:
            self.remote_movie_start.emit()

    @pyqtSlot()
    def on_remote_movie_started(self) -> None:
        self._remote_panel.set_busy(False)
        self._remote_panel.set_recording(True)
        self._remote_view.set_focus_enabled(False)
        self.statusBar().showMessage("Registrazione in corso.")

    @pyqtSlot()
    def on_remote_movie_stopped(self) -> None:
        self._remote_panel.set_busy(False)
        self._remote_panel.set_recording(False)
        self._remote_view.set_focus_enabled(True)
        self.statusBar().showMessage("Video salvato sulla scheda.", 4000)

    @pyqtSlot(str)
    def on_remote_failed(self, message: str) -> None:
        era_scatto = self._shot_in_flight
        if era_scatto:
            self._last_shot_end = time.monotonic()
        self._end_shot()
        # Senza `entered` il live non c'è: i comandi restano spenti.
        self._remote_panel.set_busy(not self._remote_entered or self._remote_leaving)
        self.statusBar().showMessage(message)
        if self._remote_view.af_pending():
            self._remote_view.set_af_result(False)
        if not self._remote_entered or not self._remote_view.has_frame():
            self._remote_view.show_message(message, retry=True)
        if (not era_scatto and not self._remote_entered and self._sequence.is_running()
                and self._remote_active and not self._remote_leaving and not self._closing):
            # Un rientro automatico non è riuscito: la sentinella del live ha già
            # avvisato e non lo farà più. Un altro tentativo fra poco, nel limite.
            QTimer.singleShot(self.REENTRY_RETRY_MS, self._retry_reentry)
        if era_scatto:
            self._sequence.shot_done(False)
            # Il live si è perso durante lo scatto, e lo scatto è fallito: la
            # sentinella ha già avvisato (una volta sola), quindi il rientro
            # automatico si chiede qui. Altrimenti la sequenza si fermerebbe.
            if (self._sequence.is_running() and self._live_down is not None
                    and self._remote_active and not self._remote_leaving and not self._closing):
                self._live_lost(self._live_down)

    @pyqtSlot(object)
    def on_remote_props(self, props: object) -> None:
        if not self._remote_active:
            return
        if not props:
            # risposta 200 con un corpo illeggibile: come una lettura fallita
            self.on_remote_props_failed("Elenco delle impostazioni illeggibile")
            return
        self._props_pending = max(0, self._props_pending - 1)
        self._remote_panel.props.set_props(props)
        self._remote_props = props
        self._props_fresh = True
        self._update_sequence_summary()
        voluta, self._mode_to_restore = self._mode_to_restore, None
        in_sequenza = self._sequence.is_running() and self._sequence_mode is not None
        if in_sequenza:
            voluta = self._sequence_mode    # vale sempre, anche se il rientro non l'ha ricordata
        modo = props.get("takemode")
        if (voluta and modo is not None and modo.value != voluta and voluta in modo.values
                and not self._remote_leaving and not self._closing):
            if in_sequenza:
                self._mode_restores += 1
                if self._mode_restores > self.MAX_MODE_RESTORES:
                    # La fotocamera rifiuta: meglio fermarsi che scattare in un'altra
                    # modalità. La sequenza resta non pronta e si chiude da sola.
                    self.statusBar().showMessage(
                        f"La fotocamera non torna in {voluta}: la sequenza non scatta.")
                    return
            self._remote_panel.props.change("takemode", voluta)
        elif in_sequenza and modo is not None and modo.value == voluta:
            self._mode_restores = 0

    @pyqtSlot(str)
    def on_remote_props_failed(self, message: str) -> None:
        if not self._remote_active:
            return
        self._props_pending = max(0, self._props_pending - 1)
        self._mode_to_restore = None        # senza l'elenco non si sa che modalità rimettere
        self._props_fresh = False
        self._remote_panel.props.set_unavailable("Impostazioni non disponibili")
        self.statusBar().showMessage(message)
        if self._sequence.is_running():
            # Senza l'elenco la sequenza non sa in che modalità è la fotocamera:
            # si rilegge fra poco, invece di scattare alla cieca.
            QTimer.singleShot(self.PROPS_RETRY_MS, self._reread_props)

    PROPS_RETRY_MS = 2000

    def _reread_props(self) -> None:
        if (not self._sequence.is_running() or not self._remote_active or not self._remote_entered
                or self._remote_leaving or self._closing or self._props_pending):
            return
        self._props_pending += 1
        self.remote_read_props.emit()

    @pyqtSlot(object)
    def on_lens_changed(self, limits: object) -> None:
        self._remote_panel.props.set_lens_limits(limits)

    def on_remote_prop_changed(self, name: str, value: str) -> None:
        if (not self._remote_active or self._remote_leaving or self._closing
                or not self._remote_entered or self._remote_panel.is_recording()):
            return
        self.statusBar().showMessage("Impostazione inviata alla fotocamera…", 2000)
        self._props_pending += 1            # set_prop rilegge sempre l'elenco: una risposta sola
        self.remote_set_prop.emit(name, value)

    # -- sequenze -------------------------------------------------------------

    def _confirm(self, title: str, text: str) -> bool:
        risposta = QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return risposta == QMessageBox.StandardButton.Yes

    def _sequence_fps(self) -> int:
        valore = int(self._settings.value("sequence/fps", DEFAULT_FRAME_RATE))
        return valore if valore in FRAME_RATES else DEFAULT_FRAME_RATE

    def _update_sequence_summary(self) -> None:
        sezione = self._remote_panel.sequence
        piano = sezione.plan()
        occupata = shot_busy_seconds(self._remote_props)
        sezione.set_summary(piano.summary(occupata, self._sequence_fps()),
                            warning=piano.cycle(occupata) > piano.interval)

    def _on_sequence_changed(self) -> None:
        sezione = self._remote_panel.sequence
        self._settings.setValue("sequence/interval", sezione.interval())
        self._settings.setValue("sequence/duration", sezione.duration())
        self._update_sequence_summary()

    def _sequence_ready(self) -> bool:
        """Il telecomando può scattare adesso: lo legge il regista a ogni giro."""
        return (self._remote_active and self._remote_entered and not self._remote_leaving
                and not self._closing and not self._shot_in_flight
                and not self._remote_panel.is_recording()
                and self._live_down is None and not self._live_restart_pending
                and self._props_pending == 0 and self._mode_to_restore is None
                and not self._live_paused and self._sequence_mode_ok())

    MAX_MODE_RESTORES = 3       # tentativi di fila di rimettere la modalità della sequenza

    def _sequence_mode_ok(self) -> bool:
        """Durante una sequenza: la fotocamera è nella modalità di partenza,
        secondo un elenco letto dopo l'ultimo ingresso in modalità scatto."""
        if self._sequence_mode is None:
            return True
        modo = self._remote_props.get("takemode") if self._props_fresh else None
        return modo is not None and modo.value == self._sequence_mode

    def on_sequence_start(self, interval: int, duration: int) -> None:
        if self._sequence.is_running() or self._sequence_preparing:
            return
        if not self._sequence_ready():
            # p. es. il live che riparte dopo uno scatto: il pulsante è acceso ma
            # il telecomando non può ancora scattare; un clic muto confonde.
            self.statusBar().showMessage("Il telecomando non è pronto: riprova fra poco.", 4000)
            return
        self._sequence_preparing = True
        self._sequence_plan = SequencePlan(interval, duration)
        self._remote_panel.set_sequence_running(True)
        self._remote_panel.sequence.set_progress("Preparo la sequenza…", "")
        self.statusBar().showMessage("Sequenza: leggo la scheda…")
        attesa = max(0.0, self.SNAPSHOT_SETTLE - (time.monotonic() - self._last_shot_end))
        self._snapshot_timer.start(int(attesa * 1000))

    def _sequence_snapshot(self) -> None:
        if (not self._sequence_preparing or not self._remote_active
                or self._remote_leaving or self._closing):
            return
        self.remote_snapshot_names.emit()

    @pyqtSlot(object)
    def on_remote_names(self, names: object) -> None:
        """L'istantanea dei nomi è arrivata (None: non riuscita): parte la sequenza."""
        if not self._sequence_preparing:
            return
        self._sequence_preparing = False
        if not self._sequence_ready() or self._sequence_plan is None:
            self._remote_panel.set_sequence_running(False)
            self.statusBar().showMessage("Sequenza non avviata: il telecomando non è pronto.")
            return
        self._sequence_before = names
        modo = self._remote_props.get("takemode")
        self._sequence_mode = modo.value if modo is not None else None
        self._mode_restores = 0
        bloccato = self._sequence.start(self._sequence_plan,
                                        shot_busy_seconds(self._remote_props))
        self.statusBar().showMessage(
            "Sequenza in corso." if bloccato else
            "Sequenza in corso. Attenzione: non riesco a impedire la sospensione del PC.")

    def on_sequence_stop(self) -> None:
        if self._sequence_preparing:
            self._sequence_preparing = False
            self._snapshot_timer.stop()         # un'istantanea in attesa non deve più partire
            self._remote_panel.set_sequence_running(False)
            self.statusBar().showMessage("Sequenza annullata.", 4000)
            return
        self._sequence.stop()

    def _sequence_shoot(self) -> None:
        """Lo scatto chiesto dal regista: la stessa strada del pulsante Scatta."""
        if not self._sequence_ready():
            self._sequence.shot_done(False)
            return
        self._remote_panel.set_busy(True)
        self._shot_in_flight = True
        self.statusBar().showMessage("Sequenza: scatto…")
        self.remote_shoot.emit()

    def _sequence_review_ms(self) -> int | None:
        """Durante una sequenza l'anteprima dura al più metà dell'intervallo."""
        if not self._sequence.is_running() or self._sequence_plan is None:
            return None
        return int(min(2.0, self._sequence_plan.interval / 2) * 1000)

    @pyqtSlot(object)
    def _on_sequence_ended(self, result: object) -> None:
        """Finita, fermata o interrotta: si esce dal telecomando; l'elenco che
        segue l'uscita dirà quali foto sono della sequenza."""
        self._remote_panel.set_sequence_running(False)
        self._sequence_mode = None
        if result is None:
            return
        self._keep_sequence_end(result)
        self.statusBar().showMessage(
            "Sequenza interrotta: la fotocamera non risponde." if result.aborted
            else "Sequenza finita.")
        if self._remote_active and not self._remote_leaving and not self._closing:
            self.exit_remote(self._remote_exit_view)

    def _keep_sequence_end(self, result: SequenceResult) -> None:
        """Una sequenza è finita: il risultato e gli elenchi per riconoscerne le
        foto restano fino al primo elenco riuscito, anche se intanto si rientra
        nel telecomando. Solo la fine di un'altra sequenza li sostituisce."""
        self._sequence_result = result
        self._sequence_end_before = self._sequence_before
        self._sequence_end_known = self._sequence_known

    def _offer_sequence(self, jobs: list) -> None:
        """Dopo l'elenco che segue una sequenza: quali foto ha fatto, e cosa farne."""
        if self._sequence_result is None or self._sequence_dialog is not None:
            return
        risultato, self._sequence_result = self._sequence_result, None
        file = [j.media for j in jobs]
        riserva = self._sequence_end_before is None
        prima = self._sequence_end_known if riserva else self._sequence_end_before
        self._sequence_end_before = self._sequence_end_known = None
        sconosciute = prima is None         # né istantanea né un elenco di prima
        prima = prima or set()
        nuovi = [m for m in file if m.camera_path not in prima]
        foto = [] if sconosciute else sequence_files(prima, file)
        risoluzione = str(self._settings.value("sequence/resolution", DEFAULT_RESOLUTION))
        dialog = SequenceEndDialog(
            photos=len(foto), skipped=risultato.skipped, aborted=risultato.aborted,
            only_raw=not foto and any(m.kind is MediaKind.RAW for m in nuovi),
            since_remote_entry=riserva, ffmpeg=find_ffmpeg() is not None,
            resolution=risoluzione, fps=self._sequence_fps(), unknown=sconosciute,
            parent=self)
        self._sequence_dialog = dialog
        self._sequence_offer = (foto, risultato)
        dialog.finished.connect(self._on_sequence_dialog)
        dialog.open()                       # non blocca: i worker continuano a parlare

    def _on_sequence_dialog(self, _codice: int) -> None:
        dialog, self._sequence_dialog = self._sequence_dialog, None
        offerta, self._sequence_offer = self._sequence_offer, None
        if dialog is None or offerta is None:
            return
        foto, risultato = offerta
        scelta = dialog.choice()
        dialog.deleteLater()
        if self._closing:
            return
        if not scelta.download or not foto:
            self.statusBar().showMessage("Le foto della sequenza restano sulla scheda.")
            return
        if self._downloading or self._timelapse_worker is not None:
            # Non succede per come è fatta l'interfaccia, ma due trasferimenti
            # insieme si pesterebbero i piedi nella barra di stato.
            self.statusBar().showMessage(
                "Un trasferimento è in corso: le foto della sequenza restano sulla scheda.")
            return
        self._settings.setValue("sequence/fps", scelta.fps)
        self._settings.setValue("sequence/resolution", scelta.resolution)
        self._update_sequence_summary()
        radice = Path(self.dest_root())
        try:
            cartella = radice / folder_name(risultato.started_at,
                                            lambda n: (radice / n).exists())
            lavori = plan_flat(foto, cartella)      # stesso ordine delle foto: quello di scatto
        except OSError as exc:
            # un'eccezione che esce da uno slot chiude il programma
            self.statusBar().showMessage(f"Cartella della sequenza non raggiungibile: {exc}")
            return
        self._sequence_job = SequenceJob(cartella, [lavoro.dest for lavoro in lavori],
                                         scelta.video, scelta.resolution, scelta.fps)
        self._start_download(lavori, mark_model=False)
        self._keep_awake(True)              # dopo una sequenza lunga l'utente può essere via

    def _keep_awake(self, attivo: bool) -> None:
        """Tiene sveglio il PC durante scarico e video di una sequenza."""
        if self._transfer_inhibitor is not None:
            self._transfer_inhibitor.stop()
            self._transfer_inhibitor = None
        if attivo:
            blocco = self._inhibitor_factory()
            if blocco.start(6 * 3600):
                self._transfer_inhibitor = blocco

    def _after_sequence_download(self, lavoro: SequenceJob) -> None:
        presenti = [p for p in lavoro.frames if p.exists()]
        self._sequence_missing = len(lavoro.frames) - len(presenti)
        mancanti = (f" ({self._sequence_missing} non scaricate)"
                    if self._sequence_missing else "")
        if not lavoro.video:
            self._keep_awake(False)
            self.statusBar().showMessage(
                f"Sequenza scaricata: {len(presenti)} foto in {lavoro.folder}{mancanti}")
            return
        if len(presenti) < 2:
            self._keep_awake(False)
            self.statusBar().showMessage(
                "Sequenza scaricata, ma per il video servono almeno due foto.")
            return
        worker = TimelapseWorker(presenti, lavoro.folder / VIDEO_NAME, lavoro.fps,
                                 RESOLUTIONS[lavoro.resolution])
        worker.progress.connect(self.on_timelapse_progress)
        worker.done.connect(self.on_timelapse_done)
        worker.failed.connect(self.on_timelapse_failed)
        worker.cancelled.connect(self.on_timelapse_cancelled)
        self._timelapse_worker = worker
        self._update_remote_button()        # niente telecomando mentre si monta il video
        self._show_transfer(True)
        self._file_label.setText("Montaggio del video…")
        self._rate_label.setText("")
        self._file_bar.setValue(0)
        self._total_bar.setValue(0)
        self.statusBar().showMessage("Sequenza scaricata: monto il video…")
        self._start_on_thread(worker, worker.run)

    @pyqtSlot(int, int)
    def on_timelapse_progress(self, scritti: int, totale: int) -> None:
        self._file_label.setText(f"Montaggio del video: {scritti} di {totale}")
        self._set_progress(self._total_bar, scritti, totale)

    def _timelapse_over(self, messaggio: str) -> None:
        self._timelapse_worker = None
        self._keep_awake(False)
        self._update_remote_button()
        self._file_label.setText("")
        self._total_bar.setValue(0)
        self._show_transfer(False)
        self.statusBar().showMessage(messaggio)

    @pyqtSlot(str)
    def on_timelapse_done(self, percorso: str) -> None:
        mancanti = (f" ({self._sequence_missing} foto non scaricate, il video ne è senza)"
                    if self._sequence_missing else "")
        self._timelapse_over(f"Video pronto: {percorso}{mancanti}")

    @pyqtSlot(str)
    def on_timelapse_failed(self, messaggio: str) -> None:
        self._timelapse_over(messaggio)

    @pyqtSlot()
    def on_timelapse_cancelled(self) -> None:
        self._timelapse_over("Montaggio annullato: le foto restano nella cartella.")

    def _after_shot_review(self) -> None:
        """Fine dell'anteprima di uno scatto (o subito, se l'anteprima non c'è).

        In una sequenza con intervalli lunghi il live si spegne fino a poco
        prima dello scatto successivo: sul campo si riaccende in 0,3 s senza
        rientrare in modalità scatto, e la batteria dura di più.
        """
        if (self._sequence.may_pause_live() and self._remote_active
                and not self._remote_leaving and not self._closing
                and not self._shot_in_flight and self._live_worker is not None):
            # Dopo una posa lunga il live non riparte con la sola riaccensione: alla
            # sveglia serve subito il rientro, senza aspettare 3 s per scoprirlo.
            self._pause_after_stall = self._live_restart_pending or self._live_down is not None
            self._live_restart_pending = False
            self._live_down = None
            self._live_paused = True        # da subito: nessuno scatto finché il live non torna
            self._live_waking = False
            self._live_worker.disarm()      # il live si ferma di proposito: niente avvisi
            self._remote_view.show_message("Live in pausa fino al prossimo scatto.")
            self.remote_pause_live.emit()
            return
        self._restart_live_after_shot()

    def _wake_live(self) -> None:
        """Il regista avvisa 5 s prima dello scatto: il live in pausa si riaccende."""
        if not self._live_paused or self._live_waking:
            return
        if (not self._remote_active or self._remote_leaving or self._closing
                or self._live_worker is None):
            return
        if self._pause_after_stall:
            self._pause_after_stall = False
            self._reenter_after_pause()
            return
        self._live_waking = True
        self._remote_view.show_message("Il live riparte…")
        self._live_worker.arm()             # riapre il passaggio nel firewall, riarma la sentinella
        self.remote_resume_live.emit(self._live_port)

    @pyqtSlot(bool)
    def on_remote_live_resumed(self, ok: bool) -> None:
        if not ok and self._live_paused:
            self._reenter_after_pause()

    def _reenter_after_pause(self) -> None:
        """La riaccensione semplice non è bastata: rientro completo in modalità
        scatto, con lo stesso limite dei rientri automatici."""
        self._live_paused = False
        self._live_waking = False
        self._live_lost("no_signal")

    def exit_remote(self, view: str) -> None:
        if not self._remote_active or self._remote_leaving:
            return
        if self._sequence.is_running() or self._sequence_preparing:
            conferma = self._confirm("Sequenza in corso",
                                     "Uscendo dal telecomando la sequenza si fermerà. Uscire?")
            # La domanda fa girare gli eventi: mentre era aperta la sequenza può
            # essere finita da sola, e l'uscita essere già partita o conclusa.
            if not self._remote_active or self._remote_leaving:
                return
            if not conferma:
                self._view_grid.setChecked(False)
                self._view_table.setChecked(False)
                self._view_remote.setChecked(True)
                return
            self._sequence_preparing = False
            self._snapshot_timer.stop()
            risultato = self._sequence.abort()
            self._remote_panel.set_sequence_running(False)
            if risultato is not None:
                self._keep_sequence_end(risultato)
        self._remote_leaving = True
        self._remote_exit_view = view
        self._remote_panel.set_busy(True)
        self._stop_exposure()
        self._remote_view.show_message("Uscita dal telecomando…")   # toglie Riprova
        self.statusBar().showMessage("Uscita dal telecomando…")
        if self._live_worker is not None:
            self._live_worker.disarm()
        self.remote_leave.emit()

    @pyqtSlot()
    def on_remote_left(self) -> None:
        self._teardown_remote(self._close_deadline if self._closing else None)
        self._remote_active = False
        self._remote_entered = False
        self._remote_leaving = False
        self._shot_in_flight = False
        self._live_down = None
        self._mode_to_restore = None
        self._sequence_preparing = False
        self._snapshot_timer.stop()
        self._props_pending = 0
        self._live_paused = False
        self._live_waking = False
        self._pause_after_stall = False
        self._sequence_mode = None
        self._props_fresh = False
        residuo = self._sequence.abort()        # di norma è già ferma
        if residuo is not None and self._sequence_result is None:
            self._keep_sequence_end(residuo)
        self._remote_panel.set_sequence_running(False)
        for scorciatoia in (self._shortcut_shoot, self._shortcut_movie):
            scorciatoia.setEnabled(False)
        self._remote_panel.set_recording(False)
        self._remote_panel.set_busy(False)
        self._remote_view.clear()
        self._inspector_pages.setCurrentIndex(0)
        self._inspector.set_title("Anteprima")
        if self._closing:
            return          # chiusura in corso: niente nuovo elenco né nuovi thread
        self._set_library_controls(True)
        self._suspend_thumbnails(False)
        self.set_view(self._remote_exit_view)
        self._update_remote_button()
        # Un ListWorker nuovo ha un client nuovo, senza modalità ricordata:
        # ensure_play_mode rimette davvero la fotocamera in riproduzione, e
        # le foto e i video appena fatti compaiono nell'elenco. Dopo una
        # sequenza l'elenco aspetta che la scheda veda l'ultimo scatto.
        attesa = self.SNAPSHOT_SETTLE - (time.monotonic() - self._last_shot_end)
        if self._sequence_result is not None and attesa > 0:
            self._listing = True            # niente telecomando nel frattempo
            self._update_remote_button()
            QTimer.singleShot(int(attesa * 1000), self._refresh_after_sequence)
            return
        self.on_refresh()

    def _refresh_after_sequence(self) -> None:
        if self._closing:
            return
        self.on_refresh()

    def _teardown_remote(self, deadline: float | None = None) -> None:
        """Ferma i thread del telecomando entro `deadline` (tempo monotonic).

        Un thread ancora dentro un comando alla scadenza non si distrugge con
        la finestra: si stacca, e lo si lascia finire.
        """
        if deadline is None:
            deadline = time.monotonic() + 2.0
        self._stop_exposure()
        if self._live_worker is not None:
            self._live_worker.stop()
        workers = [self._remote_worker, self._live_worker]
        for segnale in (self.remote_enter, self.remote_focus, self.remote_shoot,
                        self.remote_movie_start, self.remote_movie_stop, self.remote_leave,
                        self.remote_read_props, self.remote_set_prop,
                        self.remote_snapshot_names, self.remote_pause_live,
                        self.remote_resume_live):
            try:
                segnale.disconnect()
            except TypeError:
                pass
        for thread, worker in zip(self._remote_threads, workers):
            thread.quit()
            resto = max(0, int((deadline - time.monotonic()) * 1000))
            if thread in self._threads:
                self._threads.remove(thread)
            if thread.wait(resto):
                if worker is not None:
                    worker.blockSignals(True)
            else:
                # Niente blockSignals: il worker sta ancora emettendo da un
                # altro thread. Qt scollega le sue connessioni alla distruzione
                # della finestra.
                thread.setParent(None)
                sip.transferto(thread, None)       # mai distrutto da Python
                _DETACHED.append((thread, worker))
        self._remote_threads = []
        self._remote_worker = None
        self._live_worker = None

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Chiusura ordinata: prima si zittiscono i worker, poi si fermano
        i thread.

        Senza il primo passo un segnale gia' accodato dal thread di un
        worker verrebbe consegnato alla finestra dopo la sua distruzione.
        Chiudere durante un trasferimento e' il caso normale, non un caso
        limite: la fotocamera e' lenta e l'utente se ne va.
        """
        in_sequenza = self._sequence.is_running() or self._sequence_preparing
        posa = self._shot_in_flight and self._exposure_shown
        if in_sequenza or posa:
            # Durante uno scatto il comando di uscita resta in coda dietro di lui e
            # non parte più: la fotocamera rimane in modalità scatto.
            testo = "Chiudendo il programma la sequenza si fermerà." if in_sequenza else ""
            if self._shot_in_flight:
                testo += (" Uno scatto è in corso: la fotocamera resterà in modalità scatto"
                          " (spegnila e riaccendila).")
            if not self._confirm("Sequenza in corso" if in_sequenza else "Posa in corso",
                                 testo.strip() + " Chiudere?"):
                event.ignore()
                return
            self._sequence_preparing = False
            self._sequence.abort()
        self._snapshot_timer.stop()
        self._sequence_result = None
        self._keep_awake(False)
        self._closing = True
        self._stop_exposure()
        self._close_deadline = time.monotonic() + 3.0      # una scadenza per tutto
        if self._remote_active and self._remote_worker is not None:
            # La fotocamera deve tornare in riproduzione anche chiudendo,
            # ma senza tenere in ostaggio la chiusura: al massimo 3 s in tutto.
            attesa = QEventLoop()
            self._remote_worker.left.connect(attesa.quit)
            QTimer.singleShot(3000, attesa.quit)
            if not self._remote_leaving:
                self._remote_leaving = True
                if self._live_worker is not None:
                    self._live_worker.disarm()
                self.remote_leave.emit()
            attesa.exec()
            self._teardown_remote(self._close_deadline)
            self._remote_active = False
        if self._download_worker is not None:
            self._download_worker.cancel()     # flag atomico, sicuro fra thread
        if self._timelapse_worker is not None:
            self._timelapse_worker.cancel()         # thread-safe: termina ffmpeg
        if self._sequence_dialog is not None:
            dialog, self._sequence_dialog = self._sequence_dialog, None
            self._sequence_offer = None
            try:
                dialog.finished.disconnect(self._on_sequence_dialog)
            except TypeError:
                pass
            dialog.close()

        # Prima si fermano i thread, poi si zittiscono i worker. L'ordine
        # inverso e' una corsa critica: blockSignals dal thread della GUI
        # mentre il worker e' dentro un emit tocca la stessa lista di
        # connessioni da due thread, e il processo aborta.
        for thread in self._threads:
            thread.quit()
            if not thread.wait(2000):
                # Ancora dentro un lavoro: distruggerlo con la finestra fa abortire
                # il processo. Si stacca, come i thread del telecomando.
                thread.setParent(None)
                sip.transferto(thread, None)
                _DETACHED.append((thread, None))
        self._threads.clear()

        for worker in (self._list_worker, self._thumb_worker,
                       self._preview_worker, self._download_worker, self._timelapse_worker):
            if worker is not None:
                worker.blockSignals(True)
        super().closeEvent(event)


def application_icon() -> QIcon:
    """Preferisce l'icona installata nel tema, così una personalizzazione
    dell'utente ha la precedenza; altrimenti la copia nel pacchetto."""
    dal_tema = QIcon.fromTheme("olympus-connect")
    if not dal_tema.isNull():
        return dal_tema
    risorsa = resources.files("olympus_connect") / "resources/olympus-connect.svg"
    with resources.as_file(risorsa) as percorso:
        return QIcon(str(percorso))


def configure_logging() -> None:
    """Diagnostica per le prove sul campo: OLYMPUS_DEBUG=1 la attiva."""
    if os.environ.get("OLYMPUS_DEBUG") == "1":
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                            format="%(asctime)s %(name)s %(message)s")


def main() -> int:
    configure_logging()
    app = QApplication(sys.argv)
    # Su Wayland è questo, non il titolo, che associa la finestra al file
    # .desktop: senza, il pannello di KDE mostra un'icona generica.
    app.setDesktopFileName("olympus-connect")
    app.setApplicationName("Olympus Connect")
    app.setWindowIcon(application_icon())

    theme.apply_palette(app)
    theme.tune_fonts(app)
    app.setStyleSheet(theme.STYLESHEET)

    window = MainWindow()
    window.show()
    return app.exec()
