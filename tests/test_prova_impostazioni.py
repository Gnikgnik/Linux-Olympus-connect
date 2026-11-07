"""Le funzioni pure dello strumento di prova delle impostazioni."""
from pathlib import Path

from tools.prova_impostazioni import (
    altro_valore, ordine_ripristino, parse_desclist, porta_sorgente,
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


def test_ripristino_restores_the_mode_last_after_going_to_manual():
    passi = ordine_ripristino({"takemode": "S", "isospeedvalue": "200", "focalvalue": "5.6"})
    assert passi[0] == ("takemode", "M")
    assert passi[-1] == ("takemode", "S")
    assert set(passi[1:-1]) == {("isospeedvalue", "200"), ("focalvalue", "5.6")}


def test_ripristino_without_mode_is_just_the_values():
    assert ordine_ripristino({"wbvalue": "0"}) == [("wbvalue", "0")]
