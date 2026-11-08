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
# come 60" restano selezionabili: provati sul campo, vedi «pose lunghe».
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


# -- pose lunghe ------------------------------------------------------------
#
# Misurato sul campo (E-M10 Mark IV, 2026-11-08, in M, riduzione rumore
# «auto» con soglia NoiseReductionExposureTime=1"): starttake risponde dopo
# 16,8 s con 8" e dopo 129,0 s con 60". Oltre la soglia la fotocamera
# aggiunge un fotogramma scuro lungo quanto la posa: occupata circa il doppio
# dell'esposizione più qualche secondo. Nel frattempo non risponde a nessuna
# altra richiesta HTTP e il live smette di mandare pacchetti; il Wi-Fi resta
# su. Con 2" starttake risponde dopo circa 2 s.

SHOT_MARGIN = 15.0          # secondi oltre l'occupazione stimata
SHOT_WORST_CASE = 135.0     # 60" automatico più il fotogramma scuro (129 s sul campo), più margine
_NR_THRESHOLD = 1.0         # soglia della riduzione rumore quando l'elenco non la dà


def exposure_seconds(value: str) -> float | None:
    """La durata in secondi di un valore di tempo dell'elenco.

    '60"' e '2.5"' sono secondi; '1.3' e '250' sono frazioni (1/1,3 s,
    1/250 s). None per le pose Live, Bulb e ogni valore non leggibile."""
    testo = (value or "").strip()
    try:
        if testo.endswith('"'):
            secondi = float(testo[:-1])
        else:
            secondi = 1 / float(testo)
    except (ValueError, ZeroDivisionError):
        return None
    return secondi if secondi > 0 else None


def shot_busy_seconds(props: dict[str, CameraProp]) -> float | None:
    """Per quanto la fotocamera resta occupata dopo starttake, stimato.

    Il tempo scelto, raddoppiato per il fotogramma scuro quando la riduzione
    rumore non è «off» e la posa arriva alla soglia
    NoiseReductionExposureTime. Senza la soglia nell'elenco vale 1 s; senza
    noisereduction la si suppone attiva (il caso più lungo). None quando il
    tempo lo decide la fotocamera (modalità P, A, iAuto…) o non è leggibile."""
    tempo = props.get("shutspeedvalue")
    if tempo is None or is_automatic("shutspeedvalue", props):
        return None
    posa = exposure_seconds(tempo.value)
    if posa is None:
        return None
    rumore = props.get("noisereduction")
    soglia_prop = props.get("NoiseReductionExposureTime")
    soglia = exposure_seconds(soglia_prop.value) if soglia_prop is not None else None
    if soglia is None:
        soglia = _NR_THRESHOLD
    if (rumore is None or rumore.value != "off") and posa >= soglia:
        return 2 * posa
    return posa


def shot_timeout(props: dict[str, CameraProp]) -> float:
    """Quanto aspettare la risposta di starttake: l'occupazione stimata più
    15 s; senza stima il caso peggiore, 135 s (un 60" scelto dalla
    fotocamera più il suo fotogramma scuro, misurato in 129 s, più margine)."""
    occupata = shot_busy_seconds(props)
    if occupata is None:
        return SHOT_WORST_CASE
    return occupata + SHOT_MARGIN
