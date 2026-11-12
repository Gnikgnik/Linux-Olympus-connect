"""Impostazioni della fotocamera nel pannello del telecomando.

Un selettore per riga, come una ghiera: frecce, rotella del mouse, elenco
al clic. La fotocamera vuole un comando alla volta: mentre si gira, il
valore a schermo cambia subito (attenuato) e il comando parte 250 ms dopo
l'ultimo passo, con il solo valore finale. Il valore torna normale quando
la rilettura dell'elenco lo conferma.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from ..core.camprops import (
    CameraProp, aperture_allowed, format_value, is_automatic, is_hidden, label, step,
    visible_props,
)
from . import icons, theme
from .panels import SectionLabel
from .shot import label_column_width


class PropStepper(QWidget):
    changed = pyqtSignal(str, str)

    DEBOUNCE_MS = 250

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name
        self._prop: CameraProp | None = None
        self._confirmed = ""
        self._shown = ""
        self._automatic = False
        self._allowed: Callable[[str], bool] | None = None

        riga = QHBoxLayout(self)
        riga.setContentsMargins(0, 0, 0, 0)
        riga.setSpacing(2)
        self._label = QLabel(label(name))
        self._label.setObjectName("faint")
        self._label.setFixedWidth(label_column_width())
        riga.addWidget(self._label)

        self._prev = self._arrow("chevron-left", -1)
        self._value = QPushButton("—")
        self._value.setObjectName("propValue")
        self._value.clicked.connect(self._open_menu)
        self._next = self._arrow("chevron-right", +1)
        for freccia in (self._prev, self._next):
            # nascoste sulle righe automatiche, ma il posto resta: i valori stanno in colonna
            criterio = freccia.sizePolicy()
            criterio.setRetainSizeWhenHidden(True)
            freccia.setSizePolicy(criterio)
        riga.addWidget(self._prev)
        riga.addWidget(self._value, 1)
        riga.addWidget(self._next)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEBOUNCE_MS)
        self._timer.timeout.connect(self._send)
        self._refresh()

    def _arrow(self, icona: str, delta: int) -> QPushButton:
        pulsante = QPushButton()
        pulsante.setObjectName("propArrow")
        pulsante.setIcon(icons.icon(icona, theme.TEXT_DIM, theme.LINE_STRONG, 14))
        pulsante.setIconSize(QSize(14, 14))
        pulsante.clicked.connect(lambda: self.step(delta))
        return pulsante

    # -- stato dalla fotocamera -------------------------------------------------

    def set_prop(self, prop: CameraProp, automatic: bool) -> None:
        self._prop = prop
        self._automatic = automatic
        self._confirmed = prop.value
        if automatic:
            self._timer.stop()             # la fotocamera ha preso il controllo
        self._drop_invalid_pending()
        if not self._timer.isActive():
            self._shown = prop.value
        self._refresh()

    def set_allowed(self, allowed: Callable[[str], bool] | None) -> None:
        self._allowed = allowed
        if self._drop_invalid_pending():
            self._shown = self._confirmed
        self._refresh()

    def _drop_invalid_pending(self) -> bool:
        """Un valore in attesa che il nuovo elenco (o i nuovi limiti) non
        ammette più non deve partire: per esempio un tempo scelto in M che
        in S non esiste."""
        if self._timer.isActive() and not self._selectable(self._shown):
            self._timer.stop()
            return True
        return False

    def discard(self) -> None:
        """La riga esce dal pannello: niente più comandi, nemmeno in attesa."""
        self._timer.stop()
        try:
            self.changed.disconnect()
        except TypeError:
            pass

    # -- interazione --------------------------------------------------------------

    def step(self, delta: int) -> None:
        if self._prop is None or self._automatic or not self.isEnabled():
            return
        nuovo = step(self._prop.values, self._shown, delta, self._selectable)
        if nuovo != self._shown:
            self._shown = nuovo
            self._refresh()
            self._timer.start()

    def choose(self, value: str) -> None:
        if (self._prop is None or self._automatic or not self.isEnabled()
                or not self._selectable(value)):
            return
        self._shown = value
        self._refresh()
        self._timer.start()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            # spenta (registrazione, cambio di modalità): il comando in attesa
            # cade subito, e non riparte se la riga si riaccende prima della pausa
            if self._timer.isActive():
                self._timer.stop()
                self._shown = self._confirmed
                self._refresh()

    def wheelEvent(self, event) -> None:
        if self._name == "takemode":
            # scorrendo il pannello si cambierebbe modalità per sbaglio: solo frecce ed elenco
            event.ignore()
            return
        y = event.angleDelta().y()
        if y:
            self.step(1 if y > 0 else -1)
        event.accept()

    def _open_menu(self) -> None:
        if self._prop is None or self._automatic:
            return
        menu = QMenu(self)
        for valore in self.menu_values():
            azione = menu.addAction(format_value(self._name, valore))
            azione.setCheckable(True)
            azione.setChecked(valore == self._shown)
            azione.triggered.connect(lambda _c=False, v=valore: self.choose(v))
        menu.exec(self._value.mapToGlobal(self._value.rect().bottomLeft()))
        menu.deleteLater()                  # un menu nuovo a ogni clic: questo non serve più

    def _send(self) -> None:
        if self._automatic or not self.isEnabled():
            self._shown = self._confirmed  # niente comando: la riga non è più modificabile
            self._refresh()
            return
        if self._shown != self._confirmed:
            self.changed.emit(self._name, self._shown)

    # -- lettura (test e finestra) --------------------------------------------------

    def _selectable(self, value: str) -> bool:
        """Si può scegliere: è nell'elenco, non è nascosto, è ammesso."""
        return (self._prop is not None and value in self._prop.values
                and not is_hidden(self._name, value)
                and (self._allowed is None or self._allowed(value)))

    def menu_values(self) -> list[str]:
        if self._prop is None:
            return []
        return [v for v in self._prop.values if self._selectable(v)]

    def shown_value(self) -> str:
        return self._shown

    def confirmed_value(self) -> str:
        return self._confirmed

    def is_pending(self) -> bool:
        return self._shown != self._confirmed

    def is_automatic(self) -> bool:
        return self._automatic

    def _refresh(self) -> None:
        testo = format_value(self._name, self._shown) if self._shown else "—"
        if self._automatic:
            testo = f"{testo} · automatico"
        self._value.setText(testo)
        self._value.setProperty("pending", self.is_pending())
        self._value.style().unpolish(self._value)
        self._value.style().polish(self._value)
        self._value.setEnabled(not self._automatic)
        for freccia in (self._prev, self._next):
            freccia.setVisible(not self._automatic)


class PropsSection(QWidget):
    prop_changed = pyqtSignal(str, str)

    def __init__(self) -> None:
        super().__init__()
        colonna = QVBoxLayout(self)
        colonna.setContentsMargins(0, 0, 0, 0)
        colonna.setSpacing(2)
        colonna.addWidget(SectionLabel("Impostazioni"))
        self._rows_box = QVBoxLayout()
        self._rows_box.setContentsMargins(0, 0, 0, 0)
        self._rows_box.setSpacing(2)
        colonna.addLayout(self._rows_box)
        self._message = QLabel("")
        self._message.setObjectName("faint")
        self._message.setWordWrap(True)
        self._message.hide()
        colonna.addWidget(self._message)
        self._rows: dict[str, PropStepper] = {}
        self._order: list[str] = []
        self._limits: tuple[float, float] | None = None
        self._enabled = True
        self._mode_in_flight = False

    def set_props(self, props: dict[str, CameraProp]) -> None:
        self._message.hide()
        self._mode_in_flight = False       # la rilettura dopo il cambio di modalità è arrivata
        nomi = visible_props(props)
        for nome in [n for n in self._rows if n not in nomi]:
            self._remove_row(self._rows.pop(nome))
        for nome in nomi:
            if nome not in self._rows:
                riga = PropStepper(nome)
                riga.changed.connect(self._forward)
                self._rows[nome] = riga
            self._rows_box.removeWidget(self._rows[nome])
        for nome in nomi:
            riga = self._rows[nome]
            self._rows_box.addWidget(riga)
            riga.set_prop(props[nome], is_automatic(nome, props))
        self._order = nomi
        self._apply_enabled()
        self._apply_lens_limits()

    def set_unavailable(self, message: str) -> None:
        self._drop_rows()
        self._message.setText(message)
        self._message.show()

    def clear(self) -> None:
        """Nuova sessione del telecomando: niente righe né limiti di quella prima."""
        self._drop_rows()
        self._limits = None
        self._message.setText("")
        self._message.hide()

    def _drop_rows(self) -> None:
        for riga in self._rows.values():
            self._remove_row(riga)
        self._rows.clear()
        self._order = []
        self._mode_in_flight = False

    def _remove_row(self, riga: PropStepper) -> None:
        riga.discard()
        self._rows_box.removeWidget(riga)
        riga.deleteLater()

    def change(self, name: str, value: str) -> None:
        """Una modifica chiesta dal programma e non dall'utente (per esempio
        rimettere la modalità dopo un rientro nel live): segue la stessa via."""
        self._forward(name, value)

    def _forward(self, name: str, value: str) -> None:
        """Dopo un cambio di modalità le altre righe mostrano i valori della
        modalità vecchia (la fotocamera li tiene per modalità): restano
        spente, e i loro comandi in attesa cadono, fino alla rilettura."""
        if name == "takemode":
            self._mode_in_flight = True
            self._apply_enabled()
        self.prop_changed.emit(name, value)

    def set_lens_limits(self, limits: tuple[float, float] | None) -> None:
        self._limits = limits
        self._apply_lens_limits()

    def _apply_lens_limits(self) -> None:
        """La scala del diaframma (1.0 … 91) non dipende dall'obiettivo e la
        fotocamera accetta anche i valori fuori obiettivo: finché il live non
        ha dichiarato i limiti, la riga resta sul solo valore confermato."""
        riga = self._rows.get("focalvalue")
        if riga is None:
            return
        if self._limits is None:
            riga.set_allowed(lambda v, r=riga: v == r.confirmed_value())
        else:
            riga.set_allowed(aperture_allowed(self._limits))

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._apply_enabled()

    def _apply_enabled(self) -> None:
        for nome, riga in self._rows.items():
            riga.setEnabled(self._enabled and (not self._mode_in_flight or nome == "takemode"))

    def row(self, name: str) -> PropStepper | None:
        return self._rows.get(name)

    def names(self) -> list[str]:
        return list(self._order)

    def message(self) -> str:
        return self._message.text() if not self._message.isHidden() else ""
