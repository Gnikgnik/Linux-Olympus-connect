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


# Valori per cui il percorso di scatto (starttake e subito stoptake) non è
# stato costruito, e che la prova del 2026-11-08 non ha mai usato apposta:
# le pose Live (Live Composite, Live Time, Live Bulb) e ogni autoscatto.
# Non compaiono nell'elenco e i passi li saltano; se la fotocamera è già su
# uno di questi (scelto sul corpo), la riga lo mostra come valore attuale e
# il primo passo porta al valore selezionabile più vicino. I tempi lunghi
# come 60" restano selezionabili: da verificare sul campo.
HIDDEN: dict[str, Callable[[str], bool]] = {
    "shutspeedvalue": lambda v: v in {"livecomp", "livetime", "livebulb"},
    "drivemode": lambda v: "selftimer" in v,
}


def is_hidden(name: str, value: str) -> bool:
    regola = HIDDEN.get(name)
    return regola is not None and regola(value)


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
