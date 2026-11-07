"""Ricezione del live su un thread proprio.

Il ciclo di ricezione è bloccante e vive per tutta la sessione del
telecomando, quindi non riceve slot in coda: la finestra parla con il
worker solo attraverso metodi thread-safe (arm, disarm, take_frame, stop)
e i suoi segnali.

Vince l'ultimo fotogramma: il worker ne tiene uno solo in attesa e lo
notifica una volta; se la finestra è indietro, i fotogrammi più vecchi si
buttano invece di accumularsi in coda. Il ritardo non cresce mai.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage

from ..core.liveview import FrameAssembler

# La fotocamera spedisce il live dall'indirizzo 192.168.0.10, ma da una porta
# che dipende da quella di ricezione (vedi camera_source_port). Un datagramma
# mandato a quell'indirizzo dalla porta su cui si ascolta fa accettare al
# firewall i pacchetti di risposta: nessuna regola necessaria. La porta None
# significa «calcolala dalla porta di ricezione».
CAMERA_LIVE = ("192.168.0.10", None)
RECV_BUFFER = 4 * 1024 * 1024      # a 650 pacchetti/s il buffer predefinito trabocca


def camera_source_port(port: int) -> int:
    """Porta da cui la fotocamera spedisce il live, data la porta di ricezione.

    Il firmware scambia i due byte della porta (endianness): la sorgente dei
    pacchetti e la porta di destinazione richiesta con `startliveview` sono
    una il byte-swap dell'altra. Coppie misurate (log del firewall,
    2026-11-07): 40000 -> 16540, 35199 -> 32649, 40234 -> 10909.
    """
    return ((port & 0xFF) << 8) | (port >> 8)


class LiveViewWorker(QObject):
    listening = pyqtSignal(int)
    frame_ready = pyqtSignal()
    stalled = pyqtSignal()
    no_signal = pyqtSignal()
    resumed = pyqtSignal()

    def __init__(self, camera: tuple[str, int | None] = CAMERA_LIVE, bind_host: str = "",
                 stall_after: float = 2.0, no_signal_after: float = 3.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._camera = camera
        self._bind_host = bind_host
        self._stall_after = stall_after
        self._no_signal_after = no_signal_after
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: QImage | None = None
        self._pending = False
        self._sock: socket.socket | None = None
        self._armed_at: float | None = None

    # -- thread-safe, chiamabili dal thread della finestra --------------------

    def arm(self) -> None:
        """Da chiamare prima di ogni startliveview: apre il passaggio nel
        firewall e fa ripartire la sentinella."""
        self._armed_at = self._clock()
        sock = self._sock
        if sock is not None:
            host, port = self._camera
            try:
                if port is None:
                    port = camera_source_port(sock.getsockname()[1])
                sock.sendto(b"\x00", (host, port))
            except OSError:
                pass

    def disarm(self) -> None:
        """Il live si ferma di proposito: niente avvisi di live interrotto."""
        self._armed_at = None

    def take_frame(self) -> QImage | None:
        with self._lock:
            immagine, self._latest = self._latest, None
            self._pending = False
        return immagine

    def stop(self) -> None:
        self._stop.set()

    # -- ciclo di ricezione ---------------------------------------------------

    @pyqtSlot()
    def run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_BUFFER)
            except OSError:
                pass
            sock.bind((self._bind_host, 0))
            sock.settimeout(0.25)
        except OSError:
            self.no_signal.emit()
            return
        self._sock = sock
        self.listening.emit(sock.getsockname()[1])
        try:
            self._loop(sock)
        except Exception:
            pass                # nessuna eccezione esce dal ciclo
        finally:
            self._sock = None
            sock.close()

    def _loop(self, sock: socket.socket) -> None:
        assembler = FrameAssembler()
        ultimo: float | None = None
        fermo = muto = False
        visto: float | None = None
        while not self._stop.is_set():
            try:
                dati, _origine = sock.recvfrom(65535)
            except socket.timeout:
                dati = None
            except OSError:
                return
            adesso = self._clock()
            if dati is not None:
                jpeg = assembler.feed(dati)
                if jpeg is not None:
                    immagine = QImage.fromData(jpeg)
                    if not immagine.isNull():
                        if fermo or muto:
                            self.resumed.emit()
                            fermo = muto = False
                        ultimo = adesso
                        self._deliver(immagine)
            armato = self._armed_at
            if armato != visto:
                fermo = muto = False
                visto = armato
            if armato is None:
                fermo = muto = False
                continue
            if ultimo is None or ultimo < armato:
                if not muto and adesso - armato > self._no_signal_after:
                    muto = True
                    self.no_signal.emit()
            elif not fermo and adesso - ultimo > self._stall_after:
                fermo = True
                self.stalled.emit()

    def _deliver(self, immagine: QImage) -> None:
        with self._lock:
            self._latest = immagine
            notifica = not self._pending
            self._pending = True
        if notifica:
            self.frame_ready.emit()
