"""Sequenze di scatti a intervalli (telecomando, pezzo E1).

La fotocamera non ha un intervallometro comandabile dal PC: la sequenza la
fa l'app, facendo partire lo stesso scatto del pulsante «Scatta» a distanza
fissa. Qui stanno il piano (intervallo e durata), l'automa che decide quando
tocca scattare, i testi del pannello e il riconoscimento delle foto della
sequenza. Niente Qt e niente orologio proprio: l'istante arriva da fuori,
così le regole si provano con un orologio finto.

Misure sul campo (2026-11-11): fra due scatti rapidi passa circa 1 s, quindi
l'intervallo minimo è 2 s; il live si spegne e si riaccende in 0,3 s senza
rientrare in modalità scatto; la fotocamera risponde ancora dopo 5 minuti
ferma.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Sequence

from .model import MediaFile, MediaKind

INTERVALS = (2, 3, 5, 8, 10, 15, 20, 30, 45, 60, 120, 180, 300, 600, 900, 1800)   # secondi
DURATIONS = (300, 600, 900, 1200, 1800, 2700, 3600, 5400, 7200, 10800, 14400,
             21600, 28800, 43200)
DEFAULT_INTERVAL = 5
DEFAULT_DURATION = 1800

PAUSE_LIVE_FROM = 60        # da questo intervallo il live si spegne fra gli scatti
LIVE_LEAD = 5.0             # il live si riaccende questi secondi prima dello scatto
MIN_PAUSE = 5.0             # sotto questa durata la pausa del live non vale la pena
SNAPSHOT_SETTLE = 3.0       # l'elenco della scheda vede una foto solo poco dopo lo scatto
MAX_SKIPPED_IN_A_ROW = 3    # scatti saltati di fila che fermano la sequenza
BLOCKED_PERIOD_MIN = 30.0   # telecomando non pronto: un «saltato» ogni periodo così


def format_span(seconds: float) -> str:
    """«5 s», «45 min», «1 min 12 s», «1 h 30», «12 h»."""
    totale = int(round(seconds))
    ore, resto = divmod(totale, 3600)
    minuti, secondi = divmod(resto, 60)
    if ore:
        return f"{ore} h" + (f" {minuti:02d}" if minuti else "")
    if minuti:
        return f"{minuti} min" + (f" {secondi} s" if secondi else "")
    return f"{secondi} s"


def nearest(values: Sequence[int], value: int) -> int:
    """Il valore dell'elenco più vicino a quello chiesto (per le impostazioni salvate)."""
    return min(values, key=lambda v: (abs(v - value), v))


@dataclass(frozen=True)
class SequencePlan:
    interval: int       # secondi fra l'inizio di uno scatto e il successivo
    duration: int       # secondi dall'avvio oltre i quali non parte più nessuno scatto

    def cycle(self, busy: float | None = None) -> float:
        """Il tempo vero fra due scatti: l'intervallo, o l'occupazione stimata
        dello scatto (posa e riduzione rumore) più un secondo, se è più lunga."""
        return max(float(self.interval), (busy or 0.0) + 1.0)

    def expected_shots(self, busy: float | None = None) -> int:
        return math.ceil(self.duration / self.cycle(busy))

    def pauses_live(self) -> bool:
        return self.interval >= PAUSE_LIVE_FROM

    def summary(self, busy: float | None = None, fps: int = 25) -> str:
        """La riga di riepilogo del pannello, prima dell'avvio."""
        scatti = self.expected_shots(busy)
        if busy is not None and busy + 1.0 > self.interval:
            return ("la posa dura più dell'intervallo: scatti uno dopo l'altro, "
                    f"circa {scatti} in {format_span(self.duration)}")
        video = scatti / fps
        durata = "meno di 1 s" if video < 1 else format_span(video)
        return f"{scatti} scatti · video di {durata} a {fps} fps"


@dataclass(frozen=True)
class SequenceResult:
    taken: int              # scatti riusciti
    skipped: int            # scatti saltati (falliti, o telecomando non pronto a lungo)
    aborted: bool           # fermata da tre scatti saltati di fila
    started_at: datetime    # ora del PC all'avvio, per il nome della cartella


class SequenceState:
    """L'automa di una sequenza.

    Lo scatto k è dovuto a t0 + k × intervallo. Uno scatto dovuto parte solo
    se il telecomando è pronto; se nel frattempo ne sono scaduti altri ne
    parte uno solo, e quelli scaduti non si recuperano né contano come
    saltati. Contano come saltati gli scatti falliti e, senza uno scatto in
    corso, ogni periodo di max(intervallo, 30 s) passato con uno scatto
    dovuto e il telecomando non pronto. Tre saltati di fila fermano tutto.
    """

    def __init__(self, plan: SequencePlan, busy: float | None = None) -> None:
        self.plan = plan
        self.total = plan.expected_shots(busy)
        self.taken = 0
        self.skipped = 0
        self.index = 0              # scatti partiti finora
        self.in_flight = False
        self.finished = False
        self.aborted = False
        self._t0: float | None = None
        self._slot = 0              # lo scatto dovuto è il numero _slot della griglia
        self._next_due = 0.0
        self._free_since = 0.0
        self._blocked_since: float | None = None
        self._stopping = False
        self._row = 0               # saltati di fila

    @property
    def running(self) -> bool:
        return self._t0 is not None and not self.finished

    def start(self, now: float) -> None:
        self._t0 = now
        self._slot = 0
        self._next_due = now
        self._free_since = now

    def stop(self) -> None:
        """Nessun nuovo scatto; quello in corso finisce."""
        self._stopping = True
        if not self.in_flight:
            self.finished = True

    def _no_more_shots(self, now: float) -> bool:
        fine = self._t0 + self.plan.duration
        return self._stopping or now >= fine or self._next_due >= fine

    def tick(self, now: float, ready: bool) -> bool:
        """Vero se deve partire uno scatto adesso."""
        if not self.running or self.in_flight:
            return False
        if self._no_more_shots(now):
            self.finished = True
            return False
        if now < self._next_due:
            return False
        if not ready:
            if self._blocked_since is None:
                self._blocked_since = max(self._next_due, self._free_since)
            if now - self._blocked_since >= max(self.plan.interval, BLOCKED_PERIOD_MIN):
                self._blocked_since = now
                self._skip()
            return False
        self._blocked_since = None
        self.in_flight = True
        self.index += 1
        # Il prossimo posto della griglia strettamente dopo adesso. Il contatore
        # è intero e non torna mai indietro: con la sola divisione, un giro che
        # cade esattamente su un posto può arrotondare per difetto e far
        # ripartire lo scatto appena fatto.
        posto = max(self._slot + 1, math.floor((now - self._t0) / self.plan.interval) + 1)
        while self._t0 + posto * self.plan.interval <= now:
            posto += 1
        self._slot = posto
        self._next_due = self._t0 + posto * self.plan.interval
        return True

    def shot_done(self, ok: bool, now: float) -> None:
        if not self.in_flight:
            return
        self.in_flight = False
        self._free_since = now
        if ok:
            self.taken += 1
            self._row = 0
        else:
            self._skip()
        if not self.finished and self._no_more_shots(now):
            self.finished = True

    def _skip(self) -> None:
        self.skipped += 1
        self._row += 1
        if self._row >= MAX_SKIPPED_IN_A_ROW:
            self.aborted = True
            self.finished = True

    def next_in(self, now: float) -> float:
        return max(0.0, self._next_due - now)

    def remaining(self, now: float) -> float:
        return max(0.0, self._t0 + self.plan.duration - now) if self._t0 is not None else 0.0

    def may_pause_live(self, now: float) -> bool:
        """Dopo uno scatto: c'è abbastanza tempo per spegnere il live?"""
        return (self.running and not self.in_flight and self.plan.pauses_live()
                and not self._no_more_shots(now)
                and self._next_due - now >= LIVE_LEAD + MIN_PAUSE)

    def wake_due(self, now: float) -> bool:
        """Il live in pausa va riacceso: fra poco tocca scattare."""
        return (self.running and not self.in_flight and self.plan.pauses_live()
                and not self._no_more_shots(now) and self._next_due - now <= LIVE_LEAD)


def progress_text(state: SequenceState, now: float) -> tuple[str, str]:
    """Le due righe del pannello mentre la sequenza gira."""
    if state.index == 0:
        prima = "In partenza…"
    else:
        prima = (f"Scatto {state.index} di {state.total}" if state.index <= state.total
                 else f"Scatto {state.index}")
        if state.in_flight:
            prima += " · in corso"
        elif state.next_in(now) > 0:
            prima += f" · prossimo fra {format_span(math.ceil(state.next_in(now)))}"
        else:
            prima += " · in attesa della fotocamera"
    resto = state.remaining(now)
    resto = math.ceil(resto / 60) * 60 if resto >= 90 else math.ceil(resto)
    seconda = f"Mancano {format_span(resto)}"
    if state.skipped:
        seconda += f" · {state.skipped} " + ("saltato" if state.skipped == 1 else "saltati")
    return prima, seconda


def _shot_order(media: MediaFile) -> tuple[str, int, str]:
    """Cartella e contatore: l'ordine in cui la fotocamera ha scattato.

    L'elenco non lo garantisce (le cartelle arrivano dall'ultima alla prima)
    e il nome nemmeno: comincia con mese e giorno, e una sequenza può passare
    la mezzanotte. Il contatore sono le ultime quattro cifre prima del punto,
    e riparte da capo solo in una cartella nuova.
    """
    cartella, _, nome = media.camera_path.rpartition("/")
    cifre = nome.rpartition(".")[0][-4:]
    return cartella, int(cifre) if cifre.isdigit() else 0, nome


def sequence_files(before: Iterable[str], after: Iterable[MediaFile]) -> list[MediaFile]:
    """I JPEG della sequenza: quelli che nell'istantanea presa all'avvio
    (percorsi sulla fotocamera) non c'erano, nell'ordine di scatto."""
    visti = set(before)
    nuovi = [m for m in after if m.kind is MediaKind.JPEG and m.camera_path not in visti]
    return sorted(nuovi, key=_shot_order)


def folder_name(started: datetime, exists: Callable[[str], bool]) -> str:
    """«Sequenza 2026-11-11 14.30», con « (2)», « (3)»… se esiste già."""
    base = f"Sequenza {started:%Y-%m-%d %H.%M}"
    nome, n = base, 1
    while exists(nome):
        n += 1
        nome = f"{base} ({n})"
    return nome
