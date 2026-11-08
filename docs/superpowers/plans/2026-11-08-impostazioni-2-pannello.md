# Impostazioni dal PC, parte 2: pannello — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** nel pannello del telecomando, una sezione «Impostazioni» con selettori a freccette (rotella, elenco al clic) per modalità, tempo, diaframma, ISO, compensazione, bilanciamento, modalità di scatto, filtro artistico e variante, scena; il diaframma limitato all'obiettivo e allo zoom attuali.

**Architecture:** nel core, `core/camprops.py` legge l'elenco delle proprietà, le traduce in italiano e decide cosa mostrare e cosa è automatico; `protocol.py` legge l'elenco e invia `set_camprop` in POST; `core/liveview.py` estrae i limiti dell'obiettivo (TLV 9) da ogni fotogramma. Nella GUI, `RemoteWorker` legge e imposta (sempre rileggendo), `LiveViewWorker` segnala i limiti dell'obiettivo, `gui/props.py` contiene il selettore e la sezione, `RemotePanel` e `MainWindow` li collegano.

**Tech Stack:** Python 3.12+, PyQt6, requests; pytest con requests-mock.

**Specifica:** `docs/superpowers/specs/2026-11-08-impostazioni-dal-pc-design.md` (fatti verificati: «Fatti accertati», «Limiti dell'obiettivo nel live», «Cancello»).

## Global Constraints

- I test si lanciano con `.venv/bin/python -m pytest` (`pythonpath = ["."]`; `tests/` è un pacchetto: gli helper si importano come `from tests.… import …`).
- Nessun import di Qt in `olympus_connect/core/`. Nessuna nuova dipendenza. `exec_erase.cgi` mai invocato.
- `parse_desclist`, `format_value`, `lens_limits` non sollevano mai eccezioni. Nessuna eccezione esce da uno slot o dal ciclo di un worker.
- Formato verificato: `POST set_camprop.cgi?com=set&propname=<nome>`, corpo `<?xml version="1.0"?><set><value>VALORE</value></set>`; risposta HTTP 200 (il corpo della risposta non dice nulla sull'esito: l'esito si legge rileggendo l'elenco).
- La fotocamera accetta ogni valore dell'elenco in ogni modalità: «automatico» è una **regola per modalità** (iAuto, Scene, Video: tutta l'esposizione; P e ART: tempo e diaframma; A: tempo; S: diaframma; M: nessuna) più ogni proprietà non `settable` o con al più un valore dichiarato.
- Diaframma: solo i valori fra `TLV9[1]/10` e `TLV9[0]/10` (tolleranza 0,05); senza limiti noti, tutti.
- Il comando parte **250 ms** dopo l'ultimo passo, con il solo valore finale; dopo ogni modifica l'elenco si rilegge.
- I selettori sono spenti finché il telecomando non è pronto (`entered`), durante uno scatto, durante la registrazione e durante l'uscita.
- Testi in italiano; segno meno U+2212; cifre tabulari già globali; animazioni del sistema invariate.
- Ogni messaggio di commit termina con la riga `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Mappa dei file

| File | Stato | Responsabilità |
|---|---|---|
| `olympus_connect/core/camprops.py` | nuovo | `CameraProp`, `parse_desclist`, `format_value`, `label`, `visible_props`, `is_automatic`, `aperture_allowed`, `step` |
| `olympus_connect/core/protocol.py` | modifica | `camera_props`, `set_prop`, `_post_ok` |
| `olympus_connect/core/liveview.py` | modifica | `RtpPacket.lens_limits`, `FrameAssembler.lens_limits` |
| `olympus_connect/gui/liveview.py` | modifica | segnale `lens_changed` |
| `olympus_connect/gui/workers.py` | modifica | `RemoteWorker.read_props`, `set_prop`, segnali `props_read`, `props_failed` |
| `olympus_connect/gui/props.py` | nuovo | `PropStepper`, `PropsSection` |
| `olympus_connect/gui/theme.py` | modifica | stili `#propValue`, `#propArrow` |
| `olympus_connect/gui/remote.py` | modifica | `RemotePanel` con `PropsSection` |
| `olympus_connect/gui/main_window.py` | modifica | collegamenti |
| test e documentazione | | |

---

### Task 1: `core/camprops.py`

**Files:**
- Create: `olympus_connect/core/camprops.py`
- Test: `tests/test_camprops.py`

**Interfaces:**
- Consumes: fixture vere `tests/fixtures/remote/camprop_desclist.xml` (ghiera S) e `desclist_P.xml`, `desclist_A.xml`, `desclist_S.xml`, `desclist_M.xml`.
- Produces: `MINUS`, `EXPOSURE = ("shutspeedvalue", "focalvalue", "isospeedvalue", "expcomp")`, `ORDER`; `CameraProp(name: str, value: str, values: tuple[str, ...], settable: bool)` (frozen); `parse_desclist(body: bytes) -> dict[str, CameraProp]`; `format_value(name: str, value: str) -> str`; `label(name: str) -> str`; `visible_props(props) -> list[str]`; `is_automatic(name, props) -> bool`; `aperture_allowed(limits: tuple[float, float] | None) -> Callable[[str], bool]`; `step(values: tuple[str, ...], current: str, delta: int, allowed: Callable[[str], bool] | None = None) -> str`.

- [ ] **Step 1: Scrivi i test che falliscono**

Create `tests/test_camprops.py`:

```python
"""Le impostazioni della fotocamera: elenco, testo italiano, regole."""
from pathlib import Path

import pytest

from olympus_connect.core.camprops import (
    CameraProp, aperture_allowed, format_value, is_automatic, label,
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
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_camprops.py -q`
Expected: errore di raccolta, `ModuleNotFoundError: No module named 'olympus_connect.core.camprops'`.

- [ ] **Step 3: Scrivi il modulo**

Create `olympus_connect/core/camprops.py`:

```python
"""Impostazioni della fotocamera: elenco, testo in italiano, regole.

Verificato sulla E-M10 Mark IV (prova del 2026-11-08): l'elenco desclist
dichiara modificabili tempo, diaframma, ISO e compensazione in ogni
modalità, e la fotocamera accetta ogni valore. Che cosa la modalità lascia
decidere alla fotocamera è quindi una regola per modalità, non un dato
dell'elenco. Nessun import di Qt; niente qui solleva eccezioni.
"""
from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Callable

MINUS = "−"
EXPOSURE = ("shutspeedvalue", "focalvalue", "isospeedvalue", "expcomp")
ORDER = ("takemode", "shutspeedvalue", "focalvalue", "isospeedvalue", "expcomp",
         "wbvalue", "drivemode")

_AUTOMATIC_BY_MODE = {
    "iAuto": EXPOSURE, "Scene2": EXPOSURE, "movie": EXPOSURE,
    "P": ("shutspeedvalue", "focalvalue"), "ART": ("shutspeedvalue", "focalvalue"),
    "A": ("shutspeedvalue",), "S": ("focalvalue",), "M": (),
}

_LABELS = {
    "takemode": "Modalità", "shutspeedvalue": "Tempo", "focalvalue": "Diaframma",
    "isospeedvalue": "ISO", "expcomp": "Compensazione", "wbvalue": "Bilanciamento",
    "drivemode": "Scatto", "artfilter": "Filtro", "SceneSub": "Scena",
}

_SHUTTER_WORDS = {"livecomp": "Live Composite", "livetime": "Live Time",
                  "livebulb": "Live Bulb", "bulb": "Bulb", "time": "Time"}
_MODES = {"Scene2": "Scene", "movie": "Video"}
_WB = {"0": "Auto", "18": "Luce diurna", "16": "Ombra", "17": "Nuvoloso",
       "20": "Tungsteno", "35": "Fluorescente", "23": "Subacqueo",
       "256": "Personalizzato 1", "257": "Personalizzato 2", "258": "Personalizzato 3",
       "259": "Personalizzato 4", "512": "Temperatura personalizzata"}
_DRIVE = {"normal": "Singolo", "lowvib-normal": "Singolo anti-vibrazione",
          "silent-normal": "Singolo silenzioso", "continuous-H": "Raffica H",
          "silent-continuous-H": "Raffica H silenziosa", "continuous-L": "Raffica L",
          "lowvib-continuous-L": "Raffica L anti-vibrazione",
          "silent-continuous-L": "Raffica L silenziosa",
          "lowvib-selftimer": "Autoscatto anti-vibrazione",
          "customselftimer": "Autoscatto personalizzato",
          "lowvib-customselftimer": "Autoscatto pers. anti-vibrazione",
          "silent-customselftimer": "Autoscatto pers. silenzioso"}
_ART = {"popart": "Pop Art", "fantasic_focus": "Soft Focus",
        "daydream": "Pale & Light Color", "light_tone": "Light Tone",
        "rough_monochrome": "Grainy Film", "toy_photo": "Pin Hole",
        "miniature": "Diorama", "cross_process": "Cross Process",
        "gentle_sepia": "Gentle Sepia", "dramatic_tone": "Dramatic Tone",
        "ligne_clair": "Key Line", "pastel": "Watercolor", "vintage": "Vintage",
        "partcolor": "Partial Color", "bleach_bypass": "Bleach Bypass",
        "placame": "Instant Film"}
_VARIANTS = {"Type1": "I", "Type2": "II", "Type3": "III"}
_SCENES = {"Portrait": "Ritratto", "ePortrait": "e-Ritratto",
           "LandscapeAndPerson": "Paesaggio e ritratto",
           "NightViewPerson": "Ritratto notturno", "NightView": "Notturno",
           "NightBlastOffFirework": "Fuochi d'artificio",
           "NightLiveComposit": "Live Composite notturno", "Landscape": "Paesaggio",
           "Sunset": "Tramonto", "BeachAndSnow": "Spiaggia e neve",
           "HDRShooting": "HDR", "Candle": "Lume di candela",
           "SilentShooting": "Silenzioso"}


@dataclass(frozen=True)
class CameraProp:
    name: str
    value: str
    values: tuple[str, ...]
    settable: bool


def parse_desclist(body: bytes) -> dict[str, CameraProp]:
    try:
        radice = ElementTree.fromstring(body)
    except (ElementTree.ParseError, TypeError, ValueError):
        return {}
    out: dict[str, CameraProp] = {}
    for desc in radice.iter("desc"):
        nome = (desc.findtext("propname") or "").strip()
        if not nome:
            continue
        out[nome] = CameraProp(
            nome, (desc.findtext("value") or "").strip(),
            tuple((desc.findtext("enum") or "").split()),
            "set" in (desc.findtext("attribute") or ""),
        )
    return out


def _decimal(text: str) -> str:
    if text.endswith(".0"):
        text = text[:-2]
    return text.replace(".", ",")


def format_value(name: str, value: str) -> str:
    try:
        if name == "shutspeedvalue":
            if value in _SHUTTER_WORDS:
                return _SHUTTER_WORDS[value]
            if value.endswith('"'):
                return f"{_decimal(value[:-1])} s"
            return f"1/{_decimal(value)}"
        if name == "focalvalue":
            return f"f/{_decimal(value)}"
        if name == "expcomp":
            numero = round(float(value), 1)
            if numero == 0:
                return "0 EV"
            segno = "+" if numero > 0 else MINUS
            return f"{segno}{_decimal(f'{abs(numero):.1f}')} EV"
        if name == "takemode":
            return _MODES.get(value, value)
        if name == "wbvalue":
            return _WB.get(value, value)
        if name == "drivemode":
            return _DRIVE.get(value, value)
        if name == "artfilter":
            return _ART.get(value, value)
        if name.startswith("ArtEffectType"):
            return _VARIANTS.get(value, value)
        if name == "SceneSub":
            return _SCENES.get(value, value)
    except (ValueError, TypeError):
        pass
    return value


def label(name: str) -> str:
    if name.startswith("ArtEffectType"):
        return "Variante"
    return _LABELS.get(name, name)


def _variant_name(filtro: str) -> str:
    return "ArtEffectType" + "".join(p.capitalize() for p in filtro.split("_"))


def visible_props(props: dict[str, CameraProp]) -> list[str]:
    nomi = [n for n in ORDER if n in props]
    modo = props["takemode"].value if "takemode" in props else ""
    if modo == "ART" and "artfilter" in props:
        nomi.append("artfilter")
        variante = _variant_name(props["artfilter"].value)
        if variante in props:
            nomi.append(variante)
    if modo == "Scene2" and "SceneSub" in props:
        nomi.append("SceneSub")
    return nomi


def is_automatic(name: str, props: dict[str, CameraProp]) -> bool:
    prop = props.get(name)
    if prop is None or not prop.settable or len(prop.values) <= 1:
        return True
    modo = props["takemode"].value if "takemode" in props else ""
    return name in _AUTOMATIC_BY_MODE.get(modo, EXPOSURE if name != "takemode" else ())


def aperture_allowed(limits: tuple[float, float] | None) -> Callable[[str], bool]:
    def ammesso(value: str) -> bool:
        if limits is None:
            return True
        try:
            f = float(value)
        except ValueError:
            return True
        return limits[0] - 0.05 <= f <= limits[1] + 0.05
    return ammesso


def step(values: tuple[str, ...], current: str, delta: int,
         allowed: Callable[[str], bool] | None = None) -> str:
    """Il valore a un passo nella direzione di `delta`, saltando i non ammessi.

    Fermo agli estremi. Da un valore fuori elenco o non ammesso si va al più
    vicino ammesso nella direzione richiesta (o al primo ammesso)."""
    ammessi = [v for v in values if allowed is None or allowed(v)]
    if not ammessi:
        return current
    if current in ammessi:
        i = ammessi.index(current) + (1 if delta > 0 else -1 if delta < 0 else 0)
        return ammessi[max(0, min(i, len(ammessi) - 1))]
    if current not in values:
        return ammessi[0] if delta >= 0 else ammessi[-1]
    pos = values.index(current)
    if delta > 0:
        avanti = [v for v in values[pos + 1:] if v in ammessi]
        return avanti[0] if avanti else ammessi[-1]
    indietro = [v for v in values[:pos] if v in ammessi]
    return indietro[-1] if indietro else ammessi[0]
```

- [ ] **Step 4: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_camprops.py -q && .venv/bin/python -m pytest -q`
Expected: tutti `passed`.

Se `test_automatic_rows_by_real_mode` fallisce per M sull'ISO o sul tempo, controlla l'elenco `desclist_M.xml` (`len(values)`): la regola è quella della specifica; non cambiare il test per farlo passare, riporta la discrepanza.

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/core/camprops.py tests/test_camprops.py
git commit -m "feat(core): impostazioni della fotocamera, testo italiano e regole

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Client e limiti dell'obiettivo nel live

**Files:**
- Modify: `olympus_connect/core/protocol.py` (helper di controllo, `_post_ok`, `camera_props`, `set_prop`)
- Modify: `olympus_connect/core/liveview.py` (`RtpPacket.lens_limits`, `FrameAssembler.lens_limits`)
- Test: `tests/test_protocol.py`, `tests/test_liveview.py` (in coda)

**Interfaces:**
- Consumes: `parse_desclist`, `CameraProp` (Task 1); `OlympusClient._session`, `HEADERS`, `TIMEOUT`, `_ERRORCODE`, `log`; le fixture `tests/fixtures/liveview/*.json` via `tests.live_helpers`.
- Produces: `OlympusClient.camera_props() -> dict[str, CameraProp]`, `OlympusClient.set_prop(name: str, value: str) -> None`; `RtpPacket.lens_limits() -> tuple[float, float] | None` (min, max f-number); `FrameAssembler.lens_limits: tuple[float, float] | None` (dell'ultimo fotogramma restituito).

- [ ] **Step 1: Scrivi i test che falliscono**

In coda a `tests/test_protocol.py`:

```python
# --- impostazioni ------------------------------------------------------------------

def test_camera_props_reads_the_real_desclist(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_camprop.cgi",
                      content=(REMOTE / "camprop_desclist.xml").read_bytes())
    props = OlympusClient().camera_props()
    assert props["isospeedvalue"].value == "200"
    assert requests_mock.last_request.qs == {"com": ["desc"], "propname": ["desclist"]}


def test_set_prop_posts_the_verified_body(requests_mock):
    requests_mock.post(f"{BASE_URL}/set_camprop.cgi",
                       content=b'<?xml version="1.0"?><response/>')
    OlympusClient().set_prop("isospeedvalue", "250")
    richiesta = requests_mock.last_request
    assert richiesta.method == "POST"
    assert richiesta.qs == {"com": ["set"], "propname": ["isospeedvalue"]}
    assert richiesta.body == b'<?xml version="1.0"?><set><value>250</value></set>'


def test_set_prop_escapes_the_value(requests_mock):
    requests_mock.post(f"{BASE_URL}/set_camprop.cgi", content=b"")
    OlympusClient().set_prop("shutspeedvalue", '60"')
    assert requests_mock.last_request.body == (
        b'<?xml version="1.0"?><set><value>60&quot;</value></set>')


def test_set_prop_refusal_has_the_code(requests_mock):
    requests_mock.post(f"{BASE_URL}/set_camprop.cgi", status_code=520,
                       content=b'<?xml version="1.0"?><errorcode>1003</errorcode>')
    with pytest.raises(ProtocolError) as info:
        OlympusClient().set_prop("isospeedvalue", "250")
    assert info.value.code == 1003


def test_set_prop_unreachable_camera(requests_mock):
    import requests as _requests
    requests_mock.post(f"{BASE_URL}/set_camprop.cgi", exc=_requests.exceptions.ConnectionError)
    with pytest.raises(CameraNotReachable):
        OlympusClient().set_prop("isospeedvalue", "250")
```

In coda a `tests/test_liveview.py`:

```python
# --- limiti dell'obiettivo (TLV 9) ------------------------------------------------

def _con_tlv9(massimo, minimo):
    return rtp_header(1, 0, marker=True, tlvs=[
        (9, struct.pack(">III", massimo, minimo, minimo)),
        (26, struct.pack(">II", 640, 480) + b"\x00" * 16)]) + synthetic_jpeg(100)


def test_lens_limits_from_tlv_9():
    assert parse_rtp(_con_tlv9(220, 35)).lens_limits() == (3.5, 22.0)


def test_lens_limits_missing_or_zero_is_none():
    assert parse_rtp(rtp_header(1, 0, marker=True) + b"x").lens_limits() is None
    assert parse_rtp(_con_tlv9(0, 0)).lens_limits() is None


def test_lens_limits_of_the_real_capture():
    pacchetti, _ = packets_from_fixture("1024x0768")
    assembler = FrameAssembler()
    for p in pacchetti:
        assembler.feed(p)
    assert assembler.lens_limits == (3.6, 22.0)


def test_assembler_follows_the_lens_limits():
    assembler = FrameAssembler()
    assembler.feed(_con_tlv9(220, 35))
    assert assembler.lens_limits == (3.5, 22.0)
    assembler.feed(_con_tlv9(220, 56))
    assert assembler.lens_limits == (5.6, 22.0)
```

(Se in `tests/test_protocol.py` mancano `REMOTE` o `CameraNotReachable`, aggiungili agli import in testa: `REMOTE = Path(__file__).parent / "fixtures" / "remote"` esiste già dalla parte 2 del telecomando; `CameraNotReachable` è già importato.)

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_protocol.py tests/test_liveview.py -q`
Expected: failed con `AttributeError: 'OlympusClient' object has no attribute 'camera_props'` / `'set_prop'` e `AttributeError: 'RtpPacket' object has no attribute 'lens_limits'`.

- [ ] **Step 3: Client**

In `olympus_connect/core/protocol.py`:

1. Import: `from xml.sax.saxutils import escape` e `from .camprops import CameraProp, parse_desclist`.
2. Estrai il controllo dello stato da `_get_ok` in un metodo e aggiungi la POST:

```python
    def _check(self, path: str, response: requests.Response) -> requests.Response:
        if response.status_code != 200:
            trovato = _ERRORCODE.search(response.content or b"")
            codice = int(trovato.group(1)) if trovato else None
            dettaglio = f", codice {codice}." if codice is not None else "."
            raise ProtocolError(
                f"{path} ha risposto HTTP {response.status_code}{dettaglio}",
                response.content, code=codice,
            )
        return response

    def _get_ok(self, path: str, **params) -> requests.Response:
        return self._check(path, self._get(path, **params))

    def _post_ok(self, path: str, body: bytes, **params) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = self._session.post(url, headers=HEADERS, params=params,
                                          data=body, timeout=TIMEOUT)
        except requests.exceptions.ConnectionError as exc:
            raise CameraNotReachable("La fotocamera non risponde su 192.168.0.10.") from exc
        except requests.exceptions.Timeout as exc:
            raise CameraAsleep("La fotocamera non ha risposto entro il tempo previsto.") from exc
        return self._check(path, response)
```

3. Dopo `touch_area`:

```python
    def camera_props(self) -> dict[str, CameraProp]:
        """Tutte le impostazioni con valore, valori ammessi e modificabilità."""
        return parse_desclist(
            self._get_ok("get_camprop.cgi", com="desc", propname="desclist").content)

    def set_prop(self, name: str, value: str) -> None:
        """Imposta una proprietà. Formato verificato il 2026-11-08; la risposta
        non dice l'esito, che si legge rileggendo l'elenco."""
        corpo = f'<?xml version="1.0"?><set><value>{escape(value, {chr(34): "&quot;"})}</value></set>'
        log.debug("set_camprop %s=%s", name, value)
        self._post_ok("set_camprop.cgi", corpo.encode(), com="set", propname=name)
```

- [ ] **Step 4: Limiti dell'obiettivo**

In `olympus_connect/core/liveview.py`:

1. Dopo `TLV_FRAME_SIZE = 26`: `TLV_LENS = 9`.
2. In `RtpPacket`, dopo `frame_size`:

```python
    def lens_limits(self) -> tuple[float, float] | None:
        """(diaframma più aperto alla focale attuale, più chiuso), dal TLV 9
        in decimi di f-stop. Misurato: [220, 35, 35] a 12 mm → (3.5, 22.0)."""
        valore = (self.tlvs or {}).get(TLV_LENS)
        if valore is None or len(valore) < 8:
            return None
        massimo, minimo = struct.unpack(">II", valore[:8])
        if not massimo or not minimo:
            return None
        return minimo / 10, massimo / 10
```

3. In `FrameAssembler.__init__`: `self._pending_lens: tuple[float, float] | None = None` e `self.lens_limits: tuple[float, float] | None = None`; dove si imposta `self._pending_size = p.frame_size()` aggiungi `self._pending_lens = p.lens_limits()`; dove si imposta `self.frame_size = self._pending_size` aggiungi `self.lens_limits = self._pending_lens`.

- [ ] **Step 5: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_protocol.py tests/test_liveview.py -q && .venv/bin/python -m pytest -q`
Expected: tutti `passed`. Se `test_lens_limits_of_the_real_capture` dà un valore diverso da `(3.6, 22.0)`, riporta il valore letto (l'ultimo fotogramma della cattura a 1024 è stato registrato a 13 mm).

- [ ] **Step 6: Commit**

```bash
git add olympus_connect/core/protocol.py olympus_connect/core/liveview.py tests/test_protocol.py tests/test_liveview.py
git commit -m "feat(core): lettura e modifica delle impostazioni, limiti dell'obiettivo dal live

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Worker

**Files:**
- Modify: `olympus_connect/gui/workers.py` (`RemoteWorker`)
- Modify: `olympus_connect/gui/liveview.py` (`LiveViewWorker`)
- Test: `tests/test_workers.py`, `tests/test_liveview_worker.py` (in coda)

**Interfaces:**
- Consumes: `camera_props`, `set_prop` (Task 2); `FrameAssembler.lens_limits` (Task 2).
- Produces: `RemoteWorker.props_read = pyqtSignal(object)` (dict), `RemoteWorker.props_failed = pyqtSignal(str)`, slot `read_props()`, slot `set_prop(str, str)`; `LiveViewWorker.lens_changed = pyqtSignal(object)` (`tuple[float, float] | None`), emesso solo quando cambia.

- [ ] **Step 1: Scrivi i test che falliscono**

In coda a `tests/test_workers.py` (la classe `_RemoteClient` e la funzione `_remote` esistono già: aggiungi ai `_RemoteClient` esistenti i due metodi tramite una sottoclasse):

```python
# --- impostazioni ------------------------------------------------------------------

class _PropsClient(_RemoteClient):
    def __init__(self, fail=None, props=None):
        super().__init__(fail=fail)
        self.props = props or {"isospeedvalue": "200"}

    def camera_props(self):
        self._call("camera_props")
        return dict(self.props)

    def set_prop(self, name, value):
        self._call("set_prop", name, value)
        self.props[name] = value


def _props_worker(client):
    worker, ev = _remote(client)
    ev["props"], ev["props_failed"] = [], []
    worker.props_read.connect(ev["props"].append)
    worker.props_failed.connect(ev["props_failed"].append)
    return worker, ev


def test_read_props_emits_the_list(qapp):
    worker, ev = _props_worker(_PropsClient())
    worker.read_props()
    assert ev["props"] == [{"isospeedvalue": "200"}]


def test_set_prop_always_rereads(qapp):
    client = _PropsClient()
    worker, ev = _props_worker(client)
    worker.set_prop("isospeedvalue", "250")
    assert [c[0] for c in client.calls] == ["set_prop", "camera_props"]
    assert ev["props"] == [{"isospeedvalue": "250"}]


def test_refused_set_prop_reports_and_still_rereads(qapp):
    client = _PropsClient(fail={"set_prop": ProtocolError("x", code=1003)})
    worker, ev = _props_worker(client)
    worker.set_prop("isospeedvalue", "250")
    assert ev["failed"] and ev["props"] == [{"isospeedvalue": "200"}]


def test_failed_read_reports_props_failed(qapp):
    worker, ev = _props_worker(_PropsClient(fail={"camera_props": CameraNotReachable("x")}))
    worker.read_props()
    assert ev["props"] == [] and ev["props_failed"]
```

In coda a `tests/test_liveview_worker.py`:

```python
def test_lens_limits_are_signalled_when_they_change(qapp, live):
    import struct
    from tests.live_helpers import rtp_header
    make, fotocamera = live
    worker, eventi, destinazione = make()
    limiti = []
    worker.lens_changed.connect(limiti.append)

    def fotogramma(seq, minimo):
        jpeg = _jpeg(40)
        tlvs = [(9, struct.pack(">III", 220, minimo, minimo)),
                (26, struct.pack(">II", 40, 30) + b"\x00" * 16)]
        return rtp_header(seq, 0, marker=True, tlvs=tlvs) + jpeg

    fotocamera.sendto(fotogramma(1, 35), destinazione)
    fotocamera.sendto(fotogramma(2, 35), destinazione)
    fotocamera.sendto(fotogramma(3, 56), destinazione)
    assert _attendi(qapp, lambda: len(limiti) == 2)
    assert limiti == [(3.5, 22.0), (5.6, 22.0)]
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_workers.py tests/test_liveview_worker.py -q`
Expected: failed con `AttributeError: … 'props_read'` e `… 'lens_changed'`.

- [ ] **Step 3: `RemoteWorker`**

In `olympus_connect/gui/workers.py`, nella classe `RemoteWorker`, sotto `failed = pyqtSignal(str)`:

```python
    props_read = pyqtSignal(object)       # dict[str, CameraProp]
    props_failed = pyqtSignal(str)
```

e dopo lo slot `stop_movie`:

```python
    @pyqtSlot()
    def read_props(self) -> None:
        try:
            self.props_read.emit(self._client.camera_props())
        except Exception as exc:
            self.props_failed.emit(describe_error(exc))

    @pyqtSlot(str, str)
    def set_prop(self, name: str, value: str) -> None:
        """Rilegge sempre: anche dopo un rifiuto il pannello torna al valore vero."""
        try:
            self._client.set_prop(name, value)
        except Exception as exc:
            self.failed.emit(describe_error(exc))
        self.read_props()
```

- [ ] **Step 4: `LiveViewWorker`**

In `olympus_connect/gui/liveview.py`: sotto `resumed = pyqtSignal()` aggiungi `lens_changed = pyqtSignal(object)`; in `_loop`, prima del `while`, `limiti_visti: tuple | None = None`; dentro il ramo in cui `jpeg is not None` e l'immagine è valida, dopo `self._deliver(immagine)`:

```python
                        if assembler.lens_limits != limiti_visti:
                            limiti_visti = assembler.lens_limits
                            self.lens_changed.emit(limiti_visti)
```

- [ ] **Step 5: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_workers.py tests/test_liveview_worker.py -q` (tre volte il file del live) `&& .venv/bin/python -m pytest -q`
Expected: tutti `passed`.

- [ ] **Step 6: Commit**

```bash
git add olympus_connect/gui/workers.py olympus_connect/gui/liveview.py tests/test_workers.py tests/test_liveview_worker.py
git commit -m "feat(gui): lettura e modifica delle impostazioni nel worker, limiti dell'obiettivo dal live

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Selettore e sezione (`gui/props.py`)

**Files:**
- Create: `olympus_connect/gui/props.py`
- Modify: `olympus_connect/gui/theme.py` (`STYLESHEET`, dopo la regola `#shotHeadline`)
- Test: `tests/test_props.py`

**Interfaces:**
- Consumes: `CameraProp`, `format_value`, `label`, `visible_props`, `is_automatic`, `aperture_allowed`, `step` (Task 1); `SectionLabel` (`gui/panels.py`); `label_column_width` (`gui/shot.py`); `icons.icon` con `chevron-left`, `chevron-right`; `theme`.
- Produces: `PropStepper(name: str)` con segnale `changed(str, str)`, `DEBOUNCE_MS = 250`, metodi `set_prop(prop: CameraProp, automatic: bool)`, `set_allowed(pred | None)`, `step(delta: int)`, `choose(value: str)`, `shown_value() -> str`, `confirmed_value() -> str`, `is_pending() -> bool`, `is_automatic() -> bool`, `menu_values() -> list[str]`; `PropsSection` con segnale `prop_changed(str, str)`, metodi `set_props(props: dict)`, `set_unavailable(message: str)`, `set_lens_limits(limits)`, `set_enabled(bool)`, `row(name) -> PropStepper | None`, `names() -> list[str]`, `message() -> str`.

- [ ] **Step 1: Scrivi i test che falliscono**

Create `tests/test_props.py`:

```python
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
    s, _ = _stepper()
    s.step(1)
    s.set_prop(CameraProp("isospeedvalue", "250", ISO.values, True), False)
    assert _attendi(qapp, lambda: not s.is_pending())
    assert s.shown_value() == "250"
    s.step(1)
    assert _attendi(qapp, lambda: True, 0.4)
    s.set_prop(ISO, False)              # rifiutato: la fotocamera è rimasta a 200
    assert _attendi(qapp, lambda: s.shown_value() == "200" and not s.is_pending())


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
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_props.py -q`
Expected: errore di raccolta, `ModuleNotFoundError: No module named 'olympus_connect.gui.props'`.

- [ ] **Step 3: Stili**

In `olympus_connect/gui/theme.py`, nel foglio `STYLESHEET`, dopo la riga di `#shotHeadline`:

```css
QPushButton#propValue {{ background: transparent; border: none; color: {TEXT}; font-size: {SIZE_SMALL}px; padding: 2px 4px; text-align: center; }}
QPushButton#propValue:hover {{ background: {SURFACE}; }}
QPushButton#propValue[pending="true"] {{ color: {TEXT_FAINT}; }}
QPushButton#propValue:disabled {{ color: {TEXT_FAINT}; }}
QPushButton#propArrow {{ background: transparent; border: none; padding: 0px; min-width: 20px; max-width: 20px; }}
QPushButton#propArrow:hover {{ background: {SURFACE}; }}
```

- [ ] **Step 4: Scrivi il modulo**

Create `olympus_connect/gui/props.py`:

```python
"""Impostazioni della fotocamera nel pannello del telecomando.

Un selettore per riga, come una ghiera: frecce, rotella del mouse, elenco
al clic. La fotocamera vuole un comando alla volta: mentre si gira, il
valore a schermo cambia subito (attenuato) e il comando parte 250 ms dopo
l'ultimo passo, con il solo valore finale. Il valore torna normale quando
la rilettura dell'elenco lo conferma.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from ..core.camprops import (
    CameraProp, aperture_allowed, format_value, is_automatic, label, step, visible_props,
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
        self._auto = QLabel("automatico")
        self._auto.setObjectName("faint")
        riga.addWidget(self._prev)
        riga.addWidget(self._value, 1)
        riga.addWidget(self._next)
        riga.addWidget(self._auto)

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
        if not self._timer.isActive():
            self._shown = prop.value
        self._refresh()

    def set_allowed(self, allowed: Callable[[str], bool] | None) -> None:
        self._allowed = allowed
        self._refresh()

    # -- interazione --------------------------------------------------------------

    def step(self, delta: int) -> None:
        if self._prop is None or self._automatic or not self.isEnabled():
            return
        nuovo = step(self._prop.values, self._shown, delta, self._allowed)
        if nuovo != self._shown:
            self._shown = nuovo
            self._refresh()
            self._timer.start()

    def choose(self, value: str) -> None:
        if self._prop is None or self._automatic or not self.isEnabled():
            return
        self._shown = value
        self._refresh()
        self._timer.start()

    def wheelEvent(self, event) -> None:
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

    def _send(self) -> None:
        if self._shown != self._confirmed:
            self.changed.emit(self._name, self._shown)

    # -- lettura (test e finestra) --------------------------------------------------

    def menu_values(self) -> list[str]:
        if self._prop is None:
            return []
        return [v for v in self._prop.values if self._allowed is None or self._allowed(v)]

    def shown_value(self) -> str:
        return self._shown

    def confirmed_value(self) -> str:
        return self._confirmed

    def is_pending(self) -> bool:
        return self._shown != self._confirmed

    def is_automatic(self) -> bool:
        return self._automatic

    def _refresh(self) -> None:
        self._value.setText(format_value(self._name, self._shown) if self._shown else "—")
        self._value.setProperty("pending", self.is_pending())
        self._value.style().unpolish(self._value)
        self._value.style().polish(self._value)
        self._value.setEnabled(not self._automatic)
        for freccia in (self._prev, self._next):
            freccia.setVisible(not self._automatic)
        self._auto.setVisible(self._automatic)


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

    def set_props(self, props: dict[str, CameraProp]) -> None:
        self._message.hide()
        nomi = visible_props(props)
        for nome in [n for n in self._rows if n not in nomi]:
            riga = self._rows.pop(nome)
            self._rows_box.removeWidget(riga)
            riga.deleteLater()
        for nome in nomi:
            if nome not in self._rows:
                riga = PropStepper(nome)
                riga.changed.connect(self.prop_changed)
                self._rows[nome] = riga
            self._rows_box.removeWidget(self._rows[nome])
        for nome in nomi:
            riga = self._rows[nome]
            self._rows_box.addWidget(riga)
            riga.set_prop(props[nome], is_automatic(nome, props))
            riga.setEnabled(self._enabled)
        self._order = nomi
        if "focalvalue" in self._rows:
            self._rows["focalvalue"].set_allowed(aperture_allowed(self._limits))

    def set_unavailable(self, message: str) -> None:
        for riga in self._rows.values():
            self._rows_box.removeWidget(riga)
            riga.deleteLater()
        self._rows.clear()
        self._order = []
        self._message.setText(message)
        self._message.show()

    def set_lens_limits(self, limits: tuple[float, float] | None) -> None:
        self._limits = limits
        if "focalvalue" in self._rows:
            self._rows["focalvalue"].set_allowed(aperture_allowed(limits))

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        for riga in self._rows.values():
            riga.setEnabled(enabled)

    def row(self, name: str) -> PropStepper | None:
        return self._rows.get(name)

    def names(self) -> list[str]:
        return list(self._order)

    def message(self) -> str:
        return self._message.text() if not self._message.isHidden() else ""
```

- [ ] **Step 5: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_props.py -q && .venv/bin/python -m pytest -q`
Expected: tutti `passed`.

- [ ] **Step 6: Commit**

```bash
git add olympus_connect/gui/props.py olympus_connect/gui/theme.py tests/test_props.py
git commit -m "feat(gui): selettore delle impostazioni a freccette e sezione del pannello

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Nel pannello e nella finestra

**Files:**
- Modify: `olympus_connect/gui/remote.py` (`RemotePanel`)
- Modify: `olympus_connect/gui/main_window.py`
- Test: `tests/test_remote_view.py`, `tests/test_main_window.py` (in coda)

**Interfaces:**
- Consumes: `PropsSection` (Task 4); `RemoteWorker.read_props`, `set_prop`, `props_read`, `props_failed` (Task 3); `LiveViewWorker.lens_changed` (Task 3).
- Produces: `RemotePanel.props: PropsSection` (attributo pubblico); in `MainWindow` i segnali `remote_read_props()` e `remote_set_prop(str, str)`, gli slot `on_remote_props(object)`, `on_remote_props_failed(str)`, `on_lens_changed(object)`, `on_remote_prop_changed(str, str)`.

- [ ] **Step 1: Scrivi i test che falliscono**

In coda a `tests/test_remote_view.py`:

```python
def test_panel_has_the_settings_section_and_disables_it(qapp):
    pannello = RemotePanel()
    assert pannello.props is not None
    pannello.set_busy(True)
    assert pannello.props._enabled is False
    pannello.set_busy(False)
    assert pannello.props._enabled is True
    pannello.set_recording(True)
    assert pannello.props._enabled is False
```

In coda a `tests/test_main_window.py` (la classe `_RemoteStubClient` e `_FakeLive` esistono già):

```python
# --- impostazioni dal PC ------------------------------------------------------------

from olympus_connect.core.camprops import parse_desclist  # noqa: E402

_DESCLIST = (Path(__file__).parent / "fixtures" / "remote" / "desclist_A.xml").read_bytes()


class _PropsStubClient(_RemoteStubClient):
    valori: dict = {}

    def camera_props(self):
        _RecordingClient.chiamate.append(("camera_props",))
        props = parse_desclist(_DESCLIST)
        for nome, valore in _PropsStubClient.valori.items():
            p = props[nome]
            props[nome] = type(p)(p.name, valore, p.values, p.settable)
        return props

    def set_prop(self, name, value):
        _RecordingClient.chiamate.append(("set_prop", name, value))
        _PropsStubClient.valori[name] = value


@pytest.fixture
def props_win(qapp, settings):
    _RecordingClient.chiamate = []
    _FakeLive.istanze = []
    _PropsStubClient.valori = {}
    _RemoteStubClient.revisione = _jpeg_valido()
    win = MainWindow(client_factory=_PropsStubClient,
                     network_factory=lambda ssid: FakeNetwork(all_green()),
                     settings=settings, live_factory=_FakeLive)
    win.resize(1200, 700)
    win.show()
    win.on_connected("E-M10MarkIV")
    yield win
    win.close()


def test_entering_reads_the_settings(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("isospeedvalue") is not None)
    assert win._remote_panel.props.row("isospeedvalue").confirmed_value() == "200"


def test_a_change_reaches_the_camera_and_is_confirmed(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("isospeedvalue") is not None)
    win._remote_panel.props.row("isospeedvalue").step(1)
    assert _attendi(qapp, lambda: ("set_prop", "isospeedvalue", "250") in _RecordingClient.chiamate)
    assert _attendi(qapp, lambda: win._remote_panel.props.row("isospeedvalue").confirmed_value() == "250")


def test_settings_are_disabled_while_recording(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("isospeedvalue") is not None)
    win._remote_panel._movie.click()
    assert _attendi(qapp, win._remote_panel.is_recording)
    assert not win._remote_panel.props.row("isospeedvalue").isEnabled()


def test_lens_limits_reach_the_aperture_row(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("focalvalue") is not None)
    _FakeLive.istanze[-1].lens_changed.emit((3.5, 22.0))
    assert _attendi(qapp, lambda: "1.0" not in win._remote_panel.props.row("focalvalue").menu_values())


def test_failed_read_shows_unavailable(props_win, qapp, monkeypatch):
    def rompi(self):
        raise RuntimeError("x")
    monkeypatch.setattr(_PropsStubClient, "camera_props", rompi)
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.message() != "")
```

Aggiungi a `_FakeLive` (in `tests/test_main_window.py`) il segnale `lens_changed = pyqtSignal(object)`, accanto agli altri.

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_remote_view.py tests/test_main_window.py -q -k "settings or props or lens or change or reads"`
Expected: failed con `AttributeError: 'RemotePanel' object has no attribute 'props'`.

- [ ] **Step 3: `RemotePanel`**

In `olympus_connect/gui/remote.py`, nel costruttore di `RemotePanel`: sostituisci il blocco che aggiunge `SectionLabel("Fotocamera")` e la griglia con la riga «Modalità» (`self._take_mode`) con:

```python
        colonna.addSpacing(8)
        self.props = PropsSection()
        colonna.addWidget(self.props)
        self._take_mode_text = "—"
```

(import `from .props import PropsSection` in testa). Cambia i metodi:

```python
    def set_take_mode(self, mode: str) -> None:
        self._take_mode_text = mode or "—"

    def take_mode_text(self) -> str:
        return self._take_mode_text
```

e in fondo a `_refresh` (il metodo che abilita Scatta e Registra):

```python
        self.props.set_enabled(not self._busy and not self._recording)
```

La riga «Modalità» in sola lettura sparisce: la mostra il selettore. Il suggerimento «Clic sull'immagine per mettere a fuoco.» resta, dopo la sezione.

- [ ] **Step 4: `MainWindow`**

In `olympus_connect/gui/main_window.py`:

1. Sotto `remote_leave = pyqtSignal()`:

```python
    remote_read_props = pyqtSignal()
    remote_set_prop = pyqtSignal(str, str)
```

2. In `_build_ui`, accanto agli altri collegamenti del pannello: `self._remote_panel.props.prop_changed.connect(self.on_remote_prop_changed)`.

3. In `enter_remote`, dove si collegano i segnali del `RemoteWorker`:

```python
        remote.props_read.connect(self.on_remote_props)
        remote.props_failed.connect(self.on_remote_props_failed)
        self.remote_read_props.connect(remote.read_props, coda)
        self.remote_set_prop.connect(remote.set_prop, coda)
```

e dove si collegano quelli del live: `live.lens_changed.connect(self.on_lens_changed)`.

4. In `_teardown_remote`, aggiungi `self.remote_read_props` e `self.remote_set_prop` alla tupla dei segnali da scollegare.

5. In `on_remote_entered`, in fondo: `self.remote_read_props.emit()`.

6. Nuovi slot, nella sezione del telecomando:

```python
    @pyqtSlot(object)
    def on_remote_props(self, props: object) -> None:
        if not self._remote_active:
            return
        self._remote_panel.props.set_props(props)

    @pyqtSlot(str)
    def on_remote_props_failed(self, message: str) -> None:
        if not self._remote_active:
            return
        self._remote_panel.props.set_unavailable("Impostazioni non disponibili")
        self.statusBar().showMessage(message)

    @pyqtSlot(object)
    def on_lens_changed(self, limits: object) -> None:
        self._remote_panel.props.set_lens_limits(limits)

    def on_remote_prop_changed(self, name: str, value: str) -> None:
        if (not self._remote_active or self._remote_leaving or self._closing
                or not self._remote_entered or self._remote_panel.is_recording()):
            return
        self.statusBar().showMessage("Impostazione inviata alla fotocamera…", 2000)
        self.remote_set_prop.emit(name, value)
```

(Se `self._closing` non esiste con questo nome, usa il flag di chiusura che la finestra già ha: cerca `_closing` in `main_window.py`.)

- [ ] **Step 5: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_remote_view.py tests/test_main_window.py -q && .venv/bin/python -m pytest -q` (la suite completa tre volte)
Expected: tutti `passed`, nessun crash nativo.

- [ ] **Step 6: Guarda il pannello a schermo**

Con uno script usa e getta nella cartella scratchpad (mai nel repository), offscreen, tema e font come `main()`, finestra 1366×740, `_PropsStubClient` e `_FakeLive` dei test: entra nel telecomando, consegna un fotogramma, poi uno screenshot in modalità A (diaframma regolabile, tempo «automatico») e uno dopo aver portato `takemode` a `ART` (righe Filtro e Variante). Controlla: tutte le righe visibili a 1366×740 senza tagli, valori centrati, frecce allineate, righe automatiche attenuate.

- [ ] **Step 7: Commit**

```bash
git add olympus_connect/gui/remote.py olympus_connect/gui/main_window.py tests/test_remote_view.py tests/test_main_window.py
git commit -m "feat(gui): impostazioni dal PC nel pannello del telecomando

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Dall'inizio alla fine, documentazione

**Files:**
- Modify: `tests/test_remote_e2e.py` (fotocamera finta: POST `set_camprop`, `desclist` con i valori aggiornati; test nuovo)
- Modify: `README.md`, `DESIGN.md`

- [ ] **Step 1: La fotocamera finta accetta le impostazioni**

Nella fixture `camera` di `tests/test_remote_e2e.py`:
- uno stato `stato["valori"] = {}`;
- la risposta a `get_camprop.cgi` con `propname=desclist` parte dalla fixture vera `camprop_desclist.xml` e sostituisce il `<value>` delle proprietà presenti in `stato["valori"]`, per esempio con `xml.etree.ElementTree` (per ogni `desc` il cui `propname` è in `stato["valori"]`, imposta il testo di `value`);
- un metodo `do_POST` nel `Handler`: legge `Content-Length` byte, per `set_camprop.cgi` estrae il valore da `<value>…</value>` (con `xml.etree.ElementTree.fromstring(corpo).findtext("value")`), lo salva in `stato["valori"][propname]`, registra `("set_camprop.cgi", qs)` in `stato["richieste"]` e risponde 200 con `<?xml version="1.0"?><response/>`; ogni altra POST risponde 404.

- [ ] **Step 2: Scrivi il test**

In coda a `tests/test_remote_e2e.py`, usando la stessa costruzione della finestra del test esistente:

```python
def test_a_setting_changed_from_the_pc_reaches_the_camera(qapp, camera, tmp_path):
    impostazioni = QSettings(str(tmp_path / "e2e.ini"), QSettings.Format.IniFormat)
    impostazioni.setValue("dest_root", str(tmp_path / "dest"))
    finestra = MainWindow(
        client_factory=lambda: OlympusClient(base_url=camera["base_url"]),
        network_factory=_Net,
        settings=impostazioni,
        live_factory=lambda: LiveViewWorker(camera=camera["udp"], bind_host="127.0.0.1"),
    )
    try:
        finestra.resize(1366, 740)
        finestra.show()
        finestra.on_refresh()
        assert _attendi(qapp, lambda: finestra._model.rowCount() == 1161)
        assert _attendi(qapp, lambda: finestra._view_remote.isEnabled())
        finestra._view_remote.click()
        sezione = finestra._remote_panel.props
        assert _attendi(qapp, lambda: sezione.row("isospeedvalue") is not None), \
            "le impostazioni non sono arrivate al pannello"
        sezione.row("isospeedvalue").step(1)
        assert _attendi(qapp, lambda: camera["valori"].get("isospeedvalue") == "250"), \
            "la modifica non è arrivata alla fotocamera"
        assert _attendi(qapp, lambda: sezione.row("isospeedvalue").confirmed_value() == "250"), \
            "il pannello non ha riletto il nuovo valore"
        finestra._view_grid.click()
        assert _attendi(qapp, lambda: not finestra._remote_active)
    finally:
        finestra.close()
        qapp.processEvents()
```

- [ ] **Step 3: Verifica che passi e che fallisca senza la modifica**

Run: `.venv/bin/python -m pytest tests/test_remote_e2e.py -q` (tre volte) — Expected: tutti `passed`.
Commenta temporaneamente in `main_window.py` la riga `self.remote_set_prop.connect(remote.set_prop, coda)`; il nuovo test deve fallire con «la modifica non è arrivata alla fotocamera». Ripristina la riga (`git diff olympus_connect/` vuoto) e rilancia: passa.

- [ ] **Step 4: Suite completa**

Run: `.venv/bin/python -m pytest -q` (tre volte) — Expected: sempre tutti `passed`; annota il totale.

- [ ] **Step 5: Documentazione**

`README.md`:
- nella tabella «Cosa funziona, verificato sul campo», dopo la riga del telecomando: `| Impostazioni dal PC: modalità, tempo, diaframma, ISO, compensazione, bilanciamento, scatto, filtri | ✅ diaframma limitato all'obiettivo e allo zoom |`;
- nella sezione «Telecomando», un paragrafo: «Sotto Scatta e Registra, la sezione **Impostazioni** mostra modalità, tempo, diaframma, ISO, compensazione, bilanciamento e modalità di scatto (in ART anche filtro e variante, in Scene la scena). Le frecce, la rotella del mouse o un clic sul valore li cambiano; il comando parte un attimo dopo l'ultimo passo. Le righe che la modalità lascia alla fotocamera sono attenuate («automatico»). Il diaframma offre solo i valori possibili con l'obiettivo e lo zoom del momento.»;
- nello schema della struttura: `camprops.py` sotto `core/`, `props.py` sotto `gui/`;
- nella frase «Fuori scope…» togli «Il cambio delle impostazioni di scatto dal PC (ISO, tempi, diaframmi) non c'è ancora: si cambiano sulla fotocamera.»;
- il numero dei test.

`DESIGN.md`, nella voce del Remote mode, aggiungi: «A **Settings** section below the shoot controls: one stepper row per camera setting (label column shared with the inspector grids; `‹ value ›` with 14 px chevrons, mouse wheel, click opens the full list); a changed value is shown faint until the camera confirms it, and is sent 250 ms after the last step; rows the current mode leaves to the camera are faint with «automatico» and no arrows; the aperture list follows the lens and zoom.»

- [ ] **Step 6: Commit**

```bash
git add tests/test_remote_e2e.py README.md DESIGN.md
git commit -m "test: impostazioni dal PC dall'inizio alla fine; docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Prova con la fotocamera vera (con l'utente)

Non è codice. Il controllore guida l'utente; ricordare che **mentre il PC è collegato alla fotocamera non c'è Internet**: l'utente esegue, torna in linea, riferisce.

- [ ] **Step 1: L'utente prova**

Prima lo script delle virgolette (forma del tempo `60"` nel comando; rimette tutto com'era):

```bash
nmcli connection up olympus-connect-E-M10MKIV-P-<seriale>
.venv/bin/python diag-downloads/prova_virgolette.py
OLYMPUS_DEBUG=1 .venv/bin/python -m olympus_connect 2>&1 | tee diag-downloads/prova_app_impostazioni.txt
nmcli connection down olympus-connect-E-M10MKIV-P-<seriale>
```

Sequenza nell'app, dopo la revisione finale:
1. Telecomando: le righe compaiono; il **Diaframma** resta sul valore attuale finché arriva il live, poi l'elenco segue l'obiettivo.
2. **Modalità** P, A, S, M, iAuto, ART, Scene: le righe automatiche cambiano come previsto? In iAuto/Scene bilanciamento e scatto restano modificabili: la fotocamera li rispetta? La rotella sopra la riga Modalità non fa nulla.
3. In A: **Diaframma** con la rotella e zoom da un capo all'altro (l'elenco segue? a 12 mm compare f/3,5? fra 14 e 20 mm compare f/4,5 come sul monitor?).
4. **ISO**, **Compensazione**, **Tempo** in S; nel monitor della fotocamera gli stessi valori.
5. In **M**: **Tempo** `2"` e poi uno **scatto**; poi `60"` e uno **scatto** (l'app aspetta la fine della posa? i pulsanti tornano attivi solo dopo? la foto si vede?).
6. **Bilanciamento** e **Scatto**: i nomi coincidono con la fotocamera? autoscatto e Live Composite/Time/Bulb non compaiono. Uno scatto in raffica H.
7. **ART**: Filtro e Variante compaiono, i nomi coincidono.
8. Esci dal telecomando: la fotocamera torna a seguire la ghiera fisica?

- [ ] **Step 2: Annota l'esito**

In coda a `docs/field-report-2026-09-24.md` una sezione «Impostazioni dal PC con l'app (2026-11-DD)» con l'esito di ogni punto e le eventuali differenze di nome (bilanciamento, filtri, scene) da correggere in `core/camprops.py` con un test. Commit:

```bash
git add docs/field-report-2026-09-24.md
git commit -m "docs: prova delle impostazioni dal PC con la fotocamera vera

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
