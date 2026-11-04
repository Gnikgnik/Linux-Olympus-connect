"""Le due viste sull'elenco: griglia a miniature ed elenco dettagliato.

Un solo modello a colonne alimenta entrambe: la griglia usa la colonna del
nome, l'elenco mostra tutte le colonne e ordina.

QListView in IconMode richiede le righe solo quando servono: i 1161 file
della scheda di riferimento non creano 1161 widget, e le miniature si
scaricano solo per ciò che è effettivamente visibile — decisivo, perché
ogni miniatura è una richiesta HTTP alla fotocamera.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PyQt6.QtCore import (
    QAbstractTableModel, QModelIndex, QRect, QSize, Qt, QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtCore import QEasingCurve
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QListView, QStyle, QStyledItemDelegate,
    QTreeView,
)

from ..core.downloader import Job, JobState
from . import icons, theme
from ..core.model import MediaKind
from ..core.stats import format_size as _format_size

JOB_ROLE = Qt.ItemDataRole.UserRole + 1

THUMBNAIL_SIZE = QSize(160, 120)

COLUMNS = ["Nome", "Data e ora", "Tipo", "Dimensione", "Stato"]
COL_NAME, COL_DATE, COL_KIND, COL_SIZE, COL_STATE = range(5)

_STATE_ICON = {
    JobState.NEW: ("pending", theme.TEXT_DIM),
    JobState.PRESENT: ("check", theme.OK),
    JobState.INCOMPLETE: ("retry", theme.WARN),
}

_STATE_TEXT = {
    JobState.NEW: "da scaricare",
    JobState.PRESENT: "già presente",
    JobState.INCOMPLETE: "da riscaricare",
}

_KIND_TEXT = {
    MediaKind.JPEG: "JPEG",
    MediaKind.RAW: "RAW",
    MediaKind.MOVIE: "Video",
    MediaKind.OTHER: "altro",
}

# I file senza data leggibile vanno in fondo all'ordinamento per data,
# non in testa: una data mancante non è «molto vecchia».
_NO_DATE = datetime.max


_placeholder_icon: QIcon | None = None


def placeholder_icon() -> QIcon:
    """Riquadro vuoto della dimensione definitiva di una miniatura.

    Serve a un difetto preciso: senza decorazione il rettangolo
    dell'elemento si dimensiona sul solo testo (46x17 px misurati), e
    quando la miniatura arriva viene disegnata dentro quel rettangolo —
    si vede una striscia. Restituendo sempre un'icona, la misura è
    stabile dal primo layout e l'immagine vera la sostituisce senza
    ricalcoli.

    Costruito alla prima richiesta: QPixmap richiede un QApplication vivo,
    quindi non può essere una costante di modulo.
    """
    global _placeholder_icon
    if _placeholder_icon is None:
        pixmap = QPixmap(THUMBNAIL_SIZE)
        pixmap.fill(QColor(0, 0, 0, 20))
        _placeholder_icon = QIcon(pixmap)
    return _placeholder_icon


def _fit_to_thumbnail(pixmap: QPixmap) -> QPixmap:
    """Riporta una miniatura alla dimensione dichiarata, centrandola.

    Tutte le miniature devono occupare esattamente THUMBNAIL_SIZE, anche a
    proporzioni diverse: i video sono 16:9 e le foto 4:3, e lasciarle di
    altezza diversa farebbe cambiare la dimensione delle celle a seconda di
    cosa è già arrivato — lo stesso difetto che il segnaposto risolve.
    """
    scaled = pixmap
    if (pixmap.width() > THUMBNAIL_SIZE.width()
            or pixmap.height() > THUMBNAIL_SIZE.height()):
        scaled = pixmap.scaled(
            THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    if scaled.size() == THUMBNAIL_SIZE:
        return scaled

    canvas = QPixmap(THUMBNAIL_SIZE)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap(
        (THUMBNAIL_SIZE.width() - scaled.width()) // 2,
        (THUMBNAIL_SIZE.height() - scaled.height()) // 2,
        scaled,
    )
    painter.end()
    return canvas




@dataclass(frozen=True)
class MediaFilter:
    kinds: set[MediaKind] | None = None   # None = tutti i tipi
    only_new: bool = False

    def matches(self, job: Job) -> bool:
        if self.kinds is not None and job.media.kind not in self.kinds:
            return False
        if self.only_new and job.state is JobState.PRESENT:
            return False
        return True


class MediaListModel(QAbstractTableModel):
    """Un solo modello per entrambe le viste.

    La griglia a miniature (QListView in IconMode) usa la colonna 0;
    l'elenco dettagliato (QTreeView) mostra tutte le colonne e ordina.
    """

    def __init__(self) -> None:
        super().__init__()
        self._all: list[Job] = []
        self._visible: list[Job] = []
        self._thumbnails: dict[str, QPixmap] = {}
        self._fade: dict[str, float] = {}
        self._animations: dict[str, QVariantAnimation] = {}
        self._filter = MediaFilter()
        self._sort_column = COL_DATE
        self._sort_order = Qt.SortOrder.AscendingOrder

    # -- popolamento -------------------------------------------------------

    def set_jobs(self, jobs: list[Job]) -> None:
        self.beginResetModel()
        self._all = list(jobs)
        self._reapply_filter()
        self.endResetModel()

    def set_filter(self, media_filter: MediaFilter) -> None:
        self.beginResetModel()
        self._filter = media_filter
        self._reapply_filter()
        self.endResetModel()

    def _reapply_filter(self) -> None:
        self._visible = [j for j in self._all if self._filter.matches(j)]
        self._reapply_sort()   # il filtro non deve azzerare l'ordinamento

    def _sort_key(self, job: Job):
        if self._sort_column == COL_DATE:
            return job.media.shot_at or _NO_DATE
        if self._sort_column == COL_SIZE:
            return job.media.size
        if self._sort_column == COL_KIND:
            return _KIND_TEXT[job.media.kind]
        if self._sort_column == COL_STATE:
            return _STATE_TEXT[job.state]
        return job.media.name

    def _reapply_sort(self) -> None:
        self._visible.sort(
            key=self._sort_key,
            reverse=self._sort_order == Qt.SortOrder.DescendingOrder,
        )

    def sort(self, column: int,
             order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self.beginResetModel()
        self._sort_column = column
        self._sort_order = order
        self._reapply_sort()
        self.endResetModel()

    def visible_jobs(self) -> list[Job]:
        return list(self._visible)

    def counts_by_kind(self) -> dict[MediaKind, tuple[int, int]]:
        """Quanti file e quanti byte per tipo, per le righe-filtro del rail.

        Conta su tutti i job, non sui visibili: un filtro non deve cambiare
        i numeri che servono a decidere quale filtro attivare.
        """
        conteggi = {k: [0, 0] for k in MediaKind}
        for job in self._all:
            voce = conteggi[job.media.kind]
            voce[0] += 1
            voce[1] += job.media.size
        return {k: (v[0], v[1]) for k, v in conteggi.items()}

    def set_job_state(self, camera_path: str, state: JobState) -> bool:
        """Aggiorna lo stato di un file mentre la coda avanza.

        Se il filtro «solo non scaricati» è attivo, il file completato esce
        dalla vista: è la risposta coerente, non un caso da ignorare.
        """
        for indice, job in enumerate(self._all):
            if job.media.camera_path != camera_path:
                continue
            aggiornato = Job(job.media, job.dest, state)
            self._all[indice] = aggiornato
            if not self._filter.matches(aggiornato):
                self.beginResetModel()
                self._reapply_filter()
                self.endResetModel()
                return True
            for riga, visibile in enumerate(self._visible):
                if visibile.media.camera_path == camera_path:
                    self._visible[riga] = aggiornato
                    self.dataChanged.emit(self.index(riga, COL_NAME),
                                          self.index(riga, COL_STATE))
                    break
            return True
        return False

    def thumbnail_opacity(self, camera_path: str) -> float:
        return self._fade.get(camera_path, 1.0)

    def job_at(self, index: QModelIndex) -> Job | None:
        if not index.isValid() or index.row() >= len(self._visible):
            return None
        return self._visible[index.row()]

    def set_thumbnail(self, camera_path: str, data: bytes) -> bool:
        """Registra una miniatura. False se i byte non sono decodificabili.

        Il chiamante usa il valore di ritorno per ripetere la richiesta: un
        JPEG troncato va riscaricato, non accettato.
        """
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return False    # byte non decodificabili: nessun crash, nessuna icona
        self._thumbnails[camera_path] = _fit_to_thumbnail(pixmap)
        self._start_fade(camera_path)
        for row, job in enumerate(self._visible):
            if job.media.camera_path == camera_path:
                idx = self.index(row, COL_NAME)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
                break
        return True

    def _start_fade(self, camera_path: str) -> None:
        """Le miniature arrivano dalla rete a ritmo irregolare: comparire di
        colpo a metà di uno scorrimento è un lampo. La dissolvenza dice che
        è arrivato qualcosa, non decora."""
        vecchia = self._animations.pop(camera_path, None)
        if vecchia is not None:
            vecchia.stop()

        animazione = QVariantAnimation(self)
        animazione.setDuration(theme.DURATION)
        # Da un valore gia' visibile: vedi preview.py per il motivo.
        animazione.setStartValue(0.4)
        animazione.setEndValue(1.0)
        animazione.setEasingCurve(QEasingCurve.Type.OutCubic)

        def passo(valore):
            self._fade[camera_path] = float(valore)
            for riga, job in enumerate(self._visible):
                if job.media.camera_path == camera_path:
                    idx = self.index(riga, COL_NAME)
                    self.dataChanged.emit(idx, idx,
                                          [Qt.ItemDataRole.DecorationRole])
                    break

        animazione.valueChanged.connect(passo)
        animazione.finished.connect(
            lambda: self._animations.pop(camera_path, None))
        self._animations[camera_path] = animazione
        animazione.start()

    # -- interfaccia QAbstractTableModel -----------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole):
        if (orientation == Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole
                and 0 <= section < len(COLUMNS)):
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        job = self.job_at(index)
        if job is None:
            return None

        if role == JOB_ROLE:
            return job

        if role == Qt.ItemDataRole.DecorationRole:
            # Solo la prima colonna porta la miniatura: nella griglia è
            # l'unica visibile, nell'elenco sarebbe rumore sulle altre.
            if index.column() == COL_STATE:
                nome, colore = _STATE_ICON[job.state]
                return icons.icon(nome, colore, size=14)
            if index.column() != COL_NAME:
                return None
            pixmap = self._thumbnails.get(job.media.camera_path)
            # Mai None: vedi placeholder_icon() per il perché.
            return QIcon(pixmap) if pixmap else placeholder_icon()

        if role == Qt.ItemDataRole.DisplayRole:
            column = index.column()
            if column == COL_NAME:
                return job.media.name
            if column == COL_DATE:
                return job.media.shot_at.strftime("%d/%m/%Y %H:%M:%S") \
                    if job.media.shot_at else "—"
            if column == COL_KIND:
                return _KIND_TEXT[job.media.kind]
            if column == COL_SIZE:
                return _format_size(job.media.size)
            if column == COL_STATE:
                return _STATE_TEXT[job.state]
            return None

        if role == Qt.ItemDataRole.ToolTipRole:
            when = job.media.shot_at.strftime("%d/%m/%Y %H:%M") \
                if job.media.shot_at else "data sconosciuta"
            return (f"{job.media.name}  ·  {_STATE_TEXT[job.state]}\n"
                    f"{when} — {_format_size(job.media.size)} — "
                    f"{job.media.kind.value}")

        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == COL_SIZE:
            return int(Qt.AlignmentFlag.AlignRight |
                       Qt.AlignmentFlag.AlignVCenter)
        return None


class _JobSelection:
    """Recupero dei job selezionati, comune a griglia ed elenco.

    In una vista a colonne selezionare una riga produce un indice per
    colonna: senza deduplicare per riga lo stesso file finirebbe cinque
    volte nella coda di download.
    """

    def selected_jobs(self) -> list[Job]:
        rows = {index.row() for index in self.selectedIndexes()}
        jobs = [self._model.job_at(self._model.index(row, COL_NAME))
                for row in sorted(rows)]
        return [j for j in jobs if j is not None]


class MediaTable(QTreeView, _JobSelection):
    """Elenco dettagliato, ordinato per data di scatto."""

    def __init__(self, model: MediaListModel) -> None:
        super().__init__()
        self.setModel(model)
        self._model = model
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)   # necessario per liste lunghe
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSortingEnabled(True)
        self.sortByColumn(COL_DATE, Qt.SortOrder.AscendingOrder)
        self.header().setSectionResizeMode(COL_NAME,
                                           QHeaderView.ResizeMode.Stretch)
        self.setIconSize(QSize(22, 22))

    def size_columns(self) -> None:
        """Le colonne di dato si dimensionano sul contenuto: con una
        larghezza fissa la data finiva troncata in «01/01/2020 …», che e'
        peggio che non mostrarla."""
        for colonna in (COL_DATE, COL_KIND, COL_SIZE, COL_STATE):
            self.header().setSectionResizeMode(
                colonna, QHeaderView.ResizeMode.ResizeToContents)


CELL = QSize(THUMBNAIL_SIZE.width() + 22, THUMBNAIL_SIZE.height() + 40)


class ThumbnailDelegate(QStyledItemDelegate):
    """Disegna la cella: lastra, miniatura, nome, pastiglia di stato.

    Lo stile predefinito disegnerebbe l'icona e il testo con margini che non
    appartengono a questo sistema, e non saprebbe nulla della dissolvenza.
    """

    def sizeHint(self, option, index) -> QSize:
        # Dimensione fissa: la cella non deve cambiare quando arriva la
        # miniatura, altrimenti l'immagine viene disegnata dentro un
        # rettangolo misurato sul solo testo e si vede una striscia.
        return CELL

    def paint(self, painter: QPainter, option, index) -> None:
        model = index.model()
        job = model.data(index, JOB_ROLE)
        if job is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = option.rect.adjusted(3, 3, -3, -3)
        selezionato = bool(option.state & QStyle.StateFlag.State_Selected)
        sotto_mouse = bool(option.state & QStyle.StateFlag.State_MouseOver)

        lastra = QRect(rect.x(), rect.y(), rect.width(),
                       THUMBNAIL_SIZE.height() + 8)
        if selezionato:
            painter.setBrush(QColor(theme.ACCENT))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, theme.RADIUS + 1, theme.RADIUS + 1)
        elif sotto_mouse:
            painter.setBrush(QColor(theme.SURFACE))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, theme.RADIUS + 1, theme.RADIUS + 1)

        # La miniatura sta su un suo riquadro scuro: le foto chiare non
        # devono sanguinare nel fondo del pannello.
        painter.setBrush(QColor(theme.SHELL if not selezionato else "#15383f"))
        painter.setPen(QColor(theme.LINE))
        painter.drawRoundedRect(lastra.adjusted(4, 4, -4, 0), theme.RADIUS,
                                theme.RADIUS)

        pixmap = model._thumbnails.get(job.media.camera_path)
        if pixmap is not None:
            painter.setOpacity(model.thumbnail_opacity(job.media.camera_path))
            x = lastra.x() + (lastra.width() - pixmap.width()) // 2
            y = lastra.y() + 5 + (THUMBNAIL_SIZE.height() - pixmap.height()) // 2
            painter.drawPixmap(x, y, pixmap)
            painter.setOpacity(1.0)

        nome_rect = QRect(rect.x() + 6, lastra.bottom() + 4,
                          rect.width() - 30, 16)
        painter.setPen(QColor(theme.ACCENT_INK if selezionato else theme.TEXT_DIM))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(
            nome_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            option.fontMetrics.elidedText(job.media.name,
                                          Qt.TextElideMode.ElideMiddle,
                                          nome_rect.width()),
        )

        nome_icona, colore = _STATE_ICON[job.state]
        if job.state is not JobState.NEW:
            painter.drawPixmap(
                rect.right() - 22, lastra.bottom() + 4,
                icons.pixmap(nome_icona, 16,
                             theme.ACCENT_INK if selezionato else colore, 2.0),
            )
        painter.restore()


class MediaGrid(QListView, _JobSelection):
    thumbnail_needed = pyqtSignal(str)

    def __init__(self, model: MediaListModel) -> None:
        super().__init__()
        self.setModel(model)
        self._model = model
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(THUMBNAIL_SIZE)
        self.setGridSize(CELL)
        self.setItemDelegate(ThumbnailDelegate(self))
        self.setMouseTracking(True)          # serve allo stato hover del delegate
        self.setSpacing(4)
        self.setContentsMargins(0, 0, 0, 0)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self._requested: set[str] = set()
        self.verticalScrollBar().valueChanged.connect(
            self.request_visible_thumbnails
        )

    def set_filter(self, media_filter: MediaFilter) -> None:
        self._model.set_filter(media_filter)
        self.request_visible_thumbnails()

    def forget_thumbnail(self, camera_path: str) -> None:
        """Dimentica una richiesta, così può essere ripetuta.

        Serve quando una miniatura è arrivata corrotta o non è stata servita:
        senza questo il path resterebbe marcato come richiesto e la casella
        rimarrebbe vuota per sempre.
        """
        self._requested.discard(camera_path)

    def request_visible_thumbnails(self) -> None:
        """Chiede le miniature solo per ciò che si vede, una volta sola.

        Se nessuno è collegato a thumbnail_needed non fa nulla, e soprattutto
        non segna nulla come richiesto. Segnare un path mentre il worker non
        esiste ancora significava non chiederlo mai più: è il difetto che ha
        lasciato la griglia senza miniature dopo il ridisegno.
        """
        if self.receivers(self.thumbnail_needed) == 0:
            return
        rect = self.viewport().rect()
        for row in range(self._model.rowCount()):
            index = self._model.index(row, COL_NAME)
            # Nessuna uscita anticipata sul primo elemento fuori vista: la
            # monotonia dei rettangoli non è garantita prima che la vista
            # abbia completato il layout, e uscire prima lasciava gruppi di
            # miniature non richieste.
            if not rect.intersects(self.visualRect(index)):
                continue
            job = self._model.job_at(index)
            if job and job.media.camera_path not in self._requested:
                self._requested.add(job.media.camera_path)
                self.thumbnail_needed.emit(job.media.camera_path)
