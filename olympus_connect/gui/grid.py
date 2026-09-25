"""Griglia virtualizzata delle miniature.

QListView in IconMode richiede le righe solo quando servono: i 1161 file
della scheda di riferimento non creano 1161 widget, e le miniature si
scaricano solo per ciò che è effettivamente visibile — decisivo, perché
ogni miniatura è una richiesta HTTP alla fotocamera.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QListView

from ..core.downloader import Job, JobState
from ..core.model import MediaKind

JOB_ROLE = Qt.ItemDataRole.UserRole + 1

THUMBNAIL_SIZE = QSize(160, 120)

_STATE_SUFFIX = {
    JobState.NEW: "",
    JobState.PRESENT: "  ✓",
    JobState.INCOMPLETE: "  ⟳",
}


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


class MediaListModel(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self._all: list[Job] = []
        self._visible: list[Job] = []
        self._thumbnails: dict[str, QPixmap] = {}
        self._filter = MediaFilter()

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

    def visible_jobs(self) -> list[Job]:
        return list(self._visible)

    def job_at(self, index: QModelIndex) -> Job | None:
        if not index.isValid() or index.row() >= len(self._visible):
            return None
        return self._visible[index.row()]

    def set_thumbnail(self, camera_path: str, data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return          # byte non decodificabili: nessun crash, nessuna icona
        self._thumbnails[camera_path] = pixmap
        for row, job in enumerate(self._visible):
            if job.media.camera_path == camera_path:
                idx = self.index(row)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
                return

    # -- interfaccia QAbstractListModel ------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        job = self.job_at(index)
        if job is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return job.media.name
        if role == JOB_ROLE:
            return job
        if role == Qt.ItemDataRole.DecorationRole:
            pixmap = self._thumbnails.get(job.media.camera_path)
            return QIcon(pixmap) if pixmap else None
        if role == Qt.ItemDataRole.ToolTipRole:
            megabytes = job.media.size / 1_048_576
            when = job.media.shot_at.strftime("%d/%m/%Y %H:%M") \
                if job.media.shot_at else "data sconosciuta"
            return (f"{job.media.name}{_STATE_SUFFIX[job.state]}\n"
                    f"{when} — {megabytes:.1f} MB — {job.media.kind.value}")
        return None


class MediaGrid(QListView):
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

    def selected_jobs(self) -> list[Job]:
        jobs = [self._model.job_at(i) for i in self.selectedIndexes()]
        return [j for j in jobs if j is not None]

    def request_visible_thumbnails(self) -> None:
        """Chiede le miniature solo per ciò che si vede, una volta sola."""
        rect = self.viewport().rect()
        for row in range(self._model.rowCount()):
            index = self._model.index(row)
            item_rect = self.visualRect(index)
            if item_rect.top() > rect.bottom():
                break          # oltre il fondo della viewport: basta
            if not rect.intersects(item_rect):
                continue
            job = self._model.job_at(index)
            if job and job.media.camera_path not in self._requested:
                self._requested.add(job.media.camera_path)
                self.thumbnail_needed.emit(job.media.camera_path)
