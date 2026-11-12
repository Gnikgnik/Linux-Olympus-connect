# Sequenze, parte 2: la funzione nell'app — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** dal pannello del telecomando, far scattare la fotocamera a intervalli per una durata scelta; a fine sequenza uscire dal telecomando, chiedere all'utente, scaricare i JPEG della sequenza in una cartella dedicata e montarli in un video MP4.

**Architecture:** un automa puro (`core/sequence.py`) decide quando tocca scattare; un regista Qt (`SequenceController` in `gui/sequence.py`) lo fa girare con un timer da 200 ms e chiede alla finestra lo stesso scatto del pulsante «Scatta», leggendo da una funzione `ready()` se il telecomando è pronto. La finestra collega regista, pannello e worker: istantanea dei nomi all'avvio, live in pausa fra gli scatti con intervalli da 60 s, e a fine sequenza uscita, rilettura, finestra di scelta, scarico con `plan_flat` e montaggio con `ffmpeg` in un `TimelapseWorker`.

**Tech Stack:** Python 3.12+, PyQt6, requests, `subprocess` (`ffmpeg`, `systemd-inhibit`); pytest con requests-mock.

**Specifica:** `docs/superpowers/specs/2026-11-11-sequenze-design.md` (leggi «Esito della prova» e «Comportamento»).

## Global Constraints

- I test si lanciano con `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`. Prima di ogni commit la serie completa deve passare (675 all'inizio di questo piano); lanciala due volte prima del commit finale di ogni task, per scoprire test instabili.
- Nessuna nuova dipendenza. `exec_erase.cgi` non va mai invocato.
- Nessun agente parla con la fotocamera vera né usa la rete: solo test con client finti o con il server finto di `tests/test_remote_e2e.py`. La prova sul campo (Task 9) la fa l'utente.
- Testi dell'interfaccia in italiano, uguali a quelli scritti in questo piano. Nomi di variabili e commenti in italiano come nel resto del codice; nomi pubblici (classi, metodi, segnali) in inglese come nel resto del codice.
- Thread: ogni comando verso `RemoteWorker` è un segnale della finestra collegato con `Qt.ConnectionType.QueuedConnection` e aggiunto all'elenco dei segnali scollegati in `MainWindow._teardown_remote`. Nessun metodo di un worker si chiama direttamente dal thread della finestra, tranne quelli dichiarati thread-safe (`LiveViewWorker.arm/disarm/take_frame/stop`, `DownloadWorker.cancel`, `TimelapseWorker.cancel`).
- Le protezioni esistenti della finestra restano: `_remote_leaving`, `_closing`, `_remote_entered`, `_shot_in_flight`, la chiusura entro 3 s con i thread staccati (`_DETACHED`).
- Valori fissati dalla prova sul campo, da non cambiare: intervallo minimo 2 s; istantanea dei nomi non prima di 3 s dall'ultimo scatto; live in pausa con intervallo ≥ 60 s e riacceso 5 s prima dello scatto; rientro in rec se il live non arriva entro 3 s (la sentinella `no_signal` di `LiveViewWorker`).
- Nessun numero di serie e nessuna immagine della fotocamera in un commit. Mai i loghi o i marchi Olympus / OM SYSTEM.
- Ogni messaggio di commit ha un oggetto convenzionale in italiano e termina con la riga `Co-Authored-By: Claude <noreply@anthropic.com>`.
- Non si fa push.

## Struttura dei file

| File | Responsabilità |
|---|---|
| `olympus_connect/core/sequence.py` (già nell'albero) | piano, automa, testi del pannello, file della sequenza, nome della cartella |
| `olympus_connect/core/timelapse.py` (già nell'albero) | comando di `ffmpeg`, collegamenti numerati, lettura dell'avanzamento |
| `olympus_connect/core/power.py` (già nell'albero) | `SleepInhibitor`: tiene sveglio il PC |
| `olympus_connect/core/downloader.py` (già nell'albero) | `plan_flat`: scarico in una sola cartella |
| `olympus_connect/core/protocol.py` | `list_media(..., switch_to_play=False)` per l'istantanea in modalità scatto |
| `olympus_connect/gui/sequence.py` (nuovo) | `SpanStepper`, `SequenceSection`, `SequenceEndDialog`, `EndChoice`, `SequenceJob`, `SequenceController` |
| `olympus_connect/gui/workers.py` | `RemoteWorker.snapshot_names/pause_live/resume_live`, `TimelapseWorker` |
| `olympus_connect/gui/remote.py` | `RemotePanel.sequence`, `RemoteView.show_review(data, duration_ms)` |
| `olympus_connect/gui/grid.py` | `MediaListModel.camera_paths()` |
| `olympus_connect/gui/main_window.py` | collegamenti: avvio, scatto, pausa del live, fine sequenza, scarico, video |

---

### Task 1: I moduli di base (già scritti: verifica e revisione)

**Files:**
- Già nell'albero (commit `214ef8f` e `4d174b9`): `olympus_connect/core/sequence.py`, `olympus_connect/core/timelapse.py`, `olympus_connect/core/power.py`, `plan_flat` in `olympus_connect/core/downloader.py`
- Test già nell'albero: `tests/test_sequence.py`, `tests/test_timelapse.py`, `tests/test_power.py`, `tests/test_downloader.py`

**Nota:** questi moduli sono stati scritti e provati mentre si stendeva il piano (51 test, serie completa a 675), poi rivisti da un revisore indipendente e corretti (griglia degli scatti, nomi uguali da due cartelle, ordine di scatto, colori del video). Il task è una revisione indipendente: nessun codice nuovo, salvo le correzioni che la revisione chiede.

**Interfaces:**
- Consumes: `MediaFile`, `MediaKind` da `core/model.py`; `Job`, `JobState` da `core/downloader.py`.
- Produces (firme esatte, usate dai task successivi):

```python
# core/sequence.py
INTERVALS: tuple[int, ...]      # (2, 3, 5, 8, 10, 15, 20, 30, 45, 60, 120, 180, 300, 600, 900, 1800)
DURATIONS: tuple[int, ...]      # (300, 600, 900, 1200, 1800, 2700, 3600, 5400, 7200, 10800, 14400, 21600, 28800, 43200)
DEFAULT_INTERVAL = 5
DEFAULT_DURATION = 1800
SNAPSHOT_SETTLE = 3.0
def format_span(seconds: float) -> str            # «5 s», «45 min», «1 h 30»
def nearest(values: Sequence[int], value: int) -> int

@dataclass(frozen=True)
class SequencePlan:
    interval: int
    duration: int
    def expected_shots(self, busy: float | None = None) -> int
    def pauses_live(self) -> bool                 # interval >= 60
    def summary(self, busy: float | None = None, fps: int = 25) -> str

@dataclass(frozen=True)
class SequenceResult:
    taken: int
    skipped: int
    aborted: bool
    started_at: datetime

class SequenceState:
    def __init__(self, plan: SequencePlan, busy: float | None = None)
    total: int; taken: int; skipped: int; index: int
    in_flight: bool; finished: bool; aborted: bool
    running: bool                                  # proprietà
    def start(self, now: float) -> None
    def stop(self) -> None
    def tick(self, now: float, ready: bool) -> bool   # vero: deve partire uno scatto adesso
    def shot_done(self, ok: bool, now: float) -> None
    def may_pause_live(self, now: float) -> bool
    def wake_due(self, now: float) -> bool

def progress_text(state: SequenceState, now: float) -> tuple[str, str]
def sequence_files(before: Iterable[str], after: Iterable[MediaFile]) -> list[MediaFile]   # in ordine di scatto
def folder_name(started: datetime, exists: Callable[[str], bool]) -> str

# core/timelapse.py
RESOLUTIONS = {"Full HD": (1920, 1080), "4K": (3840, 2160)}
FRAME_RATES = (24, 25, 30)
DEFAULT_RESOLUTION = "Full HD"
DEFAULT_FRAME_RATE = 25
VIDEO_NAME = "timelapse.mp4"
def find_ffmpeg() -> str | None
def link_frames(files: Sequence[Path], folder: Path) -> int
def build_command(ffmpeg: str, frames_dir: Path, fps: int, size: tuple[int, int], out: Path) -> list[str]
def parse_progress(line: str) -> int | None

# core/power.py
class SleepInhibitor:
    def __init__(self, program: str = "systemd-inhibit")
    def start(self, seconds: float) -> bool       # falso se il comando manca
    def active(self) -> bool
    def stop(self) -> None

# core/downloader.py
def plan_flat(files: Iterable[MediaFile], folder: Path) -> list[Job]   # dest = folder / media.name; stesso ordine; nomi uguali da due cartelle prendono il prefisso della cartella
```

- [ ] **Step 1: Verifica**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_sequence.py tests/test_timelapse.py tests/test_power.py tests/test_downloader.py`
Expected: `73 passed` (con `ffmpeg` installato; senza, uno saltato).

Run: `grep -n "PyQt6\|import requests" olympus_connect/core/sequence.py olympus_connect/core/timelapse.py olympus_connect/core/power.py`
Expected: nessuna riga (i moduli di base non importano Qt né la rete).

- [ ] **Step 2: Revisione**

Il revisore confronta le regole di `SequenceState` con la specifica, sezioni «I tempi» e «Arresto ed errori», e cerca: uno scatto che parte dopo la durata; scatti recuperati in raffica; un'attesa di posa lunga contata come saltata; una sequenza che non finisce mai (per esempio `stop()` durante uno scatto che poi fallisce); `SleepInhibitor` che lascia processi vivi. Le correzioni chieste si fanno con un test che prima fallisce.

- [ ] **Step 3: Commit (solo se ci sono correzioni)**

```bash
git add olympus_connect/core tests
git commit -m "fix(core): correzioni dalla revisione dei moduli delle sequenze

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: I pezzi della sezione «Sequenza» e la finestra di fine sequenza

**Files:**
- Create: `olympus_connect/gui/sequence.py`
- Test: `tests/test_sequence_gui.py`

**Interfaces:**
- Consumes: da `core/sequence.py` `INTERVALS`, `DURATIONS`, `DEFAULT_INTERVAL`, `DEFAULT_DURATION`, `SequencePlan`, `format_span`, `nearest`; da `core/timelapse.py` `RESOLUTIONS`, `FRAME_RATES`, `DEFAULT_RESOLUTION`, `DEFAULT_FRAME_RATE`. Stili esistenti in `gui/theme.py`: `#propValue`, `#propArrow`, `#faint`, `#value`, `#primary`. `icons.icon(name, color, disabled_color, size)`, `SectionLabel` da `gui/panels.py`, `label_column_width()` da `gui/shot.py`.
- Produces:

```python
class SpanStepper(QWidget):
    changed = pyqtSignal(int)
    def __init__(self, text: str, values: Sequence[int], value: int)
    def value(self) -> int
    def set_value(self, value: int) -> None      # al più vicino dell'elenco, senza segnale
    def text(self) -> str                        # il testo mostrato: «5 s»
    def menu_values(self) -> list[int]
    def step(self, delta: int) -> None
    def choose(self, value: int) -> None

class SequenceSection(QWidget):
    start_requested = pyqtSignal(int, int)       # intervallo, durata (secondi)
    stop_requested = pyqtSignal()
    changed = pyqtSignal()
    def __init__(self, interval: int = DEFAULT_INTERVAL, duration: int = DEFAULT_DURATION)
    def interval(self) -> int
    def duration(self) -> int
    def set_values(self, interval: int, duration: int) -> None
    def plan(self) -> SequencePlan
    def set_summary(self, text: str) -> None
    def summary_text(self) -> str
    def set_progress(self, first: str, second: str) -> None
    def progress_texts(self) -> tuple[str, str]
    def set_running(self, running: bool) -> None
    def is_running(self) -> bool
    def set_enabled(self, enabled: bool) -> None

class EndChoice(NamedTuple):
    download: bool
    video: bool
    resolution: str
    fps: int

@dataclass
class SequenceJob:
    folder: Path
    frames: list[Path]
    video: bool
    resolution: str
    fps: int

class SequenceEndDialog(QDialog):
    def __init__(self, photos: int, skipped: int = 0, aborted: bool = False,
                 only_raw: bool = False, since_remote_entry: bool = False, ffmpeg: bool = True,
                 resolution: str = DEFAULT_RESOLUTION, fps: int = DEFAULT_FRAME_RATE, parent=None)
    def message(self) -> str
    def choice(self) -> EndChoice
```

- [ ] **Step 1: Scrivi i test**

`tests/test_sequence_gui.py`:

```python
"""I selettori della sequenza, la sezione del pannello e la finestra di fine sequenza."""
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QDialog

from olympus_connect.core.sequence import INTERVALS, SequencePlan
from olympus_connect.gui.sequence import (
    EndChoice, SequenceEndDialog, SequenceSection, SpanStepper,
)


def _rotella(su: bool) -> QWheelEvent:
    return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120 if su else -120),
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


# -- SpanStepper --------------------------------------------------------------------

def test_stepper_shows_the_value_and_steps_through_the_list(qapp):
    s = SpanStepper("Intervallo", INTERVALS, 5)
    visti = []
    s.changed.connect(visti.append)
    assert s.value() == 5 and s.text() == "5 s"
    s.step(1)
    assert s.value() == 8 and s.text() == "8 s"
    s.step(-2)
    assert s.value() == 3
    assert visti == [8, 3]


def test_stepper_stops_at_the_ends_without_signals(qapp):
    s = SpanStepper("Intervallo", INTERVALS, INTERVALS[0])
    visti = []
    s.changed.connect(visti.append)
    s.step(-1)
    assert s.value() == INTERVALS[0] and visti == []
    s.set_value(INTERVALS[-1])
    s.step(1)
    assert s.value() == INTERVALS[-1] and s.text() == "30 min" and visti == []


def test_stepper_snaps_an_unknown_value_and_set_value_is_silent(qapp):
    s = SpanStepper("Intervallo", INTERVALS, 7)
    assert s.value() == 8
    visti = []
    s.changed.connect(visti.append)
    s.set_value(61)
    assert s.value() == 60 and s.text() == "1 min" and visti == []


def test_stepper_wheel_and_list(qapp):
    s = SpanStepper("Intervallo", INTERVALS, 5)
    visti = []
    s.changed.connect(visti.append)
    s.wheelEvent(_rotella(True))
    s.wheelEvent(_rotella(False))
    s.wheelEvent(_rotella(False))
    assert visti == [8, 5, 3]
    assert s.menu_values() == list(INTERVALS)
    s.choose(120)
    s.choose(120)                       # già quello: nessun segnale
    s.choose(7)                         # non in elenco: ignorato
    assert s.value() == 120 and visti == [8, 5, 3, 120]


def test_a_disabled_stepper_does_not_move(qapp):
    s = SpanStepper("Intervallo", INTERVALS, 5)
    s.setEnabled(False)
    s.step(1)
    s.choose(10)
    s.wheelEvent(_rotella(True))
    assert s.value() == 5


# -- SequenceSection ----------------------------------------------------------------

def test_section_starts_with_its_values_and_reports_changes(qapp):
    sezione = SequenceSection(10, 600)
    cambi = []
    sezione.changed.connect(lambda: cambi.append(1))
    assert (sezione.interval(), sezione.duration()) == (10, 600)
    assert sezione.plan() == SequencePlan(10, 600)
    sezione._interval.step(1)
    sezione._duration.step(1)
    assert (sezione.interval(), sezione.duration()) == (15, 900) and len(cambi) == 2
    sezione.set_values(2, 43200)
    assert sezione.plan() == SequencePlan(2, 43200) and len(cambi) == 2     # set_values è muto


def test_section_button_asks_to_start_and_then_to_stop(qapp):
    sezione = SequenceSection(5, 1800)
    eventi = []
    sezione.start_requested.connect(lambda i, d: eventi.append(("avvia", i, d)))
    sezione.stop_requested.connect(lambda: eventi.append(("ferma",)))
    assert sezione._button.text().strip() == "Avvia sequenza"
    sezione._button.click()
    sezione.set_running(True)
    assert sezione.is_running() and sezione._button.text().strip() == "Ferma sequenza"
    sezione._button.click()
    assert eventi == [("avvia", 5, 1800), ("ferma",)]


def test_section_swaps_the_choices_for_the_progress_while_running(qapp):
    sezione = SequenceSection()
    sezione.set_summary("360 scatti · video di 14 s a 25 fps")
    assert sezione.summary_text() == "360 scatti · video di 14 s a 25 fps"
    assert not sezione._interval.isHidden() and not sezione._summary.isHidden()
    assert sezione._line1.isHidden() and sezione._line2.isHidden()
    sezione.set_running(True)
    sezione.set_progress("Scatto 42 di 360 · prossimo fra 3 s", "Mancano 26 min")
    assert sezione.progress_texts() == ("Scatto 42 di 360 · prossimo fra 3 s", "Mancano 26 min")
    assert sezione._interval.isHidden() and sezione._duration.isHidden()
    assert sezione._summary.isHidden()
    assert not sezione._line1.isHidden() and not sezione._line2.isHidden()
    sezione.set_running(False)
    assert not sezione._interval.isHidden() and sezione._line1.isHidden()


def test_section_disabled_blocks_start_but_never_stop(qapp):
    sezione = SequenceSection()
    sezione.set_enabled(False)
    assert not sezione._button.isEnabled() and not sezione._interval.isEnabled()
    sezione.set_running(True)
    assert sezione._button.isEnabled()          # Ferma resta sempre premibile
    sezione.set_running(False)
    assert not sezione._button.isEnabled()
    sezione.set_enabled(True)
    assert sezione._button.isEnabled() and sezione._interval.isEnabled()


# -- finestra di fine sequenza --------------------------------------------------------

def test_dialog_offers_download_and_video(qapp):
    d = SequenceEndDialog(photos=358, skipped=2, resolution="4K", fps=30)
    assert d.message().startswith("Sequenza finita: 358 foto (2 saltate).")
    assert "Scaricarle in una cartella dedicata?" in d.message()
    assert d._video.isEnabled() and d._video.isChecked()
    assert d._resolution.currentText() == "4K" and d._fps.currentData() == 30
    d._download.click()
    assert d.result() == QDialog.DialogCode.Accepted
    assert d.choice() == EndChoice(True, True, "4K", 30)


def test_dialog_not_now_downloads_nothing(qapp):
    d = SequenceEndDialog(photos=10)
    d._later.click()
    assert d.result() == QDialog.DialogCode.Rejected
    assert d.choice().download is False


def test_dialog_video_choices_follow_the_checkbox(qapp):
    d = SequenceEndDialog(photos=10)
    d._video.setChecked(False)
    assert not d._resolution.isEnabled() and not d._fps.isEnabled()
    d._fps.setCurrentIndex(0)
    d._download.click()
    assert d.choice() == EndChoice(True, False, "Full HD", 24)


def test_dialog_without_ffmpeg_or_with_one_photo_has_no_video(qapp):
    senza = SequenceEndDialog(photos=10, ffmpeg=False)
    assert not senza._video.isEnabled() and not senza._video.isChecked()
    assert "ffmpeg non è installato" in senza._video_note.text()
    una = SequenceEndDialog(photos=1)
    assert not una._video.isEnabled() and "almeno due foto" in una._video_note.text()
    una._download.click()
    assert una.choice() == EndChoice(True, False, "Full HD", 25)


def test_dialog_for_a_raw_only_camera_only_closes(qapp):
    d = SequenceEndDialog(photos=0, only_raw=True)
    assert "La fotocamera ha salvato solo RAW: niente da scaricare qui, niente video." in d.message()
    assert d._download is None and d._later is None
    d._close.click()
    assert d.choice().download is False


def test_dialog_with_no_new_photos_only_closes(qapp):
    d = SequenceEndDialog(photos=0)
    assert "Nessuna foto nuova sulla scheda." in d.message() and d._download is None


def test_dialog_says_when_it_was_aborted_and_when_the_photos_are_since_entry(qapp):
    d = SequenceEndDialog(photos=12, skipped=3, aborted=True, since_remote_entry=True)
    assert d.message().startswith(
        "Sequenza interrotta: la fotocamera non risponde. 12 foto (3 saltate).")
    assert "foto nuove dall'ingresso nel telecomando" in d.message()
```

- [ ] **Step 2: Verifica che falliscano**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_sequence_gui.py`
Expected: errore di raccolta, `ModuleNotFoundError: No module named 'olympus_connect.gui.sequence'`.

- [ ] **Step 3: Scrivi il modulo**

`olympus_connect/gui/sequence.py`:

```python
"""Sequenze di scatti nel pannello del telecomando.

La sezione «Sequenza» (intervallo, durata, riepilogo, Avvia/Ferma), la
finestra che a fine sequenza chiede se scaricare le foto e montare il
video, e il regista che fa partire gli scatti. Le regole stanno in
core/sequence.py: qui c'è solo ciò che serve a mostrarle e a farle girare.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Sequence

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton,
    QVBoxLayout, QWidget,
)

from ..core.sequence import (
    DEFAULT_DURATION, DEFAULT_INTERVAL, DURATIONS, INTERVALS, SequencePlan, format_span,
    nearest,
)
from ..core.timelapse import (
    DEFAULT_FRAME_RATE, DEFAULT_RESOLUTION, FRAME_RATES, RESOLUTIONS,
)
from . import icons, theme
from .panels import SectionLabel
from .shot import label_column_width


class SpanStepper(QWidget):
    """Un valore di tempo scelto da un elenco: frecce, rotella, elenco al clic.

    Ha l'aspetto dei selettori delle impostazioni (stessi stili), ma non
    parla con la fotocamera: il valore cambia subito e basta.
    """

    changed = pyqtSignal(int)

    def __init__(self, text: str, values: Sequence[int], value: int) -> None:
        super().__init__()
        self._values = tuple(values)
        self._current = nearest(self._values, value)

        riga = QHBoxLayout(self)
        riga.setContentsMargins(0, 0, 0, 0)
        riga.setSpacing(2)
        etichetta = QLabel(text)
        etichetta.setObjectName("faint")
        etichetta.setFixedWidth(label_column_width())
        riga.addWidget(etichetta)
        self._prev = self._arrow("chevron-left", -1)
        self._button = QPushButton()
        self._button.setObjectName("propValue")
        self._button.clicked.connect(self._open_menu)
        self._next = self._arrow("chevron-right", +1)
        riga.addWidget(self._prev)
        riga.addWidget(self._button, 1)
        riga.addWidget(self._next)
        self._refresh()

    def _arrow(self, icona: str, delta: int) -> QPushButton:
        pulsante = QPushButton()
        pulsante.setObjectName("propArrow")
        pulsante.setIcon(icons.icon(icona, theme.TEXT_DIM, theme.LINE_STRONG, 14))
        pulsante.setIconSize(QSize(14, 14))
        pulsante.clicked.connect(lambda: self.step(delta))
        return pulsante

    def value(self) -> int:
        return self._current

    def set_value(self, value: int) -> None:
        self._current = nearest(self._values, value)
        self._refresh()

    def text(self) -> str:
        return self._button.text()

    def menu_values(self) -> list[int]:
        return list(self._values)

    def step(self, delta: int) -> None:
        if not self.isEnabled():
            return
        posto = self._values.index(self._current)
        nuovo = min(max(posto + delta, 0), len(self._values) - 1)
        if nuovo != posto:
            self._current = self._values[nuovo]
            self._refresh()
            self.changed.emit(self._current)

    def choose(self, value: int) -> None:
        if not self.isEnabled() or value not in self._values or value == self._current:
            return
        self._current = value
        self._refresh()
        self.changed.emit(self._current)

    def wheelEvent(self, event) -> None:
        y = event.angleDelta().y()
        if y:
            self.step(1 if y > 0 else -1)
        event.accept()

    def _open_menu(self) -> None:
        menu = QMenu(self)
        for valore in self._values:
            azione = menu.addAction(format_span(valore))
            azione.setCheckable(True)
            azione.setChecked(valore == self._current)
            azione.triggered.connect(lambda _c=False, v=valore: self.choose(v))
        menu.exec(self._button.mapToGlobal(self._button.rect().bottomLeft()))

    def _refresh(self) -> None:
        self._button.setText(format_span(self._current))


class SequenceSection(QWidget):
    """La sezione «Sequenza» del pannello: scelte prima, avanzamento durante."""

    start_requested = pyqtSignal(int, int)      # intervallo, durata (secondi)
    stop_requested = pyqtSignal()
    changed = pyqtSignal()                      # intervallo o durata: il riepilogo va rifatto

    def __init__(self, interval: int = DEFAULT_INTERVAL,
                 duration: int = DEFAULT_DURATION) -> None:
        super().__init__()
        colonna = QVBoxLayout(self)
        colonna.setContentsMargins(0, 0, 0, 0)
        colonna.setSpacing(2)
        colonna.addWidget(SectionLabel("Sequenza"))
        self._interval = SpanStepper("Intervallo", INTERVALS, interval)
        self._duration = SpanStepper("Durata", DURATIONS, duration)
        self._summary = QLabel("")
        self._summary.setObjectName("faint")
        self._summary.setWordWrap(True)
        self._line1 = QLabel("")
        self._line1.setObjectName("value")
        self._line1.setWordWrap(True)
        self._line2 = QLabel("")
        self._line2.setObjectName("faint")
        self._button = QPushButton()
        self._button.setIconSize(QSize(18, 18))
        for widget in (self._interval, self._duration, self._summary, self._line1, self._line2):
            colonna.addWidget(widget)
        colonna.addSpacing(6)
        colonna.addWidget(self._button)

        self._interval.changed.connect(lambda _valore: self.changed.emit())
        self._duration.changed.connect(lambda _valore: self.changed.emit())
        self._button.clicked.connect(self._on_button)
        self._running = False
        self._enabled = True
        self._refresh()

    def interval(self) -> int:
        return self._interval.value()

    def duration(self) -> int:
        return self._duration.value()

    def set_values(self, interval: int, duration: int) -> None:
        self._interval.set_value(interval)
        self._duration.set_value(duration)

    def plan(self) -> SequencePlan:
        return SequencePlan(self.interval(), self.duration())

    def set_summary(self, text: str) -> None:
        self._summary.setText(text)

    def summary_text(self) -> str:
        return self._summary.text()

    def set_progress(self, first: str, second: str) -> None:
        self._line1.setText(first)
        self._line2.setText(second)

    def progress_texts(self) -> tuple[str, str]:
        return self._line1.text(), self._line2.text()

    def set_running(self, running: bool) -> None:
        self._running = running
        if not running:
            self.set_progress("", "")
        self._refresh()

    def is_running(self) -> bool:
        return self._running

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._refresh()

    def _on_button(self) -> None:
        if self._running:
            self.stop_requested.emit()
        else:
            self.start_requested.emit(self.interval(), self.duration())

    def _refresh(self) -> None:
        for widget in (self._interval, self._duration, self._summary):
            widget.setVisible(not self._running)
        for widget in (self._line1, self._line2):
            widget.setVisible(self._running)
        self._interval.setEnabled(self._enabled)
        self._duration.setEnabled(self._enabled)
        if self._running:
            self._button.setText(" Ferma sequenza")
            self._button.setIcon(icons.icon("stop", theme.ERROR, theme.LINE_STRONG, 18))
            self._button.setEnabled(True)           # Ferma si può sempre premere
        else:
            self._button.setText(" Avvia sequenza")
            self._button.setIcon(icons.icon("pending", theme.TEXT, theme.LINE_STRONG, 18))
            self._button.setEnabled(self._enabled)


class EndChoice(NamedTuple):
    download: bool
    video: bool
    resolution: str
    fps: int


@dataclass
class SequenceJob:
    """Lo scarico di una sequenza in corso: dove vanno le foto e che video fare."""
    folder: Path
    frames: list[Path]
    video: bool
    resolution: str
    fps: int


class SequenceEndDialog(QDialog):
    """A fine sequenza: scaricare le foto in una cartella dedicata? montare il video?"""

    def __init__(self, photos: int, skipped: int = 0, aborted: bool = False,
                 only_raw: bool = False, since_remote_entry: bool = False,
                 ffmpeg: bool = True, resolution: str = DEFAULT_RESOLUTION,
                 fps: int = DEFAULT_FRAME_RATE, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sequenza")
        self._download: QPushButton | None = None
        self._later: QPushButton | None = None
        self._close: QPushButton | None = None

        saltate = f" ({skipped} saltate)" if skipped else ""
        if aborted:
            righe = [f"Sequenza interrotta: la fotocamera non risponde. {photos} foto{saltate}."]
        else:
            righe = [f"Sequenza finita: {photos} foto{saltate}."]
        if since_remote_entry and photos:
            righe.append("Sono le foto nuove dall'ingresso nel telecomando: comprendono "
                         "quelle scattate a mano prima della sequenza.")
        if photos:
            righe.append("Scaricarle in una cartella dedicata?")
        elif only_raw:
            righe.append("La fotocamera ha salvato solo RAW: niente da scaricare qui, "
                         "niente video.")
        else:
            righe.append("Nessuna foto nuova sulla scheda.")

        colonna = QVBoxLayout(self)
        colonna.setSpacing(10)
        self._message = QLabel("\n".join(righe))
        self._message.setWordWrap(True)
        self._message.setMinimumWidth(380)
        colonna.addWidget(self._message)

        self._video = QCheckBox("Monta il video", self)
        self._video_note = QLabel("", self)
        self._video_note.setObjectName("faint")
        self._resolution = QComboBox(self)
        for nome in RESOLUTIONS:
            self._resolution.addItem(nome)
        self._resolution.setCurrentText(resolution if resolution in RESOLUTIONS
                                        else DEFAULT_RESOLUTION)
        self._fps = QComboBox(self)
        for valore in FRAME_RATES:
            self._fps.addItem(f"{valore} fotogrammi al secondo", valore)
        scelto = self._fps.findData(fps)
        self._fps.setCurrentIndex(scelto if scelto >= 0 else self._fps.findData(DEFAULT_FRAME_RATE))

        if not ffmpeg:
            self._video_note.setText("ffmpeg non è installato: niente video.")
        elif photos < 2:
            self._video_note.setText("Per il video servono almeno due foto.")
        possibile = ffmpeg and photos >= 2
        self._video.setEnabled(possibile)
        self._video.setChecked(possibile)
        self._video.toggled.connect(self._sync_video)

        pulsanti = QHBoxLayout()
        pulsanti.addStretch(1)
        if photos:
            griglia = QGridLayout()
            griglia.addWidget(self._video, 0, 0, 1, 2)
            griglia.addWidget(self._video_note, 1, 0, 1, 2)
            griglia.addWidget(QLabel("Risoluzione"), 2, 0)
            griglia.addWidget(self._resolution, 2, 1)
            griglia.addWidget(QLabel("Velocità"), 3, 0)
            griglia.addWidget(self._fps, 3, 1)
            colonna.addLayout(griglia)
            self._later = QPushButton("Non ora")
            self._later.clicked.connect(self.reject)
            self._download = QPushButton("Scarica")
            self._download.setObjectName("primary")
            self._download.setDefault(True)
            self._download.clicked.connect(self.accept)
            pulsanti.addWidget(self._later)
            pulsanti.addWidget(self._download)
        else:
            for widget in (self._video, self._video_note, self._resolution, self._fps):
                widget.hide()               # niente da scaricare: le scelte del video non servono
            self._close = QPushButton("Chiudi")
            self._close.setDefault(True)
            self._close.clicked.connect(self.reject)
            pulsanti.addWidget(self._close)
        colonna.addLayout(pulsanti)
        if not self._video_note.text():
            self._video_note.hide()
        self._sync_video()

    def _sync_video(self) -> None:
        attivo = self._video.isEnabled() and self._video.isChecked()
        self._resolution.setEnabled(attivo)
        self._fps.setEnabled(attivo)

    def message(self) -> str:
        return self._message.text()

    def choice(self) -> EndChoice:
        scarica = self._download is not None and self.result() == QDialog.DialogCode.Accepted
        video = scarica and self._video.isEnabled() and self._video.isChecked()
        return EndChoice(scarica, video, self._resolution.currentText(),
                         int(self._fps.currentData()))
```

- [ ] **Step 4: Verifica**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_sequence_gui.py`
Expected: `16 passed`.

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: `691 passed` (675 più 16).

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/gui/sequence.py tests/test_sequence_gui.py
git commit -m "feat(gui): sezione Sequenza del pannello e finestra di fine sequenza

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Il regista della sequenza

**Files:**
- Modify: `olympus_connect/gui/sequence.py` (in coda: `SequenceController`; import in testa)
- Test: `tests/test_sequence_controller.py`

**Interfaces:**
- Consumes: `SequencePlan`, `SequenceResult`, `SequenceState`, `progress_text` da `core/sequence.py`; `SleepInhibitor` da `core/power.py` (`start(seconds) -> bool`, `stop()`).
- Produces:

```python
class SequenceController(QObject):
    shoot_requested = pyqtSignal()          # la finestra deve far partire uno scatto adesso
    wake_requested = pyqtSignal()           # fra poco tocca scattare: il live in pausa va riacceso
    progress = pyqtSignal(str, str)         # le due righe del pannello
    ended = pyqtSignal(object)              # SequenceResult

    TICK_MS = 200

    def __init__(self, ready: Callable[[], bool], clock: Callable[[], float] = time.monotonic,
                 inhibitor=None, now: Callable[[], datetime] = datetime.now, parent=None)
    def is_running(self) -> bool
    def start(self, plan: SequencePlan, busy: float | None = None) -> bool   # falso: PC non bloccato
    def stop(self) -> None                  # Ferma: finisce quando lo scatto in corso è finito
    def abort(self) -> SequenceResult | None    # subito, senza segnale `ended`
    def shot_done(self, ok: bool) -> None
    def may_pause_live(self) -> bool
    def tick(self) -> None
```

Chi riceve `shoot_requested` **deve** far partire lo scatto oppure chiamare `shot_done(False)`: finché non arriva `shot_done` il regista considera lo scatto in corso.

- [ ] **Step 1: Scrivi i test**

`tests/test_sequence_controller.py`:

```python
"""Il regista della sequenza: timer, scatti, sveglia del live, fine."""
import time
from datetime import datetime

from olympus_connect.core.sequence import SequencePlan, SequenceResult
from olympus_connect.gui.sequence import SequenceController

QUANDO = datetime(2026, 11, 11, 14, 30, 0)


class _Orologio:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def avanti(self, secondi):
        self.t += secondi


class _Blocco:
    def __init__(self, parte=True):
        self.parte = parte
        self.eventi = []

    def start(self, seconds):
        self.eventi.append(("start", seconds))
        return self.parte

    def stop(self):
        self.eventi.append(("stop",))


def _regista(pronto=lambda: True, blocco=None):
    """Il timer vero non scatta mai: i test non fanno girare gli eventi e chiamano tick()."""
    orologio = _Orologio()
    blocco = blocco or _Blocco()
    regista = SequenceController(pronto, clock=orologio, inhibitor=blocco, now=lambda: QUANDO)
    ev = {"scatti": 0, "sveglie": 0, "righe": [], "fine": []}
    regista.shoot_requested.connect(lambda: ev.__setitem__("scatti", ev["scatti"] + 1))
    regista.wake_requested.connect(lambda: ev.__setitem__("sveglie", ev["sveglie"] + 1))
    regista.progress.connect(lambda a, b: ev["righe"].append((a, b)))
    regista.ended.connect(ev["fine"].append)
    return regista, orologio, blocco, ev


def test_start_shoots_at_once_and_blocks_the_pc_sleep(qapp):
    regista, _orologio, blocco, ev = _regista()
    assert not regista.is_running()
    assert regista.start(SequencePlan(5, 1800)) is True
    assert regista.is_running() and ev["scatti"] == 1
    assert blocco.eventi == [("start", 1800)]
    assert ev["righe"][-1] == ("Scatto 1 di 360 · in corso", "Mancano 30 min")


def test_shots_follow_the_interval(qapp):
    regista, orologio, _blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    orologio.avanti(1)
    regista.shot_done(True)
    assert ev["righe"][-1][0] == "Scatto 1 di 360 · prossimo fra 4 s"
    orologio.avanti(3.5)
    regista.tick()
    assert ev["scatti"] == 1
    orologio.avanti(0.5)
    regista.tick()
    assert ev["scatti"] == 2


def test_a_due_shot_waits_until_the_remote_is_ready(qapp):
    pronto = {"si": False}
    regista, orologio, _blocco, ev = _regista(lambda: pronto["si"])
    regista.start(SequencePlan(5, 1800))
    orologio.avanti(2)
    regista.tick()
    assert ev["scatti"] == 0 and ev["righe"][-1][0] == "In partenza…"
    pronto["si"] = True
    regista.tick()
    assert ev["scatti"] == 1


def test_stop_ends_after_the_shot_in_flight(qapp):
    regista, orologio, blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    regista.stop()
    assert regista.is_running() and ev["fine"] == []
    orologio.avanti(1)
    regista.shot_done(True)
    assert ev["fine"] == [SequenceResult(taken=1, skipped=0, aborted=False, started_at=QUANDO)]
    assert not regista.is_running() and blocco.eventi[-1] == ("stop",)
    regista.tick()                              # dopo la fine: niente
    assert ev["scatti"] == 1 and len(ev["fine"]) == 1


def test_stop_between_two_shots_ends_at_once(qapp):
    regista, orologio, _blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    orologio.avanti(1)
    regista.shot_done(True)
    regista.stop()
    assert len(ev["fine"]) == 1 and not regista.is_running()


def test_abort_returns_the_result_without_the_signal(qapp):
    regista, orologio, blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    orologio.avanti(1)
    regista.shot_done(True)
    assert regista.abort() == SequenceResult(1, 0, False, QUANDO)
    assert ev["fine"] == [] and not regista.is_running() and blocco.eventi[-1] == ("stop",)
    assert regista.abort() is None              # non c'è più nulla da fermare


def test_three_failed_shots_in_a_row_end_the_sequence_as_aborted(qapp):
    regista, orologio, _blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    for _ in range(3):
        orologio.avanti(1)
        regista.shot_done(False)
        orologio.avanti(4)
        regista.tick()
    assert ev["fine"] == [SequenceResult(0, 3, True, QUANDO)]
    assert ev["scatti"] == 3


def test_the_sequence_ends_with_its_last_shot(qapp):
    regista, orologio, _blocco, ev = _regista()
    regista.start(SequencePlan(5, 10))          # due scatti: a 0 e a 5 s
    orologio.avanti(1)
    regista.shot_done(True)
    orologio.avanti(4)
    regista.tick()
    orologio.avanti(1)
    regista.shot_done(True)
    assert ev["fine"] == [SequenceResult(2, 0, False, QUANDO)]


def test_the_live_is_woken_once_five_seconds_before_each_shot(qapp):
    regista, orologio, _blocco, ev = _regista()
    regista.start(SequencePlan(120, 900))
    orologio.avanti(1)
    regista.shot_done(True)
    assert regista.may_pause_live() and ev["sveglie"] == 0
    orologio.avanti(113)
    regista.tick()
    assert ev["sveglie"] == 0                   # mancano 6 s
    orologio.avanti(1)
    regista.tick()
    regista.tick()
    assert ev["sveglie"] == 1 and ev["scatti"] == 1
    orologio.avanti(5)
    regista.tick()
    assert ev["scatti"] == 2
    orologio.avanti(1)
    regista.shot_done(True)
    orologio.avanti(114)
    regista.tick()
    assert ev["sveglie"] == 2                   # una per scatto


def test_short_intervals_never_pause_the_live(qapp):
    regista, orologio, _blocco, _ev = _regista()
    assert not regista.may_pause_live()         # ferma
    regista.start(SequencePlan(5, 900))
    orologio.avanti(1)
    regista.shot_done(True)
    assert not regista.may_pause_live()


def test_a_missing_sleep_block_is_reported_and_the_sequence_runs(qapp):
    regista, _orologio, _blocco, ev = _regista(blocco=_Blocco(parte=False))
    assert regista.start(SequencePlan(5, 1800)) is False
    assert regista.is_running() and ev["scatti"] == 1


def test_calls_on_an_idle_controller_do_nothing(qapp):
    regista, _orologio, blocco, ev = _regista()
    regista.tick()
    regista.stop()
    regista.shot_done(True)
    assert ev == {"scatti": 0, "sveglie": 0, "righe": [], "fine": []} and blocco.eventi == []


def test_the_real_timer_drives_the_sequence(qapp):
    regista = SequenceController(lambda: True, inhibitor=_Blocco())
    scatti = []
    regista.shoot_requested.connect(lambda: scatti.append(time.monotonic()))
    regista.start(SequencePlan(1, 300))
    regista.shot_done(True)
    fine = time.monotonic() + 3
    while len(scatti) < 2 and time.monotonic() < fine:
        qapp.processEvents()
        time.sleep(0.01)
    regista.abort()
    assert len(scatti) == 2 and 0.9 <= scatti[1] - scatti[0] <= 1.6
```

- [ ] **Step 2: Verifica che falliscano**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_sequence_controller.py`
Expected: errore di raccolta, `ImportError: cannot import name 'SequenceController'`.

- [ ] **Step 3: Scrivi il regista**

In `olympus_connect/gui/sequence.py` sostituisci gli import in testa con questi (aggiungono `time`, `datetime`, `Callable`, `QObject`, `QTimer`, il blocco della sospensione e l'automa):

```python
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, NamedTuple, Sequence

from PyQt6.QtCore import QObject, QSize, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton,
    QVBoxLayout, QWidget,
)

from ..core.power import SleepInhibitor
from ..core.sequence import (
    DEFAULT_DURATION, DEFAULT_INTERVAL, DURATIONS, INTERVALS, SequencePlan, SequenceResult,
    SequenceState, format_span, nearest, progress_text,
)
from ..core.timelapse import (
    DEFAULT_FRAME_RATE, DEFAULT_RESOLUTION, FRAME_RATES, RESOLUTIONS,
)
from . import icons, theme
from .panels import SectionLabel
from .shot import label_column_width
```

In coda al file:

```python
class SequenceController(QObject):
    """Fa girare una sequenza: a ogni scadenza chiede lo stesso scatto del
    pulsante «Scatta».

    Non conosce la finestra: sa se il telecomando è pronto da una funzione
    che la finestra gli passa, e la finestra gli dice com'è finito ogni
    scatto con shot_done(). Chi riceve shoot_requested deve far partire lo
    scatto oppure chiamare shot_done(False).
    """

    shoot_requested = pyqtSignal()
    wake_requested = pyqtSignal()           # il live in pausa va riacceso: fra poco si scatta
    progress = pyqtSignal(str, str)
    ended = pyqtSignal(object)              # SequenceResult

    TICK_MS = 200

    def __init__(self, ready: Callable[[], bool],
                 clock: Callable[[], float] = time.monotonic, inhibitor=None,
                 now: Callable[[], datetime] = datetime.now, parent=None) -> None:
        super().__init__(parent)
        self._ready = ready
        self._clock = clock
        self._wall = now
        self._inhibitor = inhibitor if inhibitor is not None else SleepInhibitor()
        self._state: SequenceState | None = None
        self._started_at: datetime | None = None
        self._woken = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self.tick)

    def is_running(self) -> bool:
        return self._state is not None

    def start(self, plan: SequencePlan, busy: float | None = None) -> bool:
        """Avvia la sequenza. Falso se non è riuscito a impedire la
        sospensione del PC: la sequenza parte lo stesso."""
        self._state = SequenceState(plan, busy)
        self._started_at = self._wall()
        self._woken = False
        self._state.start(self._clock())
        bloccato = self._inhibitor.start(plan.duration)
        self._timer.start()
        self.tick()
        return bloccato

    def stop(self) -> None:
        if self._state is not None:
            self._state.stop()
            self.tick()

    def abort(self) -> SequenceResult | None:
        """Ferma subito, senza segnale: per l'uscita dal telecomando e la chiusura."""
        return self._close()

    def shot_done(self, ok: bool) -> None:
        if self._state is not None:
            self._state.shot_done(ok, self._clock())
            self._woken = False
            self.tick()

    def may_pause_live(self) -> bool:
        return self._state is not None and self._state.may_pause_live(self._clock())

    def tick(self) -> None:
        stato = self._state
        if stato is None:
            return
        adesso = self._clock()
        if not stato.finished:
            if not self._woken and stato.wake_due(adesso):
                self._woken = True
                self.wake_requested.emit()
            if stato.tick(adesso, self._ready()):
                self.shoot_requested.emit()
        if self._state is not stato:
            return                          # fermata da chi ha ricevuto un segnale
        if stato.finished:
            self.ended.emit(self._close())
        else:
            self.progress.emit(*progress_text(stato, adesso))

    def _close(self) -> SequenceResult | None:
        stato, self._state = self._state, None
        self._timer.stop()
        if stato is None:
            return None
        self._inhibitor.stop()
        return SequenceResult(stato.taken, stato.skipped, stato.aborted, self._started_at)
```

- [ ] **Step 4: Verifica**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_sequence_controller.py tests/test_sequence_gui.py`
Expected: `29 passed` (13 più 16).

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: `704 passed`.

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/gui/sequence.py tests/test_sequence_controller.py
git commit -m "feat(gui): regista della sequenza (timer, scatti, sveglia del live, fine)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: I comandi nei worker e l'elenco in modalità scatto

**Files:**
- Modify: `olympus_connect/core/protocol.py` (`list_media`), `olympus_connect/gui/workers.py` (`RemoteWorker`, nuovo `TimelapseWorker`), `olympus_connect/gui/grid.py` (`MediaListModel.camera_paths`)
- Test: `tests/test_protocol.py`, `tests/test_workers.py`, `tests/test_grid.py` (in coda a ciascuno)

**Interfaces:**
- Consumes: `find_ffmpeg`, `link_frames`, `build_command`, `parse_progress` da `core/timelapse.py`; `describe_error` e `RemoteWorker` esistenti in `gui/workers.py`; `OlympusClient.stop_liveview()`, `start_liveview(port)`, `list_media()`.
- Produces:

```python
# core/protocol.py
def list_media(self, directory: str = "/DCIM", switch_to_play: bool = True) -> list[MediaFile]
#   switch_to_play=False: non cambia modalità (get_imglist risponde anche in modalità scatto,
#   verificato sul campo il 2026-11-11)

# gui/workers.py — RemoteWorker
names_read = pyqtSignal(object)        # set[str] dei percorsi sulla scheda, oppure None
live_resumed = pyqtSignal(bool)        # startliveview riuscito / fallito
@pyqtSlot() def snapshot_names(self) -> None
@pyqtSlot() def pause_live(self) -> None            # stopliveview; un errore si ignora
@pyqtSlot(int) def resume_live(self, port: int) -> None

# gui/workers.py
class TimelapseWorker(QObject):
    progress = pyqtSignal(int, int)    # fotogrammi scritti, totale
    done = pyqtSignal(str)             # percorso del video
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    def __init__(self, files: list[Path], out: Path, fps: int, size: tuple[int, int],
                 ffmpeg: str | None = None)
    def cancel(self) -> None           # thread-safe
    @pyqtSlot() def run(self) -> None

# gui/grid.py — MediaListModel
def camera_paths(self) -> set[str]     # i percorsi di tutti i file dell'elenco, filtri ignorati
```

- [ ] **Step 1: Scrivi i test**

In coda a `tests/test_protocol.py`:

```python
def test_list_media_can_stay_in_shooting_mode(requests_mock):
    # Sul campo (2026-11-11) get_imglist risponde anche in modalità scatto: per
    # l'istantanea di inizio sequenza non si deve passare in riproduzione.
    requests_mock.get(f"{BASE_URL}/get_imglist.cgi",
                      text="VER_100\r\n/DCIM/100OLYMP,P9240123.JPG,5000000,0,19594,45820\r\n")
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")
    files = OlympusClient().list_media("/DCIM/100OLYMP", switch_to_play=False)
    assert [f.name for f in files] == ["P9240123.JPG"]
    assert not [r for r in requests_mock.request_history if "switch_cammode" in r.url]
```

In coda a `tests/test_workers.py`:

```python
# --- sequenze: istantanea dei nomi, pausa del live, montaggio -------------------------

import threading  # noqa: E402

from olympus_connect.core.timelapse import find_ffmpeg  # noqa: E402
from olympus_connect.gui.workers import RemoteWorker, TimelapseWorker  # noqa: E402


class _SequenceClient:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise RuntimeError("guasto finto")

    def list_media(self, directory="/DCIM", switch_to_play=True):
        self._call("list_media", directory, switch_to_play)
        return [media("P1.JPG"), media("P1.ORF")]

    def stop_liveview(self):
        self._call("stop_liveview")

    def start_liveview(self, port):
        self._call("start_liveview", port)


def _sequence_worker(client):
    worker = RemoteWorker(client)
    ev = {"names": [], "resumed": [], "failed": []}
    worker.names_read.connect(ev["names"].append)
    worker.live_resumed.connect(ev["resumed"].append)
    worker.failed.connect(ev["failed"].append)
    return worker, ev


def test_snapshot_reads_the_card_without_leaving_shooting_mode(qapp):
    client = _SequenceClient()
    worker, ev = _sequence_worker(client)
    worker.snapshot_names()
    assert client.calls == [("list_media", "/DCIM", False)]
    assert ev["names"] == [{"/DCIM/100OLYMP/P1.JPG", "/DCIM/100OLYMP/P1.ORF"}]


def test_a_failed_snapshot_reports_none_and_no_error(qapp):
    worker, ev = _sequence_worker(_SequenceClient(fail=["list_media"]))
    worker.snapshot_names()
    assert ev["names"] == [None] and ev["failed"] == []


def test_pause_and_resume_the_live(qapp):
    client = _SequenceClient()
    worker, ev = _sequence_worker(client)
    worker.pause_live()
    worker.resume_live(5555)
    assert client.calls == [("stop_liveview",), ("start_liveview", 5555)]
    assert ev["resumed"] == [True] and ev["failed"] == []


def test_live_pause_and_resume_failures_stay_quiet(qapp):
    worker, ev = _sequence_worker(_SequenceClient(fail=["stop_liveview", "start_liveview"]))
    worker.pause_live()
    worker.resume_live(5555)
    assert ev["resumed"] == [False] and ev["failed"] == []


def _foto(tmp_path, quante=3):
    cartella = tmp_path / "Sequenza"
    cartella.mkdir()
    foto = []
    for i in range(quante):
        percorso = cartella / f"P{i}.JPG"
        percorso.write_bytes(b"jpeg finto")
        foto.append(percorso)
    return cartella, foto


def _finto_ffmpeg(tmp_path, corpo):
    """Uno script al posto di ffmpeg: l'ultimo argomento è il file di uscita."""
    script = tmp_path / "finto-ffmpeg"
    script.write_text('#!/bin/sh\nfor a in "$@"; do out="$a"; done\n' + corpo)
    script.chmod(0o755)
    return str(script)


def _timelapse(foto, uscita, ffmpeg):
    worker = TimelapseWorker(foto, uscita, 25, (1920, 1080), ffmpeg=ffmpeg)
    ev = {"progress": [], "done": [], "failed": [], "cancelled": 0}
    worker.progress.connect(lambda n, t: ev["progress"].append((n, t)))
    worker.done.connect(ev["done"].append)
    worker.failed.connect(ev["failed"].append)
    worker.cancelled.connect(lambda: ev.__setitem__("cancelled", ev["cancelled"] + 1))
    return worker, ev


def test_timelapse_reports_progress_and_renames_the_finished_video(qapp, tmp_path):
    cartella, foto = _foto(tmp_path)
    uscita = cartella / "timelapse.mp4"
    ffmpeg = _finto_ffmpeg(tmp_path, 'echo frame=1\necho fps=25\necho frame=3\necho video > "$out"\n')
    worker, ev = _timelapse(foto, uscita, ffmpeg)
    worker.run()
    assert ev["progress"] == [(1, 3), (3, 3)]
    assert ev["done"] == [str(uscita)] and ev["failed"] == []
    assert uscita.read_text() == "video\n"
    assert not (cartella / "timelapse.mp4.part").exists()


def test_timelapse_failure_removes_the_partial_file_and_keeps_the_photos(qapp, tmp_path):
    cartella, foto = _foto(tmp_path)
    uscita = cartella / "timelapse.mp4"
    ffmpeg = _finto_ffmpeg(tmp_path, 'echo meta > "$out"\necho "codec rotto" >&2\nexit 1\n')
    worker, ev = _timelapse(foto, uscita, ffmpeg)
    worker.run()
    assert ev["done"] == [] and len(ev["failed"]) == 1
    assert "Il montaggio del video non è riuscito" in ev["failed"][0]
    assert "codec rotto" in ev["failed"][0]
    assert not uscita.exists() and not (cartella / "timelapse.mp4.part").exists()
    assert all(p.exists() for p in foto)


def test_timelapse_without_ffmpeg_fails_with_a_clear_message(qapp, tmp_path, monkeypatch):
    cartella, foto = _foto(tmp_path)
    monkeypatch.setattr("olympus_connect.gui.workers.find_ffmpeg", lambda: None)
    worker, ev = _timelapse(foto, cartella / "timelapse.mp4", None)
    worker.run()
    assert ev["failed"] == ["ffmpeg non è installato: il video non si può montare."]


def test_timelapse_can_be_cancelled_from_another_thread(qapp, tmp_path):
    cartella, foto = _foto(tmp_path)
    uscita = cartella / "timelapse.mp4"
    ffmpeg = _finto_ffmpeg(tmp_path, 'echo meta > "$out"\necho frame=1\nexec sleep 30\n')
    worker, ev = _timelapse(foto, uscita, ffmpeg)
    worker.progress.connect(lambda n, t: threading.Thread(target=worker.cancel).start())
    worker.run()                                 # torna quando il processo è stato terminato
    assert ev["cancelled"] == 1 and ev["done"] == [] and ev["failed"] == []
    assert not uscita.exists() and not (cartella / "timelapse.mp4.part").exists()


def test_timelapse_with_the_real_ffmpeg(qapp, tmp_path):
    if find_ffmpeg() is None:
        pytest.skip("ffmpeg non installato")
    from PyQt6.QtGui import QImage
    cartella = tmp_path / "Sequenza"
    cartella.mkdir()
    foto = []
    for i, colore in enumerate((0xFF203040, 0xFF806020, 0xFF20A060)):
        immagine = QImage(640, 480, QImage.Format.Format_RGB32)
        immagine.fill(colore)
        percorso = cartella / f"P{i}.JPG"
        assert immagine.save(str(percorso), "JPEG")
        foto.append(percorso)
    uscita = cartella / "timelapse.mp4"
    worker, ev = _timelapse(foto, uscita, None)
    worker.run()
    assert ev["failed"] == [] and ev["done"] == [str(uscita)]
    assert ev["progress"][-1] == (3, 3) and uscita.stat().st_size > 1000
```

In coda a `tests/test_grid.py` (se il file non ha già un modo di costruire i `Job`, usa questo così com'è):

```python
def test_model_reports_every_camera_path_whatever_the_filter(qapp, tmp_path):
    from datetime import datetime

    from olympus_connect.core.downloader import Job, JobState
    from olympus_connect.core.model import MediaFile, MediaKind
    from olympus_connect.gui.grid import MediaFilter, MediaListModel

    def lavoro(nome, kind):
        m = MediaFile(f"/DCIM/100OLYMP/{nome}", 10, datetime(2026, 11, 11, 12, 0, 0), kind)
        return Job(m, tmp_path / nome, JobState.NEW)

    modello = MediaListModel()
    assert modello.camera_paths() == set()
    modello.set_jobs([lavoro("P1.JPG", MediaKind.JPEG), lavoro("P1.ORF", MediaKind.RAW)])
    modello.set_filter(MediaFilter(kinds={MediaKind.JPEG}, only_new=False))
    assert modello.camera_paths() == {"/DCIM/100OLYMP/P1.JPG", "/DCIM/100OLYMP/P1.ORF"}
```

- [ ] **Step 2: Verifica che falliscano**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_workers.py tests/test_protocol.py tests/test_grid.py`
Expected: errore di raccolta in `tests/test_workers.py` (`ImportError: cannot import name 'TimelapseWorker'`); gli altri due nuovi test falliscono con `TypeError` (`switch_to_play`) e `AttributeError` (`camera_paths`).

- [ ] **Step 3: `list_media` senza cambio di modalità**

In `olympus_connect/core/protocol.py` sostituisci l'intestazione e le prime due righe di `list_media`:

```python
    def list_media(self, directory: str = "/DCIM",
                   switch_to_play: bool = True) -> list[MediaFile]:
        """Elenca ricorsivamente i file multimediali sulla scheda.

        Di norma passa prima in riproduzione. Con switch_to_play=False lascia
        la fotocamera com'è: get_imglist risponde anche in modalità scatto
        (verificato sul campo il 2026-11-11), e serve per fotografare l'elenco
        all'inizio di una sequenza senza spegnere il live. Letto subito dopo
        uno scatto, l'elenco non contiene ancora quella foto.
        """
        if switch_to_play:
            self.ensure_play_mode()
```

(il resto del metodo non cambia).

- [ ] **Step 4: `camera_paths` nel modello**

In `olympus_connect/gui/grid.py`, nella classe `MediaListModel`, subito dopo `set_jobs`:

```python
    def camera_paths(self) -> set[str]:
        """I percorsi sulla fotocamera di tutti i file dell'elenco, filtri ignorati."""
        return {job.media.camera_path for job in self._all}
```

- [ ] **Step 5: I comandi del `RemoteWorker`**

In `olympus_connect/gui/workers.py`, nella classe `RemoteWorker`, aggiungi i due segnali dopo `exposing`:

```python
    names_read = pyqtSignal(object)       # set[str] dei percorsi sulla scheda, o None
    live_resumed = pyqtSignal(bool)       # startliveview dopo una pausa: riuscito o no
```

e i tre comandi prima di `leave`:

```python
    @pyqtSlot()
    def snapshot_names(self) -> None:
        """L'elenco della scheda all'inizio di una sequenza, senza uscire dalla
        modalità scatto. None se non riesce: la finestra usa la regola di riserva."""
        try:
            nomi = {m.camera_path for m in self._client.list_media(switch_to_play=False)}
        except Exception:
            nomi = None
        self.names_read.emit(nomi)

    @pyqtSlot()
    def pause_live(self) -> None:
        """Spegne il live fra due scatti di una sequenza. Se non riesce il live
        resta acceso, e non è un errore da mostrare."""
        try:
            self._client.stop_liveview()
        except Exception:
            pass

    @pyqtSlot(int)
    def resume_live(self, port: int) -> None:
        """Riaccende il live senza rientrare in modalità scatto (0,3 s sul campo)."""
        try:
            self._client.start_liveview(port)
        except Exception:
            self.live_resumed.emit(False)
            return
        self.live_resumed.emit(True)
```

Il test `test_snapshot_reads_the_card_without_leaving_shooting_mode` si aspetta la chiamata `list_media("/DCIM", False)`: il client finto registra gli argomenti con i loro valori predefiniti, quindi la chiamata vera `list_media(switch_to_play=False)` va bene così.

- [ ] **Step 6: Il `TimelapseWorker`**

In `olympus_connect/gui/workers.py` aggiungi agli import in testa:

```python
import os
import subprocess
import tempfile
import threading

from ..core.timelapse import build_command, find_ffmpeg, link_frames, parse_progress
```

(se `os`, `threading` o `Path` sono già importati non ripeterli) e in coda al file:

```python
class TimelapseWorker(QObject):
    """Monta il video di una sequenza con ffmpeg, su un thread proprio.

    Scrive in «<video>.part» e rinomina solo a montaggio riuscito: un file
    col nome definitivo è sempre un video intero. Se fallisce o viene
    annullato il parziale si cancella; le foto non si toccano mai.
    """

    progress = pyqtSignal(int, int)       # fotogrammi scritti, totale
    done = pyqtSignal(str)                # percorso del video
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, files: list[Path], out: Path, fps: int, size: tuple[int, int],
                 ffmpeg: str | None = None) -> None:
        super().__init__()
        self._files = [Path(f) for f in files]
        self._out = Path(out)
        self._fps = fps
        self._size = size
        self._ffmpeg = ffmpeg
        self._cancel = threading.Event()
        self._process: subprocess.Popen | None = None

    def cancel(self) -> None:
        """Thread-safe: si chiama dal thread della finestra."""
        self._cancel.set()
        processo = self._process
        if processo is not None and processo.poll() is None:
            processo.terminate()

    @pyqtSlot()
    def run(self) -> None:
        ffmpeg = self._ffmpeg or find_ffmpeg()
        if ffmpeg is None:
            self.failed.emit("ffmpeg non è installato: il video non si può montare.")
            return
        parziale = self._out.with_name(self._out.name + ".part")
        try:
            with tempfile.TemporaryDirectory(prefix="olympus-timelapse-") as temporanea:
                cartella = Path(temporanea)
                totale = link_frames(self._files, cartella)
                with open(cartella / "errori.txt", "w+b") as errori:
                    self._process = subprocess.Popen(
                        build_command(ffmpeg, cartella, self._fps, self._size, parziale),
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errori,
                        text=True)
                    if self._cancel.is_set():
                        self._process.terminate()
                    for riga in self._process.stdout:
                        scritti = parse_progress(riga)
                        if scritti is not None:
                            self.progress.emit(min(scritti, totale), totale)
                    codice = self._process.wait()
                    errori.seek(0)
                    messaggio = errori.read().decode("utf-8", "replace").strip()
        except Exception as exc:
            parziale.unlink(missing_ok=True)
            self.failed.emit(f"Il montaggio del video non è riuscito: {exc}")
            return
        if self._cancel.is_set():
            parziale.unlink(missing_ok=True)
            self.cancelled.emit()
            return
        if codice != 0:
            parziale.unlink(missing_ok=True)
            ultima = messaggio.splitlines()[-1] if messaggio else f"codice {codice}"
            self.failed.emit(f"Il montaggio del video non è riuscito: {ultima}")
            return
        os.replace(parziale, self._out)
        self.done.emit(str(self._out))
```

- [ ] **Step 7: Verifica**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_workers.py tests/test_protocol.py tests/test_grid.py`
Expected: tutti verdi; 11 test nuovi (1 nel protocollo, 9 nei worker, 1 nella griglia).

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: `715 passed`.

Run: `pgrep -x sleep -a`
Expected: nessun `sleep 30` rimasto dal test dell'annullamento.

- [ ] **Step 8: Commit**

```bash
git add olympus_connect/core/protocol.py olympus_connect/gui/workers.py olympus_connect/gui/grid.py tests/test_protocol.py tests/test_workers.py tests/test_grid.py
git commit -m "feat(gui): istantanea della scheda, pausa del live e montaggio video nei worker

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: La sequenza nel pannello e nella finestra (avvio, scatti, arresto)

**Files:**
- Modify: `olympus_connect/gui/remote.py` (`RemoteView.show_review`, `_end_review`, `clear`; `RemotePanel`), `olympus_connect/gui/main_window.py`
- Test: `tests/test_remote_view.py`, `tests/test_main_window.py` (in coda a ciascuno)

**Interfaces:**
- Consumes: `SequenceSection`, `SequenceController` (Task 2 e 3); `RemoteWorker.snapshot_names` e `names_read` (Task 4); `SequencePlan`, `SequenceResult`, `SNAPSHOT_SETTLE`, `DEFAULT_INTERVAL`, `DEFAULT_DURATION` da `core/sequence.py`; `shot_busy_seconds` da `core/camprops.py`; `DEFAULT_FRAME_RATE`, `FRAME_RATES` da `core/timelapse.py`; `SleepInhibitor` da `core/power.py`.
- Produces (usati dai Task 6, 7 e 8):

```python
# gui/remote.py
RemoteView.show_review(self, data: bytes, duration_ms: int | None = None) -> bool
RemotePanel.sequence: SequenceSection
RemotePanel.set_sequence_running(self, running: bool) -> None

# gui/main_window.py — MainWindow
def __init__(self, client_factory=OlympusClient, network_factory=CameraNetwork,
             settings=None, live_factory=LiveViewWorker, inhibitor_factory=SleepInhibitor)
SNAPSHOT_SETTLE: float                       # attributo di classe, 3.0
remote_snapshot_names = pyqtSignal()
self._sequence: SequenceController
self._sequence_plan: SequencePlan | None
self._sequence_before: set[str] | None       # istantanea dei percorsi all'avvio; None = non riuscita
self._sequence_result: SequenceResult | None # sequenza finita, in attesa dell'elenco (Task 7)
self._remote_props: dict                     # l'ultimo elenco di impostazioni letto
def on_sequence_start(self, interval: int, duration: int) -> None
def on_sequence_stop(self) -> None
def _sequence_ready(self) -> bool
def _after_shot_review(self) -> None         # fine dell'anteprima di uno scatto (il Task 6 lo estende)
def _confirm(self, title: str, text: str) -> bool
```

- [ ] **Step 1: Scrivi i test della vista e del pannello**

In coda a `tests/test_remote_view.py`:

```python
def test_a_review_can_be_shorter_and_the_normal_length_comes_back(qapp):
    vista = RemoteView()
    vista._review_timer.setInterval(400)             # la durata «normale» di questo test
    assert vista.show_review(_jpeg(), 100)
    assert vista._review_timer.interval() == 100
    fine = time.monotonic() + 1.0
    while time.monotonic() < fine and vista.is_reviewing():
        qapp.processEvents()
        time.sleep(0.01)
    assert not vista.is_reviewing()
    assert vista._review_timer.interval() == 400     # rimessa alla fine dell'anteprima
    assert vista.show_review(_jpeg())
    assert vista._review_timer.interval() == 400
    vista.clear()


def test_clear_during_a_short_review_restores_the_normal_length(qapp):
    vista = RemoteView()
    vista._review_timer.setInterval(400)
    vista.show_review(_jpeg(), 100)
    vista.show_review(_jpeg(), 150)                  # una seconda, prima che finisca la prima
    vista.clear()
    assert vista._review_timer.interval() == 400


def test_panel_has_the_sequence_section_and_locks_the_rest_while_it_runs(qapp):
    pannello = RemotePanel()
    assert pannello.sequence._button.isEnabled()
    pannello.set_sequence_running(True)
    assert pannello.sequence.is_running()
    assert not pannello._shoot.isEnabled() and not pannello._movie.isEnabled()
    assert pannello.sequence._button.isEnabled()             # Ferma
    pannello.set_busy(True)                                  # durante ogni scatto
    assert pannello.sequence._button.isEnabled()
    pannello.set_busy(False)
    pannello.set_sequence_running(False)
    assert pannello._shoot.isEnabled() and pannello._movie.isEnabled()


def test_panel_cannot_start_a_sequence_while_busy_or_recording(qapp):
    pannello = RemotePanel()
    pannello.set_busy(True)
    assert not pannello.sequence._button.isEnabled()
    pannello.set_busy(False)
    pannello.set_recording(True)
    assert not pannello.sequence._button.isEnabled()
    pannello.set_recording(False)
    assert pannello.sequence._button.isEnabled()
```

- [ ] **Step 2: Scrivi i test della finestra**

In coda a `tests/test_main_window.py`:

```python
# --- sequenze ------------------------------------------------------------------------

import time as _time  # noqa: E402


def _media_seq(nome, kind):
    return MediaFile(f"/DCIM/100OLYMP/{nome}", 100, datetime(2026, 11, 11, 12, 0, 0), kind)


class _FintoBlocco:
    """Al posto di SleepInhibitor: i test non devono bloccare la sospensione del PC."""
    istanze: list = []

    def __init__(self):
        self.eventi = []
        _FintoBlocco.istanze.append(self)

    def start(self, seconds):
        self.eventi.append(("start", seconds))
        return True

    def stop(self):
        self.eventi.append(("stop",))


class _SequenceStubClient(_PropsStubClient):
    """La scheda finta guadagna un JPG e un ORF a ogni scatto riuscito."""
    file: list = []
    guasti = 0                   # i primi N scatti falliscono
    istantanea_rotta = False     # l'elenco in modalità scatto fallisce
    elenco_rotto = 0             # i primi N elenchi in riproduzione falliscono

    def list_media(self, directory="/DCIM", switch_to_play=True):
        _RecordingClient.chiamate.append(("list_media", switch_to_play))
        if not switch_to_play and _SequenceStubClient.istantanea_rotta:
            raise RuntimeError("elenco in modalità scatto non riuscito")
        if switch_to_play and _SequenceStubClient.elenco_rotto > 0:
            _SequenceStubClient.elenco_rotto -= 1
            raise RuntimeError("elenco non riuscito")
        return list(_SequenceStubClient.file)

    def take_picture(self, read_timeout=None):
        _RecordingClient.chiamate.append(("take_picture",))
        if _SequenceStubClient.guasti > 0:
            _SequenceStubClient.guasti -= 1
            raise RuntimeError("scatto non riuscito")
        n = len(_SequenceStubClient.file) // 2 + 1
        _SequenceStubClient.file = _SequenceStubClient.file + [
            _media_seq(f"P{n:07d}.JPG", MediaKind.JPEG), _media_seq(f"P{n:07d}.ORF", MediaKind.RAW)]


def _scatti():
    return _RecordingClient.chiamate.count(("take_picture",))


@pytest.fixture
def seq_win(qapp, settings, tmp_path, monkeypatch):
    _RecordingClient.chiamate = []
    _FakeLive.istanze = []
    _FintoBlocco.istanze = []
    _PropsStubClient.valori = {}
    _SequenceStubClient.file = [_media_seq("P0000001.JPG", MediaKind.JPEG),
                                _media_seq("P0000001.ORF", MediaKind.RAW)]
    _SequenceStubClient.guasti = 0
    _SequenceStubClient.istantanea_rotta = False
    _SequenceStubClient.elenco_rotto = 0
    _RemoteStubClient.revisione = _jpeg_valido()
    settings.setValue("dest_root", str(tmp_path / "dest"))
    monkeypatch.setattr(MainWindow, "SNAPSHOT_SETTLE", 0.0)
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: True)
    win = MainWindow(client_factory=_SequenceStubClient,
                     network_factory=lambda ssid: FakeNetwork(all_green()),
                     settings=settings, live_factory=_FakeLive, inhibitor_factory=_FintoBlocco)
    win.resize(1200, 700)
    win.show()
    win.on_connected("E-M10MarkIV")
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("isospeedvalue") is not None)
    win._remote_view.set_frame(QImage(64, 48, QImage.Format.Format_RGB32))
    yield win
    win.close()


def test_the_sequence_section_remembers_its_values(windows, qapp, settings):
    settings.setValue("sequence/interval", 10)
    settings.setValue("sequence/duration", 600)
    win = windows(settings)
    sezione = win._remote_panel.sequence
    assert (sezione.interval(), sezione.duration()) == (10, 600)
    sezione._interval.step(1)
    sezione._duration.step(-1)
    assert int(settings.value("sequence/interval")) == 15
    assert int(settings.value("sequence/duration")) == 300
    assert sezione.summary_text() == "20 scatti · video di meno di 1 s a 25 fps"


def test_the_summary_follows_the_exposure_read_from_the_camera(seq_win, qapp):
    win = seq_win
    sezione = win._remote_panel.sequence
    assert _attendi(qapp, lambda: sezione.summary_text() == "360 scatti · video di 14 s a 25 fps")
    _PropsStubClient.valori = {"takemode": "M", "shutspeedvalue": '60"'}
    win.remote_read_props.emit()
    assert _attendi(qapp, lambda: sezione.summary_text() == (
        "la posa dura più dell'intervallo: scatti uno dopo l'altro, circa 15 in 30 min"))


def test_start_takes_the_snapshot_then_shoots_at_the_interval(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert win._remote_panel.sequence.is_running()
    assert _attendi(qapp, lambda: ("list_media", False) in _RecordingClient.chiamate)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert win._sequence_before == {"/DCIM/100OLYMP/P0000001.JPG", "/DCIM/100OLYMP/P0000001.ORF"}
    assert _FintoBlocco.istanze[-1].eventi == [("start", 300)]
    assert not win._remote_panel._shoot.isEnabled() and not win._remote_panel._movie.isEnabled()
    assert not win._remote_panel.props.row("isospeedvalue").isEnabled()
    assert _attendi(qapp, lambda: _scatti() >= 2, 3.0), "il secondo scatto non è partito"
    assert _attendi(qapp, lambda: win._remote_panel.sequence.progress_texts()[0].startswith("Scatto 2"))


def test_stop_ends_the_sequence_and_leaves_the_remote(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert _attendi(qapp, lambda: not win._shot_in_flight)
    win._remote_panel.sequence._button.click()            # Ferma
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    assert win._sequence_result is not None and win._sequence_result.taken >= 1
    assert not win._sequence.is_running() and not win._remote_panel.sequence.is_running()
    assert _FintoBlocco.istanze[-1].eventi[-1] == ("stop",)
    nomi = [c[0] for c in _RecordingClient.chiamate]
    assert "stop_liveview" in nomi and "ensure_play_mode" in nomi


def test_the_sequence_ends_by_itself_at_the_duration(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 2)                           # due scatti: a 0 e a 1 s
    assert _attendi(qapp, lambda: not win._remote_active, 6.0)
    assert win._sequence_result.taken == 2 and not win._sequence_result.aborted
    assert _scatti() == 2


def test_focus_clicks_and_the_shoot_shortcut_are_ignored_during_a_sequence(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert _attendi(qapp, lambda: not win._shot_in_flight)
    prima = _scatti()
    win.on_remote_focus_requested(10, 10)
    win._shortcut_shoot.activated.emit()
    _attendi(qapp, lambda: False, 0.3)
    assert not [c for c in _RecordingClient.chiamate if c[0] == "assign_af"]
    assert _scatti() == prima                              # il prossimo è fra un secondo


def test_a_failed_shot_is_counted_and_the_sequence_goes_on(seq_win, qapp):
    win = seq_win
    _SequenceStubClient.guasti = 1
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 2, 3.0)
    assert _attendi(qapp, lambda: win._remote_panel.sequence.progress_texts()[1].endswith("1 saltato"))
    assert win._sequence.is_running()


def test_three_failed_shots_end_the_sequence_as_aborted(seq_win, qapp):
    win = seq_win
    _SequenceStubClient.guasti = 99
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: win._sequence_result is not None, 6.0)
    assert win._sequence_result.aborted and win._sequence_result.skipped == 3
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    assert _scatti() == 3


def test_the_snapshot_waits_after_a_manual_shot(seq_win, qapp, monkeypatch):
    win = seq_win
    monkeypatch.setattr(MainWindow, "SNAPSHOT_SETTLE", 0.8)
    win._remote_panel._shoot.click()
    assert _attendi(qapp, lambda: _scatti() == 1 and not win._shot_in_flight)
    win.on_sequence_start(1, 300)
    _attendi(qapp, lambda: False, 0.4)
    assert ("list_media", False) not in _RecordingClient.chiamate, "istantanea troppo presto"
    assert _attendi(qapp, lambda: ("list_media", False) in _RecordingClient.chiamate, 2.0)
    assert _attendi(qapp, lambda: len(win._sequence_before or ()) == 4)    # la foto a mano c'è già


def test_stop_while_preparing_cancels_the_start(seq_win, qapp, monkeypatch):
    win = seq_win
    monkeypatch.setattr(MainWindow, "SNAPSHOT_SETTLE", 5.0)
    win._last_shot_end = _time.monotonic()
    win.on_sequence_start(1, 300)
    assert win._remote_panel.sequence.is_running()
    win.on_sequence_stop()
    assert not win._remote_panel.sequence.is_running() and not win._sequence.is_running()
    _attendi(qapp, lambda: False, 0.3)
    assert ("list_media", False) not in _RecordingClient.chiamate and _scatti() == 0
    assert win._remote_active                                # resta nel telecomando


def test_a_failed_snapshot_still_starts_the_sequence(seq_win, qapp):
    win = seq_win
    _SequenceStubClient.istantanea_rotta = True
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert win._sequence_before is None


def test_leaving_during_a_sequence_asks_first(seq_win, qapp, monkeypatch):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert _attendi(qapp, lambda: not win._shot_in_flight)
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: False)
    win._view_grid.click()
    assert win._remote_active and not win._remote_leaving and win._sequence.is_running()
    assert win._view_remote.isChecked() and not win._view_grid.isChecked()
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: True)
    win._view_grid.click()
    assert not win._sequence.is_running()
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    assert win._sequence_result is not None and win._sequence_result.taken >= 1


def test_closing_during_a_sequence_asks_first(seq_win, qapp, monkeypatch):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: False)
    assert win.close() is False
    assert win.isVisible() and win._sequence.is_running() and not win._closing
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: True)
    assert win.close() is True
    assert not win._sequence.is_running()
    assert _FintoBlocco.istanze[-1].eventi[-1] == ("stop",)


def test_the_review_is_shorter_during_a_fast_sequence(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, win._remote_view.is_reviewing)
    assert win._remote_view._review_timer.interval() == 500     # metà dell'intervallo


def test_the_sequence_waits_while_the_remote_is_not_ready(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert _attendi(qapp, lambda: not win._shot_in_flight)
    win._remote_entered = False                               # come durante un rientro nel live
    prima = _scatti()
    _attendi(qapp, lambda: False, 1.5)
    assert _scatti() == prima
    win._remote_entered = True
    assert _attendi(qapp, lambda: _scatti() == prima + 1, 2.0)
```

- [ ] **Step 3: Verifica che falliscano**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_remote_view.py tests/test_main_window.py -k "sequence or review_can_be_shorter or clear_during or panel_has or panel_cannot or snapshot or summary_follows or failed_shot or three_failed or leaving_during or closing_during or focus_clicks"`
Expected: falliscono tutti i nuovi (`TypeError` per `duration_ms` e `inhibitor_factory`, `AttributeError` per `sequence`).

- [ ] **Step 4: L'anteprima di durata variabile**

In `olympus_connect/gui/remote.py`, nel costruttore di `RemoteView`, dopo `self._review_timer.timeout.connect(self._end_review)`:

```python
        self._review_restore: int | None = None     # durata normale, da rimettere
```

Sostituisci `show_review`, `_end_review` e, in `clear`, la riga `self._review_timer.stop()`:

```python
    def show_review(self, data: bytes, duration_ms: int | None = None) -> bool:
        """Mostra lo scatto sopra il live. `duration_ms` accorcia l'anteprima
        per questa volta (le sequenze veloci): alla fine torna la durata normale."""
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            return False
        self._review = pixmap
        self._retry.hide()                 # l'anteprima sta sopra a tutto, anche al Riprova
        self._fade.stop()
        self._fade.setStartValue(0.35)
        self._fade.setEndValue(1.0)
        self._fade.start()
        if duration_ms is None:
            self._review_timer.start()
        else:
            if self._review_restore is None:
                self._review_restore = self._review_timer.interval()
            self._review_timer.start(duration_ms)       # start(ms) cambia anche l'intervallo
        self.update()
        return True

    def _restore_review_length(self) -> None:
        if self._review_restore is not None:
            self._review_timer.setInterval(self._review_restore)
            self._review_restore = None

    def _end_review(self) -> None:
        self._restore_review_length()
        self._review = None
        if self._message and self._retry_wanted:
            self._retry.show()
        self.update()
        self.review_ended.emit()
```

e in `clear`:

```python
        self._review_timer.stop()
        self._restore_review_length()
```

- [ ] **Step 5: La sezione nel pannello**

In `olympus_connect/gui/remote.py` aggiungi l'import `from .sequence import SequenceSection` accanto a `from .props import PropsSection`. Nel costruttore di `RemotePanel`, subito dopo `self._take_mode_text = "—"`:

```python
        colonna.addSpacing(8)
        self.sequence = SequenceSection()
        colonna.addWidget(self.sequence)
```

Nel costruttore, accanto a `self._busy = False`, aggiungi `self._sequence_running = False` (prima della chiamata a `self._refresh()`). Aggiungi il metodo dopo `set_busy`:

```python
    def set_sequence_running(self, running: bool) -> None:
        """Durante una sequenza gli altri comandi sono spenti: resta solo Ferma."""
        self._sequence_running = running
        self.sequence.set_running(running)
        self._refresh()
```

e sostituisci le ultime tre righe di `_refresh`:

```python
        libero = not self._busy and not self._recording and not self._sequence_running
        self._shoot.setEnabled(libero)
        self._movie.setEnabled(not self._busy and not self._sequence_running)
        self.props.set_enabled(libero)
        self.sequence.set_enabled(not self._busy and not self._recording)
```

- [ ] **Step 6: La finestra: stato, segnali, impostazioni salvate**

In `olympus_connect/gui/main_window.py`:

Import, accanto a quelli di `..core`:

```python
from ..core.camprops import shot_busy_seconds
from ..core.power import SleepInhibitor
from ..core.sequence import (
    DEFAULT_DURATION, DEFAULT_INTERVAL, SNAPSHOT_SETTLE, SequencePlan, SequenceResult,
)
from ..core.timelapse import DEFAULT_FRAME_RATE, FRAME_RATES
```

e accanto a quelli di `.`: `from .sequence import SequenceController`.

Nella classe, dopo `remote_set_prop = pyqtSignal(str, str)`:

```python
    remote_snapshot_names = pyqtSignal()

    # Letto subito dopo uno scatto, l'elenco della scheda non contiene ancora
    # quella foto (prova sul campo del 2026-11-11): l'istantanea aspetta.
    SNAPSHOT_SETTLE = SNAPSHOT_SETTLE
```

Firma del costruttore:

```python
    def __init__(self, client_factory=OlympusClient,
                 network_factory=CameraNetwork,
                 settings: QSettings | None = None,
                 live_factory=LiveViewWorker,
                 inhibitor_factory=SleepInhibitor) -> None:
```

Nel costruttore, subito prima di `self._model = MediaListModel()`:

```python
        # Sequenze di scatti a intervalli.
        self._remote_props: dict = {}              # l'ultimo elenco di impostazioni letto
        self._last_shot_end = 0.0                  # tempo monotonic della fine dell'ultimo scatto
        self._sequence_plan: SequencePlan | None = None
        self._sequence_preparing = False           # fra Avvia e l'istantanea dei nomi
        self._sequence_before: set[str] | None = None
        self._sequence_result: SequenceResult | None = None
        self._sequence = SequenceController(self._sequence_ready,
                                            inhibitor=inhibitor_factory(), parent=self)
        self._sequence.shoot_requested.connect(self._sequence_shoot)
        self._sequence.ended.connect(self._on_sequence_ended)
```

In `_build_ui`, sostituisci `self._remote_view.review_ended.connect(self._restart_live_after_shot)` con `self._remote_view.review_ended.connect(self._after_shot_review)` e, dopo `self._remote_panel.props.prop_changed.connect(self.on_remote_prop_changed)`:

```python
        sezione = self._remote_panel.sequence
        sezione.set_values(int(self._settings.value("sequence/interval", DEFAULT_INTERVAL)),
                           int(self._settings.value("sequence/duration", DEFAULT_DURATION)))
        sezione.start_requested.connect(self.on_sequence_start)
        sezione.stop_requested.connect(self.on_sequence_stop)
        sezione.changed.connect(self._on_sequence_changed)
        self._sequence.progress.connect(sezione.set_progress)
        self._update_sequence_summary()
```

- [ ] **Step 7: La finestra: i metodi della sequenza**

Aggiungi questi metodi a `MainWindow`, subito prima di `def exit_remote`:

```python
    # -- sequenze -------------------------------------------------------------

    def _confirm(self, title: str, text: str) -> bool:
        risposta = QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return risposta == QMessageBox.StandardButton.Yes

    def _sequence_fps(self) -> int:
        valore = int(self._settings.value("sequence/fps", DEFAULT_FRAME_RATE))
        return valore if valore in FRAME_RATES else DEFAULT_FRAME_RATE

    def _update_sequence_summary(self) -> None:
        sezione = self._remote_panel.sequence
        sezione.set_summary(sezione.plan().summary(shot_busy_seconds(self._remote_props),
                                                   self._sequence_fps()))

    def _on_sequence_changed(self) -> None:
        sezione = self._remote_panel.sequence
        self._settings.setValue("sequence/interval", sezione.interval())
        self._settings.setValue("sequence/duration", sezione.duration())
        self._update_sequence_summary()

    def _sequence_ready(self) -> bool:
        """Il telecomando può scattare adesso: lo legge il regista a ogni giro."""
        return (self._remote_active and self._remote_entered and not self._remote_leaving
                and not self._closing and not self._shot_in_flight
                and not self._remote_panel.is_recording()
                and self._live_down is None and not self._live_restart_pending)

    def on_sequence_start(self, interval: int, duration: int) -> None:
        if (self._sequence.is_running() or self._sequence_preparing
                or not self._sequence_ready()):
            return
        self._sequence_preparing = True
        self._sequence_plan = SequencePlan(interval, duration)
        self._remote_panel.set_sequence_running(True)
        self._remote_panel.sequence.set_progress("Preparo la sequenza…", "")
        self.statusBar().showMessage("Sequenza: leggo la scheda…")
        attesa = max(0.0, self.SNAPSHOT_SETTLE - (time.monotonic() - self._last_shot_end))
        QTimer.singleShot(int(attesa * 1000), self._sequence_snapshot)

    def _sequence_snapshot(self) -> None:
        if (not self._sequence_preparing or not self._remote_active
                or self._remote_leaving or self._closing):
            return
        self.remote_snapshot_names.emit()

    @pyqtSlot(object)
    def on_remote_names(self, names: object) -> None:
        """L'istantanea dei nomi è arrivata (None: non riuscita): parte la sequenza."""
        if not self._sequence_preparing:
            return
        self._sequence_preparing = False
        if not self._sequence_ready() or self._sequence_plan is None:
            self._remote_panel.set_sequence_running(False)
            self.statusBar().showMessage("Sequenza non avviata: il telecomando non è pronto.")
            return
        self._sequence_before = names
        bloccato = self._sequence.start(self._sequence_plan,
                                        shot_busy_seconds(self._remote_props))
        self.statusBar().showMessage(
            "Sequenza in corso." if bloccato else
            "Sequenza in corso. Attenzione: non riesco a impedire la sospensione del PC.")

    def on_sequence_stop(self) -> None:
        if self._sequence_preparing:
            self._sequence_preparing = False
            self._remote_panel.set_sequence_running(False)
            self.statusBar().showMessage("Sequenza annullata.", 4000)
            return
        self._sequence.stop()

    def _sequence_shoot(self) -> None:
        """Lo scatto chiesto dal regista: la stessa strada del pulsante Scatta."""
        if not self._sequence_ready():
            self._sequence.shot_done(False)
            return
        self._remote_panel.set_busy(True)
        self._shot_in_flight = True
        self.statusBar().showMessage("Sequenza: scatto…")
        self.remote_shoot.emit()

    def _sequence_review_ms(self) -> int | None:
        """Durante una sequenza l'anteprima dura al più metà dell'intervallo."""
        if not self._sequence.is_running() or self._sequence_plan is None:
            return None
        return int(min(2.0, self._sequence_plan.interval / 2) * 1000)

    @pyqtSlot(object)
    def _on_sequence_ended(self, result: object) -> None:
        """Finita, fermata o interrotta: si esce dal telecomando; l'elenco che
        segue l'uscita dirà quali foto sono della sequenza."""
        self._remote_panel.set_sequence_running(False)
        if result is None:
            return
        self._sequence_result = result
        self.statusBar().showMessage(
            "Sequenza interrotta: la fotocamera non risponde." if result.aborted
            else "Sequenza finita.")
        if self._remote_active and not self._remote_leaving and not self._closing:
            self.exit_remote(self._remote_exit_view)

    def _after_shot_review(self) -> None:
        """Fine dell'anteprima di uno scatto (o subito, se l'anteprima non c'è)."""
        self._restart_live_after_shot()
```

- [ ] **Step 8: La finestra: i punti esistenti da toccare**

1. In `enter_remote`, dopo `self._live_down = None`:

```python
        self._remote_props = {}
        self._sequence_preparing = False
        self._sequence_before = None
        self._sequence_result = None       # una sequenza mai scaricata si dimentica qui
```

   dopo `remote.exposing.connect(self.on_remote_exposing)`:

```python
        remote.names_read.connect(self.on_remote_names)
```

   e dopo `self.remote_set_prop.connect(remote.set_prop, coda)`:

```python
        self.remote_snapshot_names.connect(remote.snapshot_names, coda)
```

2. In `_teardown_remote`, aggiungi `self.remote_snapshot_names` all'elenco dei segnali scollegati (dopo `self.remote_set_prop`).

3. In `on_remote_props`, dopo `self._remote_panel.props.set_props(props)`:

```python
        self._remote_props = props
        self._update_sequence_summary()
```

4. In `on_remote_focus_requested`, estendi la condizione di uscita:

```python
        if (not self._remote_active or self._remote_leaving or self._closing
                or self._remote_panel.is_recording() or self._shot_in_flight
                or self._sequence.is_running() or self._sequence_preparing):
            return
```

5. Sostituisci `on_remote_shot`:

```python
    @pyqtSlot(bytes)
    def on_remote_shot(self, review: bytes) -> None:
        self._last_shot_end = time.monotonic()
        self._end_shot(riuscito=True)
        self._remote_panel.set_busy(False)
        self.statusBar().showMessage("Scatto eseguito.", 4000)
        durata = self._sequence_review_ms()
        self._sequence.shot_done(True)      # può chiudere la sequenza e uscire dal telecomando
        if self._remote_leaving or not self._remote_active:
            return
        if not self._remote_view.show_review(review, durata):
            self._after_shot_review()       # niente anteprima da aspettare
```

6. In `on_remote_failed`, la prima riga `self._end_shot()` diventa:

```python
        era_scatto = self._shot_in_flight
        if era_scatto:
            self._last_shot_end = time.monotonic()
        self._end_shot()
```

   e in fondo al metodo:

```python
        if era_scatto:
            self._sequence.shot_done(False)
```

7. In `exit_remote`, dopo il primo `return`:

```python
        if self._sequence.is_running() or self._sequence_preparing:
            if not self._confirm("Sequenza in corso",
                                 "Uscendo dal telecomando la sequenza si fermerà. Uscire?"):
                self._view_grid.setChecked(False)
                self._view_table.setChecked(False)
                self._view_remote.setChecked(True)
                return
            self._sequence_preparing = False
            risultato = self._sequence.abort()
            self._remote_panel.set_sequence_running(False)
            if risultato is not None:
                self._sequence_result = risultato
```

8. In `on_remote_left`, dopo `self._mode_to_restore = None`:

```python
        self._sequence_preparing = False
        residuo = self._sequence.abort()        # di norma è già ferma
        if residuo is not None and self._sequence_result is None:
            self._sequence_result = residuo
        self._remote_panel.set_sequence_running(False)
```

9. In `closeEvent`, prima di `self._closing = True`:

```python
        if self._sequence.is_running() or self._sequence_preparing:
            if not self._confirm("Sequenza in corso",
                                 "Chiudendo il programma la sequenza si fermerà. Chiudere?"):
                event.ignore()
                return
            self._sequence_preparing = False
            self._sequence.abort()
        self._sequence_result = None
```

- [ ] **Step 9: Verifica**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_remote_view.py tests/test_main_window.py`
Expected: tutti verdi; 19 test nuovi (4 nella vista, 15 nella finestra).

Run due volte: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: `734 passed` entrambe le volte.

Run: `systemd-inhibit --list --no-pager | grep -c "Olympus Connect"`
Expected: `0` (i test usano il blocco finto: nessun blocco vero deve restare).

- [ ] **Step 10: Schermata**

Scrivi uno script nello scratchpad (non nel repository) che apre `MainWindow` con `_SequenceStubClient`-equivalente (client finto, `desclist_A.xml`, un fotogramma grigio sintetico), entra nel telecomando, e salva due PNG a 1366×740 in `.superpowers/sdd/<piano>/`: `task-5-sequenza.png` (prima dell'avvio) e `task-5-sequenza-in-corso.png` (dopo `on_sequence_start(5, 1800)` e il primo scatto). Guardale: la sezione Sequenza deve stare nel pannello senza testo tagliato né righe sovrapposte, anche in ART (nove righe di impostazioni). Annota nel rapporto quello che vedi. Nessuna immagine vera della fotocamera, nessun numero di serie.

- [ ] **Step 11: Commit**

```bash
git add olympus_connect/gui/remote.py olympus_connect/gui/main_window.py tests/test_remote_view.py tests/test_main_window.py
git commit -m "feat(telecomando): sequenze di scatti dal pannello (avvio, scatti a intervalli, arresto)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Il live in pausa fra gli scatti

**Files:**
- Modify: `olympus_connect/gui/main_window.py`
- Test: `tests/test_main_window.py` (in coda)

**Interfaces:**
- Consumes: `SequenceController.may_pause_live()`, `wake_requested` (Task 3); `RemoteWorker.pause_live`, `resume_live(port)`, `live_resumed(bool)` (Task 4); `LiveViewWorker.arm()` / `disarm()` / `take_frame()`; `MainWindow._after_shot_review`, `_sequence_ready`, `on_live_retry`, `_live_lost` (esistenti e Task 5).
- Produces:

```python
remote_pause_live = pyqtSignal()
remote_resume_live = pyqtSignal(int)
self._live_paused: bool       # il live è spento di proposito fra due scatti
self._live_waking: bool       # è partita la riaccensione, si aspetta il primo fotogramma
```

Comportamento (specifica, «Esito della prova», punto 3): con intervallo ≥ 60 s, alla fine dell'anteprima il live si spegne e la vista dice «Live in pausa fino al prossimo scatto.»; 5 s prima dello scatto il regista emette `wake_requested` e la finestra riaccende il live; il telecomando torna pronto al primo fotogramma. Se il live non arriva entro 3 s (sentinella `no_signal`) o `startliveview` fallisce, vale il rientro in rec esistente (`on_live_retry`), che rimette la modalità scelta dal PC. Inoltre, durante una sequenza, un live che si ferma fuori da uno scatto fa partire da solo lo stesso rientro: nessuno è lì a premere Riprova.

- [ ] **Step 1: Scrivi i test**

In coda a `tests/test_main_window.py`:

```python
# --- sequenze: live in pausa fra gli scatti ----------------------------------------------

@pytest.fixture
def pausa_win(seq_win, monkeypatch):
    """Le soglie vere sono 60 s e 5 s: qui si accorciano, le regole restano quelle."""
    import olympus_connect.core.sequence as seq
    monkeypatch.setattr(seq, "PAUSE_LIVE_FROM", 2)
    monkeypatch.setattr(seq, "LIVE_LEAD", 0.4)
    monkeypatch.setattr(seq, "MIN_PAUSE", 0.1)
    return seq_win


def _chiamate(nome):
    return [c for c in _RecordingClient.chiamate if c[0] == nome]


def _fotogramma(win):
    live = _FakeLive.istanze[-1]
    live.immagine = QImage(64, 48, QImage.Format.Format_RGB32)
    live.frame_ready.emit()


def test_the_live_pauses_after_the_review_and_wakes_before_the_next_shot(pausa_win, qapp):
    win = pausa_win
    live = _FakeLive.istanze[-1]
    armato = live.armato
    win.on_sequence_start(2, 300)
    assert _attendi(qapp, lambda: _scatti() == 1)
    assert _attendi(qapp, lambda: len(_chiamate("stop_liveview")) == 1, 3.0), "il live non va in pausa"
    assert win._live_paused and not win._live_waking
    assert win._remote_view.message() == "Live in pausa fino al prossimo scatto."
    assert not win._remote_view._retry.isVisible()
    assert _attendi(qapp, lambda: len(_chiamate("start_liveview")) == 2, 3.0), "il live non viene riacceso"
    assert win._live_waking and live.armato == armato + 1
    assert _scatti() == 1                                   # non scatta finché il live non è tornato
    _fotogramma(win)
    assert _attendi(qapp, lambda: not win._live_paused)
    assert win._remote_view.message() == ""
    assert _attendi(qapp, lambda: _scatti() == 2, 3.0)


def test_frames_arriving_right_after_the_pause_are_dropped(pausa_win, qapp):
    win = pausa_win
    win.on_sequence_start(2, 300)
    assert _attendi(qapp, lambda: win._live_paused, 3.0)
    _fotogramma(win)                                        # la coda del live appena spento
    qapp.processEvents()
    assert win._live_paused
    assert win._remote_view.message() == "Live in pausa fino al prossimo scatto."


def test_a_live_that_does_not_come_back_makes_the_full_reentry(pausa_win, qapp):
    win = pausa_win
    win.on_sequence_start(2, 300)
    assert _attendi(qapp, lambda: win._live_waking, 4.0)
    ingressi = len(_chiamate("enter_rec_mode"))
    _FakeLive.istanze[-1].no_signal.emit()                  # nessun fotogramma entro 3 s
    assert _attendi(qapp, lambda: len(_chiamate("enter_rec_mode")) == ingressi + 1)
    assert not win._live_paused and not win._live_waking
    assert not win._remote_view._retry.isVisible()
    assert _attendi(qapp, lambda: win._remote_entered)
    assert _attendi(qapp, lambda: _scatti() == 2, 3.0)


def test_a_failed_resume_makes_the_full_reentry(pausa_win, qapp, monkeypatch):
    win = pausa_win
    conta = {"n": 0}

    def start_liveview(self, port):
        conta["n"] += 1
        _RecordingClient.chiamate.append(("start_liveview", port))
        if conta["n"] == 1:                                 # la riaccensione dopo la pausa
            raise RuntimeError("startliveview non riuscito")
    monkeypatch.setattr(_SequenceStubClient, "start_liveview", start_liveview)
    win.on_sequence_start(2, 300)
    ingressi = len(_chiamate("enter_rec_mode"))
    assert _attendi(qapp, lambda: len(_chiamate("enter_rec_mode")) == ingressi + 1, 5.0)
    assert not win._live_paused


def test_short_intervals_keep_the_live_on(seq_win, qapp):
    win = seq_win                                           # soglie vere: 1 s è sotto i 60
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 2, 3.0)
    assert not _chiamate("stop_liveview") and not win._live_paused


def test_stop_during_the_pause_ends_the_sequence_and_resets_the_pause(pausa_win, qapp):
    win = pausa_win
    win.on_sequence_start(2, 300)
    assert _attendi(qapp, lambda: win._live_paused, 3.0)
    win.on_sequence_stop()
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    assert not win._live_paused and not win._live_waking
    assert win._sequence_result is not None and win._sequence_result.taken == 1


def test_a_live_lost_between_two_shots_reenters_by_itself(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert _attendi(qapp, lambda: not win._shot_in_flight)
    ingressi = len(_chiamate("enter_rec_mode"))
    _FakeLive.istanze[-1].stalled.emit()
    assert _attendi(qapp, lambda: len(_chiamate("enter_rec_mode")) == ingressi + 1)
    assert not win._remote_view._retry.isVisible()
    assert _attendi(qapp, lambda: _scatti() >= 2, 3.0)      # la sequenza prosegue


def test_outside_a_sequence_a_lost_live_still_offers_retry(seq_win, qapp):
    win = seq_win
    _FakeLive.istanze[-1].stalled.emit()
    assert _attendi(qapp, lambda: win._remote_view.message() == "Live interrotto.")
    assert win._remote_view._retry.isVisible()
```

- [ ] **Step 2: Verifica che falliscano**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_main_window.py -k "pauses_after or right_after_the_pause or does_not_come_back or failed_resume or during_the_pause or lost_between"`
Expected: 6 falliti (`AttributeError: _live_paused`, oppure il live non va in pausa). `test_short_intervals_keep_the_live_on` e `test_outside_a_sequence_a_lost_live_still_offers_retry` passano già: restano come guardie.

- [ ] **Step 3: Segnali, stato, collegamenti**

In `olympus_connect/gui/main_window.py`:

Dopo `remote_snapshot_names = pyqtSignal()`:

```python
    remote_pause_live = pyqtSignal()
    remote_resume_live = pyqtSignal(int)
```

Nel costruttore, dopo `self._sequence.ended.connect(self._on_sequence_ended)`:

```python
        self._sequence.wake_requested.connect(self._wake_live)
        # Con intervalli lunghi il live si spegne fra uno scatto e l'altro.
        self._live_paused = False
        self._live_waking = False
```

In `enter_remote`, dopo `self._sequence_result = None`:

```python
        self._live_paused = False
        self._live_waking = False
```

dopo `remote.names_read.connect(self.on_remote_names)`:

```python
        remote.live_resumed.connect(self.on_remote_live_resumed)
```

e dopo `self.remote_snapshot_names.connect(remote.snapshot_names, coda)`:

```python
        self.remote_pause_live.connect(remote.pause_live, coda)
        self.remote_resume_live.connect(remote.resume_live, coda)
```

In `_teardown_remote` aggiungi `self.remote_pause_live, self.remote_resume_live` all'elenco dei segnali scollegati.

In `on_remote_left`, dopo `self._sequence_preparing = False`:

```python
        self._live_paused = False
        self._live_waking = False
```

In `_sequence_ready` aggiungi in fondo alla condizione `and not self._live_paused`.

- [ ] **Step 4: Pausa, sveglia, rientro**

Sostituisci `_after_shot_review` (Task 5) con:

```python
    def _after_shot_review(self) -> None:
        """Fine dell'anteprima di uno scatto (o subito, se l'anteprima non c'è).

        In una sequenza con intervalli lunghi il live si spegne fino a poco
        prima dello scatto successivo: sul campo si riaccende in 0,3 s senza
        rientrare in modalità scatto, e la batteria dura di più.
        """
        if (self._sequence.may_pause_live() and self._remote_active
                and not self._remote_leaving and not self._closing
                and not self._shot_in_flight and self._live_worker is not None):
            self._live_restart_pending = False
            self._live_down = None
            self._live_paused = True        # da subito: nessuno scatto finché il live non torna
            self._live_waking = False
            self._live_worker.disarm()      # il live si ferma di proposito: niente avvisi
            self._remote_view.show_message("Live in pausa fino al prossimo scatto.")
            self.remote_pause_live.emit()
            return
        self._restart_live_after_shot()

    def _wake_live(self) -> None:
        """Il regista avvisa 5 s prima dello scatto: il live in pausa si riaccende."""
        if not self._live_paused or self._live_waking:
            return
        if (not self._remote_active or self._remote_leaving or self._closing
                or self._live_worker is None):
            return
        self._live_waking = True
        self._remote_view.show_message("Il live riparte…")
        self._live_worker.arm()             # riapre il passaggio nel firewall, riarma la sentinella
        self.remote_resume_live.emit(self._live_port)

    @pyqtSlot(bool)
    def on_remote_live_resumed(self, ok: bool) -> None:
        if not ok and self._live_paused:
            self._reenter_after_pause()

    def _reenter_after_pause(self) -> None:
        """La riaccensione semplice non è bastata: rientro completo in modalità scatto."""
        self._live_paused = False
        self._live_waking = False
        self.on_live_retry()
```

In `on_live_frame`, dopo il controllo `if self._live_worker is None: return`:

```python
        if self._live_paused:
            if not self._live_waking:
                self._live_worker.take_frame()      # la coda del live appena spento
                return
            self._live_paused = False               # il live è tornato: si può scattare
            self._live_waking = False
```

Sostituisci `_live_lost`:

```python
    def _live_lost(self, kind: str) -> None:
        """Il worker del live avvisa una volta sola per ogni interruzione:
        durante uno scatto l'avviso si ricorda e si mostra alla fine."""
        if self._live_paused:
            if self._live_waking:
                self._reenter_after_pause()         # riacceso, ma non è arrivato niente
            return
        self._live_down = kind
        if self._shot_in_flight:
            return
        if self._sequence.is_running():
            self.on_live_retry()                    # nessuno è lì a premere Riprova
            return
        self._remote_view.show_message(self._LIVE_DOWN[kind], retry=True)
```

- [ ] **Step 5: Verifica**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_main_window.py`
Expected: tutti verdi, 8 test nuovi.

Run due volte: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: `742 passed` entrambe le volte.

- [ ] **Step 6: Commit**

```bash
git add olympus_connect/gui/main_window.py tests/test_main_window.py
git commit -m "feat(telecomando): live in pausa fra gli scatti di una sequenza lenta

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Fine sequenza: finestra, scarico nella cartella dedicata, video

**Files:**
- Modify: `olympus_connect/gui/main_window.py`
- Test: `tests/test_main_window.py` (in coda)

**Interfaces:**
- Consumes: `SequenceEndDialog`, `EndChoice`, `SequenceJob` (Task 2); `TimelapseWorker` e `MediaListModel.camera_paths()` (Task 4); `sequence_files`, `folder_name` da `core/sequence.py`; `plan_flat` da `core/downloader.py`; `find_ffmpeg`, `RESOLUTIONS`, `VIDEO_NAME`, `DEFAULT_RESOLUTION` da `core/timelapse.py`; `MainWindow._sequence_result`, `_sequence_before` (Task 5).
- Produces:

```python
self._sequence_known: set[str]                 # percorsi noti prima di entrare nel telecomando
self._sequence_dialog: SequenceEndDialog | None
self._sequence_job: SequenceJob | None         # scarico di una sequenza in corso
self._timelapse_worker: TimelapseWorker | None
def _start_download(self, jobs: list, mark_model: bool = True) -> None
```

Flusso: l'uscita dal telecomando rilegge la scheda (`on_refresh` → `on_listed`); in coda a `on_listed`, se c'è un risultato di sequenza in attesa, la finestra calcola i JPEG della sequenza e apre `SequenceEndDialog` (non bloccante: `open()`). «Scarica» → `plan_flat` nella cartella `<destinazione>/Sequenza AAAA-MM-GG HH.MM` → lo scarico esistente; a scarico finito, se chiesto, `TimelapseWorker`. Se l'elenco fallisce, il risultato resta in attesa e la finestra compare al prossimo elenco riuscito.

- [ ] **Step 1: Scrivi i test**

In coda a `tests/test_main_window.py`:

```python
# --- sequenze: fine, scarico, video --------------------------------------------------------

from olympus_connect.core.downloader import Outcome  # noqa: E402
from olympus_connect.gui.sequence import SequenceEndDialog  # noqa: E402


def _scarico_finto(client, job, on_progress=None, should_cancel=None, **kw):
    """Al posto del trasferimento vero: scrive un JPEG valido della dimensione dichiarata."""
    job.dest.parent.mkdir(parents=True, exist_ok=True)
    job.dest.write_bytes(_jpeg_valido().ljust(job.media.size, b"\0")[:max(job.media.size, 1)])
    if on_progress:
        on_progress(job.media.size, job.media.size)
    return Outcome.DONE


class _FintoMontaggio(QObject):
    """Al posto di TimelapseWorker: registra gli argomenti e aspetta il test."""
    progress = pyqtSignal(int, int)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    istanze: list = []

    def __init__(self, files, out, fps, size, ffmpeg=None):
        super().__init__()
        self.files, self.out, self.fps, self.size = list(files), out, fps, size
        self.annullato = False
        _FintoMontaggio.istanze.append(self)

    def run(self):
        pass

    def cancel(self):
        self.annullato = True


@pytest.fixture
def fine_win(seq_win, monkeypatch):
    _FintoMontaggio.istanze = []
    monkeypatch.setattr("olympus_connect.gui.workers.download", _scarico_finto)
    monkeypatch.setattr("olympus_connect.gui.main_window.TimelapseWorker", _FintoMontaggio)
    monkeypatch.setattr("olympus_connect.gui.main_window.find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    return seq_win


def _sequenza_fino_alla_finestra(win, qapp, scatti=2):
    win.on_sequence_start(1, scatti)                       # uno scatto al secondo, `scatti` in tutto
    assert _attendi(qapp, lambda: win._sequence_dialog is not None, 10.0), \
        "la finestra di fine sequenza non è comparsa"
    return win._sequence_dialog


def _cartelle_sequenza(tmp_path):
    return sorted(p for p in (tmp_path / "dest").glob("Sequenza *") if p.is_dir())


def test_after_the_sequence_the_dialog_offers_its_jpegs(fine_win, qapp):
    win = fine_win
    dialog = _sequenza_fino_alla_finestra(win, qapp)
    assert isinstance(dialog, SequenceEndDialog)
    assert dialog.message().startswith("Sequenza finita: 2 foto.")
    assert "dall'ingresso nel telecomando" not in dialog.message()
    assert not win._remote_active and win._sequence_result is None


def test_not_now_leaves_the_photos_on_the_card(fine_win, qapp, tmp_path):
    win = fine_win
    dialog = _sequenza_fino_alla_finestra(win, qapp)
    dialog._later.click()
    assert _attendi(qapp, lambda: win._sequence_dialog is None)
    assert win.statusBar().currentMessage() == "Le foto della sequenza restano sulla scheda."
    assert _cartelle_sequenza(tmp_path) == [] and win._sequence_job is None


def test_download_puts_only_the_jpegs_in_a_dedicated_folder(fine_win, qapp, tmp_path):
    win = fine_win
    dialog = _sequenza_fino_alla_finestra(win, qapp)
    dialog._video.setChecked(False)
    dialog._download.click()
    assert _attendi(qapp, lambda: win.statusBar().currentMessage().startswith("Sequenza scaricata: 2 foto"), 5.0)
    [cartella] = _cartelle_sequenza(tmp_path)
    assert sorted(p.name for p in cartella.iterdir()) == ["P0000002.JPG", "P0000003.JPG"]
    assert win._sequence_job is None and _FintoMontaggio.istanze == []
    assert not (tmp_path / "dest" / "2026").exists()       # niente sottocartelle per data


def test_download_then_video_with_the_chosen_options(fine_win, qapp, tmp_path, settings):
    win = fine_win
    dialog = _sequenza_fino_alla_finestra(win, qapp)
    dialog._resolution.setCurrentText("4K")
    dialog._fps.setCurrentIndex(dialog._fps.findData(30))
    dialog._download.click()
    assert _attendi(qapp, lambda: len(_FintoMontaggio.istanze) == 1, 5.0), "il montaggio non parte"
    montaggio = _FintoMontaggio.istanze[0]
    [cartella] = _cartelle_sequenza(tmp_path)
    assert montaggio.files == [cartella / "P0000002.JPG", cartella / "P0000003.JPG"]
    assert montaggio.out == cartella / "timelapse.mp4"
    assert (montaggio.fps, montaggio.size) == (30, (3840, 2160))
    assert int(settings.value("sequence/fps")) == 30 and settings.value("sequence/resolution") == "4K"
    montaggio.progress.emit(1, 2)
    assert _attendi(qapp, lambda: win._file_label.text() == "Montaggio del video: 1 di 2")
    assert not win._cancel_button.isHidden()
    montaggio.done.emit(str(montaggio.out))
    assert _attendi(qapp, lambda: win.statusBar().currentMessage() == f"Video pronto: {montaggio.out}")
    assert win._timelapse_worker is None and win._cancel_button.isHidden()


def test_cancel_during_the_video_stops_ffmpeg_and_keeps_the_photos(fine_win, qapp, tmp_path):
    win = fine_win
    dialog = _sequenza_fino_alla_finestra(win, qapp)
    dialog._download.click()
    assert _attendi(qapp, lambda: len(_FintoMontaggio.istanze) == 1, 5.0)
    montaggio = _FintoMontaggio.istanze[0]
    win._cancel_button.click()
    assert montaggio.annullato
    montaggio.cancelled.emit()
    assert _attendi(qapp, lambda: win.statusBar().currentMessage() == (
        "Montaggio annullato: le foto restano nella cartella."))
    [cartella] = _cartelle_sequenza(tmp_path)
    assert len(list(cartella.glob("*.JPG"))) == 2 and win._timelapse_worker is None


def test_a_failed_video_says_why(fine_win, qapp):
    win = fine_win
    dialog = _sequenza_fino_alla_finestra(win, qapp)
    dialog._download.click()
    assert _attendi(qapp, lambda: len(_FintoMontaggio.istanze) == 1, 5.0)
    _FintoMontaggio.istanze[0].failed.emit("Il montaggio del video non è riuscito: codec rotto")
    assert _attendi(qapp, lambda: win.statusBar().currentMessage() == (
        "Il montaggio del video non è riuscito: codec rotto"))
    assert win._timelapse_worker is None


def test_a_manual_shot_before_the_start_is_not_part_of_the_sequence(fine_win, qapp, tmp_path):
    win = fine_win
    win._remote_panel._shoot.click()
    assert _attendi(qapp, lambda: _scatti() == 1 and not win._shot_in_flight)
    dialog = _sequenza_fino_alla_finestra(win, qapp)
    assert dialog.message().startswith("Sequenza finita: 2 foto.")
    dialog._video.setChecked(False)
    dialog._download.click()
    assert _attendi(qapp, lambda: len(_cartelle_sequenza(tmp_path)) == 1, 5.0)
    assert _attendi(qapp, lambda: win._sequence_job is None, 5.0)
    [cartella] = _cartelle_sequenza(tmp_path)
    assert sorted(p.name for p in cartella.iterdir()) == ["P0000003.JPG", "P0000004.JPG"]


def test_without_the_snapshot_every_photo_since_entering_is_offered(fine_win, qapp):
    win = fine_win
    # come se l'elenco fosse stato letto prima di entrare nel telecomando
    win._sequence_known = {m.camera_path for m in _SequenceStubClient.file}
    _SequenceStubClient.istantanea_rotta = True
    win._remote_panel._shoot.click()
    assert _attendi(qapp, lambda: _scatti() == 1 and not win._shot_in_flight)
    dialog = _sequenza_fino_alla_finestra(win, qapp)
    assert dialog.message().startswith("Sequenza finita: 3 foto.")
    assert "foto nuove dall'ingresso nel telecomando" in dialog.message()


def test_a_raw_only_camera_gets_the_explanation(fine_win, qapp, monkeypatch):
    win = fine_win

    def solo_raw(self, read_timeout=None):
        _RecordingClient.chiamate.append(("take_picture",))
        n = len(_SequenceStubClient.file) + 1
        _SequenceStubClient.file = _SequenceStubClient.file + [
            _media_seq(f"R{n:07d}.ORF", MediaKind.RAW)]
    monkeypatch.setattr(_SequenceStubClient, "take_picture", solo_raw)
    dialog = _sequenza_fino_alla_finestra(win, qapp)
    assert "La fotocamera ha salvato solo RAW" in dialog.message()
    dialog._close.click()
    assert _attendi(qapp, lambda: win._sequence_dialog is None)


def test_a_failed_listing_keeps_the_sequence_for_the_next_one(fine_win, qapp):
    win = fine_win
    _SequenceStubClient.elenco_rotto = 1
    win.on_sequence_start(1, 2)
    assert _attendi(qapp, lambda: not win._remote_active and not win._listing, 10.0)
    assert win._sequence_dialog is None and win._sequence_result is not None
    win.on_refresh()                                        # come il pulsante Aggiorna
    assert _attendi(qapp, lambda: win._sequence_dialog is not None, 5.0)
    assert win._sequence_dialog.message().startswith("Sequenza finita: 2 foto.")


def test_an_ordinary_download_forgets_a_pending_sequence_job(fine_win, qapp, tmp_path):
    from olympus_connect.gui.sequence import SequenceJob
    win = fine_win
    win._sequence_job = SequenceJob(tmp_path / "x", [], True, "Full HD", 25)
    win._start_download([])
    assert _attendi(qapp, lambda: not win._downloading)
    assert win._sequence_job is None and _FintoMontaggio.istanze == []


def test_the_grid_download_still_marks_files_as_present(fine_win, qapp, tmp_path):
    win = fine_win
    win.exit_remote("griglia")
    assert _attendi(qapp, lambda: not win._remote_active and win._model.rowCount() == 2, 5.0)
    lavori = win._model._all
    win._start_download(list(lavori))
    assert _attendi(qapp, lambda: not win._downloading, 5.0)
    assert (tmp_path / "dest" / "2026" / "2026-11-11" / "P0000001.JPG").exists()
```

- [ ] **Step 2: Verifica che falliscano**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_main_window.py -k "dialog_offers or not_now or dedicated_folder or then_video or cancel_during_the_video or failed_video or manual_shot_before or without_the_snapshot or raw_only_camera or failed_listing_keeps or forgets_a_pending or still_marks"`
Expected: 12 falliti o in errore (`AttributeError` su `TimelapseWorker`/`find_ffmpeg` nel modulo della finestra, `_sequence_dialog`, `_start_download`).

- [ ] **Step 3: Import e stato**

In `olympus_connect/gui/main_window.py`:

```python
from ..core.downloader import JobState, plan_flat
from ..core.sequence import (
    DEFAULT_DURATION, DEFAULT_INTERVAL, SNAPSHOT_SETTLE, SequencePlan, SequenceResult,
    folder_name, sequence_files,
)
from ..core.timelapse import (
    DEFAULT_FRAME_RATE, DEFAULT_RESOLUTION, FRAME_RATES, RESOLUTIONS, VIDEO_NAME, find_ffmpeg,
)
from .sequence import SequenceController, SequenceEndDialog, SequenceJob
from .workers import (
    DownloadWorker, ListWorker, PreviewWorker, RemoteWorker, ThumbWorker, TimelapseWorker,
    describe_error,
)
```

(sostituiscono le righe corrispondenti già presenti). Nel costruttore, dopo `self._live_waking = False`:

```python
        self._sequence_known: set[str] = set()     # file noti prima di entrare nel telecomando
        self._sequence_dialog: SequenceEndDialog | None = None
        self._sequence_offer: tuple | None = None  # (foto, risultato) mentre la finestra è aperta
        self._sequence_job: SequenceJob | None = None
        self._timelapse_worker: TimelapseWorker | None = None
```

In `enter_remote`, dopo `self._remote_props = {}`:

```python
        self._sequence_known = self._model.camera_paths()
```

- [ ] **Step 4: Lo scarico come metodo riusabile**

Sostituisci `on_download_selected` con:

```python
    def on_download_selected(self) -> None:
        jobs = self.selected_jobs()
        if not jobs:
            self.statusBar().showMessage("Nessun file selezionato.", 4000)
            return
        self._start_download(jobs)

    def _start_download(self, jobs: list, mark_model: bool = True) -> None:
        """Avvia un trasferimento. `mark_model=False` per lo scarico di una
        sequenza: va in un'altra cartella, e i file dell'elenco non diventano
        «già scaricati» per questo."""
        if mark_model:
            self._sequence_job = None       # uno scarico ordinario: nessun video da montare dopo
        # Miniature e anteprima sospese: il server della fotocamera è un
        # microcontrollore e due trasferimenti in volo lo degradano.
        self._suspend_thumbnails(True)
        worker = DownloadWorker(self._client_factory(), jobs)
        worker.file_started.connect(self._file_label.setText)
        worker.file_progress.connect(self._update_file_bar)
        worker.total_progress.connect(self._update_total_bar)
        worker.stats_changed.connect(self.on_stats)
        if mark_model:
            worker.file_completed.connect(self.on_file_completed)
        worker.file_failed.connect(
            lambda name, msg: self.statusBar().showMessage(f"{name}: {msg}"))
        worker.paused.connect(self.on_paused)
        worker.finished.connect(self.on_download_finished)

        self._download_worker = worker
        self._show_transfer(True)
        self._downloading = True
        self._update_remote_button()
        self._start_on_thread(worker, worker.run)
```

- [ ] **Step 5: La finestra di fine sequenza**

In `on_listed`, in fondo al metodo (dopo il blocco `if self.current_view() == VIEW_GRID:`):

```python
        self._offer_sequence(jobs)
```

Aggiungi i metodi dopo `_on_sequence_ended`:

```python
    def _offer_sequence(self, jobs: list) -> None:
        """Dopo l'elenco che segue una sequenza: quali foto ha fatto, e cosa farne."""
        if self._sequence_result is None or self._sequence_dialog is not None:
            return
        risultato, self._sequence_result = self._sequence_result, None
        file = [j.media for j in jobs]
        riserva = self._sequence_before is None
        prima = self._sequence_known if riserva else self._sequence_before
        self._sequence_before = None
        nuovi = [m for m in file if m.camera_path not in prima]
        foto = sequence_files(prima, file)
        risoluzione = str(self._settings.value("sequence/resolution", DEFAULT_RESOLUTION))
        dialog = SequenceEndDialog(
            photos=len(foto), skipped=risultato.skipped, aborted=risultato.aborted,
            only_raw=not foto and any(m.kind is MediaKind.RAW for m in nuovi),
            since_remote_entry=riserva, ffmpeg=find_ffmpeg() is not None,
            resolution=risoluzione, fps=self._sequence_fps(), parent=self)
        self._sequence_dialog = dialog
        self._sequence_offer = (foto, risultato)
        dialog.finished.connect(self._on_sequence_dialog)
        dialog.open()                       # non blocca: i worker continuano a parlare

    def _on_sequence_dialog(self, _codice: int) -> None:
        dialog, self._sequence_dialog = self._sequence_dialog, None
        offerta, self._sequence_offer = self._sequence_offer, None
        if dialog is None or offerta is None:
            return
        foto, risultato = offerta
        scelta = dialog.choice()
        dialog.deleteLater()
        if not scelta.download or not foto:
            self.statusBar().showMessage("Le foto della sequenza restano sulla scheda.")
            return
        self._settings.setValue("sequence/fps", scelta.fps)
        self._settings.setValue("sequence/resolution", scelta.resolution)
        self._update_sequence_summary()
        radice = Path(self.dest_root())
        cartella = radice / folder_name(risultato.started_at, lambda n: (radice / n).exists())
        lavori = plan_flat(foto, cartella)      # stesso ordine delle foto: quello di scatto
        self._sequence_job = SequenceJob(cartella, [lavoro.dest for lavoro in lavori],
                                         scelta.video, scelta.resolution, scelta.fps)
        self._start_download(lavori, mark_model=False)
```

- [ ] **Step 6: Dopo lo scarico, il video**

In `on_download_finished`, sostituisci l'ultima riga `self.on_refresh()` con:

```python
        lavoro, self._sequence_job = self._sequence_job, None
        if lavoro is not None:
            # Un'altra cartella: l'elenco non cambia, e il suo messaggio di stato
            # coprirebbe quello della sequenza.
            self._after_sequence_download(lavoro)
            return
        self.on_refresh()
```

Aggiungi i metodi dopo `_on_sequence_dialog`:

```python
    def _after_sequence_download(self, lavoro: SequenceJob) -> None:
        presenti = [p for p in lavoro.frames if p.exists()]
        if not lavoro.video:
            self.statusBar().showMessage(
                f"Sequenza scaricata: {len(presenti)} foto in {lavoro.folder}")
            return
        if len(presenti) < 2:
            self.statusBar().showMessage(
                "Sequenza scaricata, ma per il video servono almeno due foto.")
            return
        worker = TimelapseWorker(presenti, lavoro.folder / VIDEO_NAME, lavoro.fps,
                                 RESOLUTIONS[lavoro.resolution])
        worker.progress.connect(self.on_timelapse_progress)
        worker.done.connect(self.on_timelapse_done)
        worker.failed.connect(self.on_timelapse_failed)
        worker.cancelled.connect(self.on_timelapse_cancelled)
        self._timelapse_worker = worker
        self._show_transfer(True)
        self._file_label.setText("Montaggio del video…")
        self._rate_label.setText("")
        self._file_bar.setValue(0)
        self._total_bar.setValue(0)
        self.statusBar().showMessage("Sequenza scaricata: monto il video…")
        self._start_on_thread(worker, worker.run)

    @pyqtSlot(int, int)
    def on_timelapse_progress(self, scritti: int, totale: int) -> None:
        self._file_label.setText(f"Montaggio del video: {scritti} di {totale}")
        self._set_progress(self._total_bar, scritti, totale)

    def _timelapse_over(self, messaggio: str) -> None:
        self._timelapse_worker = None
        self._file_label.setText("")
        self._total_bar.setValue(0)
        self._show_transfer(False)
        self.statusBar().showMessage(messaggio)

    @pyqtSlot(str)
    def on_timelapse_done(self, percorso: str) -> None:
        self._timelapse_over(f"Video pronto: {percorso}")

    @pyqtSlot(str)
    def on_timelapse_failed(self, messaggio: str) -> None:
        self._timelapse_over(messaggio)

    @pyqtSlot()
    def on_timelapse_cancelled(self) -> None:
        self._timelapse_over("Montaggio annullato: le foto restano nella cartella.")
```

In `on_cancel`, in testa al metodo:

```python
        if self._timelapse_worker is not None:
            self._timelapse_worker.cancel()         # il resto lo fa on_timelapse_cancelled
            self._cancel_button.setEnabled(False)
            return
```

In `closeEvent`, dopo `self._download_worker.cancel()` (stesso livello dell'`if` che lo contiene):

```python
        if self._timelapse_worker is not None:
            self._timelapse_worker.cancel()         # thread-safe: termina ffmpeg
        if self._sequence_dialog is not None:
            self._sequence_dialog.finished.disconnect(self._on_sequence_dialog)
            self._sequence_dialog.close()
            self._sequence_dialog = None
```

e aggiungi `self._timelapse_worker` alla tupla dei worker zittiti in fondo a `closeEvent`.

- [ ] **Step 7: Verifica**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_main_window.py`
Expected: tutti verdi, 12 test nuovi.

Run due volte: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: `754 passed` entrambe le volte.

- [ ] **Step 8: Commit**

```bash
git add olympus_connect/gui/main_window.py tests/test_main_window.py
git commit -m "feat(telecomando): a fine sequenza scarico dei JPEG in una cartella dedicata e video

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Dall'inizio alla fine, e la documentazione

**Files:**
- Modify: `tests/test_remote_e2e.py` (la fotocamera finta e un test nuovo), `README.md`, `DESIGN.md`

**Interfaces:**
- Consumes: tutto quello che i Task 1–7 hanno prodotto, attraverso `MainWindow` con il client vero (`OlympusClient(base_url=…)`), HTTP vero verso il server finto, UDP vero per il live.
- Produces: il test `test_a_sequence_ends_in_a_folder_with_its_jpegs_and_a_video`.

- [ ] **Step 1: Estendi la fotocamera finta**

In `tests/test_remote_e2e.py`, nella fixture `camera`:

1. Lo stato iniziale guadagna tre chiavi:

```python
    stato = {"richieste": [], "invio": None, "modo": "play", "valori": {},
             "scatti": 0, "nuovi": [], "elenchi": []}
```

   (`nuovi` è l'elenco `(nome, contenuto)` dei file nati dagli scatti; `elenchi` registra in che modalità era la fotocamera a ogni lettura della cartella.)

2. Dopo `revisione = _jpeg(320, 240)`:

```python
    foto = _jpeg(640, 480)                      # 4:3, come la fotocamera
    grezzo = b"ORF finto " * 50
```

3. In `do_GET`, sostituisci il ramo `get_imglist.cgi` con:

```python
            if nome == "get_imglist.cgi":
                if qs["DIR"] == "/DCIM":
                    return self._send((FIXTURES / "imglist_dcim.csv").read_bytes(), "text/plain")
                stato["elenchi"].append(stato["modo"])
                righe = (FIXTURES / "imglist_dcim_100olymp.csv").read_bytes()
                for nome_file, contenuto in stato["nuovi"]:
                    righe += f"/DCIM/100OLYMP,{nome_file},{len(contenuto)},0,23626,28090\r\n".encode()
                return self._send(righe, "text/plain")
            if nome.startswith("DCIM/"):
                for nome_file, contenuto in stato["nuovi"]:
                    if nome == f"DCIM/100OLYMP/{nome_file}":
                        return self._send(contenuto, "application/octet-stream")
```

   (prima di aggiungere le righe controlla che il file della fixture finisca con un a capo: se non finisce così, aggiungi `b"\r\n"` a `righe` prima del ciclo).

4. Nel ramo `exec_takemotion.cgi`, lo `starttake` diventa:

```python
                if qs.get("com") == "starttake":
                    stato["scatti"] += 1
                    n = stato["scatti"]
                    stato["nuovi"] += [(f"PB11{n:04d}.JPG", foto), (f"PB11{n:04d}.ORF", grezzo)]
                    return self._send(b'<?xml version="1.0"?><response><take>ok</take></response>')
```

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_remote_e2e.py`
Expected: `2 passed`, come prima. Lo scatto del primo test ora aggiunge due file all'elenco finto: se la sua asserzione sul numero di righe dopo l'uscita (`== 1161`) dovesse fallire per questo, portala a `1163` e annotalo nel rapporto (è l'effetto voluto di una fotocamera finta più fedele).

- [ ] **Step 2: Scrivi il test**

In coda a `tests/test_remote_e2e.py` (aggiungi in testa `from olympus_connect.core.timelapse import find_ffmpeg`):

```python
class _Blocco:
    """Al posto di SleepInhibitor: un test non deve bloccare la sospensione del PC."""

    def start(self, seconds):
        return True

    def stop(self):
        pass


def test_a_sequence_ends_in_a_folder_with_its_jpegs_and_a_video(qapp, camera, tmp_path, monkeypatch):
    if find_ffmpeg() is None:
        pytest.skip("ffmpeg non installato")
    monkeypatch.setattr(MainWindow, "SNAPSHOT_SETTLE", 0.0)
    impostazioni = QSettings(str(tmp_path / "e2e.ini"), QSettings.Format.IniFormat)
    impostazioni.setValue("dest_root", str(tmp_path / "dest"))
    finestra = MainWindow(
        client_factory=lambda: OlympusClient(base_url=camera["base_url"]),
        network_factory=_Net,
        settings=impostazioni,
        live_factory=lambda: LiveViewWorker(camera=camera["udp"], bind_host="127.0.0.1"),
        inhibitor_factory=_Blocco,
    )
    try:
        finestra.resize(1366, 740)
        finestra.show()
        finestra.on_refresh()
        assert _attendi(qapp, lambda: finestra._model.rowCount() == 1161), "elenco non arrivato"
        assert _attendi(qapp, lambda: finestra._view_remote.isEnabled())
        finestra._view_remote.click()
        assert _attendi(qapp, finestra._remote_view.has_frame), "il live non è arrivato"
        assert _attendi(qapp, lambda: finestra._remote_panel.props.row("isospeedvalue") is not None)

        finestra.on_sequence_start(1, 3)                 # tre scatti: a 0, 1 e 2 secondi
        assert _attendi(qapp, lambda: finestra._sequence_dialog is not None, 30.0), \
            "la finestra di fine sequenza non è comparsa"
        assert camera["scatti"] == 3
        assert "rec" in camera["elenchi"], "l'istantanea non è stata presa in modalità scatto"
        assert camera["modo"] == "play", "a fine sequenza la fotocamera non è tornata in riproduzione"
        dialog = finestra._sequence_dialog
        assert dialog.message().startswith("Sequenza finita: 3 foto.")

        dialog._download.click()
        cartelle = lambda: [p for p in (tmp_path / "dest").glob("Sequenza *") if p.is_dir()]  # noqa: E731
        assert _attendi(qapp, lambda: len(cartelle()) == 1, 20.0), "la cartella non è stata creata"
        video = cartelle()[0] / "timelapse.mp4"
        assert _attendi(qapp, video.exists, 60.0), "il video non è stato montato"
        assert _attendi(qapp, lambda: finestra.statusBar().currentMessage().startswith("Video pronto"))
        nomi = sorted(p.name for p in cartelle()[0].iterdir())
        assert nomi == ["PB110001.JPG", "PB110002.JPG", "PB110003.JPG", "timelapse.mp4"]
        assert video.stat().st_size > 1000
        assert not list((tmp_path / "dest").rglob("*.ORF")), "i RAW dovevano restare sulla scheda"
    finally:
        finestra.close()
        qapp.processEvents()
```

- [ ] **Step 3: Verifica, anche al contrario**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_remote_e2e.py`
Expected: `3 passed` (con `ffmpeg`).

Verifica al contrario: in `olympus_connect/gui/main_window.py` commenta la riga `self._sequence.shoot_requested.connect(self._sequence_shoot)`, rilancia il test nuovo e controlla che **fallisca** («la finestra di fine sequenza non è comparsa» oppure `camera["scatti"] == 3`). Poi `git checkout olympus_connect/gui/main_window.py`, controlla con `git diff --stat olympus_connect/` che non resti nulla, e rilancia: `3 passed`. Riporta nel rapporto l'uscita delle due esecuzioni. Lo stato rotto non si committa mai.

- [ ] **Step 4: README**

In `README.md`:
- nella tabella «Cosa funziona, verificato sul campo» una riga nuova dopo quella delle impostazioni dal PC: `| Sequenze di scatti a intervalli (intervallometro), scarico dei JPEG in una cartella dedicata e video timelapse | da verificare sul campo: i comandi sono misurati (ciclo di scatto 1 s, live che si spegne e si riaccende in 0,3 s), la funzione nell'app non ancora |`;
- nella sezione Telecomando, dopo il paragrafo sulle impostazioni, un paragrafo: sotto le impostazioni la sezione **Sequenza**; si scelgono intervallo (da 2 secondi a 30 minuti) e durata (da 5 minuti a 12 ore) e si preme **Avvia sequenza**; durante la sequenza gli altri comandi sono spenti e **Ferma sequenza** la interrompe dopo lo scatto in corso; con intervalli da un minuto in su il live si spegne fra uno scatto e l'altro per risparmiare batteria; il PC non va in sospensione; alla fine il programma esce dal telecomando e chiede se scaricare i JPEG della sequenza in una cartella `Sequenza AAAA-MM-GG HH.MM` dentro la destinazione e se montarli in un video `timelapse.mp4` (Full HD o 4K; 24, 25 o 30 fotogrammi al secondo); i RAW restano sulla scheda;
- fra i requisiti, `ffmpeg` come facoltativo: serve solo per il video delle sequenze (`sudo pacman -S ffmpeg`);
- nell'albero dei file: `core/sequence.py`, `core/timelapse.py`, `core/power.py`, `gui/sequence.py`, `tools/prova_sequenza.py`, ognuno con una riga di descrizione nello stile delle altre;
- il numero dei test nella frase «N test, nessuno dei quali richiede la fotocamera collegata»: quello stampato dall'ultima esecuzione completa.

- [ ] **Step 5: DESIGN**

In `DESIGN.md`, nella voce **Remote mode**, dopo la frase sulla sezione Settings aggiungi: «A **Sequence** section below it: two steppers of the same kind (interval, duration) without the pending state, a faint summary line ("360 scatti · video di 14 s a 25 fps"), and a full-width button, **Avvia sequenza** (clock glyph) that becomes **Ferma sequenza** (red stop glyph); while a sequence runs the steppers and the summary give way to two lines, progress in `{colors.text}` and time left faint, and every other control of the panel is disabled. With intervals of a minute or more the live view is replaced between shots by the message "Live in pausa fino al prossimo scatto." on the sunken background.» E, fra le finestre di dialogo (o in coda alla stessa voce se non c'è un elenco di dialoghi): «**End of sequence** dialog: one message, a "Monta il video" checkbox with resolution and frame-rate menus that follow it, primary **Scarica** and plain **Non ora**; with nothing to download, only **Chiudi**.»

- [ ] **Step 6: Verifica finale e commit**

Run due volte: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: `755 passed` entrambe le volte (754 più il test dall'inizio alla fine).

Controllo dei seriali, senza stamparli:

```bash
n=0; while IFS= read -r s; do n=$((n+1)); [ -z "$s" ] && continue; echo "voce $n: $( (git diff; git diff --cached) | grep -c -F -- "$s")"; done < diag-downloads/seriali.txt
```

Expected: `0` per ogni voce.

```bash
git add tests/test_remote_e2e.py README.md DESIGN.md
git commit -m "test: una sequenza dall'inizio alla fine (scatti, scarico, video); docs

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: Prova finale con la fotocamera vera (con l'utente)

**Files:**
- Modify: `docs/field-report-2026-09-24.md` (in coda), `README.md` (la riga della tabella)

Non è codice. **Prima di questa prova** il controllore fa fare la revisione finale di tutto il ramo `feat/sequenze` (un revisore solo, sull'intero diff rispetto a `main`) e corregge quello che trova: l'utente prova il codice già rivisto. Poi guida l'utente; **mentre il PC è collegato alla fotocamera non c'è Internet**: l'utente esegue, torna in linea, riferisce. Ogni prova scrive il registro in `diag-downloads/` (ignorata da git).

- [ ] **Step 1: L'utente prova**

Fotocamera appoggiata, batteria carica, ghiera su A. Con la fotocamera in Wi-Fi:

```bash
nmcli connection up olympus-connect-E-M10MKIV-P-<seriale>
OLYMPUS_DEBUG=1 .venv/bin/python -m olympus_connect 2>&1 | tee diag-downloads/prova_app_sequenze.txt
nmcli connection down olympus-connect-E-M10MKIV-P-<seriale>
```

Le sei prove della specifica («Prova finale con la fotocamera»), in ordine; fra una e l'altra si resta nell'app:
1. **Timelapse di giorno:** intervallo 2 s, durata 5 min. Il conteggio avanza, nessuno scatto saltato; alla fine la finestra, **Scarica** con video Full HD a 25: la cartella `Sequenza …` ha 150 JPEG circa e `timelapse.mp4`, che si apre e dura 6 secondi.
2. **Pose lunghe:** modalità M dal pannello, tempo 15", intervallo 5 s, durata 5 min. Il riepilogo dice «la posa dura più dell'intervallo»; gli scatti partono uno dopo l'altro; dopo ogni scatto il live riparte; alla fine **Non ora**.
3. **Intervallo lungo:** modalità M dal pannello (ghiera su A), tempo 1/250, intervallo 2 min, durata 15 min. Fra gli scatti la vista dice «Live in pausa fino al prossimo scatto.», poi «Il live riparte…» pochi secondi prima di ogni scatto; il pannello mostra ancora M dopo ogni riaccensione; otto scatti, nessuno saltato.
4. **Attesa lunga:** intervallo 15 min, durata 30 min (due scatti). Il secondo scatto parte: la fotocamera risponde ancora dopo 15 minuti a live spento. Il PC non è andato in sospensione.
5. **Arresti:** una sequenza fermata a metà con **Ferma sequenza**; una lasciata a metà tornando alle Miniature (deve chiedere conferma); in entrambi i casi compare la finestra di fine sequenza.
6. **Scatto a mano prima di Avvia:** uno scatto con **Scatta**, subito dopo **Avvia sequenza** (intervallo 2 s, 5 min, fermata dopo qualche scatto): nella finestra il numero di foto è quello della sequenza, senza la foto a mano.

Da guardare in particolare (dalla revisione finale):
- prova 2 e 3: dopo **ogni** scatto la fotocamera è ancora in M (pannello; poi l'EXIF dei JPEG scaricati, nel pannello Dettagli della griglia);
- prova 2: quante volte il rientro nel live dopo una posa non riesce (barra di stato, o «Riprova» a schermo): ora c'è un secondo tentativo automatico;
- prova 4: se dopo 15 minuti a live spento risponde ancora `startliveview`, o se scatta lo spegnimento automatico della fotocamera (che non si comanda dal PC);
- una chiusura del programma durante una posa lunga: deve chiedere conferma; poi guardare la fotocamera (resta in modalità scatto? la sessione dopo si collega normalmente?);
- l'uscita dal telecomando durante una posa lunga: l'attesa (fino a circa 2 minuti), poi nella finestra finale c'è anche l'ultima foto.

- [ ] **Step 2: Leggi il registro e annota l'esito**

Run: `grep -n -i "error\|errore\|traceback\|1004" diag-downloads/prova_app_sequenze.txt | head -40`

In coda a `docs/field-report-2026-09-24.md` una sezione «Sequenze con l'app (AAAA-MM-GG)» con la data vera e l'esito di ognuna delle sei prove (riferito dall'utente e letto dal registro), più le due verifiche rimaste dal cancello: la modalità scelta dal PC dopo la riaccensione del live (prova 3) e la risposta dopo 15 minuti (prova 4). Se una prova fallisce: fermati, annota cosa è successo, correggi con un test che prima fallisce e fai ripetere quella prova. Se la prova 4 fallisce vale la riga «non risponde» della tabella del cancello nella specifica (lettura leggera ogni 30 s fra gli scatti), da realizzare come correzione.

- [ ] **Step 3: README e commit**

Con tutte le prove riuscite, la riga della tabella del README diventa: `| Sequenze di scatti a intervalli (intervallometro), scarico dei JPEG in una cartella dedicata e video timelapse | ✅ da 2 s a 15 min fra gli scatti; live in pausa negli intervalli lunghi |`.

```bash
n=0; while IFS= read -r s; do n=$((n+1)); [ -z "$s" ] && continue; echo "voce $n: $( (git diff; git diff --cached) | grep -c -F -- "$s")"; done < diag-downloads/seriali.txt
git add docs/field-report-2026-09-24.md README.md
git commit -m "docs: sequenze verificate sul campo

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Dopo questo piano

La scelta dell'utente: unire `feat/sequenze` a `main` e pubblicare, oppure no. Resta da fare il pezzo E2, la posa B, con una specifica sua.
