"""Icone disegnate, un solo peso di tratto.

Non si usano glifi Unicode né emoji come icone: hanno metriche e peso
diversi dal resto dell'interfaccia e tradiscono subito un insieme
assemblato invece che disegnato. Qui ogni icona è un tracciato su una
griglia 20×20 con tratto 1.6, estremità e giunzioni tonde.
"""
from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

GRID = 20
STROKE = 1.9   # a 16-18 px un tratto piu' sottile sparisce

# Solo tracciati: il colore arriva al momento del disegno, così la stessa
# icona serve stato normale, disattivato e accento.
_PATHS: dict[str, str] = {
    "camera": (
        '<path d="M2.5 6.5h3l1.2-2h6.6l1.2 2h3v10h-15z"/>'
        '<circle cx="10" cy="11" r="3.2"/>'
    ),
    "refresh": (
        '<path d="M16.4 11.2a6.6 6.6 0 1 1-1.3-5.4"/>'
        '<path d="M16.9 3.6v3.4h-3.4"/>'
    ),
    "download": (
        '<path d="M10 3v9"/><path d="M6.2 8.6 10 12.4l3.8-3.8"/>'
        '<path d="M3.5 15.2v1.6h13v-1.6"/>'
    ),
    "folder": (
        '<path d="M2.8 5.6h5l1.4 2h8v9h-14.4z"/>'
    ),
    "grid": (
        '<rect x="3.2" y="3.2" width="5.6" height="5.6" rx="1"/>'
        '<rect x="11.2" y="3.2" width="5.6" height="5.6" rx="1"/>'
        '<rect x="3.2" y="11.2" width="5.6" height="5.6" rx="1"/>'
        '<rect x="11.2" y="11.2" width="5.6" height="5.6" rx="1"/>'
    ),
    "list": (
        '<path d="M3 5h14"/><path d="M3 10h14"/><path d="M3 15h14"/>'
    ),
    "chevron-left": '<path d="M12.2 4.5 6.8 10l5.4 5.5"/>',
    # Un chevron a 18 px con tratto 1.9 perde l'angolo nella giunzione
    # tonda e si legge come un trattino. Il riquadro con il divisorio dice
    # la stessa cosa senza ambiguita'. Niente puntini di contenuto: a
    # questo tratto le estremita' tonde si fondono in una macchia.
    "panel-left": (
        '<rect x="2.8" y="4.2" width="14.4" height="11.6" rx="1.6"/>'
        '<path d="M8 4.2v11.6"/>'
    ),
    "panel-right": (
        '<rect x="2.8" y="4.2" width="14.4" height="11.6" rx="1.6"/>'
        '<path d="M12 4.2v11.6"/>'
    ),
    "chevron-right": '<path d="M7.8 4.5 13.2 10l-5.4 5.5"/>',
    "close": '<path d="M5.2 5.2l9.6 9.6"/><path d="M14.8 5.2l-9.6 9.6"/>',
    "retry": (
        '<path d="M3.5 10a6.5 6.5 0 1 0 1.9-4.6"/>'
        '<path d="M3.2 3.2v3.4h3.4"/>'
    ),
    "check": '<path d="M4.5 10.5 8.3 14.3 15.5 6.2"/>',
    "pending": '<circle cx="10" cy="10" r="6.4"/><path d="M10 6.2V10l2.6 1.8"/>',
    "alert": (
        '<path d="M10 3.4 18 16.6H2z"/>'
        '<path d="M10 8.2v3.6"/><path d="M10 14.1v.1"/>'
    ),
    "raw": (
        '<rect x="3" y="4.4" width="14" height="11.2" rx="1.6"/>'
        '<path d="M3 12.6 7.4 8.8l4 3.6"/>'
        '<circle cx="13" cy="8" r="1.4"/>'
    ),
    "film": (
        '<rect x="2.6" y="4.6" width="14.8" height="10.8" rx="1.4"/>'
        '<path d="M6.4 4.6v10.8"/><path d="M13.6 4.6v10.8"/>'
    ),
    "jpeg": (
        '<rect x="3" y="4.4" width="14" height="11.2" rx="1.6"/>'
        '<path d="M3 13 7.6 9.2l3.2 2.8 2-1.6 4.2 3.2"/>'
    ),
    "wifi": (
        '<path d="M3.2 8.2a10 10 0 0 1 13.6 0"/>'
        '<path d="M6.4 11.8a5.6 5.6 0 0 1 7.2 0"/>'
        '<path d="M10 15.6v.2"/>'
    ),
}


def _svg(name: str, color: str) -> bytes:
    body = _PATHS[name]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}" '
        f'fill="none" stroke="{color}" stroke-width="{STROKE}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    ).encode("utf-8")


@lru_cache(maxsize=256)
def pixmap(name: str, size: int, color: str, dpr: float = 1.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color)))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * dpr, size * dpr))
    painter.end()
    return pm


@lru_cache(maxsize=256)
def icon(name: str, color: str, disabled_color: str | None = None,
         size: int = 20) -> QIcon:
    """QIcon con lo stato disattivato disegnato, non sbiadito da Qt.

    Lasciare che Qt schiarisca l'icona produce un grigio che non appartiene
    alla palette; qui lo stato disattivato è un colore scelto.
    """
    result = QIcon()
    result.addPixmap(pixmap(name, size, color, 2.0), QIcon.Mode.Normal)
    result.addPixmap(pixmap(name, size, color, 2.0), QIcon.Mode.Active)
    result.addPixmap(pixmap(name, size, color, 2.0), QIcon.Mode.Selected)
    if disabled_color:
        result.addPixmap(pixmap(name, size, disabled_color, 2.0),
                         QIcon.Mode.Disabled)
    return result


def available() -> tuple[str, ...]:
    return tuple(sorted(_PATHS))
