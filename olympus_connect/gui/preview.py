"""Anteprima grande del file selezionato.

La mossa caratteristica di questa interfaccia: la fotocamera genera con
get_screennail un JPEG da circa 1 MB anche per i RAW, quindi si può
giudicare uno scatto prima di scaricarlo. Una miniatura da 9 KB non lo
consente, ed è la differenza fra scegliere e tirare a indovinare.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve, QPropertyAnimation, QSize, Qt, pyqtProperty,
)
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QLabel, QSizePolicy

from . import theme


class PreviewView(QLabel):
    """Mostra l'anteprima, con dissolvenza all'arrivo.

    L'immagine arriva dalla rete con latenza variabile: comparire di colpo
    a metà di uno scorrimento è un lampo che distrae. La dissolvenza non
    decora, segnala che qualcosa è arrivato.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(160)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            f"background: {theme.SUNKEN};"
            f"border: 1px solid {theme.LINE};"
            f"border-radius: {theme.RADIUS}px;"
            f"color: {theme.TEXT_FAINT}; font-size: {theme.SIZE_SMALL}px;"
        )
        self._pixmap: QPixmap | None = None
        self._opacity = 1.0
        self._placeholder = "Nessun file selezionato"
        self.setText(self._placeholder)

        self._fade = QPropertyAnimation(self, b"fadeOpacity")
        self._fade.setDuration(theme.DURATION)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_fade_opacity(self) -> float:
        return self._opacity

    def set_fade_opacity(self, value: float) -> None:
        self._opacity = value
        self.update()

    fadeOpacity = pyqtProperty(float, fget=get_fade_opacity,
                               fset=set_fade_opacity)

    # -- contenuto ------------------------------------------------------

    def show_message(self, text: str) -> None:
        self._pixmap = None
        self._opacity = 1.0
        self.setFixedHeight(200)
        self.setText(text)
        self.update()

    def show_image(self, data: bytes) -> bool:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return False
        self._pixmap = pixmap
        self.setText("")
        self._apply_aspect()
        # Si parte da un'immagine gia' visibile, non da zero: una
        # dissolvenza che parte dall'invisibile rende l'elemento assente
        # ogni volta che qualcosa affama il ciclo degli eventi, e il
        # difetto si manifesta come «la miniatura non arriva».
        self._fade.stop()
        self._fade.setStartValue(0.35)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self.update()
        return True

    def has_image(self) -> bool:
        return self._pixmap is not None

    def _apply_aspect(self) -> None:
        """La cornice si stringe sull'immagine.

        Una foto 4:3 in una colonna larga 324 px e' alta 243: dare alla
        cornice tutta l'altezza del pannello non la ingrandisce, lascia solo
        due bande vuote dentro il bordo. La cornice segue l'immagine, e lo
        spazio che avanza sta sotto il blocco invece che dentro di esso.
        """
        if self._pixmap is None or self._pixmap.width() == 0:
            self.setFixedHeight(200)
            return
        proporzione = self._pixmap.height() / self._pixmap.width()
        larghezza = max(self.width(), 1)
        self.setFixedHeight(max(160, int(larghezza * proporzione) + 2))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_aspect()

    # -- disegno --------------------------------------------------------

    def paintEvent(self, event) -> None:
        if self._pixmap is None:
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(self._opacity)

        area = self.rect().adjusted(1, 1, -1, -1)
        scaled = self._pixmap.scaled(
            area.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = area.x() + (area.width() - scaled.width()) // 2
        y = area.y() + (area.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
