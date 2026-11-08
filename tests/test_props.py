"""Il selettore delle impostazioni e la sezione del pannello."""
import time
from pathlib import Path

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent

from olympus_connect.core.camprops import CameraProp, parse_desclist
from olympus_connect.gui.props import PropsSection, PropStepper

REMOTE = Path(__file__).parent / "fixtures" / "remote"
ISO = CameraProp("isospeedvalue", "200", ("Auto", "Low", "200", "250", "320"), True)
APERTURA = CameraProp("focalvalue", "3.5", ("1.0", "2.0", "3.5", "4.0", "5.6", "8.0"), True)


def _attendi(qapp, condizione, secondi=2.0):
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        qapp.processEvents()
        if condizione():
            return True
        time.sleep(0.01)
    return condizione()


def _stepper(prop=ISO, automatic=False):
    s = PropStepper(prop.name)
    s.set_prop(prop, automatic)
    eventi = []
    s.changed.connect(lambda n, v: eventi.append((n, v)))
    return s, eventi


def test_arrows_change_the_shown_value_and_mark_it_pending(qapp):
    s, _ = _stepper()
    s.step(1)
    assert s.shown_value() == "250" and s.is_pending()
    assert s.confirmed_value() == "200"


def test_only_the_final_value_is_sent_after_the_pause(qapp):
    s, eventi = _stepper()
    s.step(1)
    s.step(1)
    assert eventi == []
    assert _attendi(qapp, lambda: eventi == [("isospeedvalue", "320")])
    time.sleep(0.4)
    qapp.processEvents()
    assert eventi == [("isospeedvalue", "320")]


def test_a_reread_confirms_or_reverts(qapp):
    s, eventi = _stepper()
    s.step(1)
    s.set_prop(CameraProp("isospeedvalue", "250", ISO.values, True), False)
    assert _attendi(qapp, lambda: not s.is_pending())
    assert s.shown_value() == "250"
    s.step(1)
    assert not _attendi(qapp, lambda: False, 0.4)   # lascia scadere la pausa: il comando parte
    assert eventi[-1] == ("isospeedvalue", "320")
    s.set_prop(ISO, False)              # rifiutato: la fotocamera è rimasta a 200
    assert _attendi(qapp, lambda: s.shown_value() == "200" and not s.is_pending())


def test_a_pending_change_is_dropped_when_the_row_turns_automatic(qapp):
    s, eventi = _stepper(APERTURA)
    s.step(1)
    s.set_prop(APERTURA, True)          # la ghiera è passata a S: l'apertura è automatica
    assert s.shown_value() == "3.5" and not s.is_pending()
    assert not _attendi(qapp, lambda: eventi, 0.4)


def test_a_pending_change_is_dropped_when_the_row_is_disabled(qapp):
    s, eventi = _stepper()
    s.step(1)
    s.setEnabled(False)
    assert not _attendi(qapp, lambda: eventi, 0.4)
    assert s.shown_value() == "200" and not s.is_pending()


def test_wheel_steps(qapp):
    s, _ = _stepper()
    evento = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
    s.wheelEvent(evento)
    assert s.shown_value() == "250"


def test_choose_from_the_list(qapp):
    s, eventi = _stepper()
    assert s.menu_values() == list(ISO.values)
    s.choose("Auto")
    assert s.shown_value() == "Auto"
    assert _attendi(qapp, lambda: eventi == [("isospeedvalue", "Auto")])


def test_automatic_rows_do_not_move(qapp):
    s, eventi = _stepper(automatic=True)
    assert s.is_automatic()
    s.step(1)
    assert s.shown_value() == "200"
    time.sleep(0.35)
    qapp.processEvents()
    assert eventi == []


def test_aperture_is_limited_to_the_lens(qapp):
    from olympus_connect.core.camprops import aperture_allowed
    s, _ = _stepper(APERTURA)
    s.set_allowed(aperture_allowed((3.5, 5.6)))
    assert s.menu_values() == ["3.5", "4.0", "5.6"]
    s.step(-1)
    assert s.shown_value() == "3.5"


def test_section_rows_follow_the_mode(qapp):
    props = parse_desclist((REMOTE / "camprop_desclist.xml").read_bytes())
    sezione = PropsSection()
    sezione.set_props(props)
    assert sezione.names() == ["takemode", "shutspeedvalue", "focalvalue", "isospeedvalue",
                               "expcomp", "wbvalue", "drivemode"]
    assert sezione.row("focalvalue").is_automatic()        # ghiera S
    art = dict(props)
    art["takemode"] = CameraProp("takemode", "ART", props["takemode"].values, True)
    sezione.set_props(art)
    assert sezione.names()[-2:] == ["artfilter", "ArtEffectTypeRoughMonochrome"]
    sezione.set_props(props)
    assert "artfilter" not in sezione.names()


def test_section_forwards_changes_and_lens_limits(qapp):
    props = parse_desclist((REMOTE / "desclist_A.xml").read_bytes())
    sezione = PropsSection()
    cambi = []
    sezione.prop_changed.connect(lambda n, v: cambi.append((n, v)))
    sezione.set_props(props)
    sezione.set_lens_limits((3.5, 22.0))
    assert "1.0" not in sezione.row("focalvalue").menu_values()
    sezione.row("isospeedvalue").step(1)
    assert _attendi(qapp, lambda: cambi == [("isospeedvalue", "250")])


def test_section_unavailable_and_disabled(qapp):
    sezione = PropsSection()
    sezione.set_unavailable("Impostazioni non disponibili")
    assert sezione.names() == [] and sezione.message() == "Impostazioni non disponibili"
    sezione.set_props(parse_desclist((REMOTE / "desclist_M.xml").read_bytes()))
    assert sezione.message() == ""
    sezione.set_enabled(False)
    assert not sezione.row("isospeedvalue").isEnabled()


def test_lens_limits_none_allows_every_aperture_again(qapp):
    s = PropsSection()
    props = {"takemode": CameraProp("takemode", "A", ("P", "A", "S", "M"), True),
             "focalvalue": APERTURA}
    s.set_props(props)
    s.set_lens_limits((3.5, 5.6))
    assert s.row("focalvalue").menu_values() == ["3.5", "4.0", "5.6"]
    s.set_lens_limits(None)
    assert s.row("focalvalue").menu_values() == list(APERTURA.values)


def test_automatic_rows_say_so_and_keep_the_value_column(qapp):
    s, _ = _stepper(APERTURA, automatic=True)
    assert s._value.text() == "f/3,5 · automatico"
    s.resize(300, 24)
    s.show()
    qapp.processEvents()
    x_auto = s._value.geometry().x()
    s.set_prop(APERTURA, False)
    qapp.processEvents()
    assert s._value.text() == "f/3,5"
    assert s._value.geometry().x() == x_auto
    s.hide()


def test_clear_forgets_rows_and_lens_limits(qapp):
    sezione = PropsSection()
    sezione.set_lens_limits((3.5, 5.6))
    sezione.set_props(parse_desclist((REMOTE / "desclist_A.xml").read_bytes()))
    assert sezione.names()
    sezione.clear()
    assert sezione.names() == [] and sezione.message() == ""
    sezione.set_props(parse_desclist((REMOTE / "desclist_A.xml").read_bytes()))
    assert "1.0" in sezione.row("focalvalue").menu_values()


TEMPO = CameraProp("shutspeedvalue", "livecomp",
                   ("livecomp", "livetime", "livebulb", '60"', '30"', "125", "250"), True)
SCATTO = CameraProp("drivemode", "normal",
                    ("normal", "lowvib-selftimer", "customselftimer", "continuous-H"), True)


def test_hidden_values_are_not_offered_but_the_current_one_is_shown(qapp):
    s, _ = _stepper(TEMPO)
    assert s.menu_values() == ['60"', '30"', "125", "250"]     # i tempi lunghi restano
    assert s._value.text() == "Live Composite"                 # scelto sul corpo: si vede
    s.step(-1)
    assert s.shown_value() == '60"'                            # al più vicino selezionabile
    s2, _ = _stepper(TEMPO)
    s2.step(1)
    assert s2.shown_value() == '60"'


def test_stepping_skips_the_self_timer(qapp):
    s, _ = _stepper(SCATTO)
    assert s.menu_values() == ["normal", "continuous-H"]
    s.step(1)
    assert s.shown_value() == "continuous-H"
    s.choose("customselftimer")                 # non è nell'elenco: ignorato
    assert s.shown_value() == "continuous-H"
