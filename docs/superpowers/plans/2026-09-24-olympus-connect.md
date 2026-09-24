# Olympus Connect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Applicazione desktop Linux (PyQt6) che si collega via Wi-Fi a una Olympus OM-D E-M10 Mark IV e scarica foto JPEG, RAW ORF e video MOV con ripresa dei trasferimenti interrotti.

**Architecture:** Core sincrono che non importa Qt (`model` → dati puri, `protocol` → HTTP/OPC, `network` → nmcli + diagnostica, `downloader` → streaming con ripresa), avvolto da una GUI PyQt6 che esegue ogni operazione di rete in un `QThread` e comunica per signal. I download sono seriali per scelta: il server HTTP della fotocamera è un microcontrollore.

**Tech Stack:** Python 3.14, `requests`, PyQt6, `pytest`, `requests-mock`. Tutte già presenti sul sistema tranne `pytest`/`requests-mock`.

**Spec:** [`docs/superpowers/specs/2026-09-24-olympus-connect-design.md`](../specs/2026-09-24-olympus-connect-design.md)

## Global Constraints

Ogni task eredita implicitamente questi vincoli.

- Base URL fotocamera: `http://192.168.0.10`
- Header obbligatori su **ogni** richiesta: `User-Agent: OI.Share v2`, `Host: 192.168.0.10`
- Timeout HTTP: 3 s connect, 15 s read
- Chunk di streaming: 65536 byte (64 KiB)
- Download **seriali**: mai più di una richiesta HTTP in volo verso la fotocamera
- Retry su caduta della connessione: 3 tentativi, backoff 2 s / 4 s / 8 s
- Destinazione predefinita: `~/Immagini/Olympus`
- Layout destinazione: `<dest>/YYYY/YYYY-MM-DD/NOMEFILE.EXT`
- Regola duplicati: salta se esiste stesso nome **e** stessa dimensione in byte
- **Nessuna cancellazione** di file sulla fotocamera: `exec_erase.cgi` non va mai invocato
- Il core (`olympus_connect/core/`) non importa `PyQt6`. Nessuna eccezione.
- Il core non chiama `print()`: comunica per valori di ritorno ed eccezioni
- `nmcli` e i comandi di sistema sono invocati solo attraverso un runner iniettabile
- La password Wi-Fi non viene mai scritta su file dall'applicazione: la conserva NetworkManager
- Interfaccia utente e messaggi d'errore in italiano; identificatori del codice in inglese

## File Structure

| File | Responsabilità |
|---|---|
| `olympus_connect/core/model.py` | `MediaKind`, `MediaFile`, decodifica data FAT, path di destinazione. Zero I/O. |
| `olympus_connect/core/errors.py` | Gerarchia di eccezioni tipizzate condivisa dal core. |
| `olympus_connect/core/protocol.py` | `OlympusClient`: commandlist, caminfo, cammode, imglist, thumbnail, streaming con `Range`. |
| `olympus_connect/core/downloader.py` | `Job`, `plan()`, `download()`: `.part`, ripresa, rename atomico, mtime, retry. |
| `olympus_connect/core/network.py` | `CameraNetwork`: scala diagnostica, profilo NetworkManager. |
| `olympus_connect/gui/workers.py` | `ListWorker`, `ThumbWorker`, `DownloadWorker`: `QObject` su `QThread`. |
| `olympus_connect/gui/grid.py` | `MediaListModel` + `MediaGrid` (`QListView` `IconMode`, virtualizzato). |
| `olympus_connect/gui/main_window.py` | Toolbar, status bar, pannello progressi, filtri, `QSettings`. |
| `olympus_connect/__main__.py` | Entry point: `python -m olympus_connect`. |
| `diag.py` | Strumento diagnostico da terminale. Permanente. |
| `tests/` | `test_model.py`, `test_protocol.py`, `test_downloader.py`, `test_network.py`, `conftest.py`, `fixtures/` |

---

### Task 0: Verifica sul campo con `diag.py`

**Questo task va eseguito con la fotocamera accesa, in modalità Wi-Fi, e il PC collegato al suo access point.** Risponde a tre domande che la spec dichiara aperte e produce le fixture usate da tutti i task successivi.

**Deviazione dichiarata dal TDD:** questo task non è test-first, e la deviazione è intenzionale. Lo scopo di `diag.py` è *scoprire* un comportamento hardware ignoto; scrivere prima un test significherebbe codificare l'assunzione che stiamo cercando di verificare. Da Task 1 in poi si torna a TDD stretto, e le fixture prodotte qui sono ciò che lo rende possibile.

**Files:**
- Create: `diag.py`
- Create: `tests/fixtures/` (output registrato)

**Interfaces:**
- Consumes: niente
- Produces: `tests/fixtures/commandlist.xml`, `tests/fixtures/caminfo.xml`, `tests/fixtures/imglist_dcim.csv`, `tests/fixtures/imglist_100olymp.csv`, più il referto `docs/field-report-2026-09-24.md` che registra le risposte alle tre domande aperte.

- [x] **Step 1: Creare `diag.py`**

> **Eseguito.** Il file in `diag.py` è la versione autorevole e differisce dalla bozza qui sotto in due punti, corretti dopo verifica empirica sul sistema reale: il gradino della rotta verifica che l'indirizzo sia **on-link** (assenza di ` via `) invece che l'esito di `ip route get`, e l'SSID attivo si legge da `nmcli device show` invece che dal campo `ACTIVE` di `nmcli device wifi`, inaffidabile. Le stesse due correzioni sono state applicate al Task 6. La bozza resta per tracciabilità.

```python
#!/usr/bin/env python3
"""Strumento diagnostico per la connessione alla fotocamera Olympus.

Eseguire con la fotocamera accesa in modalità Wi-Fi e il PC collegato
al suo access point:

    python3 diag.py
"""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import requests

BASE = "http://192.168.0.10"
UA = {"User-Agent": "OI.Share v2", "Host": "192.168.0.10"}
TIMEOUT = (3, 15)
FIXTURES = Path("tests/fixtures")


def step(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def ladder() -> bool:
    """Scala diagnostica: un gradino per ogni causa distinta."""
    print("\n== Scala diagnostica ==")

    wifi = subprocess.run(
        ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
        capture_output=True, text=True, timeout=10,
    )
    wifi_lines = [ln for ln in wifi.stdout.splitlines() if ":wifi:" in ln]
    step("dispositivo Wi-Fi presente", bool(wifi_lines), ", ".join(wifi_lines))

    ssid = subprocess.run(
        ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"],
        capture_output=True, text=True, timeout=10,
    )
    active = [ln.split(":", 1)[1] for ln in ssid.stdout.splitlines() if ln.startswith("yes:")]
    step("rete Wi-Fi attiva", bool(active), ", ".join(active) or "nessuna")

    route = subprocess.run(
        ["ip", "route", "get", "192.168.0.10"],
        capture_output=True, text=True, timeout=10,
    )
    step("route verso 192.168.0.10", route.returncode == 0, route.stdout.strip() or route.stderr.strip())

    tcp_ok = False
    try:
        with socket.create_connection(("192.168.0.10", 80), timeout=3):
            tcp_ok = True
    except OSError as exc:
        step("TCP 192.168.0.10:80", False, str(exc))
    if tcp_ok:
        step("TCP 192.168.0.10:80", True, "accetta")
    else:
        print("\n  La fotocamera non risponde sulla porta 80. Verifica che il Wi-Fi")
        print("  sia attivo sulla fotocamera e che il PC sia collegato al suo AP.")
        return False

    # Il gradino decisivo: la fotocamera risponde senza lo User-Agent di OI.Share?
    for label, headers in (("con User-Agent OI.Share", UA), ("senza User-Agent", {})):
        try:
            r = requests.get(f"{BASE}/get_caminfo.cgi", headers=headers, timeout=TIMEOUT)
            step(f"get_caminfo.cgi {label}", r.status_code == 200,
                 f"HTTP {r.status_code}, {len(r.content)} byte")
        except requests.RequestException as exc:
            step(f"get_caminfo.cgi {label}", False, type(exc).__name__)

    return True


def get(path: str, **params) -> requests.Response:
    r = requests.get(f"{BASE}/{path}", headers=UA, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def record() -> list[list[str]]:
    """Salva l'output grezzo della fotocamera e restituisce le righe di imglist."""
    print("\n== Registrazione fixture ==")
    FIXTURES.mkdir(parents=True, exist_ok=True)

    for name, path in (("commandlist.xml", "get_commandlist.cgi"),
                       ("caminfo.xml", "get_caminfo.cgi")):
        body = get(path).content
        (FIXTURES / name).write_bytes(body)
        print(f"  salvato {name} ({len(body)} byte)")

    get("switch_cammode.cgi", mode="play")
    print("  switch_cammode mode=play OK")

    rows: list[list[str]] = []
    for name, directory in (("imglist_dcim.csv", "/DCIM"),):
        body = get("get_imglist.cgi", DIR=directory).content
        (FIXTURES / name).write_bytes(body)
        print(f"  salvato {name} ({len(body)} byte)")
        for line in body.decode("ascii", "replace").splitlines():
            parts = line.split(",")
            if len(parts) == 6:
                rows.append(parts)

    # Le sottodirectory (bit 16 in attrib) vanno esplorate a loro volta.
    for parts in list(rows):
        if int(parts[3]) & 16:
            subdir = f"{parts[0]}/{parts[1]}"
            body = get("get_imglist.cgi", DIR=subdir).content
            fname = f"imglist_{parts[1].lower()}.csv"
            (FIXTURES / fname).write_bytes(body)
            print(f"  salvato {fname} ({len(body)} byte) per {subdir}")
            for line in body.decode("ascii", "replace").splitlines():
                p = line.split(",")
                if len(p) == 6 and not int(p[3]) & 16:
                    rows.append(p)

    return [p for p in rows if not int(p[3]) & 16]


def probe_downloads(rows: list[list[str]]) -> None:
    """Verifica: RAW scaricabile? MOV scaricabile? Range supportato?"""
    print("\n== Download e Range ==")
    by_ext: dict[str, list[str]] = {}
    for parts in rows:
        ext = Path(parts[1]).suffix.upper()
        by_ext.setdefault(ext, []).append(f"{parts[0]}/{parts[1]}")

    print(f"  estensioni sulla scheda: "
          f"{', '.join(f'{k} x{len(v)}' for k, v in sorted(by_ext.items()))}")

    out = Path("diag-downloads")
    out.mkdir(exist_ok=True)

    for ext in (".JPG", ".ORF", ".MOV"):
        paths = by_ext.get(ext)
        if not paths:
            print(f"  [SALTATO] nessun file {ext} sulla scheda")
            continue
        camera_path = paths[0]

        try:
            r = requests.get(f"{BASE}{camera_path}", headers=UA, timeout=TIMEOUT, stream=True)
            size = 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
            ok = r.status_code == 200 and size > 0
            step(f"download {ext} ({camera_path})", ok, f"HTTP {r.status_code}, {size} byte")
            if not ok:
                continue
        except requests.RequestException as exc:
            step(f"download {ext} ({camera_path})", False, type(exc).__name__)
            continue

        # Richiesta Range: la fotocamera risponde 206 (ripresa possibile) o 200?
        headers = dict(UA, Range="bytes=1024-")
        r = requests.get(f"{BASE}{camera_path}", headers=headers, timeout=TIMEOUT, stream=True)
        got = sum(len(c) for c in r.iter_content(65536))
        step(f"Range su {ext}", r.status_code == 206,
             f"HTTP {r.status_code}, Content-Range={r.headers.get('Content-Range', 'assente')}, "
             f"{got} byte (attesi {size - 1024})")


def main() -> int:
    print("Olympus Connect — diagnostica")
    if not ladder():
        return 1
    try:
        rows = record()
    except requests.RequestException as exc:
        print(f"\nErrore durante la registrazione: {exc}")
        return 1
    print(f"\n  {len(rows)} file trovati sulla scheda")
    probe_downloads(rows)
    print("\nFatto. Fixture in tests/fixtures/, campioni in diag-downloads/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Eseguire con la fotocamera collegata**

```bash
python3 diag.py
```

Atteso: la scala diagnostica arriva a `get_caminfo.cgi`, le fixture vengono scritte, e la sezione finale stampa un OK/FAIL per `.JPG`, `.ORF`, `.MOV` e per il `Range` di ciascuno.

- [ ] **Step 3: Registrare il referto**

Creare `docs/field-report-2026-09-24.md` con l'output completo di `diag.py` incollato, più una tabella di tre righe che risponde:

| Domanda | Risposta | Conseguenza sul piano |
|---|---|---|
| ORF scaricabile via `GET`? | | se no: `MediaKind.RAW` resta nel modello ma i job RAW vengono marcati non disponibili |
| MOV scaricabile via `GET`? | | se no: idem per `MediaKind.MOVIE` |
| `Range` produce `206`? | | se no: Task 6 implementa solo ripartenza da zero, e il test "ignora Range" diventa l'unico percorso |
| `get_caminfo.cgi` risponde senza `User-Agent`? | | se sì: la causa #1 della spec è smentita e va corretta nella spec |

- [ ] **Step 4: Commit**

```bash
git add diag.py tests/fixtures docs/field-report-2026-09-24.md
git commit -m "feat: diag.py e referto della verifica sul campo con E-M10 Mark IV"
```

- [ ] **Step 5: Aggiornare la spec se un'assunzione è caduta**

Se il referto smentisce un'assunzione della spec, correggere la sezione *Assunzioni da verificare* di `docs/superpowers/specs/2026-09-24-olympus-connect-design.md` con il fatto accertato, e committare separatamente. Non lasciare la spec in contraddizione con il referto.

---

### Task 1: Modello dati e decodifica della data FAT

**Files:**
- Create: `olympus_connect/__init__.py` (vuoto)
- Create: `olympus_connect/core/__init__.py` (vuoto)
- Create: `olympus_connect/core/model.py`
- Create: `tests/__init__.py` (vuoto)
- Test: `tests/test_model.py`
- Create: `pyproject.toml`

**Interfaces:**
- Consumes: niente
- Produces:
  - `MediaKind` — `Enum` con membri `JPEG`, `RAW`, `MOVIE`, `OTHER`
  - `classify(name: str) -> MediaKind`
  - `decode_fat_datetime(date: int, time: int) -> datetime` — solleva `ValueError` su valori impossibili
  - `MediaFile(camera_path: str, size: int, shot_at: datetime | None, kind: MediaKind)` — dataclass congelata, con proprietà `name: str` e `dest_relpath: Path`
  - `parse_imglist(text: str) -> tuple[list[MediaFile], list[str]]` — restituisce (file, sottodirectory da esplorare)

- [ ] **Step 1: Creare `pyproject.toml`**

```toml
[project]
name = "olympus-connect"
version = "0.1.0"
description = "Scarica foto e video da fotocamere Olympus via Wi-Fi su Linux"
requires-python = ">=3.12"
dependencies = ["requests", "PyQt6"]

[project.optional-dependencies]
dev = ["pytest", "requests-mock"]

[project.scripts]
olympus-connect = "olympus_connect.__main__:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Installare le dipendenze di sviluppo**

```bash
python3 -m venv .venv && .venv/bin/pip install -q -e '.[dev]'
```

Nota: `PyQt6` e `requests` sono già installati a livello di sistema, ma il venv li reinstalla per isolamento. Se l'installazione di PyQt6 nel venv fosse lenta o problematica, creare il venv con `--system-site-packages` e installare solo `pytest requests-mock`.

- [ ] **Step 3: Scrivere i test che falliscono**

```python
# tests/test_model.py
from datetime import datetime
from pathlib import Path

import pytest

from olympus_connect.core.model import (
    MediaFile, MediaKind, classify, decode_fat_datetime, parse_imglist,
)


def test_decode_fat_datetime_reference_value():
    # 19594 = 2018-04-10, 45820 = 22:23:44 (secondi in unità da 2)
    assert decode_fat_datetime(19594, 45820) == datetime(2018, 4, 10, 22, 23, 44)


def test_decode_fat_datetime_epoch_is_1980():
    # date=0x0021 -> anno 1980, mese 1, giorno 1; time=0 -> 00:00:00
    assert decode_fat_datetime(0x0021, 0) == datetime(1980, 1, 1, 0, 0, 0)


def test_decode_fat_datetime_rejects_zero():
    # mese 0 e giorno 0 non esistono: la fotocamera ha dato un valore non valido
    with pytest.raises(ValueError):
        decode_fat_datetime(0, 0)


@pytest.mark.parametrize("name,expected", [
    ("P9240123.JPG", MediaKind.JPEG),
    ("p9240123.jpg", MediaKind.JPEG),
    ("P9240123.JPEG", MediaKind.JPEG),
    ("P9240123.ORF", MediaKind.RAW),
    ("P9240123.MOV", MediaKind.MOVIE),
    ("P9240123.MP4", MediaKind.MOVIE),
    ("OLYMPUS.INI", MediaKind.OTHER),
])
def test_classify(name, expected):
    assert classify(name) == expected


def test_media_file_name_and_dest_relpath():
    f = MediaFile("/DCIM/100OLYMP/P9240123.JPG", 5_000_000,
                  datetime(2026, 9, 24, 18, 5, 0), MediaKind.JPEG)
    assert f.name == "P9240123.JPG"
    assert f.dest_relpath == Path("2026/2026-09-24/P9240123.JPG")


def test_media_file_without_date_goes_to_senza_data():
    f = MediaFile("/DCIM/100OLYMP/P9240123.JPG", 1, None, MediaKind.JPEG)
    assert f.dest_relpath == Path("senza-data/P9240123.JPG")


def test_parse_imglist_returns_files_and_subdirs():
    text = (
        "/DCIM,100OLYMP,0,16,19594,45820\r\n"
        "/DCIM/100OLYMP,P9240123.JPG,5000000,0,19594,45820\r\n"
        "/DCIM/100OLYMP,P9240123.ORF,17150208,0,19594,45820\r\n"
    )
    files, subdirs = parse_imglist(text)
    assert subdirs == ["/DCIM/100OLYMP"]
    assert [f.camera_path for f in files] == [
        "/DCIM/100OLYMP/P9240123.JPG",
        "/DCIM/100OLYMP/P9240123.ORF",
    ]
    assert files[1].size == 17_150_208
    assert files[1].kind == MediaKind.RAW


def test_parse_imglist_skips_hidden_system_and_volume():
    text = (
        "/DCIM/100OLYMP,HIDDEN.JPG,1,2,19594,45820\r\n"
        "/DCIM/100OLYMP,SYSTEM.JPG,1,4,19594,45820\r\n"
        "/DCIM/100OLYMP,VOLUME.JPG,1,8,19594,45820\r\n"
        "/DCIM/100OLYMP,GOOD.JPG,1,0,19594,45820\r\n"
    )
    files, _ = parse_imglist(text)
    assert [f.name for f in files] == ["GOOD.JPG"]


def test_parse_imglist_ignores_malformed_lines():
    text = "garbage\r\n\r\n/DCIM/100OLYMP,GOOD.JPG,1,0,19594,45820\r\n"
    files, _ = parse_imglist(text)
    assert [f.name for f in files] == ["GOOD.JPG"]


def test_parse_imglist_ignores_version_header():
    # La fotocamera reale premette una riga "VER_100" alla risposta.
    text = "VER_100\r\n/DCIM/100OLYMP,GOOD.JPG,1,0,19594,45820\r\n"
    files, _ = parse_imglist(text)
    assert [f.name for f in files] == ["GOOD.JPG"]


def test_parse_imglist_keeps_read_only_files():
    # attrib=1 è il bit FAT read-only e compare nei dati reali della scheda
    # (P9061187.ORF): è un file normale, non va scartato con hidden/system.
    text = "/DCIM/100OLYMP,P9061187.ORF,19578383,1,23626,28090\r\n"
    files, _ = parse_imglist(text)
    assert [f.name for f in files] == ["P9061187.ORF"]


def test_decode_fat_datetime_matches_real_camera_row():
    # Riga reale: /DCIM/100OLYMP,P2100001.ORF,19578383,0,23626,28090
    # EXIF letto da exiv2 sullo stesso file: 2026-02-10 13:45:53.
    # Lo scarto di 1 s è la granularità a 2 secondi del formato FAT.
    assert decode_fat_datetime(23626, 28090) == datetime(2026, 2, 10, 13, 45, 52)


def test_parse_imglist_keeps_file_with_invalid_date():
    # Una data non valida non deve far perdere il file.
    text = "/DCIM/100OLYMP,ODD.JPG,1,0,0,0\r\n"
    files, _ = parse_imglist(text)
    assert files[0].shot_at is None
```

- [ ] **Step 4: Eseguire i test per verificare che falliscano**

Run: `.venv/bin/pytest tests/test_model.py -v`
Atteso: FAIL con `ModuleNotFoundError: No module named 'olympus_connect'`

- [ ] **Step 5: Implementare `model.py`**

```python
"""Dati puri: nessun I/O, nessuna rete, nessun Qt."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

# Bit di attributo nel formato imglist della fotocamera (convenzione FAT).
ATTR_HIDDEN = 2
ATTR_SYSTEM = 4
ATTR_VOLUME = 8
ATTR_DIRECTORY = 16

CHUNK_SIZE = 65536


class MediaKind(Enum):
    JPEG = "jpeg"
    RAW = "raw"
    MOVIE = "movie"
    OTHER = "other"


_EXTENSIONS = {
    ".JPG": MediaKind.JPEG,
    ".JPEG": MediaKind.JPEG,
    ".ORF": MediaKind.RAW,
    ".MOV": MediaKind.MOVIE,
    ".MP4": MediaKind.MOVIE,
}


def classify(name: str) -> MediaKind:
    return _EXTENSIONS.get(Path(name).suffix.upper(), MediaKind.OTHER)


def decode_fat_datetime(date: int, time: int) -> datetime:
    """Decodifica i campi data/ora di get_imglist.cgi (campi di bit FAT).

    Solleva ValueError se i campi non descrivono una data reale: alcune
    fotocamere emettono zeri per file senza timestamp valido.
    """
    return datetime(
        1980 + (date >> 9),
        (date >> 5) & 0x0F,
        date & 0x1F,
        time >> 11,
        (time >> 5) & 0x3F,
        2 * (time & 0x1F),
    )


@dataclass(frozen=True)
class MediaFile:
    camera_path: str   # es. "/DCIM/100OLYMP/P9240123.JPG"
    size: int          # byte
    shot_at: datetime | None
    kind: MediaKind

    @property
    def name(self) -> str:
        return self.camera_path.rsplit("/", 1)[-1]

    @property
    def dest_relpath(self) -> Path:
        if self.shot_at is None:
            return Path("senza-data") / self.name
        return Path(f"{self.shot_at:%Y}") / f"{self.shot_at:%Y-%m-%d}" / self.name


def parse_imglist(text: str) -> tuple[list[MediaFile], list[str]]:
    """Interpreta la risposta CSV di get_imglist.cgi.

    Restituisce (file trovati, sottodirectory da esplorare a loro volta).
    Righe malformate vengono ignorate: la fotocamera a volte emette
    intestazioni o righe vuote.
    """
    files: list[MediaFile] = []
    subdirs: list[str] = []

    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) != 6:
            continue
        directory, name = parts[0], parts[1]
        try:
            size, attrib, date, time = (int(p) for p in parts[2:])
        except ValueError:
            continue

        path = f"{directory}/{name}"

        if attrib & ATTR_DIRECTORY:
            subdirs.append(path)
            continue
        if attrib & (ATTR_HIDDEN | ATTR_SYSTEM | ATTR_VOLUME):
            continue

        try:
            shot_at: datetime | None = decode_fat_datetime(date, time)
        except ValueError:
            shot_at = None   # non perdere il file per una data illeggibile

        files.append(MediaFile(path, size, shot_at, classify(name)))

    return files, subdirs
```

- [ ] **Step 6: Eseguire i test per verificare che passino**

Run: `.venv/bin/pytest tests/test_model.py -v`
Atteso: PASS, 14 test

- [ ] **Step 7: Verificare il parsing contro la fixture reale**

```bash
.venv/bin/python -c "
from pathlib import Path
from olympus_connect.core.model import parse_imglist
for p in sorted(Path('tests/fixtures').glob('imglist_*.csv')):
    files, subdirs = parse_imglist(p.read_text('ascii', 'replace'))
    print(p.name, len(files), 'file,', len(subdirs), 'subdir')
    for f in files[:3]:
        print('   ', f.camera_path, f.size, f.shot_at, f.kind.value)
"
```

Atteso: date plausibili (non 1980, non nel futuro) e dimensioni coerenti con i tipi di file. Se le date sono assurde, la decodifica FAT va corretta **ora**, prima che tutto il resto vi si appoggi.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml olympus_connect tests
git commit -m "feat(core): modello dati, decodifica data FAT, parsing imglist"
```

---

### Task 2: Client di protocollo — sessione, identificazione, elenco

**Files:**
- Create: `olympus_connect/core/errors.py`
- Create: `olympus_connect/core/protocol.py`
- Test: `tests/test_protocol.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Consumes: `MediaFile`, `parse_imglist` da Task 1
- Produces:
  - `errors.py`: `OlympusError` (base), `CameraNotReachable`, `ProtocolError`, `CameraAsleep`, `IncompleteDownload`, `NetworkUnavailable`, `WrongNetwork`
  - `OlympusClient(base_url: str = "http://192.168.0.10", session: requests.Session | None = None)`
  - `OlympusClient.connect() -> str` — restituisce il modello, popola `.commands: set[str]`
  - `OlympusClient.ensure_play_mode() -> None`
  - `OlympusClient.keep_awake(seconds: int = 3600) -> bool`
  - `OlympusClient.list_media(directory: str = "/DCIM") -> list[MediaFile]`
  - `OlympusClient.thumbnail(camera_path: str) -> bytes`

- [ ] **Step 1: Creare `errors.py`**

```python
"""Eccezioni del core. Nessun messaggio tecnico crudo verso l'utente:
la GUI mappa questi tipi su testi in italiano."""


class OlympusError(Exception):
    """Base di tutti gli errori del core."""


class CameraNotReachable(OlympusError):
    """Nessuna rotta, connessione rifiutata, o la fotocamera non risponde."""


class CameraAsleep(OlympusError):
    """La fotocamera ha chiuso la connessione o l'access point a metà operazione."""


class ProtocolError(OlympusError):
    """Risposta inattesa dalla fotocamera."""

    def __init__(self, message: str, raw: bytes = b"") -> None:
        super().__init__(message)
        self.raw = raw


class IncompleteDownload(OlympusError):
    """Il trasferimento è finito con una dimensione diversa da quella attesa."""


class NetworkUnavailable(OlympusError):
    """nmcli assente o non utilizzabile: la gestione della rete è indisponibile."""


class WrongNetwork(OlympusError):
    """Il PC è associato a una rete Wi-Fi che non è quella della fotocamera."""
```

- [ ] **Step 2: Scrivere `tests/conftest.py`**

```python
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    """Legge una fixture registrata dalla fotocamera reale (Task 0)."""
    def _read(name: str) -> bytes:
        path = FIXTURES / name
        if not path.exists():
            pytest.skip(f"fixture {name} assente: eseguire diag.py con la fotocamera")
        return path.read_bytes()
    return _read
```

- [ ] **Step 3: Scrivere i test che falliscono**

```python
# tests/test_protocol.py
import pytest
import requests

from olympus_connect.core.errors import CameraNotReachable, ProtocolError
from olympus_connect.core.protocol import BASE_URL, HEADERS, OlympusClient

CAMINFO = b'<?xml version="1.0"?><caminfo><model>E-M10MarkIV</model></caminfo>'
# Il root reale è <oishare>, non <oimp>: verificato sulla fotocamera.
COMMANDLIST = (
    b'<?xml version="1.0"?><oishare>'
    b'<version>4.50</version>'
    b'<cgi name="get_imglist"><http_method type="get"/></cgi>'
    b'<cgi name="switch_cammode"><http_method type="get"/></cgi>'
    b'<cgi name="get_thumbnail"><http_method type="get"/></cgi>'
    b'</oishare>'
)
COMMANDLIST_WITH_TIMEOUT = COMMANDLIST.replace(
    b'</oishare>',
    b'<cgi name="set_timeout"><http_method type="get"/></cgi></oishare>',
)


def test_headers_include_oishare_user_agent():
    # Senza questo header la fotocamera scarta la richiesta: è la causa
    # per cui browser e wget non scaricano nulla.
    assert HEADERS["User-Agent"] == "OI.Share v2"
    assert HEADERS["Host"] == "192.168.0.10"


def test_connect_returns_model_and_populates_commands(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi", content=COMMANDLIST)
    requests_mock.get(f"{BASE_URL}/get_caminfo.cgi", content=CAMINFO)

    client = OlympusClient()
    assert client.connect() == "E-M10MarkIV"
    assert "get_imglist" in client.commands
    assert "switch_cammode" in client.commands


def test_connect_sends_user_agent(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi", content=COMMANDLIST)
    requests_mock.get(f"{BASE_URL}/get_caminfo.cgi", content=CAMINFO)

    OlympusClient().connect()
    assert requests_mock.request_history[0].headers["User-Agent"] == "OI.Share v2"


def test_connect_raises_camera_not_reachable_on_connection_error(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi",
                      exc=requests.exceptions.ConnectionError)
    with pytest.raises(CameraNotReachable):
        OlympusClient().connect()


def test_connect_raises_protocol_error_on_garbage(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi", content=b"not xml at all")
    with pytest.raises(ProtocolError):
        OlympusClient().connect()


def test_ensure_play_mode_issues_switch_cammode(requests_mock):
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")
    client = OlympusClient()
    client.ensure_play_mode()
    assert requests_mock.last_request.qs["mode"] == ["play"]


def test_ensure_play_mode_is_idempotent(requests_mock):
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")
    client = OlympusClient()
    client.ensure_play_mode()
    client.ensure_play_mode()
    assert len(requests_mock.request_history) == 1


def test_keep_awake_sends_timeoutsec(requests_mock):
    # Senza supporto Range un'interruzione costa l'intero file (il MOV più
    # grande sulla scheda è di 944 MB): evitare che la fotocamera si
    # addormenti è la mitigazione più efficace disponibile.
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi", content=COMMANDLIST_WITH_TIMEOUT)
    requests_mock.get(f"{BASE_URL}/get_caminfo.cgi", content=CAMINFO)
    requests_mock.get(f"{BASE_URL}/set_timeout.cgi", content=b"")

    client = OlympusClient()
    client.connect()
    assert client.keep_awake(1800) is True
    assert requests_mock.last_request.qs["timeoutsec"] == ["1800"]


def test_keep_awake_returns_false_when_camera_lacks_the_command():
    # Altri modelli non dichiarano set_timeout: non è un errore, si rinuncia.
    client = OlympusClient()
    client.commands = {"get_imglist"}
    assert client.keep_awake() is False


def test_keep_awake_swallows_failures(requests_mock):
    # Un keep-alive fallito non deve impedire di scaricare le foto.
    requests_mock.get(f"{BASE_URL}/set_timeout.cgi", status_code=500, content=b"nope")
    client = OlympusClient()
    client.commands = {"set_timeout"}
    assert client.keep_awake() is False


def test_list_media_recurses_into_subdirectories(requests_mock):
    def body(request, context):
        directory = request.qs["dir"][0]
        if directory == "/dcim":
            return "/DCIM,100OLYMP,0,16,19594,45820\r\n"
        return "/DCIM/100OLYMP,P9240123.JPG,5000000,0,19594,45820\r\n"

    requests_mock.get(f"{BASE_URL}/get_imglist.cgi", text=body)
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")

    files = OlympusClient().list_media()
    assert [f.name for f in files] == ["P9240123.JPG"]


def test_list_media_treats_404_as_empty_directory(requests_mock):
    # La fotocamera risponde 404 per una directory vuota: non è un errore.
    requests_mock.get(f"{BASE_URL}/get_imglist.cgi", status_code=404)
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")
    assert OlympusClient().list_media() == []


def test_thumbnail_returns_bytes(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_thumbnail.cgi", content=b"\xff\xd8jpeg")
    assert OlympusClient().thumbnail("/DCIM/100OLYMP/P9240123.JPG") == b"\xff\xd8jpeg"


def test_connect_parses_real_camera_fixtures(requests_mock, fixture_bytes):
    # Il test che conta: output registrato dalla fotocamera reale (Task 0).
    requests_mock.get(f"{BASE_URL}/get_commandlist.cgi",
                      content=fixture_bytes("commandlist.xml"))
    requests_mock.get(f"{BASE_URL}/get_caminfo.cgi",
                      content=fixture_bytes("caminfo.xml"))

    client = OlympusClient()
    model = client.connect()
    assert model
    assert "get_imglist" in client.commands
```

- [ ] **Step 4: Eseguire i test per verificare che falliscano**

Run: `.venv/bin/pytest tests/test_protocol.py -v`
Atteso: FAIL con `ModuleNotFoundError: No module named 'olympus_connect.core.protocol'`

- [ ] **Step 5: Implementare `protocol.py`**

```python
"""Client HTTP per il protocollo OPC delle fotocamere Olympus.

La fotocamera in modalità access point espone un server HTTP su
192.168.0.10 che scarta le richieste prive dello User-Agent di OI.Share.
"""
from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from contextlib import contextmanager
from typing import Iterator

import requests

from .errors import CameraAsleep, CameraNotReachable, OlympusError, ProtocolError
from .model import MediaFile, parse_imglist

BASE_URL = "http://192.168.0.10"

HEADERS = {
    "User-Agent": "OI.Share v2",   # senza questo la fotocamera non risponde
    "Host": "192.168.0.10",
}

TIMEOUT = (3, 15)   # (connect, read) in secondi


class OlympusClient:
    def __init__(self, base_url: str = BASE_URL,
                 session: requests.Session | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self.commands: set[str] = set()
        self.model: str = ""
        self._mode: str | None = None

    # -- livello trasporto ------------------------------------------------

    def _get(self, path: str, *, stream: bool = False,
             headers: dict[str, str] | None = None, **params) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            return self._session.get(
                url, headers={**HEADERS, **(headers or {})},
                params=params, timeout=TIMEOUT, stream=stream,
            )
        except requests.exceptions.ConnectionError as exc:
            raise CameraNotReachable(
                "La fotocamera non risponde su 192.168.0.10."
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise CameraAsleep(
                "La fotocamera non ha risposto entro il tempo previsto."
            ) from exc

    def _get_ok(self, path: str, **params) -> requests.Response:
        response = self._get(path, **params)
        if response.status_code != 200:
            raise ProtocolError(
                f"{path} ha risposto HTTP {response.status_code}.", response.content
            )
        return response

    @staticmethod
    def _xml(response: requests.Response) -> ElementTree.Element:
        try:
            return ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise ProtocolError(
                f"Risposta XML non interpretabile da {response.url}.", response.content
            ) from exc

    # -- identificazione --------------------------------------------------

    def connect(self) -> str:
        """Interroga la fotocamera su cosa supporta. Restituisce il modello."""
        root = self._xml(self._get_ok("get_commandlist.cgi"))
        self.commands = {
            elem.attrib["name"].strip()
            for elem in root.iter("cgi") if "name" in elem.attrib
        }
        if not self.commands:
            raise ProtocolError("get_commandlist.cgi non elenca alcun comando.")

        info = self._xml(self._get_ok("get_caminfo.cgi"))
        model = info.findtext("model")
        self.model = model.strip() if model else "modello sconosciuto"
        return self.model

    def ensure_play_mode(self) -> None:
        """La fotocamera elenca i file solo in modalità riproduzione."""
        if self._mode == "play":
            return
        self._get_ok("switch_cammode.cgi", mode="play")
        self._mode = "play"

    def keep_awake(self, seconds: int = 3600) -> bool:
        """Chiede alla fotocamera di non spegnere l'access point.

        Questo firmware non supporta le richieste Range, quindi un
        trasferimento interrotto riparte da zero: su un video da centinaia
        di MB il costo è alto e prevenire lo standby è la difesa migliore.

        Restituisce False se la fotocamera non dichiara set_timeout o se la
        richiesta fallisce: è un'ottimizzazione, non un requisito, e non
        deve mai impedire di scaricare.
        """
        if "set_timeout" not in self.commands:
            return False
        try:
            self._get_ok("set_timeout.cgi", timeoutsec=seconds)
            return True
        except OlympusError:
            return False

    # -- elenco e miniature -----------------------------------------------

    def list_media(self, directory: str = "/DCIM") -> list[MediaFile]:
        """Elenca ricorsivamente i file multimediali sulla scheda."""
        self.ensure_play_mode()
        found: list[MediaFile] = []
        pending = [directory]
        seen: set[str] = set()

        while pending:
            current = pending.pop()
            if current in seen:        # difesa contro directory cicliche
                continue
            seen.add(current)

            response = self._get("get_imglist.cgi", DIR=current)
            if response.status_code == 404:
                continue               # directory vuota, non un errore
            if response.status_code != 200:
                raise ProtocolError(
                    f"get_imglist.cgi su {current} ha risposto "
                    f"HTTP {response.status_code}.", response.content
                )

            files, subdirs = parse_imglist(response.text)
            found.extend(files)
            pending.extend(subdirs)

        return found

    def thumbnail(self, camera_path: str) -> bytes:
        return self._get_ok("get_thumbnail.cgi", DIR=camera_path).content
```

- [ ] **Step 6: Eseguire i test per verificare che passino**

Run: `.venv/bin/pytest tests/test_protocol.py -v`
Atteso: PASS. Il test `test_connect_parses_real_camera_fixtures` passa se Task 0 è stato eseguito, altrimenti è `SKIPPED` — se è SKIPPED, **fermarsi**: significa che non stiamo validando contro la fotocamera reale.

- [ ] **Step 7: Commit**

```bash
git add olympus_connect/core/errors.py olympus_connect/core/protocol.py tests
git commit -m "feat(core): client di protocollo OPC con elenco ricorsivo"
```

---

### Task 3: Streaming con rilevamento del supporto `Range`

**Files:**
- Modify: `olympus_connect/core/protocol.py` (aggiunge `Stream` e `open_stream`)
- Test: `tests/test_protocol.py` (aggiunge la classe di test dello streaming)

**Interfaces:**
- Consumes: `OlympusClient._get` da Task 2
- Produces:
  - `Stream(total: int, resumed: bool, chunks: Iterator[bytes])` — dataclass
  - `OlympusClient.open_stream(camera_path: str, offset: int = 0)` — context manager che produce `Stream`. `resumed` è `True` solo se il server ha risposto `206`, cioè se la ripresa è realmente in corso.

- [ ] **Step 1: Scrivere i test che falliscono**

```python
# da aggiungere a tests/test_protocol.py
from olympus_connect.core.protocol import Stream


def test_open_stream_reports_total_from_content_length(requests_mock):
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG",
                      content=b"x" * 1000,
                      headers={"Content-Length": "1000"})
    with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG") as stream:
        assert stream.total == 1000
        assert stream.resumed is False
        assert b"".join(stream.chunks) == b"x" * 1000


def test_open_stream_with_offset_sends_range_header(requests_mock):
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG", content=b"y" * 100)
    with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG", offset=500):
        pass
    assert requests_mock.last_request.headers["Range"] == "bytes=500-"


def test_open_stream_detects_honoured_range(requests_mock):
    # 206 + Content-Range: la ripresa funziona, total viene dal denominatore.
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG",
                      status_code=206, content=b"z" * 500,
                      headers={"Content-Range": "bytes 500-999/1000"})
    with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG", offset=500) as stream:
        assert stream.resumed is True
        assert stream.total == 1000


def test_open_stream_detects_ignored_range(requests_mock):
    # 200 nonostante il Range: il firmware lo ignora, si riparte da zero.
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG",
                      status_code=200, content=b"w" * 1000,
                      headers={"Content-Length": "1000"})
    with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG", offset=500) as stream:
        assert stream.resumed is False
        assert stream.total == 1000


def test_open_stream_raises_protocol_error_on_404(requests_mock):
    requests_mock.get(f"{BASE_URL}/DCIM/100OLYMP/A.JPG", status_code=404)
    with pytest.raises(ProtocolError):
        with OlympusClient().open_stream("/DCIM/100OLYMP/A.JPG"):
            pass
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `.venv/bin/pytest tests/test_protocol.py -k open_stream -v`
Atteso: FAIL con `ImportError: cannot import name 'Stream'`

- [ ] **Step 3: Implementare `Stream` e `open_stream`**

```python
# da aggiungere a olympus_connect/core/protocol.py

from dataclasses import dataclass, field
from .model import CHUNK_SIZE


@dataclass
class Stream:   # `contextmanager` e `Iterator` sono già importati dal Task 2
    """Un trasferimento in corso.

    resumed è True solo se il server ha risposto 206: è l'unico segnale
    affidabile che il Range è stato onorato e che si sta riprendendo
    invece di ricominciare.
    """
    total: int
    resumed: bool
    chunks: Iterator[bytes]


def _total_from_content_range(value: str) -> int | None:
    # "bytes 500-999/1000" -> 1000
    _, _, denominator = value.partition("/")
    return int(denominator) if denominator.isdigit() else None
```

E come metodo di `OlympusClient`:

```python
    @contextmanager
    def open_stream(self, camera_path: str, offset: int = 0) -> Iterator[Stream]:
        """Scarica un file in streaming, opzionalmente riprendendo da offset."""
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        response = self._get(camera_path, stream=True, headers=headers)
        try:
            if response.status_code not in (200, 206):
                raise ProtocolError(
                    f"{camera_path} ha risposto HTTP {response.status_code}.",
                    response.content,
                )

            resumed = response.status_code == 206
            total: int | None = None
            if resumed:
                total = _total_from_content_range(
                    response.headers.get("Content-Range", "")
                )
            if total is None:
                length = response.headers.get("Content-Length")
                total = int(length) if length and length.isdigit() else 0
                if resumed:
                    total += offset

            yield Stream(total=total, resumed=resumed,
                         chunks=response.iter_content(CHUNK_SIZE))
        finally:
            response.close()
```

- [ ] **Step 4: Eseguire i test per verificare che passino**

Run: `.venv/bin/pytest tests/test_protocol.py -v`
Atteso: PASS, tutti

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/core/protocol.py tests/test_protocol.py
git commit -m "feat(core): streaming con rilevamento del supporto Range"
```

---

### Task 4: Pianificazione dei download e regola dei duplicati

**Files:**
- Create: `olympus_connect/core/downloader.py`
- Test: `tests/test_downloader.py`

**Interfaces:**
- Consumes: `MediaFile` da Task 1
- Produces:
  - `JobState` — `Enum` con `NEW`, `PRESENT`, `INCOMPLETE`
  - `Job(media: MediaFile, dest: Path, state: JobState)` — dataclass; `dest` è il path assoluto finale
  - `plan(files: Iterable[MediaFile], dest_root: Path) -> list[Job]`
  - `pending(jobs: Iterable[Job]) -> list[Job]` — i soli job da trasferire (`NEW` + `INCOMPLETE`)

- [ ] **Step 1: Scrivere i test che falliscono**

```python
# tests/test_downloader.py
from datetime import datetime
from pathlib import Path

from olympus_connect.core.downloader import Job, JobState, pending, plan
from olympus_connect.core.model import MediaFile, MediaKind


def media(name="P9240123.JPG", size=1000, kind=MediaKind.JPEG) -> MediaFile:
    return MediaFile(f"/DCIM/100OLYMP/{name}", size,
                     datetime(2026, 9, 24, 18, 5, 0), kind)


def test_plan_marks_missing_file_as_new(tmp_path):
    jobs = plan([media()], tmp_path)
    assert jobs[0].state is JobState.NEW
    assert jobs[0].dest == tmp_path / "2026/2026-09-24/P9240123.JPG"


def test_plan_marks_existing_file_with_same_size_as_present(tmp_path):
    dest = tmp_path / "2026/2026-09-24/P9240123.JPG"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x" * 1000)

    assert plan([media(size=1000)], tmp_path)[0].state is JobState.PRESENT


def test_plan_marks_existing_file_with_different_size_as_incomplete(tmp_path):
    # Dimensione diversa significa file non corrispondente: va riscaricato.
    dest = tmp_path / "2026/2026-09-24/P9240123.JPG"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x" * 42)

    assert plan([media(size=1000)], tmp_path)[0].state is JobState.INCOMPLETE


def test_plan_puts_undated_file_in_senza_data(tmp_path):
    undated = MediaFile("/DCIM/100OLYMP/ODD.JPG", 5, None, MediaKind.JPEG)
    assert plan([undated], tmp_path)[0].dest == tmp_path / "senza-data/ODD.JPG"


def test_plan_does_not_create_directories(tmp_path):
    # plan() deve essere una pura ispezione: nessun effetto sul disco.
    plan([media()], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_pending_excludes_present_jobs(tmp_path):
    jobs = [
        Job(media("A.JPG"), tmp_path / "A.JPG", JobState.NEW),
        Job(media("B.JPG"), tmp_path / "B.JPG", JobState.PRESENT),
        Job(media("C.JPG"), tmp_path / "C.JPG", JobState.INCOMPLETE),
    ]
    assert {j.media.name for j in pending(jobs)} == {"A.JPG", "C.JPG"}


def test_pending_orders_smallest_first(tmp_path):
    # Senza Range un'interruzione costa il file in corso: conviene che il
    # file in corso sia il più piccolo possibile il più a lungo possibile.
    jobs = [
        Job(media("BIG.MOV", 944_000_000), tmp_path / "BIG.MOV", JobState.NEW),
        Job(media("SMALL.ORF", 20_000_000), tmp_path / "SMALL.ORF", JobState.NEW),
        Job(media("MID.MOV", 129_000_000), tmp_path / "MID.MOV", JobState.NEW),
    ]
    assert [j.media.name for j in pending(jobs)] == ["SMALL.ORF", "MID.MOV", "BIG.MOV"]
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `.venv/bin/pytest tests/test_downloader.py -v`
Atteso: FAIL con `ModuleNotFoundError: No module named 'olympus_connect.core.downloader'`

- [ ] **Step 3: Implementare la parte di pianificazione**

```python
"""Pianificazione e trasferimento dei file dalla fotocamera al disco."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from .model import MediaFile


class JobState(Enum):
    NEW = "new"                # assente a destinazione
    PRESENT = "present"        # già presente con la stessa dimensione
    INCOMPLETE = "incomplete"  # presente ma di dimensione diversa


@dataclass(frozen=True)
class Job:
    media: MediaFile
    dest: Path
    state: JobState


def plan(files: Iterable[MediaFile], dest_root: Path) -> list[Job]:
    """Confronta l'elenco della fotocamera col disco. Non scrive nulla.

    Un file col nome definitivo è per costruzione integro (vedi download():
    il rename è atomico e avviene solo a trasferimento verificato), quindi
    nome + dimensione bastano a riconoscere un file già scaricato.
    """
    jobs: list[Job] = []
    for media in files:
        dest = dest_root / media.dest_relpath
        if not dest.exists():
            state = JobState.NEW
        elif dest.stat().st_size == media.size:
            state = JobState.PRESENT
        else:
            state = JobState.INCOMPLETE
        jobs.append(Job(media, dest, state))
    return jobs


def pending(jobs: Iterable[Job]) -> list[Job]:
    """I job da trasferire, dal più piccolo al più grande.

    Questo firmware non supporta le richieste Range: un'interruzione costa
    l'intero file in corso. Mettendo i file piccoli per primi, il lavoro a
    rischio in ogni istante è quasi sempre minimo — chiudere 1000 ORF da
    20 MB prima di attaccare un MOV da 944 MB è strettamente migliore che
    il contrario.
    """
    return sorted(
        (j for j in jobs if j.state is not JobState.PRESENT),
        key=lambda j: j.media.size,
    )
```

- [ ] **Step 4: Eseguire i test per verificare che passino**

Run: `.venv/bin/pytest tests/test_downloader.py -v`
Atteso: PASS, 6 test

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/core/downloader.py tests/test_downloader.py
git commit -m "feat(core): pianificazione download e regola dei duplicati"
```

---

### Task 5: Trasferimento con recupero e rename atomico

Questo è il task più importante del piano: è ciò che rende sopportabile scaricare 27,9 GB su un Wi-Fi 2,4 GHz da una fotocamera che si addormenta.

> **Aggiornato dopo il Task 0.** La fotocamera **non supporta le richieste `Range`**: risponde `HTTP 200` senza `Content-Range` e rimanda il file intero. La ripresa da un offset è quindi impossibile su questo firmware, e il recupero consiste nel ripartire da zero. Il rilevamento resta a runtime (`stream.resumed`) e il ramo di ripresa resta nel codice, perché serve ad altri modelli e i test lo coprono in entrambe le direzioni. Il file `.part` con rename atomico **non va rimosso**: senza di esso un trasferimento interrotto lascerebbe un file col nome definitivo, e la regola dei duplicati di `plan()` lo considererebbe completo per sempre.

**Files:**
- Modify: `olympus_connect/core/downloader.py`
- Test: `tests/test_downloader.py`

**Interfaces:**
- Consumes: `Job`, `JobState` da Task 4; `OlympusClient.open_stream`, `Stream` da Task 3; `CameraAsleep`, `IncompleteDownload` da Task 2
- Produces:
  - `Outcome` — `Enum` con `DONE`, `CANCELLED`
  - `download(client, job, on_progress=None, should_cancel=None, retries=3, sleep=time.sleep) -> Outcome`
    - `on_progress: Callable[[int, int], None]` riceve (byte scritti, byte totali)
    - `should_cancel: Callable[[], bool]` interrogata tra i chunk
    - `sleep` è iniettabile perché i test non devono attendere 14 secondi reali

- [ ] **Step 1: Scrivere il server HTTP di test**

Aggiungere a `tests/conftest.py`:

```python
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class FlakyCamera:
    """Server HTTP locale che imita la fotocamera, inclusi i suoi difetti.

    mode:
      "normal"      - serve tutto, onora Range con 206
      "no_range"    - ignora Range, risponde sempre 200 con il file intero
      "drop_at_half"- chiude la connessione a metà del corpo, una volta sola
    """

    def __init__(self, payload: bytes, mode: str = "normal") -> None:
        self.payload = payload
        self.mode = mode
        self.dropped = False
        self.requests: list[tuple[str, str | None]] = []

        camera = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass   # silenzio nei test

            def do_GET(self):
                rng = self.headers.get("Range")
                camera.requests.append((self.path, rng))

                start = 0
                if rng and camera.mode != "no_range":
                    start = int(rng.removeprefix("bytes=").split("-")[0])
                    self.send_response(206)
                    self.send_header(
                        "Content-Range",
                        f"bytes {start}-{len(camera.payload) - 1}/{len(camera.payload)}",
                    )
                else:
                    self.send_response(200)

                body = camera.payload[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()

                if camera.mode == "drop_at_half" and not camera.dropped:
                    camera.dropped = True
                    self.wfile.write(body[: len(body) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                    return

                self.wfile.write(body)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
```

- [ ] **Step 2: Scrivere i test che falliscono**

```python
# da aggiungere a tests/test_downloader.py
import os

import pytest

from olympus_connect.core.downloader import Outcome, download
from olympus_connect.core.errors import CameraAsleep
from olympus_connect.core.protocol import OlympusClient
from tests.conftest import FlakyCamera

PAYLOAD = bytes(range(256)) * 400   # 102400 byte, > 1 chunk da 64 KiB


def job_for(tmp_path, size=len(PAYLOAD)):
    from olympus_connect.core.downloader import Job, JobState
    return Job(media(size=size), tmp_path / "out" / "P9240123.JPG", JobState.NEW)


def test_download_writes_file_and_reports_progress(tmp_path):
    with FlakyCamera(PAYLOAD) as camera:
        client = OlympusClient(base_url=camera.base_url)
        seen: list[tuple[int, int]] = []
        job = job_for(tmp_path)

        assert download(client, job, on_progress=lambda w, t: seen.append((w, t))) is Outcome.DONE

    assert job.dest.read_bytes() == PAYLOAD
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))


def test_download_creates_parent_directories(tmp_path):
    with FlakyCamera(PAYLOAD) as camera:
        job = job_for(tmp_path)
        download(OlympusClient(base_url=camera.base_url), job)
    assert job.dest.parent.is_dir()


def test_download_leaves_no_part_file_on_success(tmp_path):
    with FlakyCamera(PAYLOAD) as camera:
        job = job_for(tmp_path)
        download(OlympusClient(base_url=camera.base_url), job)
    assert list(job.dest.parent.glob("*.part")) == []


def test_download_sets_mtime_to_shot_time(tmp_path):
    with FlakyCamera(PAYLOAD) as camera:
        job = job_for(tmp_path)
        download(OlympusClient(base_url=camera.base_url), job)
    assert job.dest.stat().st_mtime == pytest.approx(
        job.media.shot_at.timestamp(), abs=1
    )


def test_download_resumes_from_existing_part_file_when_range_works(tmp_path):
    # Ramo per altri modelli: la E-M10 IV non supporta Range (Task 0), ma il
    # rilevamento è a runtime e questo percorso va comunque protetto.
    job = job_for(tmp_path)
    part = job.dest.with_suffix(job.dest.suffix + ".part")
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:40000])

    with FlakyCamera(PAYLOAD) as camera:
        download(OlympusClient(base_url=camera.base_url), job)
        assert camera.requests[0][1] == "bytes=40000-"

    assert job.dest.read_bytes() == PAYLOAD


def test_download_restarts_when_server_ignores_range(tmp_path):
    job = job_for(tmp_path)
    part = job.dest.with_suffix(job.dest.suffix + ".part")
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:40000])

    with FlakyCamera(PAYLOAD, mode="no_range") as camera:
        download(OlympusClient(base_url=camera.base_url), job)

    # Il file deve essere corretto, non 40000 byte + il file intero appeso.
    assert job.dest.read_bytes() == PAYLOAD


def test_download_recovers_from_connection_dropped_at_half(tmp_path):
    # Il test che conta: la fotocamera chiude a metà e il trasferimento
    # riprende dal byte raggiunto invece di ricominciare.
    job = job_for(tmp_path)
    with FlakyCamera(PAYLOAD, mode="drop_at_half") as camera:
        client = OlympusClient(base_url=camera.base_url)
        assert download(client, job, sleep=lambda s: None) is Outcome.DONE
        assert len(camera.requests) == 2
        assert camera.requests[1][1] is not None   # il retry ha chiesto un Range

    assert job.dest.read_bytes() == PAYLOAD


def test_download_discards_part_file_larger_than_expected(tmp_path):
    # Un .part più grande del file atteso è spazzatura: va buttato.
    job = job_for(tmp_path)
    part = job.dest.with_suffix(job.dest.suffix + ".part")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"q" * (len(PAYLOAD) + 5000))

    with FlakyCamera(PAYLOAD) as camera:
        download(OlympusClient(base_url=camera.base_url), job)

    assert job.dest.read_bytes() == PAYLOAD


def test_download_cancels_between_chunks_and_keeps_part(tmp_path):
    job = job_for(tmp_path)
    with FlakyCamera(PAYLOAD) as camera:
        client = OlympusClient(base_url=camera.base_url)
        assert download(client, job, should_cancel=lambda: True) is Outcome.CANCELLED

    assert not job.dest.exists()
    part = job.dest.with_suffix(job.dest.suffix + ".part")
    assert part.exists()   # il lavoro fatto resta, pronto per la ripresa


def test_download_raises_camera_asleep_after_exhausting_retries(tmp_path):
    class AlwaysDropping(OlympusClient):
        def open_stream(self, camera_path, offset=0):
            raise CameraAsleep("connessione caduta")

    job = job_for(tmp_path)
    with pytest.raises(CameraAsleep):
        download(AlwaysDropping(base_url="http://127.0.0.1:1"), job,
                 sleep=lambda s: None)


def test_download_backs_off_with_expected_delays(tmp_path):
    slept: list[float] = []

    class AlwaysDropping(OlympusClient):
        def open_stream(self, camera_path, offset=0):
            raise CameraAsleep("connessione caduta")

    job = job_for(tmp_path)
    with pytest.raises(CameraAsleep):
        download(AlwaysDropping(base_url="http://127.0.0.1:1"), job,
                 sleep=slept.append)

    assert slept == [2, 4, 8]
```

- [ ] **Step 3: Eseguire i test per verificare che falliscano**

Run: `.venv/bin/pytest tests/test_downloader.py -v`
Atteso: FAIL con `ImportError: cannot import name 'Outcome'`

- [ ] **Step 4: Implementare `download()`**

```python
# da aggiungere a olympus_connect/core/downloader.py

import os
import time
from typing import Callable

import requests

from .errors import CameraAsleep, IncompleteDownload

BACKOFF_SECONDS = (2, 4, 8)


class Outcome(Enum):
    DONE = "done"
    CANCELLED = "cancelled"


def _part_path(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".part")


def download(
    client,
    job: Job,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> Outcome:
    """Trasferisce un file, riprendendo dopo le disconnessioni.

    La fotocamera spegne l'access point dopo un periodo di inattività: su un
    file grande il sintomo è una connessione chiusa a metà. Il lavoro svolto
    sopravvive nel file .part e il tentativo successivo riprende da lì.

    Il rename al nome definitivo è atomico e avviene solo dopo la verifica
    della dimensione: un file col nome finale è quindi sempre integro, ed è
    questo che rende sicura la regola dei duplicati di plan().
    """
    attempts = retries + 1
    for attempt in range(attempts):
        try:
            return _attempt(client, job, on_progress, should_cancel)
        except (CameraAsleep, IncompleteDownload,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError) as exc:
            if attempt == attempts - 1:
                raise CameraAsleep(
                    f"Trasferimento di {job.media.name} interrotto dopo "
                    f"{attempts} tentativi."
                ) from exc
            sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])

    raise AssertionError("non raggiungibile")


def _attempt(
    client,
    job: Job,
    on_progress: Callable[[int, int], None] | None,
    should_cancel: Callable[[], bool] | None,
) -> Outcome:
    expected = job.media.size
    part = _part_path(job.dest)
    job.dest.parent.mkdir(parents=True, exist_ok=True)

    offset = part.stat().st_size if part.exists() else 0
    if offset >= expected:
        offset = 0   # .part incoerente: ricomincia

    with client.open_stream(job.media.camera_path, offset) as stream:
        resuming = offset > 0 and stream.resumed
        written = offset if resuming else 0
        # "wb" tronca: se il Range non è stato onorato, il .part parziale
        # viene buttato invece di far crescere un file corrotto.
        with open(part, "ab" if resuming else "wb") as handle:
            for chunk in stream.chunks:
                if should_cancel is not None and should_cancel():
                    handle.flush()
                    return Outcome.CANCELLED
                handle.write(chunk)
                written += len(chunk)
                if on_progress is not None:
                    on_progress(written, expected)

    if written != expected:
        raise IncompleteDownload(
            f"{job.media.name}: ricevuti {written} byte su {expected} attesi."
        )

    os.replace(part, job.dest)
    if job.media.shot_at is not None:
        stamp = job.media.shot_at.timestamp()
        os.utime(job.dest, (stamp, stamp))
    return Outcome.DONE
```

- [ ] **Step 5: Eseguire i test per verificare che passino**

Run: `.venv/bin/pytest tests/test_downloader.py -v`
Atteso: PASS, tutti

- [ ] **Step 6: Eseguire l'intera suite**

Run: `.venv/bin/pytest -v`
Atteso: PASS, nessuno SKIPPED tra i test che usano le fixture reali

- [ ] **Step 7: Commit**

```bash
git add olympus_connect/core/downloader.py tests
git commit -m "feat(core): trasferimento con ripresa via Range e rename atomico"
```

---

### Task 6: Scala diagnostica della rete

**Files:**
- Create: `olympus_connect/core/network.py`
- Test: `tests/test_network.py`

**Interfaces:**
- Consumes: `NetworkUnavailable` da Task 2
- Produces:
  - `CommandResult(returncode: int, stdout: str, stderr: str)` — dataclass
  - `Runner = Callable[[list[str]], CommandResult]`
  - `run_command(argv: list[str]) -> CommandResult` — runner reale, `subprocess` con timeout 10 s
  - `Rung(name: str, ok: bool, detail: str)` — dataclass
  - `Diagnosis(rungs: list[Rung])` con proprietà `ok: bool` e `problem: str | None` (il nome del primo gradino fallito)
  - `CameraNetwork(ssid: str = "", runner: Runner = run_command, probe_tcp: Callable[[], bool] = ...)`
  - `CameraNetwork.wifi_devices() -> list[str]`
  - `CameraNetwork.active_connection(device: str) -> str | None`
  - `CameraNetwork.is_onlink() -> tuple[bool, str]`
  - `CameraNetwork.diagnose() -> Diagnosis`

**Due correzioni rispetto alla prima stesura, verificate empiricamente durante il Task 0:**

1. **Il gradino della rotta va verificato come «on-link», non come «esiste».** `ip route get 192.168.0.10` riesce sempre, perché la default route del router di casa copre qualunque indirizzo: l'output misurato sul sistema reale era `192.168.0.10 via 192.168.1.254 dev wlan0`, identico alla rotta verso `8.8.8.8`. Il gradino passerebbe proprio nello stato che deve rilevare. La verifica corretta è l'assenza di ` via ` nella rotta: quando si è sull'AP della fotocamera la consegna è diretta.
2. **`nmcli -t -f ACTIVE,SSID device wifi` non è affidabile.** Sul sistema reale riportava `no:` per tutte le reti pur essendo connessi. Sostituito con `nmcli -t -f GENERAL.CONNECTION,GENERAL.STATE device show <dev>`, che restituisce il nome del profilo attivo e lo stato `100 (connected)`. Il valore è il nome del *profilo* NetworkManager, non l'SSID: il confronto va fatto sia contro l'SSID sia contro `profile_name`, perché collegandosi col nostro profilo il nome è `olympus-connect-<ssid>`.

- [ ] **Step 1: Scrivere i test che falliscono**

```python
# tests/test_network.py
import pytest

from olympus_connect.core.network import (
    CameraNetwork, CommandResult, Diagnosis, Rung,
)

DEVICES_WITH_WIFI = "wlan0:wifi:connected\neth0:ethernet:unavailable\n"
DEVICES_NO_WIFI = "eth0:ethernet:connected\n"

# Output reale di `nmcli -t -f GENERAL.CONNECTION,GENERAL.STATE device show wlan0`
CONNECTED_TO = "GENERAL.CONNECTION:{name}\nGENERAL.STATE:100 (connected)\n"
DISCONNECTED = "GENERAL.CONNECTION:\nGENERAL.STATE:30 (disconnected)\n"

# Output reale di `ip -o route get 192.168.0.10`, misurato sul sistema.
ONLINK = "192.168.0.10 dev wlan0 src 192.168.0.11 uid 1000 \\    cache\n"
VIA_GATEWAY = ("192.168.0.10 via 192.168.1.254 dev wlan0 "
               "src 192.168.1.74 uid 1000 \\    cache\n")


def fake_runner(responses: dict[str, CommandResult]):
    """Runner che risponde in base a una sottostringa dell'argv."""
    calls: list[list[str]] = []

    def run(argv):
        calls.append(argv)
        joined = " ".join(argv)
        for key, result in responses.items():
            if key in joined:
                return result
        return CommandResult(1, "", f"comando non atteso: {joined}")

    run.calls = calls
    return run


def ok(stdout=""):
    return CommandResult(0, stdout, "")


def runner_for(*, devices=DEVICES_WITH_WIFI, connection="OM-D E-M10 IV",
               route=ONLINK):
    return fake_runner({
        "-f DEVICE,TYPE,STATE": ok(devices),
        "device show": ok(CONNECTED_TO.format(name=connection)
                          if connection else DISCONNECTED),
        "route get": ok(route),
    })


def test_active_connection_reads_device_show():
    runner = runner_for(connection="OM-D E-M10 IV")
    assert CameraNetwork(runner=runner).active_connection("wlan0") == "OM-D E-M10 IV"


def test_active_connection_is_none_when_device_disconnected():
    runner = runner_for(connection=None)
    assert CameraNetwork(runner=runner).active_connection("wlan0") is None


def test_is_onlink_true_for_direct_route():
    net = CameraNetwork(runner=runner_for(route=ONLINK))
    onlink, detail = net.is_onlink()
    assert onlink
    assert "192.168.0.10" in detail


def test_is_onlink_false_when_route_goes_through_gateway():
    # Il difetto che questo test blocca: `ip route get` riesce sempre grazie
    # alla default route, quindi il solo codice di ritorno segnalerebbe OK
    # mentre il PC è ancora sul Wi-Fi di casa.
    net = CameraNetwork(runner=runner_for(route=VIA_GATEWAY))
    onlink, detail = net.is_onlink()
    assert not onlink
    assert "192.168.1.254" in detail


def test_diagnose_all_green():
    net = CameraNetwork(ssid="OM-D E-M10 IV", runner=runner_for(),
                        probe_tcp=lambda: True)
    diagnosis = net.diagnose()
    assert diagnosis.ok
    assert diagnosis.problem is None


def test_diagnose_reports_missing_wifi_device_first():
    net = CameraNetwork(runner=runner_for(devices=DEVICES_NO_WIFI),
                        probe_tcp=lambda: False)
    diagnosis = net.diagnose()
    assert not diagnosis.ok
    assert diagnosis.problem == "dispositivo Wi-Fi"


def test_diagnose_reports_wrong_network_and_names_it():
    net = CameraNetwork(ssid="OM-D E-M10 IV",
                        runner=runner_for(connection="casa-5G",
                                          route=VIA_GATEWAY),
                        probe_tcp=lambda: False)
    diagnosis = net.diagnose()
    assert diagnosis.problem == "rete della fotocamera"
    detail = {r.name: r.detail for r in diagnosis.rungs}["rete della fotocamera"]
    assert "casa-5G" in detail   # deve dire a quale rete si è collegati


def test_diagnose_accepts_connection_via_our_own_profile():
    # Collegandosi col nostro profilo il nome attivo è olympus-connect-<ssid>,
    # non l'SSID: non deve essere confuso con «rete sbagliata».
    net = CameraNetwork(ssid="CAM",
                        runner=runner_for(connection="olympus-connect-CAM"),
                        probe_tcp=lambda: True)
    assert net.diagnose().ok


def test_diagnose_reports_route_not_onlink_before_tcp():
    net = CameraNetwork(ssid="OM-D E-M10 IV",
                        runner=runner_for(route=VIA_GATEWAY),
                        probe_tcp=lambda: False)
    assert net.diagnose().problem == "fotocamera sulla rete locale"


def test_diagnose_reports_tcp_failure_when_onlink():
    net = CameraNetwork(ssid="OM-D E-M10 IV", runner=runner_for(),
                        probe_tcp=lambda: False)
    assert net.diagnose().problem == "porta 80 della fotocamera"


def test_diagnose_skips_ssid_rung_when_ssid_unknown():
    # Senza SSID configurato non si può verificare di essere sulla rete giusta;
    # il gradino non va inventato, va omesso.
    net = CameraNetwork(ssid="", runner=runner_for(connection="qualcosa"),
                        probe_tcp=lambda: True)
    names = [r.name for r in net.diagnose().rungs]
    assert "rete della fotocamera" not in names


def test_diagnose_raises_network_unavailable_when_nmcli_missing():
    from olympus_connect.core.errors import NetworkUnavailable

    def missing(argv):
        raise FileNotFoundError("nmcli")

    with pytest.raises(NetworkUnavailable):
        CameraNetwork(runner=missing).diagnose()
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `.venv/bin/pytest tests/test_network.py -v`
Atteso: FAIL con `ModuleNotFoundError: No module named 'olympus_connect.core.network'`

- [ ] **Step 3: Implementare la parte diagnostica di `network.py`**

```python
"""Gestione della rete della fotocamera: diagnostica e profilo NetworkManager.

Tutti i comandi di sistema passano da un runner iniettabile, così i test
verificano gli argv emessi senza toccare la rete reale.
"""
from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from .errors import NetworkUnavailable

CAMERA_IP = "192.168.0.10"
CAMERA_PORT = 80
COMMAND_TIMEOUT = 10
PROFILE_PREFIX = "olympus-connect-"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], CommandResult]


def run_command(argv: list[str]) -> CommandResult:
    completed = subprocess.run(
        argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def probe_camera_port() -> bool:
    try:
        with socket.create_connection((CAMERA_IP, CAMERA_PORT), timeout=3):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class Rung:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Diagnosis:
    rungs: list[Rung] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(rung.ok for rung in self.rungs)

    @property
    def problem(self) -> str | None:
        """Nome del primo gradino fallito: la causa più a monte."""
        for rung in self.rungs:
            if not rung.ok:
                return rung.name
        return None


class CameraNetwork:
    def __init__(self, ssid: str = "", runner: Runner = run_command,
                 probe_tcp: Callable[[], bool] = probe_camera_port) -> None:
        self.ssid = ssid
        self._run = runner
        self._probe_tcp = probe_tcp

    @property
    def profile_name(self) -> str:
        """Il nostro profilo NetworkManager. Non tocchiamo nessun altro profilo."""
        return f"{PROFILE_PREFIX}{self.ssid}"

    def _nmcli(self, *args: str) -> CommandResult:
        try:
            return self._run(["nmcli", *args])
        except FileNotFoundError as exc:
            raise NetworkUnavailable(
                "nmcli non è disponibile: la gestione automatica della rete "
                "è disattivata."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise NetworkUnavailable("nmcli non ha risposto entro 10 secondi.") from exc

    def wifi_devices(self) -> list[str]:
        result = self._nmcli("-t", "-f", "DEVICE,TYPE,STATE", "device")
        return [ln.split(":")[0] for ln in result.stdout.splitlines()
                if ":wifi:" in ln]

    def active_connection(self, device: str) -> str | None:
        """Nome del profilo NetworkManager attivo sul dispositivo, se connesso.

        Il campo ACTIVE di `nmcli device wifi` riporta 'no' per tutte le reti
        anche quando una è connessa, quindi non è utilizzabile: si legge
        invece GENERAL.CONNECTION da `nmcli device show`.
        """
        result = self._nmcli("-t", "-f", "GENERAL.CONNECTION,GENERAL.STATE",
                             "device", "show", device)
        name, state = None, ""
        for line in result.stdout.splitlines():
            key, _, value = line.partition(":")
            if key == "GENERAL.CONNECTION":
                name = value or None
            elif key == "GENERAL.STATE":
                state = value
        return name if state.startswith("100") else None

    def is_onlink(self) -> tuple[bool, str]:
        """La fotocamera è raggiungibile direttamente, senza gateway?

        `ip route get` riesce sempre grazie alla default route, quindi il
        codice di ritorno non distingue «sono sull'AP della fotocamera» da
        «sono sul Wi-Fi di casa». La differenza è la presenza di ' via '.
        """
        result = self._run(["ip", "-o", "route", "get", CAMERA_IP])
        route = " ".join(result.stdout.split())
        if result.returncode != 0:
            return False, result.stderr.strip() or "nessuna rotta"
        return " via " not in f" {route} ", route

    def diagnose(self) -> Diagnosis:
        """Un gradino per ogni causa distinta, dalla più a monte alla più a valle."""
        rungs: list[Rung] = []

        devices = self.wifi_devices()
        rungs.append(Rung(
            "dispositivo Wi-Fi", bool(devices),
            ", ".join(devices) if devices else "nessuna interfaccia Wi-Fi trovata",
        ))
        if not devices:
            return Diagnosis(rungs)

        active = next(
            (c for c in (self.active_connection(d) for d in devices) if c), None
        )

        if self.ssid:
            # Collegandosi col nostro profilo il nome attivo è il profilo,
            # non l'SSID: entrambi valgono come «rete giusta».
            on_camera = active in (self.ssid, self.profile_name)
            rungs.append(Rung(
                "rete della fotocamera", on_camera,
                f"collegato a «{active}»" if active else "nessuna rete Wi-Fi attiva",
            ))
            if not on_camera:
                return Diagnosis(rungs)

        onlink, route = self.is_onlink()
        rungs.append(Rung(
            "fotocamera sulla rete locale", onlink,
            route if onlink else
            f"la rotta passa da un gateway: {route}",
        ))
        if not onlink:
            return Diagnosis(rungs)

        reachable = self._probe_tcp()
        rungs.append(Rung(
            "porta 80 della fotocamera", reachable,
            "risponde" if reachable else
            "nessuna risposta: il Wi-Fi della fotocamera è attivo?",
        ))
        return Diagnosis(rungs)
```

- [ ] **Step 4: Eseguire i test per verificare che passino**

Run: `.venv/bin/pytest tests/test_network.py -v`
Atteso: PASS, 8 test

- [ ] **Step 5: Verificare la diagnostica sul sistema reale**

```bash
.venv/bin/python -c "
from olympus_connect.core.network import CameraNetwork
for rung in CameraNetwork().diagnose().rungs:
    print(('OK  ' if rung.ok else 'FAIL'), rung.name, '—', rung.detail)
"
```

Atteso: con la fotocamera collegata, tutti OK. Senza, il primo gradino fallito deve essere quello giusto (`rotta verso 192.168.0.10` o `porta 80 della fotocamera`).

- [ ] **Step 6: Commit**

```bash
git add olympus_connect/core/network.py tests/test_network.py
git commit -m "feat(core): scala diagnostica della connessione alla fotocamera"
```

---

### Task 7: Profilo NetworkManager dedicato

**Files:**
- Modify: `olympus_connect/core/network.py`
- Test: `tests/test_network.py`

**Interfaces:**
- Consumes: `CameraNetwork`, `Runner`, `CommandResult`, `profile_name`, `PROFILE_PREFIX` da Task 6
- Produces:
  - `CameraNetwork.profile_exists() -> bool`
  - `CameraNetwork.ensure_profile(password: str | None = None) -> None`
  - `CameraNetwork.connect() -> None`
  - `CameraNetwork.disconnect() -> None`

- [ ] **Step 1: Scrivere i test che falliscono**

```python
# da aggiungere a tests/test_network.py

def test_profile_name_is_namespaced():
    # Il profilo deve essere riconoscibile come nostro: non tocchiamo
    # e non sovrascriviamo mai le connessioni esistenti dell'utente.
    assert CameraNetwork(ssid="OM-D E-M10 IV").profile_name == "olympus-connect-OM-D E-M10 IV"


def test_profile_exists_checks_nmcli_connection_list():
    runner = fake_runner({"-f NAME connection show": ok(
        "casa-5G\nolympus-connect-OM-D E-M10 IV\n")})
    assert CameraNetwork(ssid="OM-D E-M10 IV", runner=runner).profile_exists()


def test_profile_exists_false_when_absent():
    runner = fake_runner({"-f NAME connection show": ok("casa-5G\n")})
    assert not CameraNetwork(ssid="OM-D E-M10 IV", runner=runner).profile_exists()


def test_ensure_profile_creates_profile_with_never_default():
    runner = fake_runner({
        "-f NAME connection show": ok("casa-5G\n"),
        "connection add": ok(),
        "connection modify": ok(),
    })
    CameraNetwork(ssid="CAM", runner=runner).ensure_profile(password="secret123")

    added = next(c for c in runner.calls if "add" in c)
    joined = " ".join(added)
    # never-default è ciò che impedisce alla fotocamera di rubare la
    # default route e farti perdere Internet durante il download.
    assert "ipv4.never-default" in joined and "yes" in added
    assert "connection.autoconnect" in joined
    assert "ipv4.route-metric" in joined
    assert "ipv6.method" in joined
    assert "802-11-wireless.ssid" in joined
    assert "CAM" in added


def test_ensure_profile_passes_password_as_psk():
    runner = fake_runner({
        "-f NAME connection show": ok("\n"),
        "connection add": ok(),
        "connection modify": ok(),
    })
    CameraNetwork(ssid="CAM", runner=runner).ensure_profile(password="secret123")
    joined = " ".join(" ".join(c) for c in runner.calls)
    assert "wifi-sec.key-mgmt" in joined
    assert "secret123" in joined


def test_ensure_profile_does_not_recreate_existing_profile():
    runner = fake_runner({
        "-f NAME connection show": ok("olympus-connect-CAM\n"),
        "connection modify": ok(),
    })
    CameraNetwork(ssid="CAM", runner=runner).ensure_profile()
    assert not any("add" in c for c in runner.calls)


def test_ensure_profile_without_password_and_without_profile_raises():
    from olympus_connect.core.errors import NetworkUnavailable

    runner = fake_runner({"-f NAME connection show": ok("casa-5G\n")})
    with pytest.raises(NetworkUnavailable):
        CameraNetwork(ssid="CAM", runner=runner).ensure_profile()


def test_connect_brings_profile_up():
    runner = fake_runner({"connection up": ok()})
    CameraNetwork(ssid="CAM", runner=runner).connect()
    assert runner.calls[-1] == ["nmcli", "connection", "up", "olympus-connect-CAM"]


def test_connect_raises_when_nmcli_fails():
    from olympus_connect.core.errors import NetworkUnavailable

    runner = fake_runner({"connection up": CommandResult(4, "", "no such device")})
    with pytest.raises(NetworkUnavailable, match="no such device"):
        CameraNetwork(ssid="CAM", runner=runner).connect()


def test_disconnect_brings_profile_down():
    runner = fake_runner({"connection down": ok()})
    CameraNetwork(ssid="CAM", runner=runner).disconnect()
    assert runner.calls[-1] == ["nmcli", "connection", "down", "olympus-connect-CAM"]
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `.venv/bin/pytest tests/test_network.py -k profile -v`
Atteso: FAIL con `AttributeError: 'CameraNetwork' object has no attribute 'profile_name'`

- [ ] **Step 3: Implementare la gestione del profilo**

```python
# da aggiungere a olympus_connect/core/network.py
# (PROFILE_PREFIX e profile_name sono già definiti dal Task 6)

# Impostazioni che rendono usabile l'AP della fotocamera su Linux:
# never-default evita che la fotocamera diventi la default route (perdita
# di Internet), la metrica alta la mette dietro alle reti vere, autoconnect
# disattivato evita agganci a sorpresa, ipv6 disattivato elimina rumore.
PROFILE_SETTINGS = [
    ("ipv4.never-default", "yes"),
    ("ipv4.route-metric", "900"),
    ("ipv6.method", "disabled"),
    ("connection.autoconnect", "no"),
]
```

```python
    def _checked(self, *args: str) -> CommandResult:
        result = self._nmcli(*args)
        if result.returncode != 0:
            raise NetworkUnavailable(
                result.stderr.strip() or
                f"nmcli {' '.join(args)} è terminato con codice {result.returncode}."
            )
        return result

    def profile_exists(self) -> bool:
        result = self._nmcli("-t", "-f", "NAME", "connection", "show")
        return self.profile_name in result.stdout.splitlines()

    def ensure_profile(self, password: str | None = None) -> None:
        """Crea o aggiorna il nostro profilo. Non tocca nessun altro profilo.

        La password viene passata a NetworkManager, che la conserva: questa
        applicazione non la scrive mai su disco.
        """
        if not self.ssid:
            raise NetworkUnavailable("SSID della fotocamera non configurato.")

        if not self.profile_exists():
            if not password:
                raise NetworkUnavailable(
                    f"Profilo «{self.profile_name}» assente: serve la password "
                    "del Wi-Fi della fotocamera per crearlo."
                )
            self._checked(
                "connection", "add",
                "type", "wifi",
                "con-name", self.profile_name,
                "ssid", self.ssid,
                "--", "802-11-wireless.ssid", self.ssid,
                "wifi-sec.key-mgmt", "wpa-psk",
                "wifi-sec.psk", password,
                *[part for setting in PROFILE_SETTINGS for part in setting],
            )
            return

        # Profilo esistente: riallinea le impostazioni, che potrebbero essere
        # state create da una versione precedente o modificate a mano.
        self._checked(
            "connection", "modify", self.profile_name,
            *[part for setting in PROFILE_SETTINGS for part in setting],
        )
        if password:
            self._checked(
                "connection", "modify", self.profile_name,
                "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password,
            )

    def connect(self) -> None:
        self._checked("connection", "up", self.profile_name)

    def disconnect(self) -> None:
        self._checked("connection", "down", self.profile_name)
```

- [ ] **Step 4: Eseguire i test per verificare che passino**

Run: `.venv/bin/pytest tests/test_network.py -v`
Atteso: PASS, tutti

- [ ] **Step 5: Verificare `ensure_profile` contro `nmcli` reale**

Con la fotocamera accesa e l'SSID e la password a portata di mano:

```bash
.venv/bin/python -c "
import getpass
from olympus_connect.core.network import CameraNetwork
ssid = input('SSID della fotocamera: ')
net = CameraNetwork(ssid=ssid)
net.ensure_profile(password=getpass.getpass('Password Wi-Fi: '))
print('profilo creato:', net.profile_name)
"
nmcli -f connection.id,ipv4.never-default,ipv4.route-metric,connection.autoconnect connection show "olympus-connect-<SSID>"
```

Atteso: `never-default: yes`, `route-metric: 900`, `autoconnect: no`. Poi verificare che `nmcli connection up` si agganci alla fotocamera **senza** perdere la connessione Internet esistente — è il punto dell'intero task.

- [ ] **Step 6: Commit**

```bash
git add olympus_connect/core/network.py tests/test_network.py
git commit -m "feat(core): profilo NetworkManager dedicato con never-default"
```

---

### Task 8: Worker Qt

**Files:**
- Create: `olympus_connect/gui/__init__.py` (vuoto)
- Create: `olympus_connect/gui/workers.py`
- Test: `tests/test_workers.py`

**Interfaces:**
- Consumes: `OlympusClient` (Task 2-3), `Job`, `plan`, `pending`, `download`, `Outcome` (Task 4-5), `CameraNetwork` (Task 6-7), eccezioni (Task 2)
- Produces:
  - `ListWorker(client, dest_root)` — signal `listed(list)` (lista di `Job`), `failed(str)`
  - `ThumbWorker(client)` — slot `request(str)`, signal `ready(str, bytes)`, `failed(str, str)`
  - `DownloadWorker(client, jobs)` — signal `file_started(str)`, `file_progress(int, int)`, `total_progress(int, int)`, `file_done(str)`, `file_failed(str, str)`, `paused(str)`, `finished()`; slot `cancel()`, `resume()`
  - `describe_error(exc: Exception) -> str` — mappa le eccezioni del core su messaggi italiani per l'utente

- [ ] **Step 1: Scrivere i test che falliscono**

```python
# tests/test_workers.py
"""I worker si testano senza avviare thread: si invoca direttamente il metodo
di lavoro e si raccolgono i signal emessi. Serve un QApplication perché i
QObject con signal ne richiedono uno."""
from datetime import datetime
from pathlib import Path

import pytest

from olympus_connect.core.downloader import Job, JobState
from olympus_connect.core.errors import (
    CameraAsleep, CameraNotReachable, NetworkUnavailable, ProtocolError,
)
from olympus_connect.core.model import MediaFile, MediaKind
from olympus_connect.gui.workers import (
    DownloadWorker, ListWorker, ThumbWorker, describe_error,
)


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def media(name="A.JPG", size=10):
    return MediaFile(f"/DCIM/100OLYMP/{name}", size,
                     datetime(2026, 9, 24, 12, 0, 0), MediaKind.JPEG)


class FakeClient:
    def __init__(self, files=None, thumb=b"\xff\xd8", fail=None):
        self._files = files or []
        self._thumb = thumb
        self._fail = fail

    def connect(self):
        if self._fail:
            raise self._fail
        return "E-M10MarkIV"

    def ensure_play_mode(self):
        pass

    def list_media(self, directory="/DCIM"):
        if self._fail:
            raise self._fail
        return self._files

    def thumbnail(self, camera_path):
        if self._fail:
            raise self._fail
        return self._thumb


@pytest.mark.parametrize("exc,fragment", [
    (CameraNotReachable("x"), "non risponde"),
    (CameraAsleep("x"), "disconness"),
    (NetworkUnavailable("x"), "rete"),
    (ProtocolError("x"), "risposta"),
    (OSError(28, "No space left on device"), "spazio"),
])
def test_describe_error_produces_italian_actionable_text(exc, fragment):
    message = describe_error(exc)
    assert fragment.lower() in message.lower()
    assert message[0].isupper()


def test_list_worker_emits_jobs(qapp, tmp_path):
    worker = ListWorker(FakeClient(files=[media()]), tmp_path)
    received = []
    worker.listed.connect(received.append)
    worker.run()

    assert len(received) == 1
    assert received[0][0].state is JobState.NEW


def test_list_worker_emits_failed_with_italian_message(qapp, tmp_path):
    worker = ListWorker(FakeClient(fail=CameraNotReachable("boom")), tmp_path)
    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors and "non risponde" in errors[0]


def test_thumb_worker_emits_ready_with_path_and_bytes(qapp):
    worker = ThumbWorker(FakeClient(thumb=b"\xff\xd8jpeg"))
    got = []
    worker.ready.connect(lambda p, b: got.append((p, b)))
    worker.request("/DCIM/100OLYMP/A.JPG")

    assert got == [("/DCIM/100OLYMP/A.JPG", b"\xff\xd8jpeg")]


def test_thumb_worker_failure_does_not_raise(qapp):
    worker = ThumbWorker(FakeClient(fail=ProtocolError("nope")))
    failures = []
    worker.failed.connect(lambda p, m: failures.append(p))
    worker.request("/DCIM/100OLYMP/A.JPG")   # non deve propagare

    assert failures == ["/DCIM/100OLYMP/A.JPG"]


def test_download_worker_reports_per_file_and_total_progress(qapp, tmp_path, monkeypatch):
    jobs = [
        Job(media("A.JPG", 100), tmp_path / "A.JPG", JobState.NEW),
        Job(media("B.JPG", 300), tmp_path / "B.JPG", JobState.NEW),
    ]

    def fake_download(client, job, on_progress=None, should_cancel=None, **kw):
        from olympus_connect.core.downloader import Outcome
        on_progress(job.media.size, job.media.size)
        return Outcome.DONE

    monkeypatch.setattr("olympus_connect.gui.workers.download", fake_download)

    worker = DownloadWorker(FakeClient(), jobs)
    totals, done, finished = [], [], []
    worker.total_progress.connect(lambda w, t: totals.append((w, t)))
    worker.file_done.connect(done.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.run()

    assert done == ["A.JPG", "B.JPG"]
    assert totals[-1] == (400, 400)   # somma delle dimensioni dei job
    assert finished == [True]


def test_download_worker_continues_after_single_file_failure(qapp, tmp_path, monkeypatch):
    jobs = [
        Job(media("A.JPG", 100), tmp_path / "A.JPG", JobState.NEW),
        Job(media("B.JPG", 100), tmp_path / "B.JPG", JobState.NEW),
    ]

    def fake_download(client, job, on_progress=None, should_cancel=None, **kw):
        from olympus_connect.core.downloader import Outcome
        if job.media.name == "A.JPG":
            raise ProtocolError("file illeggibile")
        on_progress(100, 100)
        return Outcome.DONE

    monkeypatch.setattr("olympus_connect.gui.workers.download", fake_download)

    worker = DownloadWorker(FakeClient(), jobs)
    failed, done = [], []
    worker.file_failed.connect(lambda n, m: failed.append(n))
    worker.file_done.connect(done.append)
    worker.run()

    assert failed == ["A.JPG"]
    assert done == ["B.JPG"]   # la coda non si ferma per un file


def test_download_worker_pauses_on_camera_asleep(qapp, tmp_path, monkeypatch):
    jobs = [Job(media("A.JPG", 100), tmp_path / "A.JPG", JobState.NEW)]

    def fake_download(client, job, on_progress=None, should_cancel=None, **kw):
        raise CameraAsleep("AP caduto")

    monkeypatch.setattr("olympus_connect.gui.workers.download", fake_download)

    worker = DownloadWorker(FakeClient(), jobs)
    paused, finished = [], []
    worker.paused.connect(paused.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.run()

    # Una fotocamera addormentata mette in pausa la coda, non la conclude:
    # i job restanti devono poter riprendere.
    assert paused and "disconness" in paused[0].lower()
    assert finished == []
    assert worker.remaining() == 1


def test_download_worker_cancel_stops_queue(qapp, tmp_path, monkeypatch):
    jobs = [Job(media(f"{c}.JPG", 100), tmp_path / f"{c}.JPG", JobState.NEW)
            for c in "AB"]

    def fake_download(client, job, on_progress=None, should_cancel=None, **kw):
        from olympus_connect.core.downloader import Outcome
        return Outcome.CANCELLED if should_cancel() else Outcome.DONE

    monkeypatch.setattr("olympus_connect.gui.workers.download", fake_download)

    worker = DownloadWorker(FakeClient(), jobs)
    worker.cancel()
    done = []
    worker.file_done.connect(done.append)
    worker.run()

    assert done == []
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `.venv/bin/pytest tests/test_workers.py -v`
Atteso: FAIL con `ModuleNotFoundError: No module named 'olympus_connect.gui'`

- [ ] **Step 3: Implementare `workers.py`**

```python
"""Worker Qt che avvolgono il core. Il core non conosce Qt; questo modulo è
l'unico punto in cui i due mondi si incontrano."""
from __future__ import annotations

import errno
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from ..core.downloader import Job, Outcome, download, pending, plan
from ..core.errors import (
    CameraAsleep, CameraNotReachable, IncompleteDownload, NetworkUnavailable,
    OlympusError, ProtocolError, WrongNetwork,
)


def describe_error(exc: Exception) -> str:
    """Traduce un'eccezione in un messaggio che dice all'utente cosa fare."""
    if isinstance(exc, CameraNotReachable):
        return ("La fotocamera non risponde su 192.168.0.10. Attiva il Wi-Fi "
                "sulla fotocamera e collega il PC al suo access point.")
    if isinstance(exc, CameraAsleep):
        return ("La fotocamera si è disconnessa, probabilmente per inattività. "
                "Riattiva il Wi-Fi sulla fotocamera e premi Riprova. "
                "Questo modello non supporta la ripresa, quindi il file "
                "interrotto riparte da zero; i file già completati restano.")
    if isinstance(exc, WrongNetwork):
        return f"Rete Wi-Fi sbagliata. {exc}"
    if isinstance(exc, NetworkUnavailable):
        return f"Gestione della rete non disponibile. {exc}"
    if isinstance(exc, IncompleteDownload):
        return f"Trasferimento incompleto. {exc}"
    if isinstance(exc, ProtocolError):
        return f"Risposta inattesa dalla fotocamera. {exc}"
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "Spazio su disco esaurito: libera spazio e riprova."
    if isinstance(exc, PermissionError):
        return f"Permessi insufficienti per scrivere: {exc}"
    if isinstance(exc, OSError):
        return f"Errore di scrittura su disco: {exc}"
    return f"Errore inatteso: {exc}"


class ListWorker(QObject):
    """Elenca i file sulla fotocamera e li confronta col disco."""

    listed = pyqtSignal(list)   # list[Job]
    failed = pyqtSignal(str)

    def __init__(self, client, dest_root: Path) -> None:
        super().__init__()
        self._client = client
        self._dest_root = Path(dest_root)

    @pyqtSlot()
    def run(self) -> None:
        try:
            self._client.connect()
            files = self._client.list_media()
            self.listed.emit(plan(files, self._dest_root))
        except Exception as exc:        # nessuna eccezione esce da un worker
            self.failed.emit(describe_error(exc))


class ThumbWorker(QObject):
    """Scarica le miniature su richiesta, una alla volta."""

    ready = pyqtSignal(str, bytes)
    failed = pyqtSignal(str, str)

    def __init__(self, client) -> None:
        super().__init__()
        self._client = client

    @pyqtSlot(str)
    def request(self, camera_path: str) -> None:
        try:
            self.ready.emit(camera_path, self._client.thumbnail(camera_path))
        except Exception as exc:
            self.failed.emit(camera_path, describe_error(exc))


class DownloadWorker(QObject):
    """Esegue una coda di download seriali, con pausa e ripresa."""

    file_started = pyqtSignal(str)
    file_progress = pyqtSignal(int, int)
    total_progress = pyqtSignal(int, int)
    file_done = pyqtSignal(str)
    file_failed = pyqtSignal(str, str)
    paused = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, client, jobs: list[Job]) -> None:
        super().__init__()
        self._client = client
        self._queue = list(pending(jobs))
        self._total = sum(j.media.size for j in self._queue)
        self._completed_bytes = 0
        self._cancelled = False

    def remaining(self) -> int:
        return len(self._queue)

    @pyqtSlot()
    def cancel(self) -> None:
        self._cancelled = True

    @pyqtSlot()
    def resume(self) -> None:
        self._cancelled = False
        self.run()

    @pyqtSlot()
    def run(self) -> None:
        while self._queue:
            if self._cancelled:
                return
            job = self._queue[0]
            self.file_started.emit(job.media.name)

            def on_progress(written: int, total: int, job=job) -> None:
                self.file_progress.emit(written, total)
                self.total_progress.emit(self._completed_bytes + written, self._total)

            try:
                outcome = download(
                    self._client, job,
                    on_progress=on_progress,
                    should_cancel=lambda: self._cancelled,
                )
            except CameraAsleep as exc:
                # La coda resta intatta: l'utente riconnette e riprende.
                self.paused.emit(describe_error(exc))
                return
            except Exception as exc:
                self._queue.pop(0)
                self._completed_bytes += job.media.size
                self.file_failed.emit(job.media.name, describe_error(exc))
                continue

            if outcome is Outcome.CANCELLED:
                return

            self._queue.pop(0)
            self._completed_bytes += job.media.size
            self.total_progress.emit(self._completed_bytes, self._total)
            self.file_done.emit(job.media.name)

        self.finished.emit()
```

- [ ] **Step 4: Eseguire i test per verificare che passino**

Run: `.venv/bin/xvfb-run -a pytest tests/test_workers.py -v` (oppure senza `xvfb-run` se c'è un display)
Atteso: PASS, tutti

Se `QApplication` non parte in ambiente headless, impostare `QT_QPA_PLATFORM=offscreen`:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_workers.py -v
```

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/gui tests/test_workers.py
git commit -m "feat(gui): worker Qt e traduzione degli errori del core"
```

---

### Task 9: Griglia delle miniature

**Files:**
- Create: `olympus_connect/gui/grid.py`
- Test: `tests/test_grid.py`

**Interfaces:**
- Consumes: `Job`, `JobState` (Task 4), `MediaKind` (Task 1)
- Produces:
  - `MediaListModel(QAbstractListModel)` — `set_jobs(list[Job])`, `job_at(QModelIndex) -> Job | None`, `set_thumbnail(camera_path: str, data: bytes)`, `visible_jobs() -> list[Job]`
  - `JOB_ROLE` — `Qt.ItemDataRole` custom per recuperare il `Job` da un indice
  - `MediaFilter(kinds: set[MediaKind] | None, only_new: bool)` — dataclass; `matches(job) -> bool`
  - `MediaGrid(QListView)` — signal `thumbnail_needed(str)`, metodo `set_filter(MediaFilter)`, `selected_jobs() -> list[Job]`

- [ ] **Step 1: Scrivere i test che falliscono**

```python
# tests/test_grid.py
from datetime import datetime
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt

from olympus_connect.core.downloader import Job, JobState
from olympus_connect.core.model import MediaFile, MediaKind
from olympus_connect.gui.grid import JOB_ROLE, MediaFilter, MediaListModel


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def job(name="A.JPG", kind=MediaKind.JPEG, state=JobState.NEW):
    m = MediaFile(f"/DCIM/100OLYMP/{name}", 100,
                  datetime(2026, 9, 24, 12, 0, 0), kind)
    return Job(m, Path("/tmp") / name, state)


def test_model_row_count_matches_jobs(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG"), job("B.JPG")])
    assert model.rowCount() == 2


def test_model_display_role_is_file_name(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    assert model.data(model.index(0), Qt.ItemDataRole.DisplayRole) == "A.JPG"


def test_model_job_role_returns_job(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    assert model.data(model.index(0), JOB_ROLE).media.name == "A.JPG"


def test_model_set_thumbnail_emits_data_changed(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    changed = []
    model.dataChanged.connect(lambda tl, br, roles: changed.append(tl.row()))

    model.set_thumbnail("/DCIM/100OLYMP/A.JPG", b"\xff\xd8")
    assert changed == [0]


def test_model_set_thumbnail_for_unknown_path_is_ignored(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG")])
    model.set_thumbnail("/DCIM/100OLYMP/GHOST.JPG", b"\xff\xd8")   # non deve sollevare


def test_filter_by_kind(qapp):
    f = MediaFilter(kinds={MediaKind.RAW}, only_new=False)
    assert f.matches(job("A.ORF", kind=MediaKind.RAW))
    assert not f.matches(job("A.JPG", kind=MediaKind.JPEG))


def test_filter_only_new_excludes_present(qapp):
    f = MediaFilter(kinds=None, only_new=True)
    assert f.matches(job(state=JobState.NEW))
    assert f.matches(job(state=JobState.INCOMPLETE))
    assert not f.matches(job(state=JobState.PRESENT))


def test_model_applies_filter_to_row_count(qapp):
    model = MediaListModel()
    model.set_jobs([
        job("A.JPG", kind=MediaKind.JPEG),
        job("B.ORF", kind=MediaKind.RAW),
    ])
    model.set_filter(MediaFilter(kinds={MediaKind.RAW}, only_new=False))

    assert model.rowCount() == 1
    assert model.data(model.index(0), Qt.ItemDataRole.DisplayRole) == "B.ORF"


def test_model_visible_jobs_respects_filter(qapp):
    model = MediaListModel()
    model.set_jobs([job("A.JPG"), job("B.ORF", kind=MediaKind.RAW)])
    model.set_filter(MediaFilter(kinds={MediaKind.JPEG}, only_new=False))

    assert [j.media.name for j in model.visible_jobs()] == ["A.JPG"]
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_grid.py -v`
Atteso: FAIL con `ModuleNotFoundError: No module named 'olympus_connect.gui.grid'`

- [ ] **Step 3: Implementare `grid.py`**

```python
"""Griglia virtualizzata delle miniature.

QListView in IconMode richiede le righe solo quando servono: 2000 foto sulla
scheda non creano 2000 widget, e le miniature si scaricano solo per ciò che
è effettivamente visibile.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QListView

from ..core.downloader import Job, JobState
from ..core.model import MediaKind

JOB_ROLE = Qt.ItemDataRole.UserRole + 1

THUMBNAIL_SIZE = QSize(160, 120)

_STATE_SUFFIX = {
    JobState.NEW: "",
    JobState.PRESENT: "  ✓",
    JobState.INCOMPLETE: "  ⟳",
}


@dataclass(frozen=True)
class MediaFilter:
    kinds: set[MediaKind] | None = None   # None = tutti i tipi
    only_new: bool = False

    def matches(self, job: Job) -> bool:
        if self.kinds is not None and job.media.kind not in self.kinds:
            return False
        if self.only_new and job.state is JobState.PRESENT:
            return False
        return True


class MediaListModel(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self._all: list[Job] = []
        self._visible: list[Job] = []
        self._thumbnails: dict[str, QPixmap] = {}
        self._filter = MediaFilter()

    # -- popolamento -------------------------------------------------------

    def set_jobs(self, jobs: list[Job]) -> None:
        self.beginResetModel()
        self._all = list(jobs)
        self._reapply_filter()
        self.endResetModel()

    def set_filter(self, media_filter: MediaFilter) -> None:
        self.beginResetModel()
        self._filter = media_filter
        self._reapply_filter()
        self.endResetModel()

    def _reapply_filter(self) -> None:
        self._visible = [j for j in self._all if self._filter.matches(j)]

    def visible_jobs(self) -> list[Job]:
        return list(self._visible)

    def job_at(self, index: QModelIndex) -> Job | None:
        if not index.isValid() or index.row() >= len(self._visible):
            return None
        return self._visible[index.row()]

    def set_thumbnail(self, camera_path: str, data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        self._thumbnails[camera_path] = pixmap
        for row, job in enumerate(self._visible):
            if job.media.camera_path == camera_path:
                idx = self.index(row)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
                return

    # -- interfaccia QAbstractListModel ------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        job = self.job_at(index)
        if job is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return job.media.name
        if role == JOB_ROLE:
            return job
        if role == Qt.ItemDataRole.DecorationRole:
            pixmap = self._thumbnails.get(job.media.camera_path)
            return QIcon(pixmap) if pixmap else None
        if role == Qt.ItemDataRole.ToolTipRole:
            megabytes = job.media.size / 1_048_576
            when = job.media.shot_at.strftime("%d/%m/%Y %H:%M") \
                if job.media.shot_at else "data sconosciuta"
            return (f"{job.media.name}{_STATE_SUFFIX[job.state]}\n"
                    f"{when} — {megabytes:.1f} MB — {job.media.kind.value}")
        return None


class MediaGrid(QListView):
    thumbnail_needed = pyqtSignal(str)

    def __init__(self, model: MediaListModel) -> None:
        super().__init__()
        self.setModel(model)
        self._model = model
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(THUMBNAIL_SIZE)
        self.setGridSize(QSize(THUMBNAIL_SIZE.width() + 24,
                               THUMBNAIL_SIZE.height() + 40))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self._requested: set[str] = set()
        self.verticalScrollBar().valueChanged.connect(self.request_visible_thumbnails)

    def set_filter(self, media_filter: MediaFilter) -> None:
        self._model.set_filter(media_filter)
        self.request_visible_thumbnails()

    def selected_jobs(self) -> list[Job]:
        jobs = [self._model.job_at(i) for i in self.selectedIndexes()]
        return [j for j in jobs if j is not None]

    def request_visible_thumbnails(self) -> None:
        """Chiede le miniature solo per ciò che si vede, una volta sola."""
        rect = self.viewport().rect()
        for row in range(self._model.rowCount()):
            index = self._model.index(row)
            if not rect.intersects(self.visualRect(index)):
                continue
            job = self._model.job_at(index)
            if job and job.media.camera_path not in self._requested:
                self._requested.add(job.media.camera_path)
                self.thumbnail_needed.emit(job.media.camera_path)
```

- [ ] **Step 4: Eseguire i test per verificare che passino**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_grid.py -v`
Atteso: PASS, 9 test

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/gui/grid.py tests/test_grid.py
git commit -m "feat(gui): griglia virtualizzata con filtri e miniature pigre"
```

---

### Task 10: Finestra principale e cablaggio

**Files:**
- Create: `olympus_connect/gui/main_window.py`
- Create: `olympus_connect/__main__.py`
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: tutto il core, `workers.py` (Task 8), `grid.py` (Task 9)
- Produces:
  - `MainWindow(QMainWindow)` — costruttore senza argomenti obbligatori; accetta `client_factory`, `network_factory`, `settings` iniettabili per i test
  - `main() -> int` — entry point

- [ ] **Step 1: Scrivere i test che falliscono**

```python
# tests/test_main_window.py
"""Smoke test: la finestra si costruisce, i filtri si propagano al modello,
e uno stato diagnostico fallito produce un messaggio in barra di stato."""
from datetime import datetime
from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings

from olympus_connect.core.downloader import Job, JobState
from olympus_connect.core.model import MediaFile, MediaKind
from olympus_connect.core.network import Diagnosis, Rung


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "test.ini"), QSettings.Format.IniFormat)


class FakeNetwork:
    def __init__(self, diagnosis):
        self._diagnosis = diagnosis
        self.ssid = "CAM"

    def diagnose(self):
        return self._diagnosis


def all_green():
    return Diagnosis([Rung("porta 80 della fotocamera", True, "risponde")])


def all_red():
    return Diagnosis([Rung("dispositivo Wi-Fi", False, "nessuna interfaccia")])


def test_window_constructs(qapp, settings, tmp_path):
    from olympus_connect.gui.main_window import MainWindow
    window = MainWindow(network_factory=lambda ssid: FakeNetwork(all_green()),
                        settings=settings)
    assert window.windowTitle()


def test_window_uses_default_destination_when_unset(qapp, settings):
    from olympus_connect.gui.main_window import MainWindow
    window = MainWindow(network_factory=lambda ssid: FakeNetwork(all_green()),
                        settings=settings)
    assert window.dest_root() == Path.home() / "Immagini" / "Olympus"


def test_window_remembers_destination(qapp, settings, tmp_path):
    from olympus_connect.gui.main_window import MainWindow
    settings.setValue("dest_root", str(tmp_path / "Scatti"))
    window = MainWindow(network_factory=lambda ssid: FakeNetwork(all_green()),
                        settings=settings)
    assert window.dest_root() == tmp_path / "Scatti"


def test_failed_diagnosis_shows_first_failing_rung(qapp, settings):
    from olympus_connect.gui.main_window import MainWindow
    window = MainWindow(network_factory=lambda ssid: FakeNetwork(all_red()),
                        settings=settings)
    window.refresh_status()
    assert "Wi-Fi" in window.statusBar().currentMessage()


def test_window_remembers_filters(qapp, settings):
    from olympus_connect.gui.main_window import MainWindow

    first = MainWindow(network_factory=lambda ssid: FakeNetwork(all_green()),
                       settings=settings)
    first._filter_raw.setChecked(False)
    first._only_new.setChecked(True)

    second = MainWindow(network_factory=lambda ssid: FakeNetwork(all_green()),
                        settings=settings)
    assert second._filter_raw.isChecked() is False
    assert second._only_new.isChecked() is True
    assert second._filter_jpeg.isChecked() is True


def test_summary_counts_new_and_present(qapp, settings, tmp_path):
    from olympus_connect.gui.main_window import MainWindow
    window = MainWindow(network_factory=lambda ssid: FakeNetwork(all_green()),
                        settings=settings)

    def job(name, state):
        m = MediaFile(f"/DCIM/100OLYMP/{name}", 100,
                      datetime(2026, 9, 24, 12, 0, 0), MediaKind.JPEG)
        return Job(m, tmp_path / name, state)

    window.on_listed([job("A.JPG", JobState.NEW),
                      job("B.JPG", JobState.NEW),
                      job("C.JPG", JobState.PRESENT)])

    message = window.statusBar().currentMessage()
    assert "2" in message and "1" in message
```

- [ ] **Step 2: Eseguire i test per verificare che falliscano**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_main_window.py -v`
Atteso: FAIL con `ModuleNotFoundError: No module named 'olympus_connect.gui.main_window'`

- [ ] **Step 3: Implementare `main_window.py`**

```python
"""Finestra principale: cabla worker, griglia e stato della rete."""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QSettings, QThread, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QToolBar,
    QVBoxLayout, QWidget,
)

from ..core.errors import NetworkUnavailable, OlympusError
from ..core.model import MediaKind
from ..core.network import CameraNetwork
from ..core.protocol import OlympusClient
from .grid import MediaFilter, MediaGrid, MediaListModel
from .workers import DownloadWorker, ListWorker, ThumbWorker, describe_error

DEFAULT_DEST = Path.home() / "Immagini" / "Olympus"


class MainWindow(QMainWindow):
    def __init__(self, client_factory=OlympusClient,
                 network_factory=CameraNetwork,
                 settings: QSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Olympus Connect")
        self.resize(1000, 700)

        self._settings = settings or QSettings("olympus-connect", "olympus-connect")
        self._client_factory = client_factory
        self._network_factory = network_factory
        self._client = None
        self._threads: list[QThread] = []
        self._download_worker: DownloadWorker | None = None

        self._model = MediaListModel()
        self._grid = MediaGrid(self._model)

        self._build_ui()
        self.refresh_status()

    # -- costruzione interfaccia ------------------------------------------

    def _build_ui(self) -> None:
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        self._connect_action = toolbar.addAction("Connetti", self.on_connect)
        self._refresh_action = toolbar.addAction("Aggiorna", self.on_refresh)
        self._download_action = toolbar.addAction("Scarica selezionati",
                                                 self.on_download_selected)
        toolbar.addSeparator()
        toolbar.addAction("Cartella destinazione…", self.on_choose_destination)

        self._filter_jpeg = QCheckBox("JPEG")
        self._filter_raw = QCheckBox("RAW")
        self._filter_movie = QCheckBox("Video")
        self._only_new = QCheckBox("Solo non scaricati")
        self._filter_boxes = {
            "jpeg": self._filter_jpeg,
            "raw": self._filter_raw,
            "movie": self._filter_movie,
            "only_new": self._only_new,
        }
        for key, box in self._filter_boxes.items():
            default = key != "only_new"   # i tipi partono attivi, il filtro no
            box.setChecked(self._settings.value(f"filter/{key}", default, type=bool))
            box.stateChanged.connect(self.on_filter_changed)
            toolbar.addWidget(box)

        self._file_label = QLabel("")
        self._file_bar = QProgressBar()
        self._total_bar = QProgressBar()
        self._cancel_button = QPushButton("Annulla")
        self._resume_button = QPushButton("Riprova")
        self._cancel_button.setEnabled(False)
        self._resume_button.setEnabled(False)
        self._cancel_button.clicked.connect(self.on_cancel)
        self._resume_button.clicked.connect(self.on_resume)

        buttons = QHBoxLayout()
        buttons.addWidget(self._cancel_button)
        buttons.addWidget(self._resume_button)

        layout = QVBoxLayout()
        layout.addWidget(self._grid, stretch=1)
        layout.addWidget(self._file_label)
        layout.addWidget(self._file_bar)
        layout.addWidget(self._total_bar)
        layout.addLayout(buttons)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self._thumb_worker: ThumbWorker | None = None

    # -- impostazioni -----------------------------------------------------

    def dest_root(self) -> Path:
        stored = self._settings.value("dest_root", "")
        return Path(stored) if stored else DEFAULT_DEST

    def camera_ssid(self) -> str:
        return str(self._settings.value("ssid", "") or "")

    def on_choose_destination(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Cartella di destinazione", str(self.dest_root())
        )
        if chosen:
            self._settings.setValue("dest_root", chosen)
            self.statusBar().showMessage(f"Destinazione: {chosen}", 5000)

    # -- stato della rete --------------------------------------------------

    def refresh_status(self) -> None:
        try:
            diagnosis = self._network_factory(self.camera_ssid()).diagnose()
        except NetworkUnavailable as exc:
            self.statusBar().showMessage(describe_error(exc))
            return

        if diagnosis.ok:
            self.statusBar().showMessage("Fotocamera raggiungibile.")
            return

        failing = next(r for r in diagnosis.rungs if not r.ok)
        self.statusBar().showMessage(f"{failing.name}: {failing.detail}")

    def on_connect(self) -> None:
        ssid = self.camera_ssid()
        if not ssid:
            ssid, accepted = QInputDialog.getText(
                self, "Rete della fotocamera",
                "SSID mostrato dalla fotocamera:"
            )
            if not accepted or not ssid:
                return
            self._settings.setValue("ssid", ssid)

        network = self._network_factory(ssid)
        try:
            if not network.profile_exists():
                # La password va a NetworkManager e non viene mai salvata da noi.
                password, accepted = QInputDialog.getText(
                    self, "Password Wi-Fi",
                    f"Password della rete «{ssid}»:",
                    echo=QLineEdit.EchoMode.Password,
                )
                if not accepted or not password:
                    return
                network.ensure_profile(password=password)
            else:
                network.ensure_profile()
            network.connect()
        except OlympusError as exc:
            QMessageBox.warning(self, "Connessione", describe_error(exc))
            return

        self.refresh_status()
        self.on_refresh()

    # -- elenco -----------------------------------------------------------

    def _start(self, worker, method: str = "run") -> QThread:
        thread = QThread(self)
        worker.moveToThread(thread)
        getattr(thread.started, "connect")(getattr(worker, method))
        thread.start()
        self._threads.append(thread)
        return thread

    def on_refresh(self) -> None:
        self._client = self._client_factory()
        worker = ListWorker(self._client, self.dest_root())
        worker.listed.connect(self.on_listed)
        worker.failed.connect(self.on_failed)
        self._list_worker = worker           # mantiene il riferimento vivo
        self._start(worker)

    @pyqtSlot(list)
    def on_listed(self, jobs: list) -> None:
        self._model.set_jobs(jobs)
        self.on_filter_changed()

        from ..core.downloader import JobState
        new = sum(1 for j in jobs if j.state is not JobState.PRESENT)
        present = len(jobs) - new
        self.statusBar().showMessage(
            f"{new} da scaricare, {present} già presenti."
        )

        if self._client is not None:
            self._ensure_thumb_worker()
            self._grid.request_visible_thumbnails()

    def _ensure_thumb_worker(self) -> None:
        """Un solo worker per le miniature, sul proprio thread."""
        if self._thumb_worker is not None:
            return
        worker = ThumbWorker(self._client)
        worker.ready.connect(
            lambda path, data: self._model.set_thumbnail(path, data)
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.start()
        self._threads.append(thread)
        self._thumb_worker = worker
        # QueuedConnection: la richiesta viene eseguita sul thread del worker.
        # Con una connessione diretta il download della miniatura girerebbe
        # sul thread della GUI e la bloccherebbe.
        self._grid.thumbnail_needed.connect(
            worker.request, Qt.ConnectionType.QueuedConnection
        )

    @pyqtSlot(str)
    def on_failed(self, message: str) -> None:
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Fotocamera", message)

    # -- filtri ------------------------------------------------------------

    def on_filter_changed(self) -> None:
        for key, box in self._filter_boxes.items():
            self._settings.setValue(f"filter/{key}", box.isChecked())

        kinds: set[MediaKind] = set()
        if self._filter_jpeg.isChecked():
            kinds.add(MediaKind.JPEG)
        if self._filter_raw.isChecked():
            kinds.add(MediaKind.RAW)
        if self._filter_movie.isChecked():
            kinds.add(MediaKind.MOVIE)
        self._grid.set_filter(
            MediaFilter(kinds=kinds, only_new=self._only_new.isChecked())
        )

    # -- download ----------------------------------------------------------

    def on_download_selected(self) -> None:
        jobs = self._grid.selected_jobs()
        if not jobs:
            self.statusBar().showMessage("Nessun file selezionato.", 4000)
            return
        if self._client is None:
            self.statusBar().showMessage("Aggiorna prima l'elenco.", 4000)
            return

        worker = DownloadWorker(self._client, jobs)
        worker.file_started.connect(self._file_label.setText)
        worker.file_progress.connect(self._update_file_bar)
        worker.total_progress.connect(self._update_total_bar)
        worker.file_failed.connect(
            lambda name, msg: self.statusBar().showMessage(f"{name}: {msg}")
        )
        worker.paused.connect(self.on_paused)
        worker.finished.connect(self.on_download_finished)

        self._download_worker = worker
        self._cancel_button.setEnabled(True)
        self._resume_button.setEnabled(False)
        self._start(worker)

    def _update_file_bar(self, written: int, total: int) -> None:
        self._file_bar.setMaximum(max(total, 1))
        self._file_bar.setValue(written)

    def _update_total_bar(self, written: int, total: int) -> None:
        self._total_bar.setMaximum(max(total, 1))
        self._total_bar.setValue(written)

    def on_cancel(self) -> None:
        if self._download_worker is not None:
            self._download_worker.cancel()
        self._cancel_button.setEnabled(False)
        self._resume_button.setEnabled(True)

    def on_resume(self) -> None:
        if self._download_worker is None:
            return
        self._resume_button.setEnabled(False)
        self._cancel_button.setEnabled(True)
        self._download_worker.resume()

    @pyqtSlot(str)
    def on_paused(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self._cancel_button.setEnabled(False)
        self._resume_button.setEnabled(True)

    @pyqtSlot()
    def on_download_finished(self) -> None:
        self._file_label.setText("")
        self._cancel_button.setEnabled(False)
        self._resume_button.setEnabled(False)
        self.statusBar().showMessage("Download completato.")
        self.on_refresh()   # ricalcola lo stato dei file a destinazione

    # -- chiusura ----------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._download_worker is not None:
            self._download_worker.cancel()
        for thread in self._threads:
            thread.quit()
            thread.wait(2000)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
```

- [ ] **Step 4: Creare `__main__.py`**

```python
import sys

from .gui.main_window import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Eseguire i test per verificare che passino**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_main_window.py -v`
Atteso: PASS, 6 test

- [ ] **Step 6: Eseguire l'intera suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -v`
Atteso: PASS, tutta la suite

- [ ] **Step 7: Commit**

```bash
git add olympus_connect tests/test_main_window.py
git commit -m "feat(gui): finestra principale, filtri, coda di download"
```

---

### Task 11: Prova end-to-end con la fotocamera e README

**Files:**
- Create: `README.md`
- Modify: `docs/field-report-2026-09-24.md` (aggiunge la sezione della prova end-to-end)

**Interfaces:**
- Consumes: tutto
- Produces: niente codice

- [ ] **Step 1: Avviare l'applicazione con la fotocamera accesa**

```bash
QT_QPA_PLATFORM=xcb .venv/bin/python -m olympus_connect
```

- [ ] **Step 2: Percorrere la lista di verifica manuale**

Annotare l'esito di ciascun punto in `docs/field-report-2026-09-24.md`:

- [ ] la barra di stato mostra lo stato corretto **prima** di collegarsi alla fotocamera
- [ ] "Connetti" chiede SSID e password la prima volta, e non le richiede la seconda
- [ ] **la connessione Internet resta funzionante** mentre si è collegati alla fotocamera (`ping -c1 1.1.1.1` da un altro terminale) — è la verifica di `ipv4.never-default`
- [ ] l'elenco si popola e la barra di stato riporta "N da scaricare, M già presenti"
- [ ] le miniature compaiono scorrendo, e la finestra **non si blocca** durante il caricamento
- [ ] i filtri JPEG / RAW / Video / Solo non scaricati riducono la griglia come previsto
- [ ] il download di un JPEG produce un file apribile, con data di modifica pari alla data di scatto
- [ ] il download di un ORF produce un file che `exiv2` legge: `exiv2 ~/Immagini/Olympus/*/*/*.ORF`
- [ ] il download di un MOV produce un file riproducibile
- [ ] "Annulla" durante un MOV lascia un `.part`, e un nuovo download lo riprende invece di ricominciare (verificare con `ls -l` che il `.part` cresca dal valore precedente)
- [ ] spegnendo il Wi-Fi della fotocamera a metà di un MOV, la coda si mette in pausa con un messaggio comprensibile; riaccendendolo, "Riprova" completa il file (ripartendo da zero, come previsto su questo firmware)
- [ ] `keep_awake` viene invocato alla connessione e la fotocamera non si addormenta durante un download lungo
- [ ] rilanciando il download degli stessi file, tutti risultano "già presenti" e nulla viene ritrasferito
- [ ] chiudendo la finestra durante un download, il processo termina senza errori e senza restare appeso

- [ ] **Step 3: Scrivere il `README.md`**

Contenuto obbligatorio:

- cos'è e per quale fotocamera è stato verificato (modello e versione firmware letti da `get_caminfo.cgi`)
- **la spiegazione del problema su Linux**: l'header `User-Agent: OI.Share v2` richiesto dalla fotocamera, e la default route sottratta da NetworkManager. È l'informazione per cui qualcun altro troverà utile questo repository.
- installazione: `python -m venv`, `pip install -e .`
- avvio: `python -m olympus_connect`
- uso di `diag.py` quando qualcosa non funziona
- tabella dei formati effettivamente verificati sul campo (JPEG / ORF / MOV), con l'esito reale del Task 0, non quello sperato
- cosa il software non fa: non cancella file dalla fotocamera, non fa live view né scatto remoto

- [ ] **Step 4: Commit**

```bash
git add README.md docs/field-report-2026-09-24.md
git commit -m "docs: README e referto della prova end-to-end"
```

---

## Note per chi esegue il piano

**Se il Task 0 smentisce un'assunzione**, fermarsi e riferire prima di proseguire. In particolare:

- ~~**`Range` non supportato**~~ — **accertato il 2026-09-24: non è supportato.** Le conseguenze sono già applicate nel piano: Task 5 rietichettato "recupero" invece di "ripresa", i test coprono entrambi i rami, `pending()` ordina dal file più piccolo, `keep_awake` aggiunto al Task 2, pulsante "Riprova" nel Task 10. `_attempt` gestisce già `stream.resumed is False` ripartendo da capo, quindi il codice non cambia.
- **ORF o MOV non scaricabili**: i tipi restano nel modello (li vediamo comunque nella griglia), ma `plan()` deve marcarli come non disponibili e la griglia deve mostrarli disabilitati. Aggiungere un task per questo invece di lasciare download che falliscono in silenzio.
- **La fotocamera risponde anche senza lo `User-Agent`**: la causa del problema è altrove. Prima di scrivere codice, usare l'output di `diag.py` per capire quale gradino della scala fallisce davvero, e correggere la sezione *Problema* della spec.

**Il core non importa Qt.** Se un task sembra richiederlo, il confine è nel posto sbagliato: fermarsi e riferire.
