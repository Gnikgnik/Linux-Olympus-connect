"""Velocità e tempo stimato di un trasferimento. Nessun I/O, nessun Qt."""
from __future__ import annotations

import time
from collections import deque
from typing import Callable


def format_rate(rate: float) -> str:
    if rate >= 1_000_000:
        return f"{rate / 1_000_000:.1f} MB/s"
    if rate >= 1_000:
        return f"{rate / 1_000:.1f} kB/s"
    return f"{rate:.0f} B/s"


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 86_400:
        return "oltre un giorno"
    if seconds >= 3_600:
        return f"{seconds // 3600} h {(seconds % 3600) // 60} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds} s"


class TransferStats:
    """Media mobile della velocità, da cui il tempo stimato.

    La finestra mobile esiste perché la media sull'intero trasferimento è
    inutile: una pausa iniziale, o la fotocamera che si riprende dopo
    un'interruzione, la deprimerebbero per il resto della sessione.
    """

    def __init__(self, total: int, clock: Callable[[], float] = time.monotonic,
                 window: float = 8.0) -> None:
        self.total = total
        self.written = 0
        self._clock = clock
        self._window = window
        self._samples: deque[tuple[float, int]] = deque()

    def update(self, written: int) -> None:
        self.written = written
        now = self._clock()
        self._samples.append((now, written))
        # Scarta i campioni fuori finestra, tenendone sempre almeno due:
        # con uno solo non esiste una pendenza da misurare.
        while len(self._samples) > 2 and now - self._samples[0][0] > self._window:
            self._samples.popleft()

    def speed(self) -> float | None:
        """Byte al secondo, o None se non ancora misurabile."""
        if len(self._samples) < 2:
            return None
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        elapsed = t1 - t0
        if elapsed <= 0:
            return None
        return max(0.0, (b1 - b0) / elapsed)

    def eta(self) -> float | None:
        """Secondi rimanenti, 0 se completo, None se sconosciuto.

        Fermo non è "infinito" né "subito": è sconosciuto, e dirlo è più
        onesto che mostrare un numero inventato.
        """
        remaining = self.total - self.written
        if remaining <= 0:
            return 0
        rate = self.speed()
        if not rate:
            return None
        return remaining / rate


def format_size(size: int) -> str:
    """Dimensione di un file, in base 1000.

    Stessa base di format_rate, deliberatamente: con basi diverse la
    divisione dimensione / velocita' non darebbe il tempo mostrato a
    schermo, e la documentazione del progetto gia' cita i totali in
    base 1000 (27,9 GB).
    """
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.2f} GB"
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.0f} kB"
    return f"{size} B"
