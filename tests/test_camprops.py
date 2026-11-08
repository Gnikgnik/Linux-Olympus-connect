"""Le impostazioni della fotocamera: elenco, testo italiano, regole."""
from pathlib import Path

import pytest

from olympus_connect.core.camprops import (
    CameraProp, aperture_allowed, format_value, is_automatic, is_hidden, label,
    parse_desclist, step, visible_props,
)

REMOTE = Path(__file__).parent / "fixtures" / "remote"


def _elenco(nome):
    return parse_desclist((REMOTE / nome).read_bytes())


def test_reads_the_real_desclist():
    props = _elenco("camprop_desclist.xml")
    iso = props["isospeedvalue"]
    assert iso == CameraProp("isospeedvalue", "200", iso.values, True)
    assert iso.values[:3] == ("Auto", "Low", "200")
    assert props["touchactiveframe"].settable is False
    assert props["takemode"].value == "S"


def test_garbage_is_empty():
    assert parse_desclist(b"") == {}
    assert parse_desclist(b"<non xml") == {}


@pytest.mark.parametrize("nome,valore,testo", [
    ("shutspeedvalue", "250", "1/250"),
    ("shutspeedvalue", "1.3", "1/1,3"),
    ("shutspeedvalue", '60"', "60 s"),
    ("shutspeedvalue", '1.3"', "1,3 s"),
    ("shutspeedvalue", "livecomp", "Live Composite"),
    ("shutspeedvalue", "livetime", "Live Time"),
    ("shutspeedvalue", "livebulb", "Live Bulb"),
    ("focalvalue", "5.6", "f/5,6"),
    ("focalvalue", "10", "f/10"),
    ("focalvalue", "2.0", "f/2"),
    ("isospeedvalue", "Auto", "Auto"),
    ("isospeedvalue", "6400", "6400"),
    ("expcomp", "0.0", "0 EV"),
    ("expcomp", "+0.3", "+0,3 EV"),
    ("expcomp", "-0.7", "−0,7 EV"),
    ("takemode", "Scene2", "Scene"),
    ("takemode", "movie", "Video"),
    ("takemode", "A", "A"),
    ("wbvalue", "0", "Auto"),
    ("wbvalue", "18", "Luce diurna"),
    ("wbvalue", "512", "Temperatura personalizzata"),
    ("wbvalue", "999", "999"),
    ("drivemode", "lowvib-normal", "Singolo anti-vibrazione"),
    ("drivemode", "continuous-H", "Raffica H"),
    ("artfilter", "rough_monochrome", "Grainy Film"),
    ("artfilter", "placame", "Instant Film"),
    ("ArtEffectTypePopart", "Type2", "II"),
    ("SceneSub", "Sunset", "Tramonto"),
    ("sconosciuta", "x", "x"),
])
def test_format_value(nome, valore, testo):
    assert format_value(nome, valore) == testo


def test_labels():
    assert label("shutspeedvalue") == "Tempo"
    assert label("ArtEffectTypePopart") == "Variante"
    assert label("SceneSub") == "Scena"


def _con_modo(props, modo):
    nuovo = dict(props)
    nuovo["takemode"] = CameraProp("takemode", modo, props["takemode"].values, True)
    return nuovo


def test_visible_rows_follow_the_mode():
    props = _elenco("camprop_desclist.xml")
    base = ["takemode", "shutspeedvalue", "focalvalue", "isospeedvalue", "expcomp",
            "wbvalue", "drivemode"]
    assert visible_props(props) == base
    art = _con_modo(props, "ART")          # filtro attuale: rough_monochrome
    assert visible_props(art) == base + ["artfilter", "ArtEffectTypeRoughMonochrome"]
    assert visible_props(_con_modo(props, "Scene2")) == base + ["SceneSub"]


@pytest.mark.parametrize("file,automatiche", [
    ("desclist_P.xml", {"shutspeedvalue", "focalvalue"}),
    ("desclist_A.xml", {"shutspeedvalue"}),
    ("desclist_S.xml", {"focalvalue"}),
    ("desclist_M.xml", {"expcomp"}),     # in M l'elenco dichiara il solo 0.0
])
def test_automatic_rows_by_real_mode(file, automatiche):
    props = _elenco(file)
    trovate = {n for n in ("shutspeedvalue", "focalvalue", "isospeedvalue", "expcomp")
               if is_automatic(n, props)}
    assert trovate == automatiche
    assert not is_automatic("takemode", props)


def test_iauto_makes_all_exposure_automatic():
    props = _con_modo(_elenco("desclist_M.xml"), "iAuto")
    assert all(is_automatic(n, props) for n in
               ("shutspeedvalue", "focalvalue", "isospeedvalue", "expcomp"))
    assert not is_automatic("wbvalue", props)


def test_read_only_or_missing_is_automatic():
    props = _elenco("camprop_desclist.xml")
    assert is_automatic("touchactiveframe", props)
    assert is_automatic("assente", props)


def test_aperture_limits_from_the_lens():
    ammesso = aperture_allowed((3.5, 22.0))
    assert [v for v in ("1.0", "3.2", "3.5", "5.6", "22", "25") if ammesso(v)] == ["3.5", "5.6", "22"]
    assert aperture_allowed(None)("1.0") is True
    assert aperture_allowed((3.6, 22.0))("3.5") is False


VALORI = ("1.0", "2.0", "3.5", "4.0", "5.6", "8.0")


def test_step_moves_and_stops_at_the_ends():
    assert step(VALORI, "3.5", 1) == "4.0"
    assert step(VALORI, "3.5", -1) == "2.0"
    assert step(VALORI, "8.0", 1) == "8.0"
    assert step(VALORI, "1.0", -1) == "1.0"


def test_step_skips_values_not_allowed():
    ammesso = aperture_allowed((3.5, 5.6))
    assert step(VALORI, "3.5", -1, ammesso) == "3.5"     # sotto il limite: fermo
    assert step(VALORI, "4.0", 1, ammesso) == "5.6"
    assert step(VALORI, "5.6", 1, ammesso) == "5.6"


def test_step_from_an_out_of_range_value_goes_to_the_nearest_allowed():
    ammesso = aperture_allowed((3.5, 5.6))
    assert step(VALORI, "1.0", 1, ammesso) == "3.5"
    assert step(VALORI, "8.0", -1, ammesso) == "5.6"
    assert step(VALORI, "assente", 1, ammesso) == "3.5"


def test_values_the_shoot_path_was_not_built_for_are_hidden():
    for valore in ("livecomp", "livetime", "livebulb"):
        assert is_hidden("shutspeedvalue", valore)
    assert not is_hidden("shutspeedvalue", '60"')        # i tempi lunghi restano
    assert not is_hidden("shutspeedvalue", "bulb")
    autoscatti = [v for v in _elenco("desclist_M.xml")["drivemode"].values
                  if is_hidden("drivemode", v)]
    assert autoscatti == ["lowvib-selftimer", "customselftimer",
                          "lowvib-customselftimer", "silent-customselftimer"]
    assert not is_hidden("drivemode", "silent-continuous-H")
    assert not is_hidden("isospeedvalue", "livecomp")    # la regola è per proprietà
