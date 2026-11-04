"""Finestra principale: rail, area di lavoro, ispettore, barra di stato.

I due pannelli laterali si ritirano perché lo schermo di riferimento è alto
768 px: a pannelli fissi il contenuto resterebbe una fascia. L'animazione
serve a dire dove sono andati, non a fare scena.
"""
from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

from PyQt6.QtCore import QSettings, QSize, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from ..core.downloader import JobState
from ..core.errors import NetworkUnavailable, OlympusError
from ..core.model import MediaKind
from ..core.network import CameraNetwork
from ..core.protocol import OlympusClient
from ..core.stats import format_duration, format_rate, format_size
from . import icons, theme
from .grid import MediaFilter, MediaGrid, MediaListModel, MediaTable
from .panels import CollapsiblePanel, SectionLabel, Separator
from .preview import PreviewView
from .workers import (
    DownloadWorker, ListWorker, PreviewWorker, ThumbWorker, describe_error,
)

DEFAULT_DEST = Path.home() / "Immagini" / "Olympus"

VIEW_GRID = "griglia"
VIEW_TABLE = "elenco"
VIEW_LABELS = {VIEW_GRID: "Miniature", VIEW_TABLE: "Elenco dettagliato"}

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

    def __init__(self, client_factory=OlympusClient,
                 network_factory=CameraNetwork,
                 settings: QSettings | None = None) -> None:
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

        self._model = MediaListModel()
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
        self._view_grid.clicked.connect(lambda: self.set_view(VIEW_GRID))
        self._view_table.clicked.connect(lambda: self.set_view(VIEW_TABLE))
        for pulsante in self._filter_boxes.values():
            pulsante.toggled.connect(self.on_filter_changed)
        self.on_filter_changed()
        self._refresh_counts()
        self._update_selection_label()

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
        corpo = pannello.layout_body()

        self._preview = PreviewView()
        corpo.addWidget(self._preview)

        corpo.addWidget(SectionLabel("Dettagli"))
        self._details = QGridLayout()
        self._details.setContentsMargins(0, 0, 0, 0)
        self._details.setHorizontalSpacing(10)
        self._details.setVerticalSpacing(4)
        self._details.setColumnStretch(1, 1)
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

    @pyqtSlot(list)
    def on_listed(self, jobs: list) -> None:
        self._model.set_jobs(jobs)
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
        for worker in (self._thumb_worker, self._preview_worker):
            if worker is not None:
                worker.set_suspended(suspended)
        if not suspended and self.current_view() == VIEW_GRID:
            self._grid.request_visible_thumbnails()

    @pyqtSlot(str)
    def on_failed(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self._empty_hint.setText(message)

    # ------------------------------------------------------------------
    # selezione e anteprima
    # ------------------------------------------------------------------

    def on_selection_changed(self, *_args) -> None:
        self._update_selection_label()
        jobs = self.selected_jobs()
        if not jobs:
            self._preview.show_message("Nessun file selezionato")
            self._clear_details()
            return
        self._show_details(jobs[-1])
        if len(jobs) == 1 and self._preview_worker is not None:
            self._preview_path = jobs[0].media.camera_path
            self._preview.show_message("Caricamento anteprima…")
            self.preview_needed.emit(self._preview_path)

    def _update_selection_label(self) -> None:
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

        # Miniature e anteprima sospese: il server della fotocamera è un
        # microcontrollore e due trasferimenti in volo lo degradano.
        self._suspend_thumbnails(True)
        worker = DownloadWorker(self._client_factory(), jobs)
        worker.file_started.connect(self._file_label.setText)
        worker.file_progress.connect(self._update_file_bar)
        worker.total_progress.connect(self._update_total_bar)
        worker.stats_changed.connect(self.on_stats)
        worker.file_completed.connect(self.on_file_completed)
        worker.file_failed.connect(
            lambda name, msg: self.statusBar().showMessage(f"{name}: {msg}"))
        worker.paused.connect(self.on_paused)
        worker.finished.connect(self.on_download_finished)

        self._download_worker = worker
        self._show_transfer(True)
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
        if self._download_worker is not None:
            self._download_worker.cancel()
        self._show_transfer(True, con_riprova=True)
        self._cancel_button.setEnabled(False)
        self._suspend_thumbnails(False)
        self._file_label.setText("Trasferimento annullato")

    def on_resume(self) -> None:
        if self._download_worker is None:
            return
        self._show_transfer(True)
        self._suspend_thumbnails(True)
        self._download_worker.resume()

    @pyqtSlot(str)
    def on_paused(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self._show_transfer(True, con_riprova=True)
        self._cancel_button.setEnabled(False)
        self._file_label.setText("In pausa: la fotocamera si è disconnessa")
        self._suspend_thumbnails(False)

    @pyqtSlot()
    def on_download_finished(self) -> None:
        self._file_label.setText("")
        self._rate_label.setText("")
        self._file_bar.setValue(0)
        self._total_bar.setValue(0)
        self._show_transfer(False)
        self._suspend_thumbnails(False)
        self.statusBar().showMessage("Download completato.")
        self.on_refresh()

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Chiusura ordinata: prima si zittiscono i worker, poi si fermano
        i thread.

        Senza il primo passo un segnale gia' accodato dal thread di un
        worker verrebbe consegnato alla finestra dopo la sua distruzione.
        Chiudere durante un trasferimento e' il caso normale, non un caso
        limite: la fotocamera e' lenta e l'utente se ne va.
        """
        if self._download_worker is not None:
            self._download_worker.cancel()     # flag atomico, sicuro fra thread

        # Prima si fermano i thread, poi si zittiscono i worker. L'ordine
        # inverso e' una corsa critica: blockSignals dal thread della GUI
        # mentre il worker e' dentro un emit tocca la stessa lista di
        # connessioni da due thread, e il processo aborta.
        for thread in self._threads:
            thread.quit()
            thread.wait(2000)
        self._threads.clear()

        for worker in (self._list_worker, self._thumb_worker,
                       self._preview_worker, self._download_worker):
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


def main() -> int:
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
