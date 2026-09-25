"""Worker Qt che avvolgono il core. Il core non conosce Qt; questo modulo è
l'unico punto in cui i due mondi si incontrano."""
from __future__ import annotations

import errno
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from ..core.downloader import Job, Outcome, download, pending, plan
from ..core.errors import (
    CameraAsleep, CameraNotReachable, IncompleteDownload, NetworkUnavailable,
    ProtocolError, WrongNetwork,
)


def describe_error(exc: Exception) -> str:
    """Traduce un'eccezione in un messaggio che dice all'utente cosa fare."""
    if isinstance(exc, CameraNotReachable):
        return ("La fotocamera non risponde su 192.168.0.10. Attiva il Wi-Fi "
                "sulla fotocamera e collega il PC al suo access point.")
    if isinstance(exc, CameraAsleep):
        return ("La fotocamera si è disconnessa, probabilmente per inattività. "
                "Riattiva il Wi-Fi sulla fotocamera e premi Riprova. Questo "
                "modello non supporta la ripresa, quindi il file interrotto "
                "riparte da zero; i file già completati restano.")
    if isinstance(exc, WrongNetwork):
        return f"Rete Wi-Fi sbagliata. {exc}"
    if isinstance(exc, NetworkUnavailable):
        return f"Gestione della rete non disponibile. {exc}"
    if isinstance(exc, IncompleteDownload):
        return f"Trasferimento incompleto. {exc}"
    if isinstance(exc, ProtocolError):
        return f"Risposta inattesa dalla fotocamera. {exc}"
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "Spazio su disco esaurito: libera spazio e riprova."
    if isinstance(exc, PermissionError):
        return f"Permessi insufficienti per scrivere: {exc}"
    if isinstance(exc, OSError):
        return f"Errore di scrittura su disco: {exc}"
    return f"Errore inatteso: {exc}"


class ListWorker(QObject):
    """Elenca i file sulla fotocamera e li confronta col disco."""

    connected = pyqtSignal(str)   # modello della fotocamera
    listed = pyqtSignal(list)     # list[Job]
    failed = pyqtSignal(str)

    def __init__(self, client, dest_root: Path) -> None:
        super().__init__()
        self._client = client
        self._dest_root = Path(dest_root)

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.connected.emit(self._client.connect())
            # Non ottiene nulla su questo firmware, ma non costa e non
            # fallisce in modo visibile: vedi OlympusClient.keep_awake.
            self._client.keep_awake()
            files = self._client.list_media()
            self.listed.emit(plan(files, self._dest_root))
        except Exception as exc:        # nessuna eccezione esce da un worker
            self.failed.emit(describe_error(exc))


class ThumbWorker(QObject):
    """Scarica le miniature su richiesta, una alla volta.

    Verificato sul campo: get_thumbnail restituisce un JPEG valido anche per
    ORF e MOV, fra 8 e 13 KB.
    """

    ready = pyqtSignal(str, bytes)
    failed = pyqtSignal(str, str)

    def __init__(self, client) -> None:
        super().__init__()
        self._client = client

    @pyqtSlot(str)
    def request(self, camera_path: str) -> None:
        try:
            self.ready.emit(camera_path, self._client.thumbnail(camera_path))
        except Exception as exc:
            self.failed.emit(camera_path, describe_error(exc))


class DownloadWorker(QObject):
    """Esegue una coda di download seriali, con pausa e ripresa.

    La coda arriva da pending(), quindi è ordinata dal file più piccolo:
    senza supporto Range un'interruzione costa il file in corso, e conviene
    che sia il più piccolo possibile il più a lungo possibile.
    """

    file_started = pyqtSignal(str)
    file_progress = pyqtSignal(int, int)
    total_progress = pyqtSignal(int, int)
    file_done = pyqtSignal(str)
    file_failed = pyqtSignal(str, str)
    paused = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, client, jobs: list[Job]) -> None:
        super().__init__()
        self._client = client
        self._queue = list(pending(jobs))
        self._total = sum(j.media.size for j in self._queue)
        self._completed_bytes = 0
        self._cancelled = False

    def remaining(self) -> int:
        return len(self._queue)

    @pyqtSlot()
    def cancel(self) -> None:
        self._cancelled = True

    @pyqtSlot()
    def resume(self) -> None:
        self._cancelled = False
        self.run()

    @pyqtSlot()
    def run(self) -> None:
        while self._queue:
            if self._cancelled:
                return
            job = self._queue[0]
            self.file_started.emit(job.media.name)

            def on_progress(written: int, total: int) -> None:
                self.file_progress.emit(written, total)
                self.total_progress.emit(self._completed_bytes + written,
                                         self._total)

            try:
                outcome = download(
                    self._client, job,
                    on_progress=on_progress,
                    should_cancel=lambda: self._cancelled,
                )
            except CameraAsleep as exc:
                # La coda resta intatta: l'utente riconnette e riprova.
                self.paused.emit(describe_error(exc))
                return
            except Exception as exc:
                self._queue.pop(0)
                self._completed_bytes += job.media.size
                self.file_failed.emit(job.media.name, describe_error(exc))
                continue

            if outcome is Outcome.CANCELLED:
                return

            self._queue.pop(0)
            self._completed_bytes += job.media.size
            self.total_progress.emit(self._completed_bytes, self._total)
            self.file_done.emit(job.media.name)

        self.finished.emit()
