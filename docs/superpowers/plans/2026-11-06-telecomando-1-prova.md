# Telecomando, parte 1: prova sul campo — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** misurare sulla fotocamera vera la modalità scatto: visione dal vivo (formato dei pacchetti, fotogrammi al secondo, firewall), messa a fuoco col tocco, scatto, revisione, video; ricavarne fixture senza immagini né seriali e le decisioni per la parte 2.

**Architecture:** un passaggio `probe_remote()` in `diag.py`, attivo solo con `--telecomando`, che salva risposte e pacchetti grezzi in `diag-downloads/telecomando/` (ignorata da git); uno strumento `tools/struttura_liveview.py` che ne ricava fixture con le sole intestazioni dei pacchetti. È il cancello della specifica: la parte 2 (lettore dei pacchetti, worker, interfaccia) si scrive come piano separato sui dati veri.

**Tech Stack:** Python 3.12+, `socket`, `threading`, `struct`, requests; pytest.

**Specifica:** `docs/superpowers/specs/2026-11-06-telecomando-design.md`, sezione «Prova sul campo (pezzo A)».

## Global Constraints

- I test si lanciano con `.venv/bin/python -m pytest` (non c'è lo script `pytest`).
- Nessuna nuova dipendenza.
- `exec_erase.cgi` non va mai invocato.
- La prova torna **sempre** in riproduzione, in un blocco `finally`.
- Il passaggio del telecomando gira **solo** con `--telecomando`; con quell'opzione `diag.py` salta la registrazione delle fixture e le altre prove (non riscrive `tests/fixtures/`).
- **Dati personali:** nessuna immagine (fotogrammi del live, revisione, ultimo JPEG) e nessun numero di serie entra in un commit. Pacchetti grezzi e immagini restano in `diag-downloads/`, ignorata da git.
- Porta UDP della prova: **40000**, fissa, così una regola temporanea del firewall vale anche al tentativo successivo.
- Ogni messaggio di commit termina con la riga `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Mappa dei file

| File | Stato | Responsabilità |
|---|---|---|
| `diag.py` | modifica | `rtp_summary`, `salva_pacchetti`/`leggi_pacchetti`, `probe_remote`, opzione `--telecomando` |
| `pyproject.toml` | modifica | `pythonpath = ["."]` per i test, così `import diag` funziona |
| `tests/test_diag_remote.py` | nuovo | test degli helper puri di `diag.py` |
| `tools/struttura_liveview.py` | nuovo | fixture con le sole intestazioni dei pacchetti |
| `tests/fixtures/liveview/` | nuovo | `0640x0480.json`, `1024x0768.json` |
| `tests/fixtures/remote/` | nuovo | risposte di `get_camprop` verificate senza seriali |
| specifica, resoconto sul campo | modifica | esito e decisioni |

---

### Task 1: `probe_remote` in `diag.py`

**Files:**
- Modify: `diag.py` (import in testa; costanti e funzioni nuove dopo `probe_keepalive`; `run()` e `main()`; docstring del modulo)
- Modify: `pyproject.toml` (sezione `[tool.pytest.ini_options]`)
- Test: `tests/test_diag_remote.py`

**Interfaces:**
- Consumes: in `diag.py` esistono `BASE`, `UA`, `TIMEOUT`, `step(name, ok, detail) -> bool`, `ladder() -> bool`, `Tee`, `REPORT`.
- Produces:
  - `LIVE_PORT = 40000`, `LIVE_SECONDS = 5.0`, `REMOTE_OUT = Path("diag-downloads/telecomando")`;
  - `rtp_summary(packet: bytes) -> dict | None` con le chiavi `version, padding, extension, csrc, marker, payload_type, seq, timestamp, ssrc, ext_profile, ext_len, header_len, payload_len, payload_soi, payload_eoi`;
  - `salva_pacchetti(path: Path, pacchetti: list[tuple[float, tuple[str, int], bytes]]) -> None` e `leggi_pacchetti(path: Path) -> list[tuple[float, bytes]]`: formato a record, ciascuno `struct.pack(">dI", t, len(dati)) + dati`, con `t` in secondi dal primo pacchetto;
  - `probe_remote() -> None`;
  - `run(telecomando: bool = False) -> int`; `main()` passa `"--telecomando" in sys.argv[1:]`.
  - File in `diag-downloads/telecomando/`: `camprop_takemode.xml`, `camprop_desclist.xml`, `live_0640x0480.bin`, `live_1024x0768.bin`, `live_video.bin`, `recview.jpg`, `lastjpg.jpg` (se arrivano), `af.txt` (corpo della risposta della messa a fuoco).

- [ ] **Step 1: Rendi `diag.py` importabile dai test**

In `pyproject.toml`, dentro `[tool.pytest.ini_options]`, sotto `testpaths = ["tests"]`:

```toml
pythonpath = ["."]
```

- [ ] **Step 2: Scrivi i test che falliscono**

Create `tests/test_diag_remote.py`:

```python
"""Gli helper puri della prova del telecomando in diag.py.

La prova vera gira solo con la fotocamera; questi pezzi no, e da loro
dipende lo strumento che ricava le fixture dai pacchetti registrati.
"""
import struct

from diag import leggi_pacchetti, rtp_summary, salva_pacchetti


def _rtp(marker=0, seq=7, ts=1000, ssrc=0x1234, ext=None, payload=b"\xff\xd8ab"):
    b0 = 0x80 | (0x10 if ext is not None else 0)
    b1 = (0x80 if marker else 0) | 96
    head = struct.pack(">BBHII", b0, b1, seq, ts, ssrc)
    if ext is not None:
        profile, data = ext
        head += struct.pack(">HH", profile, len(data) // 4) + data
    return head + payload


def test_reads_the_fixed_header():
    s = rtp_summary(_rtp(marker=1, seq=513, ts=90000, ssrc=0xABCD))
    assert (s["version"], s["marker"], s["payload_type"]) == (2, 1, 96)
    assert (s["seq"], s["timestamp"], s["ssrc"]) == (513, 90000, 0xABCD)
    assert s["extension"] == 0 and s["ext_profile"] is None
    assert s["header_len"] == 12 and s["payload_len"] == 4
    assert s["payload_soi"] is True and s["payload_eoi"] is False


def test_reads_the_extension_and_skips_it():
    s = rtp_summary(_rtp(ext=(0x0001, b"\x00" * 8), payload=b"xy\xff\xd9"))
    assert s["extension"] == 1
    assert (s["ext_profile"], s["ext_len"]) == (1, 8)
    assert s["header_len"] == 12 + 4 + 8
    assert s["payload_len"] == 4 and s["payload_eoi"] is True


def test_short_or_truncated_packets_are_none_or_safe():
    assert rtp_summary(b"") is None
    assert rtp_summary(b"\x80" * 11) is None
    pacchetto = _rtp(ext=(1, b"\x00" * 8))
    for n in range(len(pacchetto)):
        rtp_summary(pacchetto[:n])          # non deve sollevare


def test_packets_round_trip_through_the_file(tmp_path):
    pacchetti = [(10.0, ("192.168.0.10", 5555), b"uno"),
                 (10.5, ("192.168.0.10", 5555), b""),
                 (11.25, ("192.168.0.10", 5555), b"\x00" * 1500)]
    path = tmp_path / "live.bin"
    salva_pacchetti(path, pacchetti)
    assert leggi_pacchetti(path) == [(0.0, b"uno"), (0.5, b""), (1.25, b"\x00" * 1500)]


def test_empty_capture_writes_an_empty_file(tmp_path):
    path = tmp_path / "vuoto.bin"
    salva_pacchetti(path, [])
    assert path.read_bytes() == b""
    assert leggi_pacchetti(path) == []
```

- [ ] **Step 3: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_diag_remote.py -q`
Expected: errore di raccolta, `ImportError: cannot import name 'leggi_pacchetti' from 'diag'`.

- [ ] **Step 4: Aggiungi gli import**

In testa a `diag.py`, insieme agli altri import della libreria standard:

```python
import struct
import threading
import time
```

- [ ] **Step 5: Aggiungi helper e prova**

In `diag.py`, subito dopo la funzione `probe_keepalive`:

```python
# -- telecomando --------------------------------------------------------------

LIVE_PORT = 40000           # fissa: una regola del firewall vale anche al secondo tentativo
LIVE_SECONDS = 5.0
REMOTE_OUT = Path("diag-downloads/telecomando")


def rtp_summary(packet: bytes) -> dict | None:
    """I campi dell'intestazione RTP (RFC 3550) di un pacchetto del live.

    Non interpreta il contenuto: serve a scoprire come la fotocamera spezza
    i fotogrammi. None per un pacchetto più corto dell'intestazione fissa.
    """
    if len(packet) < 12:
        return None
    b0, b1 = packet[0], packet[1]
    seq, timestamp, ssrc = struct.unpack(">HII", packet[2:12])
    csrc = b0 & 0x0F
    pos = 12 + 4 * csrc
    extension = (b0 >> 4) & 1
    ext_profile = ext_len = None
    if extension and len(packet) >= pos + 4:
        ext_profile, words = struct.unpack(">HH", packet[pos:pos + 4])
        ext_len = 4 * words
        pos += 4 + ext_len
    payload = packet[pos:] if pos <= len(packet) else b""
    return {
        "version": b0 >> 6, "padding": (b0 >> 5) & 1, "extension": extension,
        "csrc": csrc, "marker": b1 >> 7, "payload_type": b1 & 0x7F,
        "seq": seq, "timestamp": timestamp, "ssrc": ssrc,
        "ext_profile": ext_profile, "ext_len": ext_len,
        "header_len": min(pos, len(packet)), "payload_len": len(payload),
        "payload_soi": payload[:2] == b"\xff\xd8",
        "payload_eoi": payload[-2:] == b"\xff\xd9",
    }


def salva_pacchetti(path: Path, pacchetti: list[tuple[float, tuple[str, int], bytes]]) -> None:
    """Registra i pacchetti grezzi: tempo relativo al primo, lunghezza, byte."""
    inizio = pacchetti[0][0] if pacchetti else 0.0
    with path.open("wb") as f:
        for t, _origine, dati in pacchetti:
            f.write(struct.pack(">dI", t - inizio, len(dati)) + dati)


def leggi_pacchetti(path: Path) -> list[tuple[float, bytes]]:
    dati = path.read_bytes()
    out: list[tuple[float, bytes]] = []
    pos = 0
    while pos + 12 <= len(dati):
        t, n = struct.unpack(">dI", dati[pos:pos + 12])
        out.append((t, dati[pos + 12:pos + 12 + n]))
        pos += 12 + n
    return out


def _cgi(path: str, **params) -> tuple[int, bytes, float]:
    """Una richiesta alla fotocamera: (stato, corpo, millisecondi). Non solleva."""
    inizio = time.monotonic()
    try:
        r = requests.get(f"{BASE}/{path}", headers=UA, params=params, timeout=TIMEOUT)
        return r.status_code, r.content, (time.monotonic() - inizio) * 1000
    except requests.RequestException as exc:
        return 0, type(exc).__name__.encode(), (time.monotonic() - inizio) * 1000


def _riporta(nome: str, risposta: tuple[int, bytes, float]) -> bool:
    stato, corpo, ms = risposta
    if corpo[:2] == b"\xff\xd8":
        testo = f"JPEG {len(corpo)} byte"
    else:
        testo = " ".join(corpo[:160].decode("utf-8", "replace").split()) or "(vuoto)"
    return step(nome, stato == 200, f"HTTP {stato}, {ms:.0f} ms: {testo}")


def _ricevi(sock: socket.socket, secondi: float) -> list[tuple[float, tuple[str, int], bytes]]:
    pacchetti: list[tuple[float, tuple[str, int], bytes]] = []
    sock.settimeout(0.25)
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        try:
            dati, origine = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        pacchetti.append((time.monotonic(), origine, dati))
    return pacchetti


def _regole_firewall() -> None:
    print("    Probabile blocco del firewall. Per riprovare, apri la porta")
    print("    solo per la fotocamera, riesegui, poi richiudila:")
    print(f"      sudo ufw allow in from 192.168.0.10 proto udp to any port {LIVE_PORT}")
    print(f"      sudo ufw delete allow in from 192.168.0.10 proto udp to any port {LIVE_PORT}")


def _analizza(nome: str, pacchetti, secondi: float) -> None:
    if not pacchetti:
        step(f"live {nome}", False, f"nessun pacchetto in {secondi:.0f} s")
        _regole_firewall()
        return
    sommari = [s for s in (rtp_summary(p) for _t, _o, p in pacchetti) if s]
    dimensioni = [len(p) for _t, _o, p in pacchetti]
    origini = sorted({f"{o[0]}:{o[1]}" for _t, o, _p in pacchetti})
    inizi = sum(1 for s in sommari if s["payload_soi"])
    marcatori = sum(1 for s in sommari if s["marker"])
    fini = sum(1 for s in sommari if s["payload_eoi"])
    salti = sum(1 for a, b in zip(sommari, sommari[1:])
                if (b["seq"] - a["seq"]) % 65536 != 1)
    step(f"live {nome}", True,
         f"{len(pacchetti)} pacchetti ({len(pacchetti) / secondi:.0f}/s), "
         f"circa {inizi / secondi:.1f} fotogrammi/s, dimensioni "
         f"{min(dimensioni)}–{max(dimensioni)} byte, origine {', '.join(origini)}")
    print(f"    inizi JPEG {inizi}, marcatori RTP {marcatori}, fini JPEG {fini}, "
          f"salti di sequenza {salti}, RTP non leggibili {len(pacchetti) - len(sommari)}")
    if sommari:
        s = sommari[0]
        print(f"    RTP: versione {s['version']}, payload type {s['payload_type']}, "
              f"CSRC {s['csrc']}, padding {s['padding']}")
    con_estensione = [(s, p) for s, (_t, _o, p) in zip(sommari, pacchetti) if s["extension"]]
    print(f"    pacchetti con estensione: {len(con_estensione)}")
    if con_estensione:
        s, p = con_estensione[0]
        print(f"    prima estensione: profilo 0x{s['ext_profile']:04x}, {s['ext_len']} byte, "
              f"intestazione {p[:min(s['header_len'], 48)].hex(' ')}")


def probe_remote() -> None:
    """La modalità scatto: live, messa a fuoco, scatto, revisione, video.

    Gira solo con --telecomando: scatta davvero una foto e gira un video di
    3 secondi. Pacchetti e immagini restano in diag-downloads/, perché
    mostrano ciò che la fotocamera inquadra.
    """
    print("\n== Telecomando (modalità scatto) ==")
    print("  Scatta una foto e gira un video di 3 secondi.")
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", LIVE_PORT))
    except OSError as exc:
        step(f"porta UDP {LIVE_PORT}", False, f"{type(exc).__name__}: {exc}")
        return

    try:
        _riporta("switch_cammode rec 0640x0480",
                 _cgi("switch_cammode.cgi", mode="rec", lvqty="0640x0480"))

        for nome, params in (("takemode", {"com": "get", "propname": "takemode"}),
                             ("desclist", {"com": "desc", "propname": "desclist"})):
            risposta = _cgi("get_camprop.cgi", **params)
            _riporta(f"get_camprop {nome}", risposta)
            (REMOTE_OUT / f"camprop_{nome}.xml").write_bytes(risposta[1])

        for qualita in ("0640x0480", "1024x0768"):
            if qualita != "0640x0480":
                _riporta(f"switch_cammode rec {qualita}",
                         _cgi("switch_cammode.cgi", mode="rec", lvqty=qualita))
            _riporta(f"startliveview {qualita}",
                     _cgi("exec_takemisc.cgi", com="startliveview", port=LIVE_PORT))
            pacchetti = _ricevi(sock, LIVE_SECONDS)
            _riporta(f"stopliveview {qualita}", _cgi("exec_takemisc.cgi", com="stopliveview"))
            _analizza(qualita, pacchetti, LIVE_SECONDS)
            salva_pacchetti(REMOTE_OUT / f"live_{qualita}.bin", pacchetti)
            _ricevi(sock, 0.5)          # scarta i pacchetti in coda dopo lo stop

        _riporta("switch_cammode rec 0640x0480 (scatto)",
                 _cgi("switch_cammode.cgi", mode="rec", lvqty="0640x0480"))

        risposta = _cgi("exec_takemotion.cgi", com="assignafframe", point="0320x0240")
        _riporta("assignafframe 0320x0240", risposta)
        (REMOTE_OUT / "af.txt").write_bytes(risposta[1])
        _riporta("releaseafframe", _cgi("exec_takemotion.cgi", com="releaseafframe"))

        _riporta("starttake", _cgi("exec_takemotion.cgi", com="starttake"))
        _riporta("stoptake", _cgi("exec_takemotion.cgi", com="stoptake"))
        for com, file in (("getrecview", "recview.jpg"), ("getlastjpg", "lastjpg.jpg")):
            risposta = _cgi("exec_takemisc.cgi", com=com)
            _riporta(com, risposta)
            if risposta[1][:2] == b"\xff\xd8":
                (REMOTE_OUT / file).write_bytes(risposta[1])

        # Video con il live attivo: il live continua durante la registrazione?
        _riporta("startliveview (video)",
                 _cgi("exec_takemisc.cgi", com="startliveview", port=LIVE_PORT))
        raccolti: list = []
        ricevitore = threading.Thread(
            target=lambda: raccolti.extend(_ricevi(sock, 6.0)), daemon=True)
        ricevitore.start()
        time.sleep(1.0)
        inizio_video = time.monotonic()
        risposta = _cgi("exec_takemotion.cgi", com="startmovietake", liveview="on")
        if not _riporta("startmovietake liveview=on", risposta):
            _riporta("startmovietake (senza liveview)",
                     _cgi("exec_takemotion.cgi", com="startmovietake"))
        time.sleep(3.0)
        _riporta("stopmovietake", _cgi("exec_takemotion.cgi", com="stopmovietake"))
        fine_video = time.monotonic()
        ricevitore.join()
        durante = sum(1 for t, _o, _p in raccolti if inizio_video <= t <= fine_video)
        step("live durante la registrazione", durante > 0,
             f"{durante} pacchetti in {fine_video - inizio_video:.1f} s di registrazione")
        salva_pacchetti(REMOTE_OUT / "live_video.bin", raccolti)
    finally:
        _cgi("exec_takemisc.cgi", com="stopliveview")
        sock.close()
        stato, _corpo, _ms = _cgi("switch_cammode.cgi", mode="play")
        print("  ripristinata modalità play" if stato == 200
              else "  ATTENZIONE: impossibile ripristinare la modalità play")

    stato, corpo, ms = _cgi("get_imglist.cgi", DIR="/DCIM/100OLYMP")
    nomi = [r.split(",")[1] for r in corpo.decode("ascii", "replace").splitlines()
            if r.count(",") == 5]
    step("nuovi file sulla scheda", stato == 200 and bool(nomi),
         f"HTTP {stato}, {len(nomi)} file, ultimi: {', '.join(nomi[-2:])}")
```

- [ ] **Step 6: L'opzione `--telecomando` in `run()` e `main()`**

In `run()`, cambia la firma in `def run(telecomando: bool = False) -> int:` e, subito dopo il blocco `if not ladder(): return 1`, inserisci:

```python
    if telecomando:
        # Niente registrazione delle fixture e niente altre prove: questa
        # esecuzione serve solo alla modalità scatto, e non deve riscrivere
        # tests/fixtures/ con i dati veri della scheda.
        probe_remote()
        print("\nFatto. Risposte e pacchetti in diag-downloads/telecomando/.")
        return 0
```

In `main()`, sostituisci `return run()` con:

```python
        return run(telecomando="--telecomando" in sys.argv[1:])
```

Nella docstring del modulo, dopo il blocco d'uso `python3 diag.py`, aggiungi:

```
Con l'opzione --telecomando prova invece la modalità scatto (visione dal
vivo, messa a fuoco, scatto, video): scatta davvero una foto e gira un
video di 3 secondi. Ghiera su P, A, S, M o iAuto.
```

- [ ] **Step 7: Verifica**

Run: `.venv/bin/python -m pytest tests/test_diag_remote.py -q && .venv/bin/python -m py_compile diag.py && .venv/bin/python -m pytest -q`
Expected: `5 passed`; nessun errore di compilazione; suite completa verde (314 + 5).

Non eseguire `diag.py`: senza fotocamera fallisce, e con `--telecomando` scatterebbe.

- [ ] **Step 8: Commit**

```bash
git add diag.py pyproject.toml tests/test_diag_remote.py
git commit -m "feat(diag): prova sul campo della modalità scatto (--telecomando)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Esecuzione sul campo, fixture, decisioni (con l'utente)

È il **cancello** della specifica. Il resto del telecomando si pianifica solo dopo.

**Files:**
- Create: `tools/struttura_liveview.py`
- Create: `tests/fixtures/liveview/0640x0480.json`, `tests/fixtures/liveview/1024x0768.json`, `tests/fixtures/liveview/video.json` (se il live durante il video è arrivato)
- Create: `tests/fixtures/remote/camprop_takemode.xml`, `tests/fixtures/remote/camprop_desclist.xml`, `tests/fixtures/remote/af.txt`
- Modify: `docs/superpowers/specs/2026-11-06-telecomando-design.md` (sezione «Prova sul campo», paragrafo «Cancello»), `docs/field-report-2026-09-24.md` (in coda)

**Interfaces:**
- Consumes: `rtp_summary`, `leggi_pacchetti` da `diag.py`; i file in `diag-downloads/telecomando/`; `diag-downloads/seriali.txt` (seriali veri della fotocamera, ignorato da git).
- Produces: per ogni registrazione, un JSON `{"qualita": str, "secondi": float, "pacchetti": [{"t": float, "intestazione": "<hex delle prime header_len byte>", "payload_len": int, "soi": bool, "eoi": bool}, ...]}`: le intestazioni RTP vere (con l'estensione) e la lunghezza del contenuto, **senza** il contenuto JPEG. La parte 2 ricostruisce pacchetti di prova sostituendo il contenuto con un JPEG sintetico della stessa lunghezza complessiva.

- [ ] **Step 1: L'utente esegue la prova**

Con la fotocamera in Wi-Fi, il PC collegato al suo access point, la ghiera su P, A, S, M o iAuto e un soggetto qualunque davanti all'obiettivo:

```bash
.venv/bin/python diag.py --telecomando
```

Se il referto dice «nessun pacchetto» e stampa le regole del firewall, l'utente applica la prima, riesegue, poi applica la seconda per richiuderla. Poi torna in linea.

- [ ] **Step 2: Leggi il referto**

Run: `sed -n '/== Telecomando/,$p' diag-report.txt`
Annota ogni riga. `diag-report.txt` è ignorato da git.

- [ ] **Step 3: Scrivi lo strumento delle fixture**

Create `tools/struttura_liveview.py`:

```python
#!/usr/bin/env python3
"""Ricava dai pacchetti del live registrati le fixture senza immagini.

I pacchetti grezzi in diag-downloads/telecomando/ contengono i fotogrammi
del live, cioè ciò che la fotocamera inquadrava: non vanno in git. Qui si
tiene solo la struttura: l'intestazione RTP vera di ogni pacchetto, estensione
compresa, e la lunghezza del contenuto.

Uso, dalla cartella del progetto:

    .venv/bin/python tools/struttura_liveview.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from diag import leggi_pacchetti, rtp_summary  # noqa: E402

SORGENTE = Path("diag-downloads/telecomando")
DESTINAZIONE = Path("tests/fixtures/liveview")
REGISTRAZIONI = {"live_0640x0480.bin": "0640x0480",
                 "live_1024x0768.bin": "1024x0768",
                 "live_video.bin": "video"}


def struttura(pacchetti: list[tuple[float, bytes]], qualita: str) -> dict:
    righe = []
    for t, dati in pacchetti:
        s = rtp_summary(dati)
        if s is None:
            continue
        righe.append({"t": round(t, 4),
                      "intestazione": dati[:s["header_len"]].hex(),
                      "payload_len": s["payload_len"],
                      "soi": s["payload_soi"], "eoi": s["payload_eoi"]})
    secondi = round(pacchetti[-1][0], 3) if pacchetti else 0.0
    return {"qualita": qualita, "secondi": secondi, "pacchetti": righe}


def main() -> int:
    DESTINAZIONE.mkdir(parents=True, exist_ok=True)
    scritti = 0
    for nome, qualita in REGISTRAZIONI.items():
        sorgente = SORGENTE / nome
        if not sorgente.exists():
            print(f"  {nome}: assente")
            continue
        pacchetti = leggi_pacchetti(sorgente)
        if not pacchetti:
            print(f"  {nome}: vuoto, nessuna fixture")
            continue
        dati = struttura(pacchetti, qualita)
        (DESTINAZIONE / f"{qualita}.json").write_text(
            json.dumps(dati, indent=1) + "\n")
        print(f"  {qualita}.json: {len(dati['pacchetti'])} pacchetti")
        scritti += 1
    return 0 if scritti else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Genera le fixture e copia le risposte testuali**

```bash
.venv/bin/python tools/struttura_liveview.py
mkdir -p tests/fixtures/remote
cp diag-downloads/telecomando/camprop_takemode.xml diag-downloads/telecomando/camprop_desclist.xml diag-downloads/telecomando/af.txt tests/fixtures/remote/
```

Expected: almeno `0640x0480.json` scritto. Se lo strumento esce con 1 (nessun live registrato), il live non è arrivato: vai allo Step 7 con esito **stop** salvo che la riprova con la regola del firewall sia ancora da fare.

- [ ] **Step 5: Verifica che non entrino immagini né seriali**

```bash
grep -c '"intestazione"' tests/fixtures/liveview/*.json
while read -r s; do [ -n "$s" ] && grep -rlaF "$s" tests/fixtures/liveview tests/fixtures/remote && echo "TROVATO"; done < diag-downloads/seriali.txt; echo fine
LC_ALL=C grep -laP '\xff\xd8\xff' tests/fixtures/liveview/* tests/fixtures/remote/* ; echo "jpeg: fine"
```

Expected: un conteggio per file; il ciclo stampa solo `fine`; nessun file elencato prima di `jpeg: fine`. Le intestazioni sono in esadecimale, quindi un JPEG non può finirci intero; il terzo controllo verifica che nessun file copiato contenga byte JPEG grezzi. Se un seriale compare in una risposta di `get_camprop`, sostituiscilo a parità di lunghezza con `0` e ripeti il controllo.

- [ ] **Step 6: Guarda le immagini, senza copiarle**

Apri `diag-downloads/telecomando/recview.jpg` (e `lastjpg.jpg` se c'è) solo per annotarne dimensioni in pixel e in byte: `file diag-downloads/telecomando/*.jpg && ls -l diag-downloads/telecomando/*.jpg`. Non entrano in git.

- [ ] **Step 7: Annota esito e decisioni**

Nella specifica, sezione «Prova sul campo (pezzo A)», sostituisci il paragrafo che inizia con «**Cancello.**» con l'esito, nella forma:

```markdown
**Esito della prova sul campo (2026-11-DD):** <passa | stop>.
- Live: <N> pacchetti/s e <F> fotogrammi/s a 640×480, <N>/<F> a 1024×768; pacchetti da <min> a <max> byte; origine <porta>; firewall: <nessuna regola necessaria | regola necessaria>.
- Formato: RTP versione <v>, payload type <pt>; inizio fotogramma riconosciuto da <estensione presente | payload che inizia con FF D8>; fine fotogramma da <marcatore RTP | FF D9 finale>; estensione: profilo 0x<pppp>, <L> byte.
- Qualità predefinita del live: <qualità> (la più alta con almeno 15 fotogrammi/s).
- Messa a fuoco: assignafframe risponde <…>; esito <comunicato così | non comunicato>.
- Scatto: starttake/stoptake <esito, tempi>; getrecview <dimensioni>; getlastjpg <esito>.
- Video: startmovietake <con | senza> liveview=on <esito>; live durante la registrazione: <sì | no>.
- Modalità della ghiera: get_camprop takemode risponde <…>.
```

con i valori veri del referto al posto delle parentesi angolari, e la data vera. In coda a `docs/field-report-2026-09-24.md` una sezione «Aggiornamento 2026-11-DD: modalità scatto» con le righe del referto da «== Telecomando» in poi, dopo aver verificato con il ciclo dello Step 5 che non contengano seriali.

Regola dell'esito: **passa** se c'è almeno una qualità con fotogrammi ricomponibili (inizi e fini JPEG presenti) **e** lo scatto ha risposto HTTP 200; altrimenti **stop**: si annota e il telecomando non prosegue.

- [ ] **Step 8: Commit**

```bash
git add tools/struttura_liveview.py tests/fixtures/liveview/ tests/fixtures/remote/ docs/superpowers/specs/2026-11-06-telecomando-design.md docs/field-report-2026-09-24.md
git status --short
git commit -m "test: struttura vera del live e risposte della modalità scatto

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

`git status --short` non deve mostrare file di `diag-downloads/` né altre fixture modificate.

---

## Dopo questo piano

Con l'esito **passa**, si scrive il piano della parte 2 (`docs/superpowers/plans/2026-11-DD-telecomando-2-live-e-scatto.md`) sui dati veri: `core/liveview.py`, i comandi in `protocol.py`, `LiveViewWorker`, `RemoteWorker`, `gui/remote.py`, il modo nella finestra, i test e la documentazione della specifica.
