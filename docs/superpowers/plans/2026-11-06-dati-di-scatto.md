# Dati di scatto nel pannello — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** mostrare nel pannello di destra tempo, diaframma, ISO, focale, obiettivo, modalità, compensazione, bilanciamento, flash e risoluzione del file selezionato, letti dalla fotocamera via Wi-Fi prima di scaricarlo.

**Architecture:** un lettore EXIF puro nel core (`core/exif.py`) che accetta TIFF nudo, blocco `Exif\0\0` o JPEG intero; il `PreviewWorker` esistente chiede i dati prima dell'anteprima ed emette `shot_ready`; un widget nuovo (`gui/shot.py`) li mostra sotto "Dettagli". La fonte dei dati (A: `get_exif.cgi`, B: EXIF dentro l'anteprima) la decide la prova sul campo dei Task 1–2, che fa da cancello: se nessuna delle due funziona, il piano si ferma lì.

**Tech Stack:** Python 3.12+, PyQt6, requests, pytest con requests-mock, exiv2 (solo per generare i valori attesi nei test, non a runtime).

**Specifica:** `docs/superpowers/specs/2026-11-06-dati-di-scatto-design.md`

## Global Constraints

- I test si lanciano con `.venv/bin/python -m pytest` (il venv usa `--system-site-packages`, non c'è lo script `pytest`).
- Nessun import di Qt in `olympus_connect/core/`.
- Nessuna nuova dipendenza a runtime: niente Pillow; `pyproject.toml` non cambia.
- `parse_exif` non solleva mai eccezioni, con nessun input.
- Nessuna eccezione deve uscire da uno slot dei worker: sono invocati in coda, e un'eccezione fa abortire il processo.
- Testi dell'interfaccia in italiano. Segno meno U+2212 (`−`), segno di moltiplicazione U+00D7 (`×`), virgola decimale.
- Animazioni: `theme.DURATION` (180 ms), `QEasingCurve.Type.OutCubic`, dissolvenza da opacità **0,35**, mai da 0.
- `exec_erase.cgi` non va mai invocato.
- **Dati personali:** nessun numero di serie reale della fotocamera o dell'obiettivo può entrare in un commit. I file di `tests/fixtures/` che `diag.py` riscrive vanno riportati alla versione anonimizzata prima di ogni commit.
- Ogni messaggio di commit termina con la riga `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Mappa dei file

| File | Stato | Responsabilità |
|---|---|---|
| `diag.py` | modifica | passaggio `probe_exif`: interroga `get_exif` e l'anteprima, salva le risposte grezze |
| `tools/anonimizza_exif.py` | nuovo | sovrascrive i seriali nelle fixture EXIF a parità di lunghezza e scrive i valori attesi |
| `olympus_connect/core/exif.py` | nuovo | `ShotInfo`, `parse_exif`, `FormattedShot`, `format_shot` |
| `olympus_connect/core/protocol.py` | modifica | `has_exif()`, `exif()` |
| `olympus_connect/gui/workers.py` | modifica | `PreviewWorker.shot_ready`, richiesta dei dati prima dell'anteprima |
| `olympus_connect/gui/shot.py` | nuovo | `ShotSection`: la sezione "Scatto" del pannello |
| `olympus_connect/gui/theme.py` | modifica | stile `#shotHeadline` |
| `olympus_connect/gui/main_window.py` | modifica | inserisce `ShotSection`, collega `shot_ready`, nasconde la sezione nei casi previsti |
| `tests/test_exif.py` | nuovo | lettore e formattazione |
| `tests/test_protocol.py`, `tests/test_workers.py`, `tests/test_main_window.py`, `tests/test_end_to_end.py` | modifica | test dei rispettivi strati |
| `tests/fixtures/exif/` | nuovo | risposte vere anonimizzate e `attesi.json` |
| `README.md`, `DESIGN.md`, `docs/field-report-2026-09-24.md`, la specifica | modifica | documentazione |

---

### Task 1: Prova sul campo in `diag.py`

**Files:**
- Modify: `diag.py` (nuove funzioni dopo `probe_previews`, chiamata in `run()`, docstring del modulo)

**Interfaces:**
- Consumes: le costanti e gli helper già in `diag.py`: `BASE`, `UA`, `TIMEOUT`, `FIXTURES`, `step(name, ok, detail)`, la lista `files` restituita da `record()` (ogni elemento è la riga CSV spezzata: `parts[0]` cartella, `parts[1]` nome).
- Produces: dopo un'esecuzione con la fotocamera, i file `tests/fixtures/exif/jpg.bin`, `orf.bin`, `mov.bin`, `screennail_orf.jpg` e `tests/fixtures/exif/sorgenti.txt` (percorso sulla fotocamera di ciascun campione), più le righe del referto in `diag-report.txt`.

- [ ] **Step 1: Aggiungi gli helper e il passaggio**

Inserisci dopo la funzione `probe_previews`:

```python
def _exif_shape(body: bytes) -> str:
    """In che forma get_exif restituisce i dati: decide come leggerli."""
    if body[:4] in (b"II*\x00", b"MM\x00*"):
        return "TIFF nudo"
    if body[:6] == b"Exif\x00\x00":
        return "blocco Exif"
    if body[:2] == b"\xff\xd8":
        return "JPEG"
    return "sconosciuta"


def probe_exif(files: list[list[str]]) -> None:
    """Da dove si leggono i dati di scatto?

    Due fonti possibili (specifica 2026-11-06-dati-di-scatto-design.md):
    A, get_exif.cgi, una richiesta dedicata; B, l'EXIF che la fotocamera
    potrebbe copiare dentro il JPEG dell'anteprima. Questo passaggio
    registra entrambe, così la scelta si fa sui byte veri.
    """
    import time

    print("\n== Dati di scatto ==")

    first: dict[str, str] = {}
    for parts in files:
        first.setdefault(Path(parts[1]).suffix.upper(), f"{parts[0]}/{parts[1]}")

    out = FIXTURES / "exif"
    out.mkdir(parents=True, exist_ok=True)
    sorgenti: list[str] = []

    for ext in (".JPG", ".ORF", ".MOV"):
        camera_path = first.get(ext)
        if not camera_path:
            print(f"  [SALTATO] get_exif {ext}: nessun file")
            continue
        try:
            inizio = time.monotonic()
            r = requests.get(f"{BASE}/get_exif.cgi", headers=UA,
                             params={"DIR": camera_path}, timeout=TIMEOUT)
            ms = (time.monotonic() - inizio) * 1000
            body = r.content
            forma = _exif_shape(body)
            step(f"get_exif {ext}", r.status_code == 200 and forma != "sconosciuta",
                 f"HTTP {r.status_code}, {r.headers.get('Content-Type', '?')}, "
                 f"{len(body)} byte, {ms:.0f} ms, forma: {forma}, "
                 f"inizio: {body[:16].hex(' ')}")
            nome = f"{ext[1:].lower()}.bin"
            (out / nome).write_bytes(body)
            sorgenti.append(f"{nome} {camera_path}")
        except requests.RequestException as exc:
            step(f"get_exif {ext}", False, type(exc).__name__)

    camera_path = first.get(".ORF")
    if camera_path:
        try:
            r = requests.get(f"{BASE}/get_screennail.cgi", headers=UA,
                             params={"DIR": camera_path}, timeout=TIMEOUT)
            body = r.content
            posizione = body.find(b"Exif\x00\x00", 0, 65536)
            step("EXIF dentro l'anteprima di un ORF",
                 r.status_code == 200 and posizione >= 0,
                 f"HTTP {r.status_code}, {len(body)} byte, "
                 + (f"blocco Exif alla posizione {posizione}" if posizione >= 0
                    else "nessun blocco Exif"))
            (out / "screennail_orf.jpg").write_bytes(body)
            sorgenti.append(f"screennail_orf.jpg {camera_path}")
        except requests.RequestException as exc:
            step("EXIF dentro l'anteprima di un ORF", False, type(exc).__name__)

    (out / "sorgenti.txt").write_text("\n".join(sorgenti) + "\n")
```

- [ ] **Step 2: Chiamalo da `run()`**

In `run()`, subito dopo `probe_previews(files)`:

```python
    probe_exif(files)
```

e cambia il messaggio finale in:

```python
    print("\nFatto. Fixture in tests/fixtures/ (dati di scatto in "
          "tests/fixtures/exif/), campioni in diag-downloads/.")
```

- [ ] **Step 3: Aggiorna la docstring del modulo**

Nell'elenco «Risponde a tre domande» aggiungi una quarta voce e cambia «tre» in «quattro»:

```
  - get_exif restituisce i dati di scatto, e in che forma? L'anteprima li contiene?
```

- [ ] **Step 4: Verifica che compili e che la suite resti verde**

Run: `.venv/bin/python -m py_compile diag.py && .venv/bin/python -m pytest -q`
Expected: nessun errore di compilazione; `228 passed`.

- [ ] **Step 5: Commit**

```bash
git add diag.py
git commit -m "feat(diag): prova sul campo dei dati di scatto

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Esecuzione sul campo, anonimizzazione, decisione (con l'utente)

Questo task richiede la fotocamera ed è un **cancello**: il resto del piano procede solo se una delle due fonti funziona.

**Files:**
- Create: `tools/anonimizza_exif.py`
- Create: `tests/fixtures/exif/jpg.bin`, `orf.bin`, `mov.bin`, `screennail_orf.jpg`, `sorgenti.txt`, `attesi.json` (prodotti dalla prova e dallo script)
- Modify: `docs/superpowers/specs/2026-11-06-dati-di-scatto-design.md` (sezione «Fonte dei dati»)

**Interfaces:**
- Consumes: i file prodotti dal Task 1; `tests/fixtures/caminfo.xml` **appena riscritto da diag.py**, che contiene i seriali veri.
- Produces: `tests/fixtures/exif/attesi.json`, un oggetto `{nome_file: {chiave_exiv2: valore_grezzo}}` con le chiavi `Exif.Photo.ExposureTime`, `Exif.Photo.FNumber`, `Exif.Photo.ISOSpeedRatings`, `Exif.Photo.FocalLength`, `Exif.Photo.LensModel`, `Exif.Photo.ExposureBiasValue`, `Exif.Photo.ExposureProgram`, `Exif.Photo.WhiteBalance`, `Exif.Photo.Flash`, `Exif.Photo.PixelXDimension`, `Exif.Photo.PixelYDimension`, `Exif.Image.ImageWidth`, `Exif.Image.ImageLength` (solo quelle presenti). Valori nella forma grezza di `exiv2 -Pkv`: razionali come `"1/60"`, interi come `"6400"`, testo come stringa. La decisione **A**, **B** o **stop** scritta nella specifica.

- [ ] **Step 1: L'utente esegue la prova**

Con la fotocamera in modalità Wi-Fi e il PC collegato al suo access point, l'utente esegue:

```bash
.venv/bin/python diag.py
```

Poi torna in linea e comunica che ha finito. Il referto è in `diag-report.txt`.

- [ ] **Step 2: Leggi il referto**

Run: `sed -n '/== Dati di scatto ==/,$p' diag-report.txt`
Annota per ogni riga: stato HTTP, forma, dimensione, tempo.

- [ ] **Step 3: Scrivi lo script di anonimizzazione**

Create `tools/anonimizza_exif.py`:

```python
#!/usr/bin/env python3
"""Toglie i numeri di serie dalle fixture EXIF registrate da diag.py.

L'EXIF contiene il seriale del corpo (BodySerialNumber), spesso quello
dell'obiettivo, e altri ancora nella sezione proprietaria Olympus. I byte
vanno sovrascritti a parità di lunghezza: offset e puntatori interni
restano validi, quindi le fixture restano leggibili come quelle vere.

Uso, dalla cartella del progetto, PRIMA di riportare caminfo.xml alla
versione anonimizzata (lo script ne legge i seriali veri):

    .venv/bin/python tools/anonimizza_exif.py

Scrive anche tests/fixtures/exif/attesi.json con i valori letti da exiv2,
che i test confrontano con parse_exif.
"""
from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

CARTELLA = Path("tests/fixtures/exif")
CAMINFO = Path("tests/fixtures/caminfo.xml")
CHIAVI = (
    "Exif.Photo.ExposureTime", "Exif.Photo.FNumber",
    "Exif.Photo.ISOSpeedRatings", "Exif.Photo.FocalLength",
    "Exif.Photo.LensModel", "Exif.Photo.ExposureBiasValue",
    "Exif.Photo.ExposureProgram", "Exif.Photo.WhiteBalance",
    "Exif.Photo.Flash", "Exif.Photo.PixelXDimension",
    "Exif.Photo.PixelYDimension", "Exif.Image.ImageWidth",
    "Exif.Image.ImageLength",
)


def come_jpeg(dati: bytes) -> bytes:
    """exiv2 legge con sicurezza i JPEG: avvolge TIFF nudo e blocco Exif
    in un JPEG minimo con il solo segmento APP1."""
    if dati[:2] == b"\xff\xd8":
        return dati
    if dati[:6] != b"Exif\x00\x00":
        dati = b"Exif\x00\x00" + dati
    return (b"\xff\xd8\xff\xe1" + struct.pack(">H", len(dati) + 2)
            + dati + b"\xff\xd9")


def exiv2(dati: bytes) -> dict[str, str]:
    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        f.write(come_jpeg(dati))
        f.flush()
        r = subprocess.run(["exiv2", "-q", "-Pkv", f.name],
                           capture_output=True, text=True, errors="replace")
    valori: dict[str, str] = {}
    for riga in r.stdout.splitlines():
        chiave, _, valore = riga.partition(" ")
        valori[chiave] = valore.strip()
    return valori


def seriali(campioni: dict[str, bytes]) -> set[str]:
    trovati: set[str] = set()
    if CAMINFO.exists():
        radice = ElementTree.fromstring(CAMINFO.read_bytes())
        for nodo in radice.iter():
            if re.fullmatch(r"Serial\d+", nodo.tag) and (nodo.text or "").strip():
                trovati.add(nodo.text.strip())
    for dati in campioni.values():
        for chiave, valore in exiv2(dati).items():
            if "serial" in chiave.lower() and len(valore.strip()) >= 4:
                trovati.add(valore.strip())
    # I segnaposto già anonimi non sono seriali da cercare.
    return {s for s in trovati if set(s) - {"X", "Y", "0"}}


def main() -> int:
    campioni = {p.name: p.read_bytes() for p in sorted(CARTELLA.iterdir())
                if p.suffix in (".bin", ".jpg")}
    if not campioni:
        print("Nessuna fixture in tests/fixtures/exif/: eseguire diag.py.")
        return 1

    da_togliere = seriali(campioni)
    print(f"Seriali da togliere: {len(da_togliere)}")

    for nome, dati in campioni.items():
        sostituzioni = 0
        for seriale in da_togliere:
            vero = seriale.encode("ascii", "replace")
            sostituzioni += dati.count(vero)
            dati = dati.replace(vero, b"0" * len(vero))
        (CARTELLA / nome).write_bytes(dati)
        campioni[nome] = dati
        print(f"  {nome}: {sostituzioni} sostituzioni")

    rimasti = [(n, s) for n, d in campioni.items() for s in da_togliere
               if s.encode("ascii", "replace") in d]
    if rimasti:
        print(f"ERRORE: seriali ancora presenti: {rimasti}")
        return 1

    attesi = {}
    for nome, dati in campioni.items():
        letti = exiv2(dati)
        valori = {k: letti[k] for k in CHIAVI if k in letti}
        if valori:
            attesi[nome] = valori
    (CARTELLA / "attesi.json").write_text(
        json.dumps(attesi, indent=2, ensure_ascii=False) + "\n")
    print(f"attesi.json: {sorted(attesi)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Anonimizza, poi riporta le fixture riscritte da diag.py**

```bash
.venv/bin/python tools/anonimizza_exif.py
```

Expected: «Seriali da togliere: N» con N ≥ 1, nessun «ERRORE», `attesi.json` scritto per almeno un file.

Salva l'elenco dei seriali veri in `diag-downloads/`, che git ignora, per la verifica del passo successivo:

```bash
grep -oE '<Serial[0-9]+>[^<]+' tests/fixtures/caminfo.xml | sed 's/.*>//' > diag-downloads/seriali.txt
for f in diag-downloads/*.jpg; do exiv2 -q -Pkv "$f" 2>/dev/null; done | grep -i serial | awk '{print $2}' >> diag-downloads/seriali.txt
sort -u -o diag-downloads/seriali.txt diag-downloads/seriali.txt
wc -l diag-downloads/seriali.txt
```

Expected: almeno 1 riga.

Poi, **dopo** lo script e il salvataggio (entrambi avevano bisogno di `caminfo.xml` con i seriali veri):

```bash
git checkout -- tests/fixtures/caminfo.xml tests/fixtures/commandlist.xml tests/fixtures/imglist_dcim.csv tests/fixtures/imglist_dcim_100olymp.csv
```

- [ ] **Step 5: Verifica che nessun seriale vero entri in git**

```bash
git status --short tests/fixtures/
while read -r s; do [ -n "$s" ] && grep -rlaF "$s" tests/fixtures/ docs/ && echo "TROVATO"; done < diag-downloads/seriali.txt; echo fine
```

Expected: `git status` mostra solo `tests/fixtures/exif/` come nuovo; il ciclo stampa solo `fine`.

- [ ] **Step 6: Decidi la fonte e annotala nella specifica**

Regola:
- **A** se `get_exif ORF` e `get_exif JPG` sono `[OK]` e `attesi.json` contiene `jpg.bin` oppure `orf.bin` con almeno tempo, diaframma e ISO.
- altrimenti **B** se la riga «EXIF dentro l'anteprima di un ORF» è `[OK]` e `attesi.json` contiene `screennail_orf.jpg` con almeno tempo, diaframma e ISO.
- altrimenti **stop**: annota l'esito nella specifica e nel resoconto sul campo, fai il commit della documentazione e **non** eseguire i task successivi.

Annota l'esito nel campo «Fonte dei dati» della specifica, sostituendo la riga che inizia con «Vince A se» con il paragrafo dell'esito, per esempio:

```markdown
**Esito della prova sul campo (2026-11-DD):** fonte **A**. `get_exif.cgi` risponde HTTP 200 in <N> ms con <forma>, <dimensione> byte per un ORF; per i MOV <esito>. L'anteprima <contiene / non contiene> un blocco Exif.
```

con i valori veri del referto al posto delle parentesi angolari.

Annota anche se `get_exif MOV` restituisce dati di scatto (cioè se `attesi.json` contiene `mov.bin` con tempo o diaframma): se no, i video restano esclusi come da specifica.

- [ ] **Step 7: Commit**

```bash
git add tools/anonimizza_exif.py tests/fixtures/exif/ docs/superpowers/specs/2026-11-06-dati-di-scatto-design.md
git status --short
git commit -m "test: risposte EXIF vere della fotocamera, anonimizzate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Prima del commit, `git status --short` non deve mostrare `caminfo.xml` né altre fixture modificate fuori da `tests/fixtures/exif/`.

---

### Task 3: Lettore EXIF (`parse_exif`)

**Files:**
- Create: `olympus_connect/core/exif.py`
- Test: `tests/test_exif.py`

**Interfaces:**
- Consumes: `tests/fixtures/exif/attesi.json` e i campioni del Task 2, tramite la fixture `fixture_bytes` di `tests/conftest.py` (salta il test se il file manca).
- Produces:
  - `ShotInfo` (dataclass frozen) con i campi, tutti con default `None`: `exposure_time: Fraction | None`, `f_number: Fraction | None`, `iso: int | None`, `focal_length: Fraction | None`, `lens: str | None`, `exposure_bias: Fraction | None`, `exposure_program: int | None`, `white_balance: int | None`, `flash_fired: bool | None`, `width: int | None`, `height: int | None`.
  - `parse_exif(data: bytes) -> ShotInfo | None`.

- [ ] **Step 1: Scrivi i test che falliscono**

Create `tests/test_exif.py`:

```python
"""Il lettore EXIF: i byte veri della fotocamera e i casi patologici.

Non deve mai sollevare: il suo chiamante è uno slot Qt in coda, dove
un'eccezione fa abortire il processo.
"""
import json
import struct
from fractions import Fraction

import pytest

from olympus_connect.core.exif import ShotInfo, parse_exif

EXIF_IFD = 0x8769


def _encode(order, kind, value):
    if kind == "A":
        raw = value.encode("ascii") + b"\x00"
        return 2, len(raw), raw
    if kind == "H":
        return 3, 1, struct.pack(order + "H", value)
    if kind == "I":
        return 4, 1, struct.pack(order + "I", value)
    if kind == "R":
        return 5, 1, struct.pack(order + "II", value.numerator, value.denominator)
    if kind == "SR":
        return 10, 1, struct.pack(order + "ii", value.numerator, value.denominator)
    raise ValueError(kind)


def _ifd(order, entries, start):
    """Una IFD che inizia a `start`, seguita dai dati che non stanno nei 4 byte."""
    n = len(entries)
    data_start = start + 2 + 12 * n + 4
    table = struct.pack(order + "H", n)
    data = b""
    for tag, kind, value in entries:
        typ, count, raw = _encode(order, kind, value)
        if len(raw) <= 4:
            field = raw.ljust(4, b"\x00")
        else:
            field = struct.pack(order + "I", data_start + len(data))
            data += raw + (b"\x00" if len(raw) % 2 else b"")
        table += struct.pack(order + "HHI", tag, typ, count) + field
    return table + struct.pack(order + "I", 0) + data


def build_tiff(ifd0, exif=None, order="<"):
    head = (b"II*\x00" if order == "<" else b"MM\x00*") + struct.pack(order + "I", 8)
    if exif is None:
        return head + _ifd(order, ifd0, 8)
    provvisorio = _ifd(order, ifd0 + [(EXIF_IFD, "I", 0)], 8)
    posizione = 8 + len(provvisorio)
    return (head + _ifd(order, ifd0 + [(EXIF_IFD, "I", posizione)], 8)
            + _ifd(order, exif, posizione))


def as_jpeg(tiff):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    app1 = b"\xff\xe1" + struct.pack(">H", 2 + 6 + len(tiff)) + b"Exif\x00\x00" + tiff
    return b"\xff\xd8" + app0 + app1 + b"\xff\xda\x00\x02" + b"\x12" * 32 + b"\xff\xd9"


IFD0 = [(0x0100, "I", 5200), (0x0101, "I", 3904)]
EXIF = [
    (0x829A, "R", Fraction(1, 60)),
    (0x829D, "R", Fraction(35, 10)),
    (0x8822, "H", 4),
    (0x8827, "H", 6400),
    (0x9204, "SR", Fraction(-7, 10)),
    (0x9209, "H", 16),
    (0x920A, "R", Fraction(140, 10)),
    (0xA403, "H", 0),
    (0xA434, "A", "LUMIX G VARIO 14-42/F3.5-5.6 II "),
]
ATTESO = ShotInfo(
    exposure_time=Fraction(1, 60), f_number=Fraction(7, 2), iso=6400,
    focal_length=Fraction(14), lens="LUMIX G VARIO 14-42/F3.5-5.6 II",
    exposure_bias=Fraction(-7, 10), exposure_program=4, white_balance=0,
    flash_fired=False, width=5200, height=3904,
)


@pytest.mark.parametrize("order", ["<", ">"])
def test_reads_every_field_in_both_byte_orders(order):
    assert parse_exif(build_tiff(IFD0, EXIF, order)) == ATTESO


def test_reads_an_exif_block_with_its_prefix():
    assert parse_exif(b"Exif\x00\x00" + build_tiff(IFD0, EXIF)) == ATTESO


def test_reads_the_app1_segment_of_a_whole_jpeg():
    assert parse_exif(as_jpeg(build_tiff(IFD0, EXIF))) == ATTESO


def test_flash_fired_is_bit_zero():
    exif = [e for e in EXIF if e[0] != 0x9209] + [(0x9209, "H", 0x19)]
    assert parse_exif(build_tiff(IFD0, exif)).flash_fired is True


def test_a_missing_field_is_none_and_the_others_survive():
    exif = [e for e in EXIF if e[0] != 0xA434]
    info = parse_exif(build_tiff(IFD0, exif))
    assert info.lens is None
    assert info.iso == 6400


def test_focal_length_zero_means_unknown():
    exif = [e for e in EXIF if e[0] != 0x920A] + [(0x920A, "R", Fraction(0, 1))]
    assert parse_exif(build_tiff(IFD0, exif)).focal_length is None


def test_pixel_dimensions_win_over_the_raw_image_size():
    exif = EXIF + [(0xA002, "I", 4608), (0xA003, "I", 3456)]
    info = parse_exif(build_tiff(IFD0, exif))
    assert (info.width, info.height) == (4608, 3456)


def test_a_pointer_past_the_end_skips_only_that_field():
    tiff = bytearray(build_tiff(IFD0, EXIF))
    # La voce LensModel (ASCII, fuori dai 4 byte) è l'ultima della IFD EXIF:
    # il suo campo valore è un offset. Lo si fa puntare oltre la fine.
    posizione = tiff.rfind(struct.pack("<HH", 0xA434, 2))
    tiff[posizione + 8:posizione + 12] = struct.pack("<I", 10_000_000)
    info = parse_exif(bytes(tiff))
    assert info.lens is None
    assert info.iso == 6400


def test_an_exif_pointer_back_to_ifd0_does_not_loop():
    tiff = build_tiff(IFD0 + [(EXIF_IFD, "I", 8)])
    info = parse_exif(tiff)
    assert info == ShotInfo(width=5200, height=3904)


def test_absurd_entry_count_is_rejected():
    tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 60000)
    assert parse_exif(tiff) is None


@pytest.mark.parametrize("data", [
    b"", b"\x00", b"garbage that is not exif",
    b"\xff\xd8\xff\xd9",                         # JPEG senza APP1
    b"\xff\xd8\xff\xe1\x00",                     # segmento troncato
    b"Exif\x00\x00",                             # blocco vuoto
    b"II*\x00\xff\xff\xff\xff",                  # IFD0 fuori dai limiti
])
def test_garbage_returns_none(data):
    assert parse_exif(data) is None


def test_no_truncation_ever_raises():
    tiff = build_tiff(IFD0, EXIF)
    for n in range(len(tiff)):
        parse_exif(tiff[:n])        # non deve sollevare
    jpeg = as_jpeg(tiff)
    for n in range(len(jpeg)):
        parse_exif(jpeg[:n])


def test_an_exif_with_none_of_our_fields_is_none():
    assert parse_exif(build_tiff([(0x010F, "A", "OM Digital Solutions")])) is None


# --- i byte veri della fotocamera -------------------------------------------

_TAGS = {
    "Exif.Photo.ExposureTime": "exposure_time",
    "Exif.Photo.FNumber": "f_number",
    "Exif.Photo.ISOSpeedRatings": "iso",
    "Exif.Photo.FocalLength": "focal_length",
    "Exif.Photo.LensModel": "lens",
    "Exif.Photo.ExposureBiasValue": "exposure_bias",
    "Exif.Photo.ExposureProgram": "exposure_program",
    "Exif.Photo.WhiteBalance": "white_balance",
}


def _attesi(fixture_bytes):
    return json.loads(fixture_bytes("exif/attesi.json"))


def test_real_camera_samples_match_exiv2(fixture_bytes):
    """Ogni campione per cui exiv2 trova dati deve dare gli stessi valori."""
    attesi = _attesi(fixture_bytes)
    assert attesi, "attesi.json è vuoto"
    for nome, valori in attesi.items():
        info = parse_exif(fixture_bytes(f"exif/{nome}"))
        assert info is not None, f"{nome}: parse_exif ha restituito None"
        for chiave, campo in _TAGS.items():
            if chiave not in valori:
                continue
            letto = getattr(info, campo)
            grezzo = valori[chiave]
            if campo == "lens":
                assert letto == grezzo.strip(), nome
            elif campo == "focal_length" and Fraction(grezzo) == 0:
                assert letto is None, nome
            elif campo in ("iso", "exposure_program", "white_balance"):
                assert letto == int(grezzo.split()[0]), (nome, campo)
            else:
                assert letto == Fraction(grezzo), (nome, campo)
        if "Exif.Photo.Flash" in valori:
            assert info.flash_fired is bool(int(valori["Exif.Photo.Flash"]) & 1), nome
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_exif.py -q`
Expected: errore di raccolta, `ModuleNotFoundError: No module named 'olympus_connect.core.exif'`.

- [ ] **Step 3: Scrivi il lettore**

Create `olympus_connect/core/exif.py`:

```python
"""Dati di scatto dall'EXIF standard.

Verificato sulle foto della E-M10 Mark IV: tutti i campi mostrati stanno
nell'EXIF standard (IFD0 e sotto-IFD EXIF), compreso il nome
dell'obiettivo. La sezione proprietaria Olympus non viene letta.

Il lettore accetta le tre forme in cui l'EXIF può arrivare: TIFF nudo,
blocco che inizia con «Exif\\0\\0», o JPEG intero. Non solleva mai
eccezioni: chi lo chiama è uno slot Qt in coda, dove un'eccezione fa
abortire il processo. Byte che non tornano danno None, o un ShotInfo con
i soli campi leggibili.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, fields
from fractions import Fraction

TAG_IMAGE_WIDTH = 0x0100
TAG_IMAGE_LENGTH = 0x0101
TAG_EXIF_IFD = 0x8769
TAG_EXPOSURE_TIME = 0x829A
TAG_F_NUMBER = 0x829D
TAG_EXPOSURE_PROGRAM = 0x8822
TAG_ISO = 0x8827
TAG_EXPOSURE_BIAS = 0x9204
TAG_FLASH = 0x9209
TAG_FOCAL_LENGTH = 0x920A
TAG_PIXEL_X = 0xA002
TAG_PIXEL_Y = 0xA003
TAG_WHITE_BALANCE = 0xA403
TAG_LENS_MODEL = 0xA434

# tipo TIFF -> byte per elemento
_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
_INTEGER_FORMATS = {1: "B", 3: "H", 4: "I", 7: "B", 9: "i"}
_MAX_ENTRIES = 512          # un'IFD vera ne ha qualche decina
_MAX_COUNT = 1 << 16


@dataclass(frozen=True)
class ShotInfo:
    exposure_time: Fraction | None = None
    f_number: Fraction | None = None
    iso: int | None = None
    focal_length: Fraction | None = None
    lens: str | None = None
    exposure_bias: Fraction | None = None
    exposure_program: int | None = None
    white_balance: int | None = None
    flash_fired: bool | None = None
    width: int | None = None
    height: int | None = None


def parse_exif(data: bytes) -> ShotInfo | None:
    try:
        tiff = _tiff_block(bytes(data))
        if tiff is None or len(tiff) < 8:
            return None
        return _read(tiff)
    except Exception:       # la garanzia: nessuna eccezione esce da qui
        return None


def _tiff_block(data: bytes) -> bytes | None:
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return data
    if data[:6] == b"Exif\x00\x00":
        return data[6:]
    if data[:2] == b"\xff\xd8":
        return _app1_from_jpeg(data)
    return None


def _app1_from_jpeg(data: bytes) -> bytes | None:
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xFF:                       # riempimento
            pos += 1
            continue
        if marker in (0xD9, 0xDA):               # fine immagine, inizio dati
            return None
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            pos += 2                             # marcatori senza lunghezza
            continue
        (length,) = struct.unpack_from(">H", data, pos + 2)
        if length < 2:
            return None
        segment = data[pos + 4:pos + 2 + length]
        if marker == 0xE1 and segment[:6] == b"Exif\x00\x00":
            return segment[6:]
        pos += 2 + length
    return None


def _read(tiff: bytes) -> ShotInfo | None:
    order = "<" if tiff[:2] == b"II" else ">"
    (ifd0,) = struct.unpack_from(order + "I", tiff, 4)
    seen: set[int] = set()
    main = _entries(tiff, order, ifd0, seen)
    exif: dict[int, object] = {}
    pointer = _int(main.get(TAG_EXIF_IFD))
    if pointer is not None:
        exif = _entries(tiff, order, pointer, seen)

    flash = _int(exif.get(TAG_FLASH))
    width = _positive_int(exif.get(TAG_PIXEL_X))
    height = _positive_int(exif.get(TAG_PIXEL_Y))
    if width is None or height is None:
        width = _positive_int(main.get(TAG_IMAGE_WIDTH))
        height = _positive_int(main.get(TAG_IMAGE_LENGTH))

    info = ShotInfo(
        exposure_time=_positive(_fraction(exif.get(TAG_EXPOSURE_TIME))),
        f_number=_positive(_fraction(exif.get(TAG_F_NUMBER))),
        iso=_positive_int(exif.get(TAG_ISO)),
        focal_length=_positive(_fraction(exif.get(TAG_FOCAL_LENGTH))),
        lens=_text(exif.get(TAG_LENS_MODEL)),
        exposure_bias=_fraction(exif.get(TAG_EXPOSURE_BIAS)),
        exposure_program=_int(exif.get(TAG_EXPOSURE_PROGRAM)),
        white_balance=_int(exif.get(TAG_WHITE_BALANCE)),
        flash_fired=None if flash is None else bool(flash & 1),
        width=width,
        height=height,
    )
    if all(getattr(info, f.name) is None for f in fields(info)):
        return None
    return info


def _entries(tiff: bytes, order: str, offset: int,
             seen: set[int]) -> dict[int, object]:
    if offset in seen or offset < 8 or offset + 2 > len(tiff):
        return {}
    seen.add(offset)
    (count,) = struct.unpack_from(order + "H", tiff, offset)
    if count > _MAX_ENTRIES:
        return {}
    out: dict[int, object] = {}
    for i in range(count):
        base = offset + 2 + 12 * i
        if base + 12 > len(tiff):
            break
        tag, typ, n = struct.unpack_from(order + "HHI", tiff, base)
        size = _TYPE_SIZES.get(typ)
        if size is None or n == 0 or n > _MAX_COUNT:
            continue
        total = size * n
        if total <= 4:
            start = base + 8
        else:
            (start,) = struct.unpack_from(order + "I", tiff, base + 8)
        if start + total > len(tiff):
            continue
        out[tag] = _decode(tiff[start:start + total], order, typ, n)
    return out


def _decode(raw: bytes, order: str, typ: int, n: int) -> object:
    if typ == 2:
        return raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
    if typ in _INTEGER_FORMATS:
        return list(struct.unpack(order + _INTEGER_FORMATS[typ] * n, raw))
    numbers = struct.unpack(order + ("i" if typ == 10 else "I") * (2 * n), raw)
    return [Fraction(numbers[k], numbers[k + 1]) if numbers[k + 1] else None
            for k in range(0, 2 * n, 2)]


def _first(value: object) -> object:
    return value[0] if isinstance(value, list) and value else None


def _int(value: object) -> int | None:
    x = _first(value)
    return x if isinstance(x, int) else None


def _positive_int(value: object) -> int | None:
    x = _int(value)
    return x if x is not None and x > 0 else None


def _fraction(value: object) -> Fraction | None:
    x = _first(value)
    return x if isinstance(x, Fraction) else None


def _positive(value: Fraction | None) -> Fraction | None:
    return value if value is not None and value > 0 else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
```

- [ ] **Step 4: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_exif.py -q`
Expected: tutti `passed`. Il test sui campioni veri passa (non viene saltato): se viene saltato, il Task 2 non ha prodotto `attesi.json`.

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/core/exif.py tests/test_exif.py
git commit -m "feat(core): lettore dei dati di scatto dall'EXIF standard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Formattazione in italiano (`format_shot`)

**Files:**
- Modify: `olympus_connect/core/exif.py` (in coda)
- Test: `tests/test_exif.py` (in coda)

**Interfaces:**
- Consumes: `ShotInfo` del Task 3.
- Produces:
  - `FormattedShot` (dataclass frozen): `headline: str`, `rows: tuple[tuple[str, str], ...]`.
  - `format_shot(info: ShotInfo) -> FormattedShot`.
  - Etichette delle righe, in quest'ordine: `"Obiettivo"`, `"Modalità"`, `"Compensazione"`, `"Bilanciamento"`, `"Flash"`, `"Risoluzione"`.
  - `HEADLINE_SEPARATOR = "   "` (tre spazi).

- [ ] **Step 1: Scrivi i test che falliscono**

Aggiungi in coda a `tests/test_exif.py`:

```python
# --- formattazione -----------------------------------------------------------

from olympus_connect.core.exif import FormattedShot, format_shot  # noqa: E402


def _riga(info, etichetta):
    return dict(format_shot(info).rows).get(etichetta)


@pytest.mark.parametrize("tempo,testo", [
    (Fraction(1, 4000), "1/4000 s"),
    (Fraction(1, 60), "1/60 s"),
    (Fraction(10, 600), "1/60 s"),
    (Fraction(1, 3), "1/3 s"),
    (Fraction(1), "1 s"),
    (Fraction(5, 2), "2,5 s"),
    (Fraction(30), "30 s"),
])
def test_exposure_time(tempo, testo):
    assert format_shot(ShotInfo(exposure_time=tempo)).headline == testo


@pytest.mark.parametrize("f,testo", [
    (Fraction(18, 10), "f/1,8"), (Fraction(8), "f/8"), (Fraction(56, 10), "f/5,6"),
])
def test_aperture(f, testo):
    assert format_shot(ShotInfo(f_number=f)).headline == testo


@pytest.mark.parametrize("ev,testo", [
    (Fraction(0), "0 EV"),
    (Fraction(3, 10), "+0,3 EV"),
    (Fraction(1, 3), "+0,3 EV"),
    (Fraction(-7, 10), "−0,7 EV"),
    (Fraction(-2, 3), "−0,7 EV"),
    (Fraction(-1, 50), "0 EV"),
    (Fraction(2), "+2 EV"),
])
def test_exposure_bias_uses_the_true_minus_sign(ev, testo):
    assert _riga(ShotInfo(exposure_bias=ev), "Compensazione") == testo


def test_headline_joins_the_four_numbers_in_order():
    info = ShotInfo(exposure_time=Fraction(1, 60), f_number=Fraction(7, 2),
                    iso=6400, focal_length=Fraction(14))
    assert format_shot(info).headline == "1/60 s   f/3,5   ISO 6400   14 mm"


def test_headline_skips_missing_numbers():
    info = ShotInfo(iso=200, focal_length=Fraction(45))
    assert format_shot(info).headline == "ISO 200   45 mm"


def test_focal_length_keeps_a_meaningful_decimal():
    assert format_shot(ShotInfo(focal_length=Fraction(175, 10))).headline == "17,5 mm"


def test_rows_follow_the_fixed_order_and_skip_missing_fields():
    info = ShotInfo(lens="M.Zuiko 45mm F1.8", exposure_program=3,
                    exposure_bias=Fraction(0), white_balance=1,
                    flash_fired=True, width=5184, height=3888)
    assert format_shot(info).rows == (
        ("Obiettivo", "M.Zuiko 45mm F1.8"),
        ("Modalità", "Priorità dei diaframmi"),
        ("Compensazione", "0 EV"),
        ("Bilanciamento", "Manuale"),
        ("Flash", "Scattato"),
        ("Risoluzione", "5184 × 3888"),
    )


@pytest.mark.parametrize("programma,testo", [
    (1, "Manuale"), (2, "Programma"), (3, "Priorità dei diaframmi"),
    (4, "Priorità dei tempi"),
])
def test_exposure_program_names(programma, testo):
    assert _riga(ShotInfo(exposure_program=programma), "Modalità") == testo


def test_unknown_exposure_program_omits_the_row():
    assert _riga(ShotInfo(exposure_program=7, iso=100), "Modalità") is None


def test_flash_not_fired_and_auto_white_balance():
    info = ShotInfo(flash_fired=False, white_balance=0)
    assert _riga(info, "Flash") == "Non scattato"
    assert _riga(info, "Bilanciamento") == "Auto"


def test_resolution_needs_both_sides():
    assert _riga(ShotInfo(width=5200), "Risoluzione") is None


def test_a_single_field_gives_a_single_row_and_no_headline():
    shot = format_shot(ShotInfo(lens="LUMIX G VARIO 14-42/F3.5-5.6 II"))
    assert shot == FormattedShot(
        headline="", rows=(("Obiettivo", "LUMIX G VARIO 14-42/F3.5-5.6 II"),))
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_exif.py -q`
Expected: errore di raccolta, `ImportError: cannot import name 'FormattedShot'`.

- [ ] **Step 3: Scrivi la formattazione**

Aggiungi in coda a `olympus_connect/core/exif.py`:

```python
# -- formattazione ------------------------------------------------------------

HEADLINE_SEPARATOR = "   "
MINUS = "−"
TIMES = "×"

_PROGRAMS = {1: "Manuale", 2: "Programma", 3: "Priorità dei diaframmi",
             4: "Priorità dei tempi"}
_WHITE_BALANCE = {0: "Auto", 1: "Manuale"}


@dataclass(frozen=True)
class FormattedShot:
    headline: str
    rows: tuple[tuple[str, str], ...]


def _decimal(value: float) -> str:
    """Una cifra decimale al massimo, solo se non nulla, virgola italiana."""
    return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def format_exposure_time(value: Fraction) -> str:
    if value < 1:
        return f"1/{round(1 / value)} s"
    return f"{_decimal(float(value))} s"


def format_bias(value: Fraction) -> str:
    rounded = round(float(value), 1)
    if rounded == 0:
        return "0 EV"
    sign = "+" if rounded > 0 else MINUS
    return f"{sign}{_decimal(abs(rounded))} EV"


def format_shot(info: ShotInfo) -> FormattedShot:
    numbers: list[str] = []
    if info.exposure_time is not None:
        numbers.append(format_exposure_time(info.exposure_time))
    if info.f_number is not None:
        numbers.append(f"f/{_decimal(float(info.f_number))}")
    if info.iso is not None:
        numbers.append(f"ISO {info.iso}")
    if info.focal_length is not None:
        numbers.append(f"{_decimal(float(info.focal_length))} mm")

    rows: list[tuple[str, str]] = []
    if info.lens:
        rows.append(("Obiettivo", info.lens))
    if info.exposure_program in _PROGRAMS:
        rows.append(("Modalità", _PROGRAMS[info.exposure_program]))
    if info.exposure_bias is not None:
        rows.append(("Compensazione", format_bias(info.exposure_bias)))
    if info.white_balance in _WHITE_BALANCE:
        rows.append(("Bilanciamento", _WHITE_BALANCE[info.white_balance]))
    if info.flash_fired is not None:
        rows.append(("Flash", "Scattato" if info.flash_fired else "Non scattato"))
    if info.width and info.height:
        rows.append(("Risoluzione", f"{info.width} {TIMES} {info.height}"))

    return FormattedShot(HEADLINE_SEPARATOR.join(numbers), tuple(rows))
```

- [ ] **Step 4: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_exif.py -q`
Expected: tutti `passed`.

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/core/exif.py tests/test_exif.py
git commit -m "feat(core): dati di scatto in italiano, una riga di testa e righe ordinate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `has_exif()` ed `exif()` nel client (solo con fonte A)

Con la fonte **B** questo task si salta: il worker legge i dati dai byte dell'anteprima.

**Files:**
- Modify: `olympus_connect/core/protocol.py` (dopo `screennail`)
- Test: `tests/test_protocol.py` (dopo `test_has_screennail_false_when_camera_lacks_it`)

**Interfaces:**
- Consumes: `OlympusClient.commands: set[str]`, `OlympusClient._get_ok(path, **params)`.
- Produces: `OlympusClient.has_exif() -> bool`, `OlympusClient.exif(camera_path: str) -> bytes`.

- [ ] **Step 1: Scrivi i test che falliscono**

In `tests/test_protocol.py`:

```python
def test_exif_returns_the_raw_bytes(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_exif.cgi", content=b"II*\x00dati")
    assert OlympusClient().exif("/DCIM/100OLYMP/A.ORF") == b"II*\x00dati"
    assert requests_mock.last_request.qs["dir"] == ["/dcim/100olymp/a.orf"]


def test_exif_raises_on_http_error(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_exif.cgi", status_code=404)
    with pytest.raises(ProtocolError):
        OlympusClient().exif("/DCIM/100OLYMP/A.ORF")


def test_has_exif_reads_the_real_commandlist(fixture_bytes):
    import xml.etree.ElementTree as ElementTree
    client = OlympusClient()
    radice = ElementTree.fromstring(fixture_bytes("commandlist.xml"))
    client.commands = {e.attrib["name"].strip() for e in radice.iter("cgi")}
    assert client.has_exif() is True


def test_has_exif_false_when_camera_lacks_it():
    client = OlympusClient()
    client.commands = {"get_thumbnail"}
    assert client.has_exif() is False
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_protocol.py -q -k exif`
Expected: 4 failed con `AttributeError: 'OlympusClient' object has no attribute 'exif'` / `'has_exif'`.

- [ ] **Step 3: Scrivi i metodi**

In `olympus_connect/core/protocol.py`, dopo `screennail`:

```python
    def has_exif(self) -> bool:
        """La fotocamera restituisce i dati di scatto di un file?

        Dichiarato dalla E-M10 Mark IV; formato e tempi misurati sul campo,
        vedi la specifica 2026-11-06-dati-di-scatto-design.md. Dove manca,
        la sezione dei dati di scatto non compare.
        """
        return "get_exif" in self.commands

    def exif(self, camera_path: str) -> bytes:
        """I dati di scatto grezzi di un file, da leggere con parse_exif."""
        return self._get_ok("get_exif.cgi", DIR=camera_path).content
```

- [ ] **Step 4: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_protocol.py -q`
Expected: tutti `passed`.

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/core/protocol.py tests/test_protocol.py
git commit -m "feat(core): get_exif nel client

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `PreviewWorker` emette i dati di scatto

**Files:**
- Modify: `olympus_connect/gui/workers.py` (import in testa, classe `PreviewWorker`)
- Test: `tests/test_workers.py` (in coda)

**Interfaces:**
- Consumes: `parse_exif` (Task 3); `classify(name) -> MediaKind` e `MediaKind` da `olympus_connect/core/model.py`; con la fonte A `client.has_exif()` e `client.exif(path)` (Task 5).
- Produces: `PreviewWorker.shot_ready = pyqtSignal(str, object)`, emesso con `(camera_path, ShotInfo | None)`; mai per i video, mai con il worker sospeso. I segnali esistenti `ready`, `failed`, `unavailable` non cambiano.

- [ ] **Step 1: Scrivi i test che falliscono**

In coda a `tests/test_workers.py`:

```python
# --- dati di scatto ------------------------------------------------------------

from fractions import Fraction  # noqa: E402

from olympus_connect.core.exif import ShotInfo  # noqa: E402

# Un EXIF minimo e valido: ISO 200, little-endian, solo IFD0 -> IFD EXIF.
_EXIF_ISO_200 = (
    b"II*\x00\x08\x00\x00\x00"
    b"\x01\x00" b"\x69\x87\x04\x00\x01\x00\x00\x00\x1a\x00\x00\x00" b"\x00\x00\x00\x00"
    b"\x01\x00" b"\x27\x88\x03\x00\x01\x00\x00\x00\xc8\x00\x00\x00" b"\x00\x00\x00\x00"
)


class _ClientConDati(FakeClient):
    def __init__(self, exif=_EXIF_ISO_200, exif_fail=None, screennail_fail=None,
                 has_exif=True):
        super().__init__()
        self.commands = {"get_exif", "get_screennail"}
        self.ordine: list[str] = []
        self._exif = exif
        self._exif_fail = exif_fail
        self._screennail_fail = screennail_fail
        self._has_exif = has_exif

    def has_exif(self):
        return self._has_exif

    def exif(self, camera_path):
        self.ordine.append("exif")
        if self._exif_fail:
            raise self._exif_fail
        return self._exif

    def has_screennail(self):
        return True

    def screennail(self, camera_path):
        self.ordine.append("screennail")
        if self._screennail_fail:
            raise self._screennail_fail
        return b"\xff\xd8grande"


def _registra(worker):
    dati, anteprime, errori = [], [], []
    worker.shot_ready.connect(lambda p, i: dati.append((p, i)))
    worker.ready.connect(lambda p, d: anteprime.append(p))
    worker.failed.connect(lambda p, m: errori.append(p))
    return dati, anteprime, errori


def test_preview_worker_reads_shot_data_before_the_preview(qapp):
    from olympus_connect.gui.workers import PreviewWorker
    client = _ClientConDati()
    worker = PreviewWorker(client)
    dati, anteprime, _ = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert client.ordine == ["exif", "screennail"]
    assert dati == [("/DCIM/100OLYMP/A.ORF", ShotInfo(iso=200))]
    assert anteprime == ["/DCIM/100OLYMP/A.ORF"]


def test_failed_shot_data_still_loads_the_preview(qapp):
    from olympus_connect.core.errors import ProtocolError
    from olympus_connect.gui.workers import PreviewWorker
    worker = PreviewWorker(_ClientConDati(exif_fail=ProtocolError("x")))
    dati, anteprime, errori = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert dati == [("/DCIM/100OLYMP/A.ORF", None)]
    assert anteprime == ["/DCIM/100OLYMP/A.ORF"]
    assert errori == []


def test_unreadable_shot_data_is_reported_as_none(qapp):
    from olympus_connect.gui.workers import PreviewWorker
    worker = PreviewWorker(_ClientConDati(exif=b"non exif"))
    dati, _, _ = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert dati == [("/DCIM/100OLYMP/A.ORF", None)]


def test_failed_preview_still_delivers_shot_data(qapp):
    from olympus_connect.core.errors import ProtocolError
    from olympus_connect.gui.workers import PreviewWorker
    worker = PreviewWorker(_ClientConDati(screennail_fail=ProtocolError("x")))
    dati, _, errori = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert dati == [("/DCIM/100OLYMP/A.ORF", ShotInfo(iso=200))]
    assert errori == ["/DCIM/100OLYMP/A.ORF"]


def test_no_shot_data_request_for_videos(qapp):
    from olympus_connect.gui.workers import PreviewWorker
    client = _ClientConDati()
    worker = PreviewWorker(client)
    dati, _, _ = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.MOV")
    assert "exif" not in client.ordine
    assert dati == []


def test_no_shot_data_when_the_camera_lacks_get_exif(qapp):
    from olympus_connect.gui.workers import PreviewWorker
    client = _ClientConDati(has_exif=False)
    worker = PreviewWorker(client)
    dati, anteprime, _ = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert client.ordine == ["screennail"]
    assert dati == []
    assert anteprime == ["/DCIM/100OLYMP/A.ORF"]


def test_no_shot_data_while_suspended(qapp):
    from olympus_connect.gui.workers import PreviewWorker
    client = _ClientConDati()
    worker = PreviewWorker(client)
    dati, _, _ = _registra(worker)
    worker.set_suspended(True)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert client.ordine == []
    assert dati == []
```

Verifica a mano `_EXIF_ISO_200`: intestazione `II*\0` + offset 8; IFD0 a 8 con una voce (tag 0x8769, tipo LONG, valore 26) e offset successivo 0, fine a 8+2+12+4 = 26; IFD EXIF a 26 con una voce (tag 0x8827, tipo SHORT, valore 200).

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_workers.py -q -k "shot or videos or get_exif"`
Expected: failed con `AttributeError: 'PreviewWorker' object has no attribute 'shot_ready'`.

- [ ] **Step 3: Modifica il worker (fonte A)**

In testa a `olympus_connect/gui/workers.py`, accanto agli altri import del core:

```python
from ..core.exif import parse_exif
from ..core.model import MediaKind, classify
```

Sostituisci in `PreviewWorker` la dichiarazione dei segnali e il metodo `request` con:

```python
    ready = pyqtSignal(str, bytes)
    failed = pyqtSignal(str, str)
    unavailable = pyqtSignal(str)
    shot_ready = pyqtSignal(str, object)     # percorso, ShotInfo | None
```

```python
    @pyqtSlot(str)
    def request(self, camera_path: str) -> None:
        if self._suspended:
            return
        # Ogni passo ha il suo try: questo slot è invocato da Qt attraverso
        # una connessione in coda, e un'eccezione che sfugge da qui non
        # risale a un chiamante Python ma fa abortire il processo.
        try:
            # Questo worker ha un client proprio, perché requests.Session non
            # è thread-safe. Un client nuovo non ha mai interrogato la
            # fotocamera e non ne conosce le capacità: senza questo passo
            # has_screennail risponde no anche quando la fotocamera le
            # anteprime le genera. Una volta sola, sul thread del worker.
            if not getattr(self._client, "commands", None):
                self._client.connect()
        except Exception as exc:
            self.failed.emit(camera_path, describe_error(exc))
            return
        # I dati di scatto prima: pochi KB, arrivano mentre l'anteprima da
        # 1 MB è ancora in viaggio.
        self._request_shot(camera_path)
        self._request_screennail(camera_path)

    def _request_shot(self, camera_path: str) -> None:
        if classify(camera_path) is MediaKind.MOVIE:
            return
        try:
            if not self._client.has_exif():
                return
            info = parse_exif(self._client.exif(camera_path))
        except Exception:
            info = None     # «non disponibili»; l'anteprima va chiesta comunque
        self.shot_ready.emit(camera_path, info)

    def _request_screennail(self, camera_path: str) -> None:
        try:
            if not self._client.has_screennail():
                # Altri modelli potrebbero non averlo: si dichiara una volta
                # invece di fallire a ogni selezione.
                self.unavailable.emit(camera_path)
                return
            self.ready.emit(camera_path, self._client.screennail(camera_path))
        except Exception as exc:
            self.failed.emit(camera_path, describe_error(exc))
```

**Variante per la fonte B.** Non c'è `_request_shot`; `request` chiama solo `_request_screennail`, che diventa:

```python
    def _request_screennail(self, camera_path: str) -> None:
        try:
            if not self._client.has_screennail():
                self.unavailable.emit(camera_path)
                return
            data = self._client.screennail(camera_path)
        except Exception as exc:
            self.failed.emit(camera_path, describe_error(exc))
            return
        self.ready.emit(camera_path, data)
        if classify(camera_path) is not MediaKind.MOVIE:
            # I dati di scatto viaggiano dentro l'anteprima (prova sul campo).
            self.shot_ready.emit(camera_path, parse_exif(data))
```

e nei test del Step 1, con la fonte B: `_ClientConDati.screennail` restituisce `b"Exif\x00\x00" + self._exif` invece di `b"\xff\xd8grande"` quando `self._exif` è impostato; `test_preview_worker_reads_shot_data_before_the_preview` verifica `client.ordine == ["screennail"]`; `test_failed_shot_data_still_loads_the_preview` e `test_no_shot_data_when_the_camera_lacks_get_exif` si tolgono; `test_failed_preview_still_delivers_shot_data` diventa `assert dati == []`.

- [ ] **Step 4: Verifica che passino tutti i test dei worker, vecchi e nuovi**

Run: `.venv/bin/python -m pytest tests/test_workers.py -q`
Expected: tutti `passed`, compreso `test_preview_worker_survives_a_client_without_the_capability_check`.

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/gui/workers.py tests/test_workers.py
git commit -m "feat(gui): il worker dell'anteprima porta anche i dati di scatto

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: La sezione "Scatto" nel pannello

**Files:**
- Create: `olympus_connect/gui/shot.py`
- Modify: `olympus_connect/gui/theme.py` (stile `#shotHeadline` nel foglio `STYLESHEET`, dopo la riga `#value`)
- Modify: `olympus_connect/gui/main_window.py` (import, `_build_inspector`, `_ensure_preview_worker`, `on_selection_changed`, `_suspend_thumbnails`, nuovo slot `on_shot_ready`)
- Test: `tests/test_main_window.py` (in coda)

**Interfaces:**
- Consumes: `ShotInfo`, `format_shot`, `FormattedShot` (Tasks 3–4); `PreviewWorker.shot_ready` (Task 6); `SectionLabel` da `gui/panels.py`; `theme.DURATION`.
- Produces: `ShotSection(QWidget)` con `show_shot(info: ShotInfo | None) -> None`, `clear() -> None`, e per i test `headline_text() -> str`, `rows() -> list[tuple[str, str]]`, `is_unavailable() -> bool`. In `MainWindow`: attributo `self._shot: ShotSection`, slot `on_shot_ready(camera_path: str, info: object)`.

- [ ] **Step 1: Scrivi i test che falliscono**

In coda a `tests/test_main_window.py`:

```python
# --- dati di scatto ---------------------------------------------------------------

from fractions import Fraction  # noqa: E402

from olympus_connect.core.exif import ShotInfo  # noqa: E402

_INFO = ShotInfo(exposure_time=Fraction(1, 60), f_number=Fraction(7, 2),
                 iso=6400, focal_length=Fraction(14),
                 lens="LUMIX G VARIO 14-42/F3.5-5.6 II", exposure_program=4)


def _seleziona(win, righe):
    from PyQt6.QtCore import QItemSelectionModel
    modello = win._grid.selectionModel()
    modello.clearSelection()
    for riga in righe:
        modello.select(win._model.index(riga, 0),
                       QItemSelectionModel.SelectionFlag.Select |
                       QItemSelectionModel.SelectionFlag.Rows)


def _finestra_con(windows, settings, tmp_path, *nomi):
    win = windows(settings)
    win.resize(1200, 700)
    win.show()
    kinds = {".ORF": MediaKind.RAW, ".MOV": MediaKind.MOVIE, ".JPG": MediaKind.JPEG}
    win.on_listed([job(n, JobState.NEW, tmp_path, kind=kinds[n[-4:]]) for n in nomi])
    return win


def test_shot_section_is_hidden_until_data_arrives(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    assert win._shot.isHidden()


def test_shot_section_shows_headline_and_rows(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    assert not win._shot.isHidden()
    assert win._shot.headline_text() == "1/60 s   f/3,5   ISO 6400   14 mm"
    assert win._shot.rows() == [
        ("Obiettivo", "LUMIX G VARIO 14-42/F3.5-5.6 II"),
        ("Modalità", "Priorità dei tempi"),
    ]


def test_shot_data_for_a_file_no_longer_selected_is_ignored(
        windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF", "B.ORF")
    _seleziona(win, [0])
    _seleziona(win, [1])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    assert win._shot.isHidden()


def test_missing_shot_data_shows_the_unavailable_line(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", None)
    assert not win._shot.isHidden()
    assert win._shot.is_unavailable()
    assert win._shot.rows() == []


def test_changing_selection_hides_the_previous_shot(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF", "B.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    _seleziona(win, [1])
    assert win._shot.isHidden()


def test_multiple_selection_hides_the_shot(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF", "B.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    _seleziona(win, [0, 1])
    assert win._shot.isHidden()


def test_starting_a_download_hides_the_shot(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    win._suspend_thumbnails(True)
    assert win._shot.isHidden()


def test_a_shot_with_only_rows_hides_the_empty_headline(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", ShotInfo(white_balance=0))
    assert win._shot.headline_text() == ""
    assert win._shot.rows() == [("Bilanciamento", "Auto")]
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_main_window.py -q -k shot`
Expected: failed con `AttributeError: 'MainWindow' object has no attribute '_shot'`.

- [ ] **Step 3: Scrivi il widget**

Create `olympus_connect/gui/shot.py`:

```python
"""La sezione «Scatto» del pannello: tempo, diaframma, ISO e il resto.

Sta sotto «Dettagli», non sopra: i dettagli si vedono subito, i dati di
scatto arrivano dalla fotocamera una frazione di secondo dopo, e aggiunti
in fondo non spostano nulla. Le righe dei campi assenti non compaiono: su
uno schermo alto 768 px un trattino costa una riga.
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect, QGridLayout, QLabel, QVBoxLayout, QWidget,
)

from ..core.exif import ShotInfo, format_shot
from . import theme
from .panels import SectionLabel

UNAVAILABLE = "Dati di scatto non disponibili"


def _label(text: str, role: str) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(role)
    return widget


class ShotSection(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(SectionLabel("Scatto"))

        self._headline = _label("", "shotHeadline")
        layout.addWidget(self._headline)

        self._unavailable = _label(UNAVAILABLE, "faint")
        layout.addWidget(self._unavailable)

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(4)
        self._grid.setColumnStretch(1, 1)
        layout.addLayout(self._grid)
        self._rows: list[tuple[str, str]] = []

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setEnabled(False)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(theme.DURATION)
        # Da un valore già visibile: una dissolvenza da 0 si legge come un
        # lampo, non come un arrivo (vedi preview.py).
        self._fade.setStartValue(0.35)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(lambda: self._effect.setEnabled(False))

        self.hide()

    # -- per la finestra -----------------------------------------------------

    def show_shot(self, info: ShotInfo | None) -> None:
        self._clear_rows()
        if info is None:
            self._headline.hide()
            self._unavailable.show()
        else:
            shot = format_shot(info)
            self._headline.setText(shot.headline)
            self._headline.setVisible(bool(shot.headline))
            self._unavailable.hide()
            for riga, (etichetta, valore) in enumerate(shot.rows):
                self._grid.addWidget(_label(etichetta, "faint"), riga, 0,
                                     Qt.AlignmentFlag.AlignTop)
                testo = _label(valore, "value")
                testo.setWordWrap(True)
                self._grid.addWidget(testo, riga, 1)
            self._rows = list(shot.rows)
        self.show()
        self._effect.setEnabled(True)
        self._fade.stop()
        self._fade.start()

    def clear(self) -> None:
        self._fade.stop()
        self._effect.setEnabled(False)
        self._clear_rows()
        self.hide()

    # -- per i test ------------------------------------------------------------

    def headline_text(self) -> str:
        return self._headline.text() if not self._headline.isHidden() else ""

    def rows(self) -> list[tuple[str, str]]:
        return list(self._rows)

    def is_unavailable(self) -> bool:
        return not self._unavailable.isHidden()

    # ---------------------------------------------------------------------------

    def _clear_rows(self) -> None:
        while self._grid.count():
            widget = self._grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []
```

- [ ] **Step 4: Aggiungi lo stile della riga di testa**

In `olympus_connect/gui/theme.py`, nel foglio `STYLESHEET`, subito dopo la riga che inizia con `#value {{`:

```css
#shotHeadline {{ color: {TEXT}; background: transparent; font-size: {SIZE_STRONG}px; font-weight: 600; }}
```

Le cifre tabulari sono già attive in tutta l'applicazione (DESIGN.md, «The Tabular Figures Rule»): niente da aggiungere.

- [ ] **Step 5: Collega la sezione alla finestra**

In `olympus_connect/gui/main_window.py`:

1. Import, accanto a `from .preview import PreviewView`:

```python
from .shot import ShotSection
```

2. In `_build_inspector`, sostituisci

```python
        corpo.addLayout(self._details)
        corpo.addStretch(1)
```

con

```python
        corpo.addLayout(self._details)

        self._shot = ShotSection()
        corpo.addWidget(self._shot)
        corpo.addStretch(1)
```

3. In `_ensure_preview_worker`, dopo `worker.unavailable.connect(self.on_preview_unavailable)`:

```python
        worker.shot_ready.connect(self.on_shot_ready)
```

4. In `on_selection_changed`, come prima riga dopo `self._update_selection_label()`:

```python
        # I dati di scatto mostrati sono quelli del file precedente: via
        # subito, ricompaiono quando arrivano quelli nuovi.
        self._shot.clear()
```

e nel ramo `if not jobs:` aggiungi `self._preview_path = None` prima del `return`, così una risposta in ritardo non riappare su una selezione vuota.

5. In `_suspend_thumbnails`, come prima riga:

```python
        if suspended:
            self._shot.clear()
```

6. Dopo `on_preview_unavailable`, il nuovo slot:

```python
    @pyqtSlot(str, object)
    def on_shot_ready(self, camera_path: str, info: object) -> None:
        if camera_path != self._preview_path or len(self.selected_jobs()) != 1:
            return      # la selezione è cambiata mentre arrivava
        self._shot.show_shot(info)
```

- [ ] **Step 6: Verifica che passino tutti i test della finestra**

Run: `.venv/bin/python -m pytest tests/test_main_window.py -q`
Expected: tutti `passed`.

- [ ] **Step 7: Guarda la sezione a schermo**

Run (con l'utente, o con lo screenshot offscreen già usato nel ridisegno): avvia l'app su dati veri e seleziona un ORF; controlla che la sezione stia sotto "Dettagli", che la riga di testa sia su una riga sola a 348 px di larghezza del pannello, e che a 1366×768 l'ultima riga ("Risoluzione") resti visibile senza tagli. Se la riga di testa va a capo con tempi lunghi (`1/4000 s   f/5,6   ISO 25600   300 mm`), riduci `HEADLINE_SEPARATOR` a due spazi in `core/exif.py` e aggiorna i due test che lo contengono.

- [ ] **Step 8: Commit**

```bash
git add olympus_connect/gui/shot.py olympus_connect/gui/theme.py olympus_connect/gui/main_window.py tests/test_main_window.py
git commit -m "feat(gui): sezione Scatto nel pannello, sotto i dettagli

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Test dall'inizio alla fine, documentazione

**Files:**
- Modify: `tests/test_end_to_end.py` (handler della finta fotocamera, nuovo test)
- Modify: `README.md` (tabella «Cosa funziona», schema della struttura, numero dei test)
- Modify: `DESIGN.md` (descrizione dell'ispettore)
- Modify: `docs/field-report-2026-09-24.md` (sezione in coda con l'esito della prova del Task 2)

**Interfaces:**
- Consumes: tutto il resto; `tests/fixtures/exif/orf.bin` (fonte A) oppure `screennail_orf.jpg` (fonte B).
- Produces: nessuna interfaccia nuova.

- [ ] **Step 1: Scrivi il test dall'inizio alla fine**

In `tests/test_end_to_end.py`, nel `do_GET` della fixture `fake_camera`, prima del ramo `get_thumbnail.cgi`:

```python
            if nome == "get_exif.cgi":
                return self._send((FIXTURES / "exif" / "orf.bin").read_bytes(),
                                  "application/octet-stream")
```

(Con la fonte B, invece, il ramo `get_screennail.cgi` restituisce `(FIXTURES / "exif" / "screennail_orf.jpg").read_bytes()` al posto di `anteprima`, e non serve il ramo `get_exif.cgi`.)

In coda al file:

```python
def test_real_client_shows_shot_data_of_the_selected_raw(qapp, fake_camera, tmp_path):
    if not (FIXTURES / "exif" / "orf.bin").exists():
        pytest.skip("fixture EXIF assente: eseguire diag.py con la fotocamera")
    impostazioni = QSettings(str(tmp_path / "e2e.ini"), QSettings.Format.IniFormat)
    impostazioni.setValue("dest_root", str(tmp_path / "dest"))
    finestra = MainWindow(
        client_factory=lambda: OlympusClient(base_url=fake_camera.base_url),
        network_factory=_Net,
        settings=impostazioni,
    )
    try:
        finestra.resize(1366, 740)
        finestra.show()
        finestra.on_refresh()
        assert _attendi(qapp, lambda: finestra._model.rowCount() == 1161)

        modello = finestra._model
        riga = next(r for r in range(modello.rowCount())
                    if modello.job_at(modello.index(r, 0)).media.name.upper()
                    .endswith(".ORF"))
        finestra._grid.selectionModel().select(
            finestra._model.index(riga, 0),
            QItemSelectionModel.SelectionFlag.Select |
            QItemSelectionModel.SelectionFlag.Rows)

        assert _attendi(qapp, lambda: not finestra._shot.isHidden()), \
            "la sezione Scatto non è comparsa"
        assert not finestra._shot.is_unavailable(), \
            "la sezione dice «non disponibili» sui byte veri della fotocamera"
        assert "ISO" in finestra._shot.headline_text()
        assert "get_exif.cgi" in fake_camera.richieste
    finally:
        finestra.close()
        qapp.processEvents()
```

- [ ] **Step 2: Verifica che passi**

Run: `.venv/bin/python -m pytest tests/test_end_to_end.py -q`
Expected: `2 passed`.

- [ ] **Step 3: Verifica che fallisca senza la modifica**

Commenta temporaneamente in `main_window.py` la riga `worker.shot_ready.connect(self.on_shot_ready)`.

Run: `.venv/bin/python -m pytest tests/test_end_to_end.py -q -k shot`
Expected: FAIL con «la sezione Scatto non è comparsa».

Ripristina la riga e rilancia: `1 passed`.

- [ ] **Step 4: Suite completa**

Run: `.venv/bin/python -m pytest -q`
Expected: tutti `passed`; annota il numero totale per il README.

- [ ] **Step 5: Documentazione**

`README.md`:
- nella tabella «Cosa funziona, verificato sul campo», dopo la riga delle miniature:

```markdown
| Dati di scatto prima di scaricare | ✅ tempo, diaframma, ISO, focale, obiettivo e altri, da <fonte del Task 2> |
```

  con al posto di `<fonte del Task 2>` «`get_exif.cgi`» oppure «l'EXIF dentro l'anteprima»;
- nel paragrafo «Uso», dopo il punto 4, un punto nuovo: «Selezionando un file, il pannello di destra mostra l'anteprima grande e i dati di scatto: tempo, diaframma, ISO, focale, obiettivo, modalità, compensazione, bilanciamento, flash e risoluzione»; rinumera i punti seguenti;
- nello schema della struttura, sotto `core/`, dopo `stats.py`:

```
│   ├── exif.py         dati di scatto dall'EXIF standard, in italiano
```

  e sotto `gui/`, dopo `preview.py`:

```
│   ├── shot.py         sezione Scatto del pannello
```

- nella sezione «Test», il numero dei test con quello del Step 4.

`DESIGN.md`, nella riga dell'**Inspector** del layout:

```markdown
- **Inspector** (right, 348px fixed): the large preview over a label/value detail grid, then the Shot section (exposure headline in Strong 13px/600, label/value rows for the fields present). Shot data arrives after the details, so it sits below them and never pushes them down.
```

`docs/field-report-2026-09-24.md`, in coda: una sezione «Aggiornamento 2026-11-DD: dati di scatto» con le righe del referto copiate da `diag-report.txt` (sezione «== Dati di scatto ==») e la fonte scelta. **Prima** di copiarle controlla che non contengano seriali: le righe di `get_exif` stampano solo i primi 16 byte, che sono l'intestazione TIFF, ma verificalo.

- [ ] **Step 6: Commit**

```bash
git add tests/test_end_to_end.py README.md DESIGN.md docs/field-report-2026-09-24.md
git status --short
git commit -m "test: dati di scatto dall'inizio alla fine; docs aggiornate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Il push su GitHub si fa solo su richiesta esplicita dell'utente.
