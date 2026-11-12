"""Pannelli laterali richiudibili.

Su uno schermo da 768 px di altezza i pannelli fissi di Lightroom lasciano
al contenuto una fascia inutilizzabile. Qui si ritirano a una colonna di
34 px, e l'animazione serve a far capire dove sono andati: senza, il
pannello sparirebbe e basta.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve, QPropertyAnimation, QRect, QSize, Qt, pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from . import icons, theme


class SectionLabel(QLabel):
    """Intestazione di gruppo dentro un pannello."""

    def __init__(self, text: str) -> None:
        super().__init__(text.upper())
        self.setObjectName("sectionLabel")


class Separator(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("separator")
        self.setFixedHeight(1)


class VerticalLabel(QWidget):
    """Il nome del pannello, ruotato, nella colonna richiusa.

    Una colonna di 36 px con una sola icona non dice che cosa contiene:
    chi l'ha richiusa deve poter ritrovare ciò che ha nascosto senza
    aprirla per tentativi.
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text
        self.setFixedWidth(22)
        self.setSizePolicy(QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Expanding)

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def sizeHint(self) -> QSize:
        # Senza un sizeHint proprio il widget vale zero in altezza e non
        # disegna nulla: un widget personalizzato deve dichiarare quanto
        # spazio vuole, non aspettarselo.
        return QSize(22, 160)

    def minimumSizeHint(self) -> QSize:
        return QSize(22, 80)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(QColor(theme.TEXT_FAINT))
        font = painter.font()
        font.setPointSize(8)
        font.setLetterSpacing(font.SpacingType.AbsoluteSpacing, 1.2)
        painter.setFont(font)

        # Si ruota il sistema di riferimento e si disegna dentro un
        # rettangolo largo quanto l'altezza del widget e alto quanto la sua
        # larghezza: cosi' il testo non puo' finire fuori dai bordi, che e'
        # quello che succede traslando a occhio.
        painter.translate(0, self.height())
        painter.rotate(-90)
        painter.drawText(
            QRect(6, 0, self.height() - 12, self.width()),
            # AlignRight nello spazio ruotato = in alto nella colonna, di
            # fianco al pulsante a cui l'etichetta si riferisce. AlignLeft
            # la spediva in fondo, a 600 px dal suo comando.
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            self._text.upper(),
        )
        painter.end()


class CollapsiblePanel(QFrame):
    """Pannello laterale con larghezza animata.

    La larghezza è una proprietà Qt vera invece di un setFixedWidth ripetuto
    in un timer, così l'animazione la interpola sola e resta fluida anche
    mentre la griglia ridisegna.
    """

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, side: str, width: int,
                 duration: int = theme.DURATION) -> None:
        super().__init__()
        self._side = side
        self._expanded_width = width
        self._expanded = True

        self.setObjectName("rail" if side == "left" else "inspector")
        self.setFixedWidth(width)

        # L'intestazione sta in un widget di altezza fissa, non in un layout
        # annidato: un layout senza altezza propria si espande quando il
        # corpo si nasconde, e il pulsante finisce centrato a meta' finestra
        # invece di restare in alto.
        self._header_widget = QWidget()
        self._header_widget.setObjectName(
            "rail" if side == "left" else "inspector")
        self._header_widget.setFixedHeight(42)
        self._header = QHBoxLayout(self._header_widget)
        self._header.setContentsMargins(12, 10, 8, 8)
        self._header.setSpacing(6)

        self._title = QLabel(title)
        self._title.setObjectName("panelTitle")

        self._button = QPushButton()
        self._button.setObjectName("collapser")
        self._button.setFixedSize(26, 26)
        # Senza iconSize Qt riscala il pixmap al suo predefinito di 16 px
        # con un filtro grossolano, e l'icona arriva sfigurata.
        self._button.setIconSize(QSize(18, 18))
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.clicked.connect(self.toggle)

        if side == "left":
            self._header.addWidget(self._title)
            self._header.addStretch()
            self._header.addWidget(self._button)
        else:
            self._header.addWidget(self._button)
            self._header.addWidget(self._title)
            self._header.addStretch()

        self.body = QWidget()
        self.body.setObjectName(self.objectName())
        self._body_layout = QVBoxLayout(self.body)
        self._body_layout.setContentsMargins(12, 4, 12, 12)
        self._body_layout.setSpacing(10)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Qt ignora del tutto i widget nascosti nei layout: a corpo
        # nascosto il layout non ha piu' nulla di elastico e distribuisce
        # lo spazio attorno all'intestazione, centrando il pulsante a meta'
        # finestra. Questo spaziatore prende il posto del corpo e tiene il
        # pulsante ancorato in alto.
        self._filler = QWidget()
        self._filler.setObjectName(self.objectName())
        self._filler.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
        self._filler.setVisible(False)
        riempimento = QVBoxLayout(self._filler)
        riempimento.setContentsMargins(0, 2, 0, 0)
        self._vertical_title = VerticalLabel(title)
        # Nessun flag di allineamento: con uno, Qt assegna al widget il suo
        # sizeHint invece di lasciarlo espandere, e l'etichetta si accorcia.
        riempimento.addWidget(self._vertical_title, 1)

        outer.addWidget(self._header_widget)
        outer.addWidget(self.body, 1)
        outer.addWidget(self._filler, 1)

        self._animation = QPropertyAnimation(self, b"panelWidth", self)   # muore col pannello
        self._animation.setDuration(duration)
        # Ease-out: il movimento parte deciso e si posa, invece di accelerare
        # alla fine come farebbe una curva lineare o in-out.
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._refresh_button()

    # -- proprietà animabile ------------------------------------------

    def get_panel_width(self) -> int:
        return self.width()

    def set_panel_width(self, value: int) -> None:
        self.setFixedWidth(int(value))

    panelWidth = pyqtProperty(int, fget=get_panel_width, fset=set_panel_width)

    # -- stato ---------------------------------------------------------

    def is_expanded(self) -> bool:
        return self._expanded

    def layout_body(self) -> QVBoxLayout:
        return self._body_layout

    def set_title(self, text: str) -> None:
        """Il titolo cambia con il contenuto: «Anteprima» o «Telecomando»."""
        self._title.setText(text)
        self._vertical_title.set_text(text)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        target = self._expanded_width if expanded else theme.COLLAPSED

        self._title.setVisible(expanded)
        self.body.setVisible(expanded)
        self._filler.setVisible(not expanded)
        self._refresh_button()

        self._animation.stop()
        if animate:
            self._animation.setStartValue(self.width())
            self._animation.setEndValue(target)
            self._animation.start()
        else:
            self.setFixedWidth(target)
        self.toggled.emit(expanded)

    def _refresh_button(self) -> None:
        name = "panel-left" if self._side == "left" else "panel-right"
        # Richiuso il pulsante e' l'unico elemento rimasto: senza un fondo
        # e un bordo non si distingue dal pannello e l'utente non ritrova
        # cio' che ha nascosto.
        colore = theme.TEXT if not self._expanded else theme.TEXT_DIM
        self._button.setIcon(icons.icon(name, colore, size=18))
        self._button.setToolTip("Nascondi il pannello" if self._expanded
                                else "Mostra il pannello")
        self._button.setProperty("collapsed", "false" if self._expanded else "true")
        self._button.style().unpolish(self._button)
        self._button.style().polish(self._button)

        margins = (12, 9, 8, 9) if self._expanded else (5, 9, 5, 9)
        self._header.setContentsMargins(*margins)
