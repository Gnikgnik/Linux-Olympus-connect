"""Palette e foglio di stile.

Scuro non per convenzione di categoria ma per la scena d'uso: una sessione
serale di revisione, dove le uniche cose che devono essere luminose sono le
fotografie. Tre livelli di neutro freddo danno profondità senza ombre.
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QPalette

# -- colori ----------------------------------------------------------------

SHELL = "#1b1e21"        # sfondo della finestra
PANEL = "#232629"        # rail e pannelli
SURFACE = "#2b2f33"      # superfici interattive a riposo
RAISED = "#343a3f"       # hover
SUNKEN = "#191c1f"       # area contenuto, piu' scura del resto: le foto ci stanno sopra
LINE = "#33383d"         # separatori
LINE_STRONG = "#3f464c"

TEXT = "#e9ebed"         # testo primario
TEXT_DIM = "#a8b0b6"     # testo secondario
TEXT_FAINT = "#959ea5"   # etichette; usato solo su PANEL e SURFACE, dove supera 4.5:1

ACCENT = "#4db8d4"       # selezione, azione primaria, stato attivo
ACCENT_HOVER = "#67cbe5"
ACCENT_PRESS = "#3ba3bf"
ACCENT_INK = "#0e1b20"   # testo sopra l'accento

OK = "#7cc08a"           # file gia' presente
WARN = "#d8a648"         # da riscaricare
ERROR = "#e4705a"        # fallito

# -- tipografia ------------------------------------------------------------

SIZE_LABEL = 10          # etichette di sezione, maiuscoletto spaziato
SIZE_SMALL = 11
SIZE_BODY = 12
SIZE_STRONG = 13
SIZE_TITLE = 16

# -- metriche --------------------------------------------------------------

RAIL_WIDTH = 232
PANEL_WIDTH = 348      # l'anteprima e' limitata dalla larghezza, non dall'altezza
COLLAPSED = 36        # come il contratto di direzione
RADIUS = 4
DURATION = 180           # ms: dentro i 150-250 di una UI in cui si lavora


def tune_fonts(app) -> None:
    """Cifre tabulari ovunque.

    Senza, le dimensioni e le percentuali ballano a ogni aggiornamento del
    progresso: le colonne numeriche di una tabella devono stare incolonnate.
    """
    font = app.font()
    font.setPointSize(10)
    try:
        font.setFeature("tnum", 1)
    except (AttributeError, TypeError):
        pass     # Qt piu' vecchio: si perde l'allineamento, non la leggibilita'
    app.setFont(font)


def apply_palette(app) -> None:
    """Palette Qt oltre al QSS: i widget nativi, i tooltip e le aree che il
    foglio di stile non raggiunge devono comunque appartenere al tema."""
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(SHELL))
    p.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Base, QColor(SUNKEN))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL))
    p.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(ACCENT_INK))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(RAISED))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_FAINT))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
               QColor(TEXT_FAINT))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
               QColor(TEXT_FAINT))
    app.setPalette(p)


STYLESHEET = f"""
QWidget {{
    background: {SHELL};
    color: {TEXT};
    font-size: {SIZE_BODY}px;
}}

QMainWindow::separator {{ width: 0px; height: 0px; }}

/* -- pannelli ---------------------------------------------------------- */

#rail, #inspector {{
    background: {PANEL};
    border: none;
}}
#rail {{ border-right: 1px solid {LINE}; }}
#inspector {{ border-left: 1px solid {LINE}; }}

#content {{ background: {SUNKEN}; }}

/* Pagine dell'ispettore: trasparenti, così si vede il fondo del pannello. */
#inspectorPages, #inspectorPage {{ background: transparent; }}

#sectionLabel {{
    color: {TEXT_FAINT};
    font-size: {SIZE_LABEL}px;
    font-weight: 600;
    letter-spacing: 1.1px;
    padding: 0px;
    background: transparent;
}}

#panelTitle {{
    color: {TEXT};
    font-size: {SIZE_STRONG}px;
    font-weight: 600;
    background: transparent;
}}

#dim  {{ color: {TEXT_DIM}; background: transparent; }}
#faint {{ color: {TEXT_FAINT}; background: transparent; font-size: {SIZE_SMALL}px; }}
#value {{ color: {TEXT}; background: transparent; font-size: {SIZE_SMALL}px; }}
#shotHeadline {{ color: {TEXT}; background: transparent; font-size: {SIZE_STRONG}px; font-weight: 600; }}

#separator {{ background: {LINE}; border: none; max-height: 1px; min-height: 1px; }}

/* -- barra superiore --------------------------------------------------- */

#topbar {{
    background: {PANEL};
    border-bottom: 1px solid {LINE};
}}

/* -- pulsanti ---------------------------------------------------------- */

QPushButton {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {LINE_STRONG};
    border-radius: {RADIUS}px;
    padding: 5px 12px;
    font-size: {SIZE_BODY}px;
}}
QPushButton:hover {{ background: {RAISED}; border-color: #4a5258; }}
QPushButton:pressed {{ background: {SUNKEN}; }}
QPushButton:disabled {{
    background: {PANEL};
    color: {TEXT_FAINT};
    border-color: {LINE};
}}
QPushButton:focus {{ border-color: {ACCENT}; outline: none; }}

QPushButton#primary {{
    background: {ACCENT};
    color: {ACCENT_INK};
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESS}; }}
QPushButton#primary:disabled {{
    background: {SURFACE};
    color: {TEXT_FAINT};
    border-color: {LINE_STRONG};
}}

QPushButton#ghost {{
    background: transparent;
    border: 1px solid transparent;
    padding: 4px 8px;
    color: {TEXT_DIM};
}}
QPushButton#ghost:hover {{ background: {SURFACE}; color: {TEXT}; }}
QPushButton#ghost:checked {{ background: {SURFACE}; color: {ACCENT}; border-color: {LINE_STRONG}; }}
QPushButton#ghost:disabled {{ background: transparent; color: {LINE_STRONG}; border-color: transparent; }}

QPushButton#collapser {{
    background: transparent;
    border: none;
    padding: 2px;
}}
QPushButton#collapser:hover {{ background: {RAISED}; border-radius: {RADIUS}px; }}
QPushButton#collapser[collapsed="true"] {{
    background: {SURFACE};
    border: 1px solid {LINE_STRONG};
    border-radius: {RADIUS}px;
}}

/* -- righe filtro ------------------------------------------------------ */

QPushButton#filterRow {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS}px;
    padding: 5px 8px;
    text-align: left;
    color: {TEXT_DIM};
}}
QPushButton#filterRow:hover {{ background: {SURFACE}; color: {TEXT}; }}
QPushButton#filterRow:checked {{ background: {SURFACE}; color: {TEXT}; }}

/* -- caselle ----------------------------------------------------------- */

QCheckBox {{ color: {TEXT_DIM}; spacing: 7px; background: transparent; }}
QCheckBox:hover {{ color: {TEXT}; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {LINE_STRONG};
    border-radius: 3px;
    background: {SUNKEN};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* -- viste ------------------------------------------------------------- */

QListView, QTreeView {{
    background: {SUNKEN};
    border: none;
    outline: none;
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}
QListView::item {{ border-radius: {RADIUS}px; padding: 3px; color: {TEXT_DIM}; }}
QListView::item:hover {{ background: {SURFACE}; color: {TEXT}; }}
QListView::item:selected {{ background: {ACCENT}; color: {ACCENT_INK}; }}

QTreeView::item {{ padding: 4px 6px; border: none; }}
QTreeView::item:hover {{ background: {SURFACE}; }}
QTreeView::item:selected {{ background: {ACCENT}; color: {ACCENT_INK}; }}

QHeaderView::section {{
    background: {PANEL};
    color: {TEXT_FAINT};
    border: none;
    border-bottom: 1px solid {LINE};
    border-right: 1px solid {LINE};
    padding: 5px 6px;
    font-size: {SIZE_LABEL}px;
    font-weight: 600;
    letter-spacing: 0.8px;
}}
QHeaderView::section:hover {{ color: {TEXT}; }}

/* -- barre di scorrimento ---------------------------------------------- */
/* I default di Qt non appartengono a nessun sistema di design. */

QScrollBar:vertical {{
    background: transparent; width: 11px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #3c4248; border-radius: 5px; min-height: 28px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: #4d555c; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: #3c4248; border-radius: 5px; min-width: 28px; margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: #4d555c; }}

/* -- avanzamento ------------------------------------------------------- */

QProgressBar {{
    background: {SUNKEN};
    border: none;
    border-radius: 2px;
    height: 4px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
/* Due barre impilate: quella del file e' smorzata, quella del totale e'
   l'accento pieno. Affiancate si leggevano come una barra sola spezzata. */
QProgressBar#file::chunk {{ background: #3f7f90; }}
QProgressBar#total::chunk {{ background: {ACCENT}; }}

/* -- varie ------------------------------------------------------------- */

QToolTip {{
    background: {RAISED};
    color: {TEXT};
    border: 1px solid {LINE_STRONG};
    border-radius: {RADIUS}px;
    padding: 5px 7px;
}}

QComboBox {{
    background: {SURFACE};
    border: 1px solid {LINE_STRONG};
    border-radius: {RADIUS}px;
    padding: 4px 8px;
    min-height: 18px;
}}
QComboBox:hover {{ border-color: #4a5258; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {RAISED};
    border: 1px solid {LINE_STRONG};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
    outline: none;
}}

QLineEdit {{
    background: {SUNKEN};
    border: 1px solid {LINE_STRONG};
    border-radius: {RADIUS}px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QStatusBar {{ background: {PANEL}; border-top: 1px solid {LINE}; color: {TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}

QMessageBox, QInputDialog, QFileDialog {{ background: {PANEL}; }}
"""
