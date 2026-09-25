"""Finestra principale: cabla worker, griglia e stato della rete."""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, QThread, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QToolBar,
    QVBoxLayout, QWidget,
)

from ..core.downloader import JobState
from ..core.errors import NetworkUnavailable, OlympusError
from ..core.model import MediaKind
from ..core.network import CameraNetwork
from ..core.protocol import OlympusClient
from .grid import MediaFilter, MediaGrid, MediaListModel
from .workers import DownloadWorker, ListWorker, ThumbWorker, describe_error

DEFAULT_DEST = Path.home() / "Immagini" / "Olympus"


class MainWindow(QMainWindow):
    def __init__(self, client_factory=OlympusClient,
                 network_factory=CameraNetwork,
                 settings: QSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Olympus Connect")
        self.resize(1000, 700)

        self._settings = settings or QSettings("olympus-connect", "olympus-connect")
        self._client_factory = client_factory
        self._network_factory = network_factory
        self._client = None
        self._threads: list[QThread] = []
        self._list_worker: ListWorker | None = None
        self._thumb_worker: ThumbWorker | None = None
        self._download_worker: DownloadWorker | None = None

        self._model = MediaListModel()
        self._grid = MediaGrid(self._model)

        self._build_ui()
        self.refresh_status()

    # -- costruzione interfaccia ------------------------------------------

    def _build_ui(self) -> None:
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        toolbar.addAction("Connetti", self.on_connect)
        toolbar.addAction("Aggiorna", self.on_refresh)
        toolbar.addAction("Scarica selezionati", self.on_download_selected)
        toolbar.addSeparator()
        toolbar.addAction("Cartella destinazione…", self.on_choose_destination)
        toolbar.addSeparator()

        self._filter_jpeg = QCheckBox("JPEG")
        self._filter_raw = QCheckBox("RAW")
        self._filter_movie = QCheckBox("Video")
        self._only_new = QCheckBox("Solo non scaricati")
        self._filter_boxes = {
            "jpeg": self._filter_jpeg,
            "raw": self._filter_raw,
            "movie": self._filter_movie,
            "only_new": self._only_new,
        }
        for key, box in self._filter_boxes.items():
            default = key != "only_new"   # i tipi partono attivi, il filtro no
            box.setChecked(self._settings.value(f"filter/{key}", default, type=bool))
            box.stateChanged.connect(self.on_filter_changed)
            toolbar.addWidget(box)

        self._file_label = QLabel("")
        self._file_bar = QProgressBar()
        self._total_bar = QProgressBar()
        self._cancel_button = QPushButton("Annulla")
        self._resume_button = QPushButton("Riprova")
        self._cancel_button.setEnabled(False)
        self._resume_button.setEnabled(False)
        self._cancel_button.clicked.connect(self.on_cancel)
        self._resume_button.clicked.connect(self.on_resume)

        buttons = QHBoxLayout()
        buttons.addWidget(self._cancel_button)
        buttons.addWidget(self._resume_button)
        buttons.addStretch()

        layout = QVBoxLayout()
        layout.addWidget(self._grid, stretch=1)
        layout.addWidget(self._file_label)
        layout.addWidget(self._file_bar)
        layout.addWidget(self._total_bar)
        layout.addLayout(buttons)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.on_filter_changed()

    # -- impostazioni -----------------------------------------------------

    def dest_root(self) -> Path:
        stored = self._settings.value("dest_root", "")
        return Path(stored) if stored else DEFAULT_DEST

    def camera_ssid(self) -> str:
        return str(self._settings.value("ssid", "") or "")

    def on_choose_destination(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Cartella di destinazione", str(self.dest_root())
        )
        if chosen:
            self._settings.setValue("dest_root", chosen)
            self.statusBar().showMessage(f"Destinazione: {chosen}", 5000)

    # -- stato della rete --------------------------------------------------

    def refresh_status(self) -> None:
        try:
            diagnosis = self._network_factory(self.camera_ssid()).diagnose()
        except OlympusError as exc:
            self.statusBar().showMessage(describe_error(exc))
            return

        if diagnosis.ok:
            self.statusBar().showMessage("Fotocamera raggiungibile.")
            return

        failing = next(r for r in diagnosis.rungs if not r.ok)
        self.statusBar().showMessage(f"{failing.name}: {failing.detail}")

    def on_connect(self) -> None:
        ssid = self.camera_ssid()
        if not ssid:
            ssid, accepted = QInputDialog.getText(
                self, "Rete della fotocamera",
                "SSID mostrato dalla fotocamera:"
            )
            if not accepted or not ssid:
                return
            self._settings.setValue("ssid", ssid)

        network = self._network_factory(ssid)
        try:
            if not network.profile_exists():
                # La password va a NetworkManager e non viene mai salvata
                # da questa applicazione.
                password, accepted = QInputDialog.getText(
                    self, "Password Wi-Fi",
                    f"Password della rete «{ssid}»:",
                    echo=QLineEdit.EchoMode.Password,
                )
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

    # -- elenco -----------------------------------------------------------

    def _start_on_thread(self, worker, slot=None) -> QThread:
        thread = QThread(self)
        worker.moveToThread(thread)
        if slot is not None:
            thread.started.connect(slot)
        thread.start()
        self._threads.append(thread)
        return thread

    def on_refresh(self) -> None:
        # Ogni worker riceve un client proprio: requests.Session non è
        # thread-safe, e condividerla fra thread fa arrivare i corpi
        # troncati (miniature a strisce).
        worker = ListWorker(self._client_factory(), self.dest_root())
        worker.connected.connect(self.on_connected)
        worker.listed.connect(self.on_listed)
        worker.failed.connect(self.on_failed)
        self._list_worker = worker           # mantiene il riferimento vivo
        self._start_on_thread(worker, worker.run)

    @pyqtSlot(str)
    def on_connected(self, model: str) -> None:
        self.statusBar().showMessage(f"Connesso a Olympus {model}.")

    @pyqtSlot(list)
    def on_listed(self, jobs: list) -> None:
        self._model.set_jobs(jobs)
        self.on_filter_changed()

        new = sum(1 for j in jobs if j.state is not JobState.PRESENT)
        present = len(jobs) - new
        self.statusBar().showMessage(
            f"{new} da scaricare, {present} già presenti."
        )

        self._ensure_thumb_worker()
        self._grid.request_visible_thumbnails()

    def _ensure_thumb_worker(self) -> None:
        """Un solo worker per le miniature, sul proprio thread e col proprio
        client HTTP."""
        if self._thumb_worker is not None:
            return
        worker = ThumbWorker(self._client_factory())
        worker.ready.connect(self.on_thumbnail_ready)
        worker.failed.connect(self.on_thumbnail_failed)
        worker.skipped.connect(self._grid.forget_thumbnail)
        self._thumb_worker = worker
        self._start_on_thread(worker)
        # QueuedConnection: la richiesta viene eseguita sul thread del
        # worker. Con una connessione diretta il download della miniatura
        # girerebbe sul thread della GUI e la bloccherebbe.
        self._grid.thumbnail_needed.connect(
            worker.request, Qt.ConnectionType.QueuedConnection
        )

    @pyqtSlot(str, bytes)
    def on_thumbnail_ready(self, camera_path: str, data: bytes) -> None:
        if not self._model.set_thumbnail(camera_path, data):
            # Byte non decodificabili: dimentica la richiesta così può
            # essere ripetuta invece di lasciare la casella vuota.
            self._grid.forget_thumbnail(camera_path)

    @pyqtSlot(str, str)
    def on_thumbnail_failed(self, camera_path: str, message: str) -> None:
        self._grid.forget_thumbnail(camera_path)

    def _suspend_thumbnails(self, suspended: bool) -> None:
        """Ferma le miniature durante un download, e le riprende dopo."""
        if self._thumb_worker is None:
            return
        self._thumb_worker.set_suspended(suspended)
        if not suspended:
            self._grid.request_visible_thumbnails()

    @pyqtSlot(str)
    def on_failed(self, message: str) -> None:
        self.statusBar().showMessage(message)

    # -- filtri ------------------------------------------------------------

    def on_filter_changed(self) -> None:
        for key, box in self._filter_boxes.items():
            self._settings.setValue(f"filter/{key}", box.isChecked())

        kinds: set[MediaKind] = set()
        if self._filter_jpeg.isChecked():
            kinds.add(MediaKind.JPEG)
        if self._filter_raw.isChecked():
            kinds.add(MediaKind.RAW)
        if self._filter_movie.isChecked():
            kinds.add(MediaKind.MOVIE)
        self._grid.set_filter(
            MediaFilter(kinds=kinds, only_new=self._only_new.isChecked())
        )

    # -- download ----------------------------------------------------------

    def on_download_selected(self) -> None:
        jobs = self._grid.selected_jobs()
        if not jobs:
            self.statusBar().showMessage("Nessun file selezionato.", 4000)
            return

        # Client proprio anche qui, e miniature sospese: il server della
        # fotocamera regge male due trasferimenti in volo insieme.
        self._suspend_thumbnails(True)
        worker = DownloadWorker(self._client_factory(), jobs)
        worker.file_started.connect(self._file_label.setText)
        worker.file_progress.connect(self._update_file_bar)
        worker.total_progress.connect(self._update_total_bar)
        worker.file_failed.connect(
            lambda name, msg: self.statusBar().showMessage(f"{name}: {msg}")
        )
        worker.paused.connect(self.on_paused)
        worker.finished.connect(self.on_download_finished)

        self._download_worker = worker
        self._cancel_button.setEnabled(True)
        self._resume_button.setEnabled(False)
        self._start_on_thread(worker, worker.run)

    def _update_file_bar(self, written: int, total: int) -> None:
        self._file_bar.setMaximum(max(total, 1))
        self._file_bar.setValue(written)

    def _update_total_bar(self, written: int, total: int) -> None:
        self._total_bar.setMaximum(max(total, 1))
        self._total_bar.setValue(written)

    def on_cancel(self) -> None:
        if self._download_worker is not None:
            self._download_worker.cancel()
        self._cancel_button.setEnabled(False)
        self._resume_button.setEnabled(True)
        self._suspend_thumbnails(False)

    def on_resume(self) -> None:
        if self._download_worker is None:
            return
        self._resume_button.setEnabled(False)
        self._cancel_button.setEnabled(True)
        self._suspend_thumbnails(True)
        self._download_worker.resume()

    @pyqtSlot(str)
    def on_paused(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self._cancel_button.setEnabled(False)
        self._resume_button.setEnabled(True)
        self._suspend_thumbnails(False)

    @pyqtSlot()
    def on_download_finished(self) -> None:
        self._file_label.setText("")
        self._cancel_button.setEnabled(False)
        self._resume_button.setEnabled(False)
        self._suspend_thumbnails(False)
        self.statusBar().showMessage("Download completato.")
        self.on_refresh()   # ricalcola lo stato dei file a destinazione

    # -- chiusura ----------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._download_worker is not None:
            self._download_worker.cancel()
        for thread in self._threads:
            thread.quit()
            thread.wait(2000)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
