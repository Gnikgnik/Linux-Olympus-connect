"""La sezione «Scatto» del pannello: tempo, diaframma, ISO e il resto.

Sta sotto «Dettagli», non sopra: i dettagli si vedono subito, i dati di
scatto arrivano dalla fotocamera una frazione di secondo dopo, e aggiunti
in fondo non spostano nulla. Le righe dei campi assenti non compaiono: su
uno schermo alto 768 px un trattino costa una riga.
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect, QGridLayout, QLabel, QVBoxLayout, QWidget,
)

from ..core.exif import ShotInfo, format_shot
from . import theme
from .panels import SectionLabel

UNAVAILABLE = "Dati di scatto non disponibili"


def _label(text: str, role: str) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(role)
    return widget


class ShotSection(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(SectionLabel("Scatto"))

        self._headline = _label("", "shotHeadline")
        layout.addWidget(self._headline)

        self._unavailable = _label(UNAVAILABLE, "faint")
        layout.addWidget(self._unavailable)

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(4)
        self._grid.setColumnStretch(1, 1)
        layout.addLayout(self._grid)
        self._rows: list[tuple[str, str]] = []

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setEnabled(False)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(theme.DURATION)
        # Da un valore già visibile: una dissolvenza da 0 si legge come un
        # lampo, non come un arrivo (vedi preview.py).
        self._fade.setStartValue(0.35)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(lambda: self._effect.setEnabled(False))

        self.hide()

    # -- per la finestra -----------------------------------------------------

    def show_shot(self, info: ShotInfo | None) -> None:
        self._clear_rows()
        if info is None:
            self._headline.hide()
            self._unavailable.show()
        else:
            shot = format_shot(info)
            self._headline.setText(shot.headline)
            self._headline.setVisible(bool(shot.headline))
            self._unavailable.hide()
            for riga, (etichetta, valore) in enumerate(shot.rows):
                self._grid.addWidget(_label(etichetta, "faint"), riga, 0,
                                     Qt.AlignmentFlag.AlignTop)
                testo = _label(valore, "value")
                testo.setWordWrap(True)
                self._grid.addWidget(testo, riga, 1)
            self._rows = list(shot.rows)
        self.show()
        self._effect.setEnabled(True)
        self._fade.stop()
        self._fade.start()

    def clear(self) -> None:
        self._fade.stop()
        self._effect.setEnabled(False)
        self._clear_rows()
        self.hide()

    # -- per i test ------------------------------------------------------------

    def headline_text(self) -> str:
        return self._headline.text() if not self._headline.isHidden() else ""

    def rows(self) -> list[tuple[str, str]]:
        return list(self._rows)

    def is_unavailable(self) -> bool:
        return not self._unavailable.isHidden()

    # ---------------------------------------------------------------------------

    def _clear_rows(self) -> None:
        while self._grid.count():
            widget = self._grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []
