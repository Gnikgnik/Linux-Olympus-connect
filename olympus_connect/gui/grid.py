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

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QListView, QTreeView

from ..core.downloader import Job, JobState
from ..core.model import MediaKind

JOB_ROLE = Qt.ItemDataRole.UserRole + 1

THUMBNAIL_SIZE = QSize(160, 120)

COLUMNS = ["Nome", "Data e ora", "Tipo", "Dimensione", "Stato"]
COL_NAME, COL_DATE, COL_KIND, COL_SIZE, COL_STATE = range(5)

_STATE_SUFFIX = {
    JobState.NEW: "",
    JobState.PRESENT: "  ✓",
    JobState.INCOMPLETE: "  ⟳",
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


def _format_size(size: int) -> str:
    megabytes = size / 1_048_576
    if megabytes >= 1024:
        return f"{megabytes / 1024:.2f} GB"
    return f"{megabytes:.1f} MB"


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
        self._thumbnails[camera_path] = pixmap
        for row, job in enumerate(self._visible):
            if job.media.camera_path == camera_path:
                idx = self.index(row, COL_NAME)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
                break
        return True

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
            if index.column() != COL_NAME:
                return None
            pixmap = self._thumbnails.get(job.media.camera_path)
            return QIcon(pixmap) if pixmap else None

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
            return (f"{job.media.name}{_STATE_SUFFIX[job.state]}\n"
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


class MediaGrid(QListView, _JobSelection):
    thumbnail_needed = pyqtSignal(str)

    def __init__(self, model: MediaListModel) -> None:
        super().__init__()
        self.setModel(model)
        self._model = model
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(THUMBNAIL_SIZE)
        self.setGridSize(QSize(THUMBNAIL_SIZE.width() + 24,
                               THUMBNAIL_SIZE.height() + 40))
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
        """Chiede le miniature solo per ciò che si vede, una volta sola."""
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
