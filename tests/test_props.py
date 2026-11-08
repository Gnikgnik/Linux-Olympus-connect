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


def test_without_lens_limits_the_aperture_stays_where_it_is(qapp):
    s = PropsSection()
    cambi = []
    s.prop_changed.connect(lambda n, v: cambi.append((n, v)))
    props = {"takemode": CameraProp("takemode", "A", ("P", "A", "S", "M"), True),
             "focalvalue": APERTURA}
    s.set_props(props)
    riga = s.row("focalvalue")
    assert riga.menu_values() == ["3.5"]                 # obiettivo ignoto: solo il valore attuale
    assert not riga.is_automatic() and riga._value.text() == "f/3,5"
    riga.step(1)
    riga.step(-1)
    riga.choose("1.0")
    assert riga.shown_value() == "3.5" and not riga.is_pending()
    s.set_lens_limits((3.5, 5.6))
    assert riga.menu_values() == ["3.5", "4.0", "5.6"]
    s.set_lens_limits(None)                              # di nuovo ignoto
    assert riga.menu_values() == ["3.5"]
    s.set_props({**props, "focalvalue": CameraProp("focalvalue", "5.6", APERTURA.values, True)})
    assert riga.menu_values() == ["5.6"]                 # segue il valore confermato
    assert not _attendi(qapp, lambda: cambi, 0.4)


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
    assert sezione.row("focalvalue").menu_values() == ["3.5"]    # limiti dimenticati


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


def test_a_pending_value_missing_from_the_new_list_is_dropped(qapp):
    s, eventi = _stepper()
    s.step(1)                                           # 250 in attesa
    s.set_prop(CameraProp("isospeedvalue", "200", ("Auto", "200", "320"), True), False)
    assert s.shown_value() == "200" and not s.is_pending()
    assert not _attendi(qapp, lambda: eventi, 0.4)


def test_a_pending_value_no_longer_allowed_is_dropped(qapp):
    s, eventi = _stepper(APERTURA)
    from olympus_connect.core.camprops import aperture_allowed
    s.set_allowed(aperture_allowed((3.5, 8.0)))
    s.step(1)                                           # 4.0 in attesa
    s.set_prop(APERTURA, False)                         # rilettura: 4.0 è ancora ammesso
    assert s.shown_value() == "4.0" and s.is_pending()
    s.set_allowed(aperture_allowed((5.6, 8.0)))         # si è zoomato: 4.0 non c'è più
    assert s.shown_value() == "3.5" and not s.is_pending()
    s.set_allowed(aperture_allowed((3.5, 8.0)))
    s.step(1)
    s.set_allowed(lambda v: v == "3.5")                 # come con l'obiettivo ignoto
    s.set_prop(APERTURA, False)
    assert s.shown_value() == "3.5" and not s.is_pending()
    assert not _attendi(qapp, lambda: eventi, 0.4)


def _art_e_p():
    props = parse_desclist((REMOTE / "camprop_desclist.xml").read_bytes())
    art = dict(props)
    art["takemode"] = CameraProp("takemode", "ART", props["takemode"].values, True)
    p = dict(props)
    p["takemode"] = CameraProp("takemode", "P", props["takemode"].values, True)
    return art, p


def test_a_removed_row_never_sends(qapp):
    art, p = _art_e_p()
    sezione = PropsSection()
    cambi = []
    sezione.prop_changed.connect(lambda n, v: cambi.append((n, v)))
    sezione.set_props(art)
    filtro = sezione.row("artfilter")
    filtro.step(1)
    sezione.set_props(p)                                # la riga del filtro sparisce
    assert sezione.row("artfilter") is None
    assert not _attendi(qapp, lambda: cambi, 0.4)
    filtro._send()                                      # anche forzandola: non arriva nulla
    assert cambi == []


def test_removed_rows_never_send_after_unavailable_or_clear(qapp):
    for svuota in (lambda s: s.set_unavailable("Impostazioni non disponibili"),
                   lambda s: s.clear()):
        sezione = PropsSection()
        cambi = []
        sezione.prop_changed.connect(lambda n, v: cambi.append((n, v)))
        sezione.set_props(parse_desclist((REMOTE / "desclist_M.xml").read_bytes()))
        riga = sezione.row("isospeedvalue")
        riga.step(1)
        svuota(sezione)
        assert not _attendi(qapp, lambda: cambi, 0.4)
        riga._send()
        assert cambi == []


def test_while_the_mode_changes_the_other_rows_do_not_send(qapp):
    """M → S, e il tempo girato prima che arrivi la rilettura di S: in S il
    tempo ha un altro valore, il comando del tempo non deve partire."""
    sezione = PropsSection()
    cambi = []
    sezione.prop_changed.connect(lambda n, v: cambi.append((n, v)))
    sezione.set_props(parse_desclist((REMOTE / "desclist_M.xml").read_bytes()))
    sezione.row("takemode").step(-1)                    # M → S
    assert sezione.row("takemode").shown_value() == "S"
    sezione.row("shutspeedvalue").step(-1)              # entro i 250 ms
    assert _attendi(qapp, lambda: cambi == [("takemode", "S")])
    assert not sezione.row("shutspeedvalue").isEnabled()
    assert not sezione.row("isospeedvalue").isEnabled()
    assert sezione.row("takemode").isEnabled()
    assert not _attendi(qapp, lambda: len(cambi) > 1, 0.4)
    sezione.row("isospeedvalue").step(1)                # spenta: niente
    sezione.set_props(parse_desclist((REMOTE / "desclist_S.xml").read_bytes()))
    tempo = sezione.row("shutspeedvalue")
    assert tempo.isEnabled() and sezione.row("isospeedvalue").isEnabled()
    assert tempo.shown_value() == "640" and not tempo.is_pending()
    assert not _attendi(qapp, lambda: len(cambi) > 1, 0.4)
    assert cambi == [("takemode", "S")]


def test_the_mode_change_respects_set_enabled(qapp):
    sezione = PropsSection()
    sezione.set_props(parse_desclist((REMOTE / "desclist_M.xml").read_bytes()))
    sezione.row("takemode").step(-1)
    assert _attendi(qapp, lambda: not sezione.row("isospeedvalue").isEnabled())
    sezione.set_enabled(True)                           # fine registrazione: la modalità è ancora in volo
    assert not sezione.row("isospeedvalue").isEnabled()
    sezione.set_enabled(False)
    sezione.set_props(parse_desclist((REMOTE / "desclist_S.xml").read_bytes()))
    assert not sezione.row("isospeedvalue").isEnabled()   # in registrazione resta spenta
    sezione.set_enabled(True)
    assert sezione.row("isospeedvalue").isEnabled()


def test_a_reread_arriving_before_the_pause_ends_does_not_revive_old_values(qapp):
    sezione = PropsSection()
    cambi = []
    sezione.prop_changed.connect(lambda n, v: cambi.append((n, v)))
    sezione.set_props(parse_desclist((REMOTE / "desclist_M.xml").read_bytes()))
    sezione.row("takemode").step(-1)                    # M → S
    assert not _attendi(qapp, lambda: False, 0.15)
    sezione.row("shutspeedvalue").step(-1)              # 500: esiste anche in S
    assert _attendi(qapp, lambda: cambi == [("takemode", "S")])
    assert sezione.row("shutspeedvalue").shown_value() == "640"   # caduto subito
    sezione.set_props(parse_desclist((REMOTE / "desclist_S.xml").read_bytes()))
    assert not _attendi(qapp, lambda: len(cambi) > 1, 0.4)
    assert sezione.row("shutspeedvalue").shown_value() == "640"


def _rotella(y=120):
    return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, y),
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


def test_the_wheel_does_not_change_the_mode(qapp):
    modo = CameraProp("takemode", "A", ("P", "A", "S", "M"), True)
    s, eventi = _stepper(modo)
    evento = _rotella()
    s.wheelEvent(evento)
    assert s.shown_value() == "A" and not s.is_pending()
    assert not evento.isAccepted()                      # la rotella resta al pannello che scorre
    s.step(1)                                           # le frecce sì
    assert s.shown_value() == "S"
    s.choose("M")                                       # e l'elenco
    assert _attendi(qapp, lambda: eventi == [("takemode", "M")])
