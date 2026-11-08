"""Il modo Telecomando: la vista del live e il pannello dei comandi.

La vista segue la dimensione reale di ogni fotogramma, non un 4:3 fisso:
durante una registrazione la fotocamera manda 640×360. Il clic diventa un
punto in coordinate del fotogramma, limitato all'area in cui la fotocamera
accetta la messa a fuoco col tocco.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QElapsedTimer, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
    pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from . import icons, theme
from .panels import SectionLabel
from .props import PropsSection

AF_BOX = 56          # lato del riquadro di messa a fuoco a schermo, in pixel


def fit_rect(container: tuple[int, int], frame: tuple[int, int]) -> tuple[float, float, float, float]:
    """Dove sta il fotogramma dentro il contenitore, con le bande: x, y, l, a."""
    cw, ch = container
    fw, fh = frame
    if cw <= 0 or ch <= 0 or fw <= 0 or fh <= 0:
        return 0.0, 0.0, 0.0, 0.0
    scala = min(cw / fw, ch / fh)
    w, h = fw * scala, fh * scala
    return (cw - w) / 2, (ch - h) / 2, w, h


def map_to_frame(pos: tuple[float, float], container: tuple[int, int],
                 frame: tuple[int, int],
                 area: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
    """Un punto del contenitore in coordinate del fotogramma; None sulle bande."""
    x0, y0, w, h = fit_rect(container, frame)
    px, py = pos
    if w <= 0 or not (x0 <= px < x0 + w and y0 <= py < y0 + h):
        return None
    fx = (px - x0) * frame[0] / w
    fy = (py - y0) * frame[1] / h
    lo_x, hi_x, lo_y, hi_y = 0, frame[0] - 1, 0, frame[1] - 1
    if area is not None:
        ax, ay, aw, ah = area
        nlx, nhx = max(lo_x, ax), min(hi_x, ax + aw - 1)
        nly, nhy = max(lo_y, ay), min(hi_y, ay + ah - 1)
        if nlx <= nhx and nly <= nhy:     # area vuota nel fotogramma: la si ignora
            lo_x, hi_x, lo_y, hi_y = nlx, nhx, nly, nhy
    return (min(max(int(fx), lo_x), hi_x), min(max(int(fy), lo_y), hi_y))


class RemoteView(QWidget):
    focus_requested = pyqtSignal(int, int)
    retry_requested = pyqtSignal()
    review_ended = pyqtSignal()

    REVIEW_MS = 2000

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._frame: QImage | None = None
        self._review: QPixmap | None = None
        self._review_opacity = 1.0
        self._message = ""
        self._retry_wanted = False
        self._area: tuple[int, int, int, int] | None = None
        self._focus_enabled = True
        self._af: tuple[int, int] | None = None
        self._af_result: bool | None = None
        self._af_pending = False

        self._retry = QPushButton(" Riprova", self)
        self._retry.setObjectName("ghost")
        self._retry.setIcon(icons.icon("retry", theme.TEXT, theme.LINE_STRONG, 18))
        self._retry.setIconSize(QSize(18, 18))
        self._retry.hide()
        self._retry.clicked.connect(self.retry_requested)

        self._review_timer = QTimer(self)
        self._review_timer.setSingleShot(True)
        self._review_timer.setInterval(self.REVIEW_MS)
        self._review_timer.timeout.connect(self._end_review)

        self._fade = QPropertyAnimation(self, b"reviewOpacity", self)
        self._fade.setDuration(theme.DURATION)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    # -- proprietà animata ----------------------------------------------------

    def _get_review_opacity(self) -> float:
        return self._review_opacity

    def _set_review_opacity(self, value: float) -> None:
        self._review_opacity = value
        self.update()

    reviewOpacity = pyqtProperty(float, fget=_get_review_opacity, fset=_set_review_opacity)

    # -- contenuto ------------------------------------------------------------

    def set_frame(self, image: QImage) -> None:
        self._frame = image
        if self._message:
            self._message = ""
            self._retry.hide()
        self.update()

    def has_frame(self) -> bool:
        return self._frame is not None

    def frame_size(self) -> tuple[int, int] | None:
        return None if self._frame is None else (self._frame.width(), self._frame.height())

    def set_touch_area(self, area: tuple[int, int, int, int] | None) -> None:
        self._area = area

    def set_focus_enabled(self, enabled: bool) -> None:
        self._focus_enabled = enabled
        if not enabled:
            self._af = None
            self.update()

    def show_af(self, x: int, y: int) -> None:
        self._af = (x, y)
        self._af_pending = True
        self._af_result = None
        self.update()

    def af_pending(self) -> bool:
        return self._af_pending

    def set_af_result(self, result: bool | None) -> None:
        self._af_pending = False
        self._af_result = result
        self.update()

    def show_review(self, data: bytes) -> bool:
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            return False
        self._review = pixmap
        self._retry.hide()                 # l'anteprima sta sopra a tutto, anche al Riprova
        self._fade.stop()
        self._fade.setStartValue(0.35)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._review_timer.start()
        self.update()
        return True

    def is_reviewing(self) -> bool:
        return self._review is not None

    def _end_review(self) -> None:
        self._review = None
        if self._message and self._retry_wanted:
            self._retry.show()
        self.update()
        self.review_ended.emit()

    def show_message(self, text: str, retry: bool = False) -> None:
        self._message = text
        self._retry_wanted = retry
        # durante l'anteprima messaggio e Riprova aspettano che finisca
        self._retry.setVisible(retry and self._review is None)
        self._place_retry()
        self.update()

    def message(self) -> str:
        return self._message

    def clear(self) -> None:
        self._frame = None
        self._review = None
        self._review_timer.stop()
        self._message = ""
        self._retry.hide()
        self._af = None
        self._area = None
        self._focus_enabled = True
        self.update()

    # -- interazione ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if (event.button() != Qt.MouseButton.LeftButton or self._frame is None
                or not self._focus_enabled or self._review is not None):
            super().mousePressEvent(event)
            return
        punto = map_to_frame((event.position().x(), event.position().y()),
                             (self.width(), self.height()),
                             (self._frame.width(), self._frame.height()), self._area)
        if punto is not None:
            self.focus_requested.emit(*punto)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_retry()

    def _place_retry(self) -> None:
        self._retry.adjustSize()
        self._retry.move((self.width() - self._retry.width()) // 2,
                         self.height() // 2 + 18)

    # -- disegno --------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.SUNKEN))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._frame is not None:
            x, y, w, h = fit_rect((self.width(), self.height()),
                                  (self._frame.width(), self._frame.height()))
            painter.drawImage(QRectF(x, y, w, h), self._frame)
            if self._af is not None:
                self._draw_af(painter, x, y, w / self._frame.width())

        if self._review is not None:
            x, y, w, h = fit_rect((self.width(), self.height()),
                                  (self._review.width(), self._review.height()))
            painter.setOpacity(self._review_opacity)
            painter.fillRect(self.rect(), QColor(theme.SUNKEN))
            painter.drawPixmap(QRectF(x, y, w, h).toRect(), self._review)
            painter.setOpacity(1.0)

        if self._message and self._review is None:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 140))
            painter.setPen(QColor(theme.TEXT))
            font = painter.font()
            font.setPixelSize(theme.SIZE_BODY)
            painter.setFont(font)
            testo = self.rect().adjusted(24, 0, -24, -40)
            painter.drawText(testo, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             self._message)
        painter.end()

    def _draw_af(self, painter: QPainter, x0: float, y0: float, scala: float) -> None:
        fx, fy = self._af
        cx, cy = x0 + fx * scala, y0 + fy * scala
        if self._af_pending:
            colore = theme.ACCENT
        elif self._af_result is True:
            colore = theme.OK
        elif self._af_result is False:
            colore = theme.ERROR
        else:
            colore = theme.TEXT
        penna = QPen(QColor(colore))
        penna.setWidthF(2.0)
        painter.setPen(penna)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(cx - AF_BOX / 2, cy - AF_BOX / 2, AF_BOX, AF_BOX))


def _value(text: str) -> QLabel:
    etichetta = QLabel(text)
    etichetta.setObjectName("value")
    return etichetta


class RemotePanel(QWidget):
    shoot_requested = pyqtSignal()
    movie_toggle_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        colonna = QVBoxLayout(self)
        colonna.setContentsMargins(0, 0, 0, 0)
        colonna.setSpacing(8)

        colonna.addWidget(SectionLabel("Scatto"))
        self._shoot = QPushButton(" Scatta")
        self._shoot.setObjectName("primary")
        self._shoot.setIcon(icons.icon("shutter", theme.ACCENT_INK, theme.TEXT_FAINT, 18))
        self._shoot.setIconSize(QSize(18, 18))
        self._shoot.setToolTip("Scatta (barra spaziatrice)")
        self._shoot.clicked.connect(self.shoot_requested)
        colonna.addWidget(self._shoot)

        self._movie = QPushButton()
        self._movie.setIconSize(QSize(18, 18))
        self._movie.setToolTip("Registra o ferma un video (tasto R)")
        self._movie.clicked.connect(self.movie_toggle_requested)
        colonna.addWidget(self._movie)

        self._elapsed_label = _value("00:00")
        self._elapsed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        colonna.addWidget(self._elapsed_label)

        colonna.addSpacing(8)
        self.props = PropsSection()
        colonna.addWidget(self.props)
        self._take_mode_text = "—"

        suggerimento = QLabel("Clic sull'immagine per mettere a fuoco.")
        suggerimento.setObjectName("faint")
        suggerimento.setWordWrap(True)
        colonna.addWidget(suggerimento)
        colonna.addStretch(1)

        self._recording = False
        self._busy = False
        self._clock = QElapsedTimer()
        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._tick)
        self._refresh()

    def set_take_mode(self, mode: str) -> None:
        self._take_mode_text = mode or "—"

    def take_mode_text(self) -> str:
        return self._take_mode_text

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        if recording:
            self._clock.start()
            self._ticker.start()
        else:
            self._ticker.stop()
        self._refresh()

    def is_recording(self) -> bool:
        return self._recording

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh()

    def elapsed_text(self) -> str:
        if not self._recording:
            return "00:00"
        secondi = self._clock.elapsed() // 1000
        return f"{secondi // 60:02d}:{secondi % 60:02d}"

    def _tick(self) -> None:
        self._elapsed_label.setText(self.elapsed_text())

    def _refresh(self) -> None:
        if self._recording:
            self._movie.setText(" Ferma")
            self._movie.setIcon(icons.icon("stop", theme.ERROR, theme.LINE_STRONG, 18))
        else:
            self._movie.setText(" Registra")
            self._movie.setIcon(icons.icon("record", theme.ERROR, theme.LINE_STRONG, 18))
        self._elapsed_label.setVisible(self._recording)
        self._elapsed_label.setText(self.elapsed_text())
        self._shoot.setEnabled(not self._busy and not self._recording)
        self._movie.setEnabled(not self._busy)
        self.props.set_enabled(not self._busy and not self._recording)
