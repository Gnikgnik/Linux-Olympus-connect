"""Le funzioni pure dello strumento di prova delle impostazioni."""
from pathlib import Path

from tools.prova_impostazioni import (
    altro_valore, mancanti, parse_desclist, piano_ripristino, porta_sorgente,
    raggruppa_piano, valori_modo,
)

DESCLIST = (Path(__file__).parent / "fixtures" / "remote" / "camprop_desclist.xml").read_bytes()


def test_reads_the_real_desclist():
    elenco = parse_desclist(DESCLIST)
    attributo, valore, valori = elenco["isospeedvalue"]
    assert attributo == "getset" and valore == "200"
    assert valori[:3] == ["Auto", "Low", "200"]
    assert elenco["touchactiveframe"][0] == "get"
    assert elenco["takemode"][1] == "S"


def test_garbage_desclist_is_empty():
    assert parse_desclist(b"non xml") == {}


def test_altro_valore_picks_a_different_allowed_value():
    assert altro_valore(["Auto", "Low", "200", "250"], "200", escludi=("Auto", "Low")) == "250"
    assert altro_valore(["normal", "lowvib-selftimer"], "normal", escludi=("lowvib-selftimer",)) is None
    assert altro_valore(["a"], "a") is None


def test_porta_sorgente_swaps_the_bytes():
    assert porta_sorgente(40000) == 16540
    assert porta_sorgente(35199) == 32649


def test_valori_modo_keeps_only_writable_exposure_values():
    elenco = {
        "isospeedvalue": ("getset", "200", []),
        "focalvalue": ("get", "5.6", []),
        "wbvalue": ("getset", "0", []),
        "shutspeedvalue": ("getset", "640", []),
    }
    assert valori_modo(elenco) == {"isospeedvalue": "200", "shutspeedvalue": "640"}


def test_piano_visits_each_mode_then_ends_in_the_original_mode():
    per_modo = {
        "P": {"isospeedvalue": "400"},
        "A": {"shutspeedvalue": "250"},
        "S": {"shutspeedvalue": "640", "isospeedvalue": "200"},
        "M": {"focalvalue": "5.6"},
    }
    piano = piano_ripristino("S", per_modo, {"wbvalue": "0", "drivemode": "lowvib-normal",
                                             "isospeedvalue": "200"})
    assert [v for k, v in piano if k == "takemode"] == ["P", "A", "M", "S", "S"]
    assert piano[-1] == ("takemode", "S")
    assert [p for p in piano if p[0] != "takemode"] == [
        ("isospeedvalue", "400"), ("shutspeedvalue", "250"), ("focalvalue", "5.6"),
        ("shutspeedvalue", "640"), ("isospeedvalue", "200"),
        ("wbvalue", "0"), ("drivemode", "lowvib-normal"),
    ]


def test_piano_without_per_mode_values_is_just_globals_in_the_original_mode():
    assert piano_ripristino("S", {}, {"wbvalue": "0"}) == [
        ("takemode", "S"), ("wbvalue", "0"), ("takemode", "S"),
    ]


def test_raggruppa_piano_keeps_each_mode_with_its_own_values():
    per_modo = {
        "P": {"isospeedvalue": "400"},
        "A": {"shutspeedvalue": "250"},
        "S": {"shutspeedvalue": "640", "isospeedvalue": "200"},
        "M": {"focalvalue": "5.6"},
    }
    gruppi = raggruppa_piano(piano_ripristino("S", per_modo, {
        "wbvalue": "0", "drivemode": "lowvib-normal", "isospeedvalue": "200"}))
    assert gruppi == [
        ("P", [("isospeedvalue", "400")]),
        ("A", [("shutspeedvalue", "250")]),
        ("M", [("focalvalue", "5.6")]),
        ("S", [("shutspeedvalue", "640"), ("isospeedvalue", "200"),
               ("wbvalue", "0"), ("drivemode", "lowvib-normal")]),
    ]


def test_raggruppa_piano_keeps_a_mode_without_values():
    assert raggruppa_piano(piano_ripristino("S", {"A": {}}, {})) == [("A", []), ("S", [])]


def test_mancanti_lists_each_setting_not_at_its_target_with_its_mode():
    esiti = [
        ("P", "isospeedvalue", "400", "400"),
        ("S", "takemode", "S", "P"),
        ("A", "shutspeedvalue", "250", None),
        ("S", "wbvalue", "0", "0"),
        ("S", "takemode", "S", "P"),
    ]
    assert mancanti(esiti) == [
        "S: takemode (atteso S, letto P)",
        "A: shutspeedvalue (atteso 250, non ripristinato)",
    ]


def test_mancanti_is_empty_when_everything_matches():
    assert mancanti([("S", "takemode", "S", "S"), ("S", "wbvalue", "0", "0")]) == []
