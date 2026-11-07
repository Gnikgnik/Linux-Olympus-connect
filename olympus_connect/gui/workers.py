"""Worker Qt che avvolgono il core. Il core non conosce Qt; questo modulo è
l'unico punto in cui i due mondi si incontrano."""
from __future__ import annotations

import errno
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from ..core.exif import parse_exif
from ..core.model import MediaKind, classify
from ..core.downloader import Job, Outcome, download, pending, plan
from ..core.stats import TransferStats
from ..core.errors import (
    CameraAsleep, CameraNotReachable, IncompleteDownload, NetworkUnavailable,
    ProtocolError, WrongNetwork,
)
from ..core.protocol import LIVE_QUALITY


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
    if isinstance(exc, ProtocolError) and exc.code == 1004:
        return ("La fotocamera non accetta il comando in questo momento: "
                "funziona solo con il live attivo. Riprova.")
    if isinstance(exc, ProtocolError) and exc.code is not None:
        return f"La fotocamera ha rifiutato il comando (codice {exc.code})."
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

    Deve avere un OlympusClient **proprio**: requests.Session non è
    thread-safe, e condividerla con gli altri worker fa arrivare i corpi
    troncati — una miniatura troncata si vede come una striscia.
    """

    ready = pyqtSignal(str, bytes)
    failed = pyqtSignal(str, str)
    skipped = pyqtSignal(str)

    def __init__(self, client) -> None:
        super().__init__()
        self._client = client
        self._suspended = False

    @pyqtSlot(bool)
    def set_suspended(self, suspended: bool) -> None:
        """Sospende le richieste mentre un download è in corso.

        Il server della fotocamera è un microcontrollore: due trasferimenti
        HTTP in volo insieme lo degradano, e il download ha la precedenza.
        """
        self._suspended = suspended

    @pyqtSlot(str)
    def request(self, camera_path: str) -> None:
        if self._suspended:
            self.skipped.emit(camera_path)
            return
        try:
            self.ready.emit(camera_path, self._client.thumbnail(camera_path))
        except Exception as exc:
            self.failed.emit(camera_path, describe_error(exc))


class PreviewWorker(QObject):
    """Anteprima grande del file selezionato, su richiesta.

    Client proprio, come ogni altro worker: requests.Session non e'
    thread-safe. Si sospende durante i download, perche' un'anteprima da
    1 MB che compete con un trasferimento rallenta entrambi.
    """

    ready = pyqtSignal(str, bytes)
    failed = pyqtSignal(str, str)
    unavailable = pyqtSignal(str)
    shot_ready = pyqtSignal(str, object)     # percorso, ShotInfo | None

    def __init__(self, client) -> None:
        super().__init__()
        self._client = client
        self._suspended = False

    @pyqtSlot(bool)
    def set_suspended(self, suspended: bool) -> None:
        self._suspended = suspended

    @pyqtSlot(str)
    def request(self, camera_path: str) -> None:
        if self._suspended:
            return
        # Ogni passo ha il suo try: questo slot e' invocato da Qt attraverso
        # una connessione in coda, e un'eccezione che sfugge da qui non
        # risale a un chiamante Python ma fa abortire il processo.
        try:
            # Questo worker ha un client proprio, perche' requests.Session non
            # e' thread-safe. Un client nuovo non ha mai interrogato la
            # fotocamera e non ne conosce le capacita': senza questo passo
            # has_screennail risponde no anche quando la fotocamera le
            # anteprime le genera. Una volta sola, sul thread del worker.
            if not getattr(self._client, "commands", None):
                self._client.connect()
        except Exception as exc:
            self.failed.emit(camera_path, describe_error(exc))
            return
        # I dati di scatto prima: pochi KB, arrivano mentre l'anteprima da
        # 1 MB e' ancora in viaggio.
        self._request_shot(camera_path)
        self._request_screennail(camera_path)

    def _request_shot(self, camera_path: str) -> None:
        if classify(camera_path) is MediaKind.MOVIE:
            return
        try:
            if not self._client.has_exif():
                return
            info = parse_exif(self._client.exif(camera_path))
        except Exception:
            info = None     # "non disponibili"; l'anteprima va chiesta comunque
        self.shot_ready.emit(camera_path, info)

    def _request_screennail(self, camera_path: str) -> None:
        try:
            if not self._client.has_screennail():
                # Altri modelli potrebbero non averlo: si dichiara una volta
                # invece di fallire a ogni selezione.
                self.unavailable.emit(camera_path)
                return
            self.ready.emit(camera_path, self._client.screennail(camera_path))
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
    file_completed = pyqtSignal(str)        # camera_path, per aggiornare l'elenco
    stats_changed = pyqtSignal(int, int, object, object)   # scritti, totale, B/s, s
    file_failed = pyqtSignal(str, str)
    paused = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, client, jobs: list[Job],
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._client = client
        self._queue = list(pending(jobs))
        self._total = sum(j.media.size for j in self._queue)
        self._completed_bytes = 0
        self._cancelled = False
        self._stats = TransferStats(self._total, clock=clock)

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
                complessivi = self._completed_bytes + written
                self.file_progress.emit(written, total)
                self.total_progress.emit(complessivi, self._total)
                self._stats.update(complessivi)
                self.stats_changed.emit(complessivi, self._total,
                                        self._stats.speed(), self._stats.eta())

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
            self._stats.update(self._completed_bytes)
            self.stats_changed.emit(self._completed_bytes, self._total,
                                    self._stats.speed(), self._stats.eta())
            self.file_completed.emit(job.media.camera_path)
            self.file_done.emit(job.media.name)

        self.finished.emit()


class RemoteWorker(QObject):
    """I comandi del telecomando, uno alla volta, su un thread proprio.

    Separato dal live: un comando che aspetta la fotocamera (uno stop del
    video dura più di un secondo e mezzo) non deve fermare l'immagine.
    Client proprio, come ogni worker: requests.Session non è thread-safe.
    """

    entered = pyqtSignal(str, object)     # modalità della ghiera, area di messa a fuoco | None
    focused = pyqtSignal(object)          # True / False / None
    shot = pyqtSignal(bytes)              # revisione; b"" se non arriva
    movie_started = pyqtSignal()
    movie_stopped = pyqtSignal()
    left = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, client, quality: str = LIVE_QUALITY) -> None:
        super().__init__()
        self._client = client
        self._quality = quality
        self._recording = False

    def is_recording(self) -> bool:
        return self._recording

    @pyqtSlot(str, int)
    def enter(self, quality: str, port: int) -> None:
        try:
            if not getattr(self._client, "commands", None):
                self._client.connect()
            self._client.enter_rec_mode(quality or self._quality)
            self._client.start_liveview(port)
        except Exception as exc:
            self.failed.emit(describe_error(exc))
            return
        modo, area = "", None
        try:
            modo = self._client.take_mode()
        except Exception:
            pass
        try:
            area = self._client.touch_area()
        except Exception:
            pass
        self.entered.emit(modo, area)

    @pyqtSlot(int, int)
    def focus(self, x: int, y: int) -> None:
        try:
            self.focused.emit(self._client.assign_af(x, y))
        except Exception as exc:
            self.failed.emit(describe_error(exc))

    @pyqtSlot()
    def shoot(self) -> None:
        try:
            self._client.take_picture()
        except Exception as exc:
            self.failed.emit(describe_error(exc))
            return
        try:
            revisione = self._client.rec_view()
        except Exception:
            revisione = b""            # lo scatto c'è; manca solo l'immagine da mostrare
        self.shot.emit(revisione)

    @pyqtSlot()
    def start_movie(self) -> None:
        try:
            self._client.start_movie()
        except Exception as exc:
            self.failed.emit(describe_error(exc))
            return
        self._recording = True
        self.movie_started.emit()

    @pyqtSlot()
    def stop_movie(self) -> None:
        try:
            self._client.stop_movie()
        except Exception as exc:
            self.failed.emit(describe_error(exc))
            return
        self._recording = False
        self.movie_stopped.emit()

    @pyqtSlot()
    def leave(self) -> None:
        """Ogni passo è tentato anche se il precedente fallisce: la
        fotocamera deve tornare in riproduzione in ogni caso."""
        if self._recording:
            try:
                self._client.stop_movie()
            except Exception:
                pass
            self._recording = False
        for passo in (self._client.stop_liveview, self._client.ensure_play_mode):
            try:
                passo()
            except Exception:
                pass
        self.left.emit()
