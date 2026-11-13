"""Sequenze di scatti nel pannello del telecomando.

La sezione «Sequenza» (intervallo, durata, riepilogo, Avvia/Ferma), la
finestra che a fine sequenza chiede se scaricare le foto e montare il
video, e il regista che fa partire gli scatti. Le regole stanno in
core/sequence.py: qui c'è solo ciò che serve a mostrarle e a farle girare.
"""
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
        menu.deleteLater()                  # un menu nuovo a ogni clic: questo non serve più

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

    def set_summary(self, text: str, warning: bool = False) -> None:
        """`warning`: il riepilogo avvisa di qualcosa (la posa più lunga
        dell'intervallo) e si vede come un avviso, non come una nota."""
        self._summary.setText(text)
        self._summary.setObjectName("warn" if warning else "faint")
        self._summary.style().unpolish(self._summary)
        self._summary.style().polish(self._summary)

    def summary_is_warning(self) -> bool:
        return self._summary.objectName() == "warn"

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
                 fps: int = DEFAULT_FRAME_RATE, unknown: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sequenza")
        if unknown:
            photos = 0                      # senza un elenco di partenza non si sa quali sono
        self._download: QPushButton | None = None
        self._later: QPushButton | None = None
        self._close: QPushButton | None = None

        saltate = f" ({skipped} saltate)" if skipped else ""
        if aborted:
            righe = ["Sequenza interrotta: la fotocamera non risponde."]
        else:
            righe = ["Sequenza finita."]
        if unknown:
            righe.append("Non riesco a distinguere le foto della sequenza da quelle che "
                         "c'erano già sulla scheda: scaricale dalla griglia.")
        else:
            righe[0] = (f"Sequenza interrotta: la fotocamera non risponde. {photos} foto{saltate}."
                        if aborted else f"Sequenza finita: {photos} foto{saltate}.")
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
            self._fps.addItem(str(valore), valore)
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
            griglia.addWidget(QLabel("Fotogrammi al secondo"), 3, 0)
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
        if self._state is not None:
            self._close()                   # una sequenza ancora in corso si chiude, senza segnale
        self._state = SequenceState(plan, busy)
        self._started_at = self._wall()
        # All'avvio il live è acceso: il primo scatto non va svegliato.
        self._woken = True
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
                if self._state is not stato:
                    return                  # fermata da chi ha ricevuto la sveglia
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
