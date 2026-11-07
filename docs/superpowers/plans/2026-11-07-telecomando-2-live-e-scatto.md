# Telecomando, parte 2: live, scatto e video — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** un terzo modo della finestra, **Telecomando**, che mostra il live della fotocamera, mette a fuoco con un clic, scatta (con revisione di 2 s) e gira video, e all'uscita riporta la fotocamera in riproduzione e rifà l'elenco.

**Architecture:** nel core, `core/liveview.py` ricompone i fotogrammi JPEG dai pacchetti RTP e `protocol.py` riceve i comandi della modalità scatto. Nella GUI, `LiveViewWorker` riceve il live su un thread proprio (vince l'ultimo fotogramma), `RemoteWorker` manda i comandi su un altro thread, `gui/remote.py` contiene la vista del live e il pannello dei comandi, e `MainWindow` coordina entrata e uscita dal modo.

**Tech Stack:** Python 3.12+, PyQt6, `socket`/`threading`, requests; pytest con requests-mock.

**Specifica:** `docs/superpowers/specs/2026-11-06-telecomando-design.md`. I fatti di riferimento sono nel blocco «Esito della prova sul campo (terza esecuzione…)».

## Global Constraints

- I test si lanciano con `.venv/bin/python -m pytest` (non c'è lo script `pytest`); `pyproject.toml` ha già `pythonpath = ["."]`.
- Nessun import di Qt in `olympus_connect/core/`. Nessuna nuova dipendenza.
- `parse_rtp` e `FrameAssembler.feed` non sollevano mai eccezioni. Nessuna eccezione esce da uno slot o dal ciclo di un worker.
- `exec_erase.cgi` non va mai invocato. Il telecomando non cancella nulla dalla scheda.
- Qualità del live: **`1024x0768`** (costante `LIVE_QUALITY`). Si cambia solo con `switch_cammode mode=play` seguito da `mode=rec&lvqty=<qualità>`.
- Live: la fotocamera spedisce da **192.168.0.10:16540**; prima di ogni `startliveview` il ricevitore manda **un datagramma** `b"\x00"` dalla propria porta a quell'indirizzo. **Nessuna regola del firewall** nell'uso normale.
- Fotogrammi: inizio = pacchetto con l'estensione RTP; fine = **solo** il marcatore RTP; `FF D8`/`FF D9` verificati sul fotogramma ricomposto, mai per pacchetto. Un salto di sequenza scarta il fotogramma in corso.
- I comandi `exec_takemotion` (messa a fuoco, scatto, video) si mandano **solo con il live acceso**: altrimenti `errorcode 1004`.
- Durante la registrazione il fotogramma è **640×360**; la vista segue la dimensione reale dell'immagine, mai un 4:3 fisso.
- Testi dell'interfaccia in italiano. Animazioni: `theme.DURATION` (180 ms), `QEasingCurve.Type.OutCubic`, dissolvenza da opacità **0,35**. Cifre tabulari già globali.
- Revisione dopo lo scatto: **2 s**. Uscita alla chiusura dell'app: al massimo **3 s**.
- Nessuna immagine catturata dalla fotocamera e nessun numero di serie in un commit.
- Ogni messaggio di commit termina con la riga `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Mappa dei file

| File | Stato | Responsabilità |
|---|---|---|
| `olympus_connect/core/liveview.py` | nuovo | `RtpPacket`, `parse_rtp`, `FrameAssembler` |
| `tests/live_helpers.py` | nuovo | costruzione di pacchetti di prova (dalle fixture o sintetici) |
| `olympus_connect/core/errors.py` | modifica | `ProtocolError.code` |
| `olympus_connect/core/protocol.py` | modifica | `LIVE_QUALITY`, codice d'errore, comandi della modalità scatto |
| `olympus_connect/gui/workers.py` | modifica | `describe_error` per i codici, `RemoteWorker` |
| `olympus_connect/gui/liveview.py` | nuovo | `LiveViewWorker` |
| `olympus_connect/gui/remote.py` | nuovo | `fit_rect`, `map_to_frame`, `RemoteView`, `RemotePanel` |
| `olympus_connect/gui/icons.py` | modifica | icone `remote`, `shutter`, `record`, `stop` |
| `olympus_connect/gui/panels.py` | modifica | `CollapsiblePanel.set_title` |
| `olympus_connect/gui/main_window.py` | modifica | il modo Telecomando |
| `tests/test_liveview.py`, `tests/test_liveview_worker.py`, `tests/test_remote_view.py`, `tests/test_remote_e2e.py` | nuovi | test |
| `tests/test_protocol.py`, `tests/test_workers.py`, `tests/test_main_window.py` | modifica | test |
| `README.md`, `DESIGN.md`, `docs/field-report-2026-09-24.md` | modifica | documentazione |

---

### Task 1: Ricomposizione dei fotogrammi (`core/liveview.py`)

**Files:**
- Create: `olympus_connect/core/liveview.py`
- Create: `tests/live_helpers.py`
- Test: `tests/test_liveview.py`

**Interfaces:**
- Consumes: le fixture `tests/fixtures/liveview/{0640x0480,1024x0768,1280x0960,scatto,video}.json`, formato `{"qualita": str, "secondi": float, "pacchetti": [{"t": float, "intestazione": hex, "payload_len": int, "soi": bool, "eoi": bool}]}`; `intestazione` contiene l'intestazione RTP vera, estensione compresa.
- Produces:
  - `TLV_AF_FRAME = 2`, `TLV_FRAME_SIZE = 26`, `MAX_FRAME_BYTES = 4 * 1024 * 1024`;
  - `RtpPacket` (dataclass frozen): `marker: bool`, `payload_type: int`, `seq: int`, `timestamp: int`, `payload: bytes`, `tlvs: dict[int, bytes] | None` (None = nessuna estensione); proprietà `starts_frame -> bool`; metodo `frame_size() -> tuple[int, int] | None`;
  - `parse_rtp(packet: bytes) -> RtpPacket | None`;
  - `FrameAssembler(max_bytes: int = MAX_FRAME_BYTES)` con `feed(packet: bytes) -> bytes | None` e attributo `frame_size: tuple[int, int] | None` (dimensione dichiarata dall'ultimo fotogramma restituito);
  - in `tests/live_helpers.py`: `synthetic_jpeg(length) -> bytes`, `padded_jpeg(base, length) -> bytes`, `rtp_header(seq, ts, marker=False, tlvs=None) -> bytes`, `frame_packets(jpeg, seq, ts=0, size=(640, 480), chunk=1400) -> list[bytes]`, `packets_from_fixture(name, jpeg_for=synthetic_jpeg) -> tuple[list[bytes], list[bytes]]`.

- [ ] **Step 1: Scrivi gli helper dei test**

Create `tests/live_helpers.py`:

```python
"""Pacchetti del live per i test: struttura vera, contenuto sintetico.

Le fixture tengono solo le intestazioni RTP vere della fotocamera e la
lunghezza di ogni contenuto: le immagini restano fuori dal repository.
Qui i fotogrammi si ricostruiscono con un JPEG sintetico della stessa
lunghezza complessiva, spezzato esattamente come la fotocamera spezzava
quello vero, così anche un FF D9 diviso fra due pacchetti si riproduce.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "liveview"


def synthetic_jpeg(length: int, fill: int = 0) -> bytes:
    """Inizia con FF D8 e finisce con FF D9: basta al ricompositore, non a un decoder."""
    if length < 4:
        raise ValueError("un JPEG sintetico richiede almeno 4 byte")
    return b"\xff\xd8" + bytes([fill]) * (length - 4) + b"\xff\xd9"


def padded_jpeg(base: bytes, length: int) -> bytes:
    """Un JPEG decodificabile lungo esattamente `length` byte.

    Aggiunge segmenti di commento (FF FE) subito dopo FF D8: i decoder li
    ignorano, quindi l'immagine resta la stessa.
    """
    manca = length - len(base)
    if manca == 0:
        return base
    if manca < 4:
        raise ValueError("servono almeno 4 byte per un segmento di commento")
    segmenti = b""
    while manca > 0:
        corpo = min(manca - 4, 65533)
        if manca - (corpo + 4) in (1, 2, 3):
            corpo -= 4          # il resto deve poter diventare un segmento intero
        segmenti += b"\xff\xfe" + struct.pack(">H", corpo + 2) + b"\x00" * corpo
        manca -= corpo + 4
    return base[:2] + segmenti + base[2:]


def rtp_header(seq: int, ts: int, marker: bool = False,
               tlvs: list[tuple[int, bytes]] | None = None) -> bytes:
    b0 = 0x80 | (0x10 if tlvs is not None else 0)
    b1 = (0x80 if marker else 0) | 96
    head = struct.pack(">BBHII", b0, b1, seq % 65536, ts, 0x1234)
    if tlvs is not None:
        corpo = b"".join(struct.pack(">HH", i, len(v) // 4) + v for i, v in tlvs)
        head += struct.pack(">HH", 1, len(corpo) // 4) + corpo
    return head


def frame_packets(jpeg: bytes, seq: int, ts: int = 0,
                  size: tuple[int, int] = (640, 480), chunk: int = 1400) -> list[bytes]:
    """Un fotogramma come lo spezza la fotocamera: estensione sul primo pacchetto
    (TLV 26 con la dimensione), marcatore sull'ultimo."""
    pezzi = [jpeg[i:i + chunk] for i in range(0, len(jpeg), chunk)]
    out = []
    for n, pezzo in enumerate(pezzi):
        tlvs = [(26, struct.pack(">II", *size) + b"\x00" * 16)] if n == 0 else None
        out.append(rtp_header(seq + n, ts, marker=n == len(pezzi) - 1, tlvs=tlvs) + pezzo)
    return out


def packets_from_fixture(name: str, jpeg_for=synthetic_jpeg) -> tuple[list[bytes], list[bytes]]:
    """(pacchetti, fotogrammi attesi) ricostruiti da una fixture.

    Un fotogramma va dal pacchetto con l'estensione al pacchetto con il
    marcatore. I pacchetti fuori da un fotogramma (inizio della cattura a
    metà fotogramma, coda troncata) ricevono un contenuto di zeri.
    """
    righe = json.loads((FIXTURES / f"{name}.json").read_text())["pacchetti"]
    teste = [bytes.fromhex(r["intestazione"]) for r in righe]
    pacchetti: list[bytes] = [t + b"\x00" * r["payload_len"] for t, r in zip(teste, righe)]
    attesi: list[bytes] = []

    def inizia(k):
        return (teste[k][0] >> 4) & 1

    i = 0
    while i < len(righe):
        if not inizia(i):
            i += 1
            continue
        j = i
        # Il fotogramma finisce al marcatore; ma se prima del marcatore
        # comincia un fotogramma nuovo, quello in corso è stato
        # abbandonato dalla fotocamera (succede a 1024 e 1280).
        while j < len(righe) and not teste[j][1] >> 7 and (j == i or not inizia(j)):
            j += 1
        if j == len(righe):
            break                                   # fotogramma troncato a fine cattura
        if j > i and inizia(j):
            i = j                                   # abbandonato: resta a zeri
            continue
        totale = sum(righe[k]["payload_len"] for k in range(i, j + 1))
        jpeg = jpeg_for(totale)
        pos = 0
        for k in range(i, j + 1):
            n = righe[k]["payload_len"]
            pacchetti[k] = teste[k] + jpeg[pos:pos + n]
            pos += n
        attesi.append(jpeg)
        i = j + 1
    return pacchetti, attesi
```

- [ ] **Step 2: Scrivi i test che falliscono**

Create `tests/test_liveview.py`:

```python
"""Ricomposizione dei fotogrammi del live sui pacchetti veri della fotocamera."""
import json
import struct

import pytest

from tests.live_helpers import (
    FIXTURES, frame_packets, packets_from_fixture, rtp_header, synthetic_jpeg,
)
from olympus_connect.core.liveview import FrameAssembler, parse_rtp


def _prima_intestazione(nome):
    righe = json.loads((FIXTURES / f"{nome}.json").read_text())["pacchetti"]
    return next(bytes.fromhex(r["intestazione"]) for r in righe
                if (bytes.fromhex(r["intestazione"])[0] >> 4) & 1)


@pytest.mark.parametrize("nome,dimensione", [
    ("0640x0480", (640, 480)), ("1024x0768", (1024, 768)), ("1280x0960", (1280, 960)),
])
def test_reads_the_frame_size_from_the_real_extension(nome, dimensione):
    p = parse_rtp(_prima_intestazione(nome) + b"\xff\xd8")
    assert p is not None and p.starts_frame
    assert p.frame_size() == dimensione
    assert 2 in p.tlvs and 26 in p.tlvs
    assert p.payload == b"\xff\xd8" and p.payload_type == 96


def test_a_packet_without_extension_does_not_start_a_frame():
    p = parse_rtp(rtp_header(7, 3000, marker=True) + b"abc")
    assert (p.seq, p.timestamp, p.marker, p.starts_frame) == (7, 3000, True, False)
    assert p.payload == b"abc" and p.frame_size() is None


@pytest.mark.parametrize("dati", [
    b"", b"\x80" * 11,
    bytes([0x40]) + rtp_header(1, 0)[1:],                    # versione 1
    rtp_header(1, 0, tlvs=[(26, b"\x00" * 8)])[:-4],          # estensione che sfora
])
def test_malformed_packets_are_none(dati):
    assert parse_rtp(dati) is None


def test_padding_is_removed():
    testa = bytearray(rtp_header(1, 0))
    testa[0] |= 0x20
    assert parse_rtp(bytes(testa) + b"dati\x00\x00\x03").payload == b"dati"


def test_no_truncation_of_a_real_header_raises():
    pacchetto = _prima_intestazione("video") + b"\xff\xd8xyz"
    for n in range(len(pacchetto) + 1):
        parse_rtp(pacchetto[:n])            # non deve sollevare


@pytest.mark.parametrize("nome,fotogrammi", [
    ("0640x0480", 96), ("1024x0768", 95), ("1280x0960", 46),
    ("scatto", 76), ("video", 231),
])
def test_rebuilds_every_frame_of_the_real_captures(nome, fotogrammi):
    pacchetti, attesi = packets_from_fixture(nome)
    assembler = FrameAssembler()
    ricomposti = [f for f in (assembler.feed(p) for p in pacchetti) if f is not None]
    assert len(attesi) == fotogrammi
    assert ricomposti == attesi


def test_follows_the_frame_size_into_and_out_of_recording():
    pacchetti, _ = packets_from_fixture("video")
    assembler = FrameAssembler()
    viste = set()
    for p in pacchetti:
        if assembler.feed(p) is not None:
            viste.add(assembler.frame_size)
    assert {(640, 480), (640, 360)} <= viste


def test_an_end_marker_split_across_packets_still_closes_the_frame():
    jpeg = synthetic_jpeg(3000)
    pacchetti = frame_packets(jpeg[:-1], seq=10) + [rtp_header(13, 0, marker=True) + b"\xd9"]
    # l'ultimo pacchetto di frame_packets porta il marcatore: lo si toglie
    penultimo = bytearray(pacchetti[-2])
    penultimo[1] &= 0x7F
    pacchetti[-2] = bytes(penultimo)
    assembler = FrameAssembler()
    assert [assembler.feed(p) for p in pacchetti][-1] == jpeg


def _due_fotogrammi():
    a, b = synthetic_jpeg(4000, 1), synthetic_jpeg(4000, 2)
    return a, b, frame_packets(a, seq=100) + frame_packets(b, seq=103)


def test_a_lost_packet_drops_only_its_frame():
    a, b, pacchetti = _due_fotogrammi()
    del pacchetti[1]
    assembler = FrameAssembler()
    assert [f for f in map(assembler.feed, pacchetti) if f] == [b]


def test_a_duplicate_or_reordered_packet_drops_the_frame():
    a, b, pacchetti = _due_fotogrammi()
    duplicati = pacchetti[:2] + [pacchetti[1]] + pacchetti[2:]
    assembler = FrameAssembler()
    assert [f for f in map(assembler.feed, duplicati) if f] == [b]
    scambiati = [pacchetti[0], pacchetti[2], pacchetti[1]] + pacchetti[3:]
    assembler = FrameAssembler()
    assert [f for f in map(assembler.feed, scambiati) if f] == [b]


def test_an_oversized_frame_is_dropped():
    assembler = FrameAssembler(max_bytes=2000)
    assert [assembler.feed(p) for p in frame_packets(synthetic_jpeg(4000), seq=1)] == [None] * 3


def test_garbage_in_the_middle_drops_the_frame_and_never_raises():
    a, b, pacchetti = _due_fotogrammi()
    pacchetti.insert(1, b"\x01\x02")
    assembler = FrameAssembler()
    assert [f for f in map(assembler.feed, pacchetti) if f] == [b]
    for spazzatura in (b"", b"\x00" * 3, bytes(range(256)), b"\x80" * 2000):
        assembler.feed(spazzatura)


def test_a_frame_without_jpeg_markers_is_not_returned():
    assembler = FrameAssembler()
    assert [assembler.feed(p) for p in frame_packets(b"x" * 3000, seq=1)][-1] is None


def test_stale_backlog_then_a_jump_in_sequence_is_handled():
    vecchio, nuovo = synthetic_jpeg(3000, 1), synthetic_jpeg(3000, 2)
    pacchetti = frame_packets(vecchio, seq=10)[:2] + frame_packets(nuovo, seq=2000)
    assembler = FrameAssembler()
    assert [f for f in map(assembler.feed, pacchetti) if f] == [nuovo]
```

- [ ] **Step 3: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_liveview.py -q`
Expected: errore di raccolta, `ModuleNotFoundError: No module named 'olympus_connect.core.liveview'`.

- [ ] **Step 4: Scrivi il modulo**

Create `olympus_connect/core/liveview.py`:

```python
"""Visione dal vivo: dai pacchetti RTP della fotocamera ai fotogrammi JPEG.

Formato misurato sulla E-M10 Mark IV (specifica 2026-11-06-telecomando,
esito della prova sul campo): RTP versione 2, payload type 96. Il primo
pacchetto di ogni fotogramma porta un'estensione (profilo 0x0001) fatta di
TLV, e il suo contenuto comincia con FF D8; l'ultimo porta il marcatore RTP.
FF D9 si verifica sul fotogramma ricomposto, mai sul singolo pacchetto: la
fotocamera può spezzarlo fra due pacchetti.

Nessun import di Qt, e niente qui solleva eccezioni: i chiamanti sono un
ciclo di ricezione su un thread e slot Qt in coda.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

TLV_AF_FRAME = 2
TLV_FRAME_SIZE = 26
MAX_FRAME_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class RtpPacket:
    marker: bool
    payload_type: int
    seq: int
    timestamp: int
    payload: bytes
    tlvs: dict[int, bytes] | None       # None: il pacchetto non ha estensione

    @property
    def starts_frame(self) -> bool:
        return self.tlvs is not None

    def frame_size(self) -> tuple[int, int] | None:
        valore = (self.tlvs or {}).get(TLV_FRAME_SIZE)
        if valore is None or len(valore) < 8:
            return None
        larghezza, altezza = struct.unpack(">II", valore[:8])
        return larghezza, altezza


def _tlvs(dati: bytes) -> dict[int, bytes] | None:
    """Id (2 byte), lunghezza in parole da 32 bit (2 byte), valore."""
    out: dict[int, bytes] = {}
    pos = 0
    while pos < len(dati):
        if pos + 4 > len(dati):
            return None
        ident, parole = struct.unpack(">HH", dati[pos:pos + 4])
        fine = pos + 4 + 4 * parole
        if fine > len(dati):
            return None
        out[ident] = dati[pos + 4:fine]
        pos = fine
    return out


def parse_rtp(packet: bytes) -> RtpPacket | None:
    try:
        if len(packet) < 12:
            return None
        b0, b1 = packet[0], packet[1]
        if b0 >> 6 != 2:
            return None
        seq, timestamp = struct.unpack(">HI", packet[2:8])
        pos = 12 + 4 * (b0 & 0x0F)
        tlvs = None
        if (b0 >> 4) & 1:
            if pos + 4 > len(packet):
                return None
            _profilo, parole = struct.unpack(">HH", packet[pos:pos + 4])
            fine = pos + 4 + 4 * parole
            if fine > len(packet):
                return None
            tlvs = _tlvs(packet[pos + 4:fine])
            if tlvs is None:
                return None
            pos = fine
        if pos > len(packet):
            return None
        payload = packet[pos:]
        if b0 & 0x20 and payload:
            riempimento = payload[-1]
            if riempimento > len(payload):
                return None
            payload = payload[:-riempimento]
        return RtpPacket(bool(b1 >> 7), b1 & 0x7F, seq, timestamp, bytes(payload), tlvs)
    except Exception:           # la garanzia: nessuna eccezione esce da qui
        return None


class FrameAssembler:
    """Ricompone i fotogrammi; scarta quelli che non arrivano interi.

    Un salto di sequenza, un duplicato o un pacchetto fuori ordine a metà
    fotogramma lo scartano: meglio un fotogramma in meno che un'immagine
    rotta. L'arretrato di una sessione precedente si presenta come un
    salto e si scarta allo stesso modo.
    """

    def __init__(self, max_bytes: int = MAX_FRAME_BYTES) -> None:
        self._max = max_bytes
        self._parts: list[bytes] | None = None
        self._size = 0
        self._last_seq: int | None = None
        self._pending_size: tuple[int, int] | None = None
        self.frame_size: tuple[int, int] | None = None

    def feed(self, packet: bytes) -> bytes | None:
        try:
            return self._feed(packet)
        except Exception:
            self._parts = None
            return None

    def _feed(self, packet: bytes) -> bytes | None:
        p = parse_rtp(packet)
        if p is None:
            self._parts = None           # spazzatura: il fotogramma in corso non è affidabile
            return None
        continuo = self._last_seq is not None and p.seq == (self._last_seq + 1) % 65536
        self._last_seq = p.seq

        if p.starts_frame:
            self._parts = [p.payload]
            self._size = len(p.payload)
            self._pending_size = p.frame_size()
        elif self._parts is None or not continuo:
            self._parts = None
            return None
        else:
            self._parts.append(p.payload)
            self._size += len(p.payload)

        if self._size > self._max:
            self._parts = None
            return None
        if not p.marker:
            return None

        jpeg = b"".join(self._parts)
        self._parts = None
        if jpeg[:2] != b"\xff\xd8" or jpeg[-2:] != b"\xff\xd9":
            return None
        self.frame_size = self._pending_size
        return jpeg
```

- [ ] **Step 5: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_liveview.py -q && .venv/bin/python -m pytest -q`
Expected: tutti `passed`; suite completa verde.

- [ ] **Step 6: Commit**

```bash
git add olympus_connect/core/liveview.py tests/live_helpers.py tests/test_liveview.py
git commit -m "feat(core): ricomposizione dei fotogrammi del live dai pacchetti RTP

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Comandi della modalità scatto nel client

**Files:**
- Modify: `olympus_connect/core/errors.py` (`ProtocolError`)
- Modify: `olympus_connect/core/protocol.py` (import `re`, costante, `_get_ok`, metodi nuovi dopo `exif`)
- Modify: `olympus_connect/gui/workers.py` (`describe_error`)
- Test: `tests/test_protocol.py`, `tests/test_workers.py`

**Interfaces:**
- Consumes: `OlympusClient._get`, `_get_ok`, `_xml`, `self._mode`, `ensure_play_mode()`; la fixture vera `tests/fixtures/remote/camprop_desclist.xml` (contiene `touchactiveframe` con valore `0096-0072_0448x0336`) e `tests/fixtures/remote/af.txt`.
- Produces:
  - `ProtocolError(message, raw=b"", code: int | None = None)` con attributo `code`;
  - in `protocol.py`: `LIVE_QUALITY = "1024x0768"`; metodi `enter_rec_mode(quality: str = LIVE_QUALITY) -> None`, `start_liveview(port: int) -> None`, `stop_liveview() -> None`, `take_mode() -> str`, `touch_area() -> tuple[int, int, int, int] | None`, `assign_af(x: int, y: int) -> bool | None`, `release_af() -> None`, `take_picture() -> None`, `start_movie() -> None`, `stop_movie() -> None`, `rec_view() -> bytes`;
  - `describe_error` traduce `code == 1004` e ogni altro `code`.

- [ ] **Step 1: Scrivi i test che falliscono**

In coda a `tests/test_protocol.py`:

```python
# --- modalità scatto ---------------------------------------------------------

from pathlib import Path  # noqa: E402

from olympus_connect.core.protocol import LIVE_QUALITY  # noqa: E402

REMOTE = Path(__file__).parent / "fixtures" / "remote"


def test_error_code_is_read_from_the_camera_response(requests_mock):
    requests_mock.get(f"{BASE_URL}/exec_takemotion.cgi", status_code=520,
                      content=b'<?xml version="1.0"?><errorcode>1004</errorcode>')
    with pytest.raises(ProtocolError) as info:
        OlympusClient().take_picture()
    assert info.value.code == 1004


def test_an_error_without_code_keeps_code_none(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_thumbnail.cgi", status_code=404)
    with pytest.raises(ProtocolError) as info:
        OlympusClient().thumbnail("/DCIM/100OLYMP/A.JPG")
    assert info.value.code is None


def test_enter_rec_mode_goes_through_play_to_change_quality(requests_mock):
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")
    client = OlympusClient()
    client.enter_rec_mode()
    assert [r.qs for r in requests_mock.request_history] == [
        {"mode": ["play"]}, {"mode": ["rec"], "lvqty": [LIVE_QUALITY]}]


def test_after_rec_mode_play_mode_is_requested_again(requests_mock):
    requests_mock.get(f"{BASE_URL}/switch_cammode.cgi", content=b"")
    client = OlympusClient()
    client.ensure_play_mode()
    client.enter_rec_mode()
    client.ensure_play_mode()
    assert requests_mock.request_history[-1].qs == {"mode": ["play"]}


def test_live_view_start_and_stop(requests_mock):
    requests_mock.get(f"{BASE_URL}/exec_takemisc.cgi", content=b"<funcidlist/>")
    client = OlympusClient()
    client.start_liveview(40123)
    client.stop_liveview()
    assert [r.qs for r in requests_mock.request_history] == [
        {"com": ["startliveview"], "port": ["40123"]}, {"com": ["stopliveview"]}]


def test_take_mode_reads_the_dial(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_camprop.cgi",
                      content=(REMOTE / "camprop_takemode.xml").read_bytes())
    assert OlympusClient().take_mode() == "S"


def test_touch_area_comes_from_the_real_desclist(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_camprop.cgi",
                      content=(REMOTE / "camprop_desclist.xml").read_bytes())
    assert OlympusClient().touch_area() == (96, 72, 448, 336)
    assert requests_mock.last_request.qs == {"com": ["desc"], "propname": ["desclist"]}


def test_touch_area_missing_is_none(requests_mock):
    requests_mock.get(f"{BASE_URL}/get_camprop.cgi",
                      content=b'<?xml version="1.0"?><desclist></desclist>')
    assert OlympusClient().touch_area() is None


@pytest.mark.parametrize("corpo,esito", [
    ((REMOTE / "af.txt").read_bytes(), True),
    (b"<response><affocus>ng</affocus></response>", False),
    (b"", None),
])
def test_assign_af_reports_the_focus_result(requests_mock, corpo, esito):
    requests_mock.get(f"{BASE_URL}/exec_takemotion.cgi", content=corpo)
    assert OlympusClient().assign_af(320, 240) is esito
    assert requests_mock.last_request.qs == {"com": ["assignafframe"], "point": ["0320x0240"]}


def test_take_picture_sends_start_then_stop(requests_mock):
    requests_mock.get(f"{BASE_URL}/exec_takemotion.cgi",
                      content=b"<response><take>ok</take></response>")
    OlympusClient().take_picture()
    assert [r.qs["com"] for r in requests_mock.request_history] == [["starttake"], ["stoptake"]]


def test_a_refused_take_still_sends_stop_and_raises(requests_mock):
    requests_mock.get(f"{BASE_URL}/exec_takemotion.cgi",
                      content=b"<response><take>ng</take></response>")
    with pytest.raises(ProtocolError):
        OlympusClient().take_picture()
    assert [r.qs["com"] for r in requests_mock.request_history] == [["starttake"], ["stoptake"]]


def test_movie_start_keeps_the_live_on(requests_mock):
    requests_mock.get(f"{BASE_URL}/exec_takemotion.cgi", content=b"")
    client = OlympusClient()
    client.start_movie()
    client.stop_movie()
    assert [r.qs for r in requests_mock.request_history] == [
        {"com": ["startmovietake"], "liveview": ["on"]}, {"com": ["stopmovietake"]}]


def test_rec_view_returns_a_jpeg_or_raises(requests_mock):
    requests_mock.get(f"{BASE_URL}/exec_takemisc.cgi", content=b"\xff\xd8revisione")
    assert OlympusClient().rec_view() == b"\xff\xd8revisione"
    requests_mock.get(f"{BASE_URL}/exec_takemisc.cgi", content=b"<x/>")
    with pytest.raises(ProtocolError):
        OlympusClient().rec_view()
```

In coda a `tests/test_workers.py`:

```python
# --- codici d'errore della fotocamera ------------------------------------------

def test_describe_error_explains_code_1004():
    testo = describe_error(ProtocolError("x", code=1004))
    assert "live" in testo and "riprova" in testo.lower()


def test_describe_error_shows_other_codes():
    assert "1005" in describe_error(ProtocolError("x", code=1005))
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_protocol.py tests/test_workers.py -q`
Expected: errore di raccolta, `ImportError: cannot import name 'LIVE_QUALITY'`.

- [ ] **Step 3: `ProtocolError.code`**

In `olympus_connect/core/errors.py` sostituisci la classe `ProtocolError` con:

```python
class ProtocolError(OlympusError):
    """Risposta inattesa dalla fotocamera.

    `code` è l'`errorcode` che la fotocamera mette nel corpo di una
    risposta HTTP 520, quando c'è: 1004, per esempio, è un comando di
    scatto inviato con il live spento.
    """

    def __init__(self, message: str, raw: bytes = b"", code: int | None = None) -> None:
        super().__init__(message)
        self.raw = raw
        self.code = code
```

- [ ] **Step 4: Codice d'errore e comandi nel client**

In `olympus_connect/core/protocol.py`:

1. Agli import della libreria standard aggiungi `import re`.
2. Dopo `TIMEOUT = (3, 15)`:

```python
# Misurato sul campo: 30 fotogrammi al secondo a 640×480 e a 1024×768,
# circa 15 a 1280×960. La più alta che resta fluida.
LIVE_QUALITY = "1024x0768"

_ERRORCODE = re.compile(rb"<errorcode>\s*(\d+)\s*</errorcode>")
```

3. Sostituisci il corpo di `_get_ok` con:

```python
    def _get_ok(self, path: str, **params) -> requests.Response:
        response = self._get(path, **params)
        if response.status_code != 200:
            trovato = _ERRORCODE.search(response.content or b"")
            codice = int(trovato.group(1)) if trovato else None
            dettaglio = f", codice {codice}." if codice is not None else "."
            raise ProtocolError(
                f"{path} ha risposto HTTP {response.status_code}{dettaglio}",
                response.content, code=codice,
            )
        return response
```

4. Dopo il metodo `exif`:

```python
    # -- modalità scatto (telecomando) --------------------------------------

    def enter_rec_mode(self, quality: str = LIVE_QUALITY) -> None:
        """Modalità scatto con la qualità del live richiesta.

        Verificato sul campo: un mode=rec mandato quando la fotocamera è già
        in rec ignora lvqty. Per cambiare qualità si passa dalla riproduzione.
        """
        self._get_ok("switch_cammode.cgi", mode="play")
        self._get_ok("switch_cammode.cgi", mode="rec", lvqty=quality)
        self._mode = "rec"

    def start_liveview(self, port: int) -> None:
        self._get_ok("exec_takemisc.cgi", com="startliveview", port=port)

    def stop_liveview(self) -> None:
        self._get_ok("exec_takemisc.cgi", com="stopliveview")

    def take_mode(self) -> str:
        """La modalità della ghiera: P, A, S, M, iAuto…"""
        root = self._xml(self._get_ok("get_camprop.cgi", com="get", propname="takemode"))
        return (root.findtext("value") or "").strip()

    def touch_area(self) -> tuple[int, int, int, int] | None:
        """L'area del live in cui la messa a fuoco col tocco è accettata:
        (x, y, larghezza, altezza). La fotocamera la descrive come
        «0096-0072_0448x0336» nella voce touchactiveframe di desclist."""
        root = self._xml(self._get_ok("get_camprop.cgi", com="desc", propname="desclist"))
        for desc in root.iter("desc"):
            if (desc.findtext("propname") or "").strip() != "touchactiveframe":
                continue
            trovato = re.fullmatch(r"\s*(\d+)-(\d+)_(\d+)x(\d+)\s*", desc.findtext("value") or "")
            if trovato:
                x, y, w, h = (int(g) for g in trovato.groups())
                return x, y, w, h
        return None

    def assign_af(self, x: int, y: int) -> bool | None:
        """Mette a fuoco nel punto (x, y) del live. Solo con il live acceso.

        True a fuoco, False messa a fuoco fallita, None se la risposta non
        dice l'esito."""
        response = self._get_ok("exec_takemotion.cgi", com="assignafframe",
                                point=f"{x:04d}x{y:04d}")
        return _focus_result(response.content)

    def release_af(self) -> None:
        self._get_ok("exec_takemotion.cgi", com="releaseafframe")

    def take_picture(self) -> None:
        """Scatta. Solo con il live acceso. stoptake segue sempre starttake,
        anche quando la fotocamera dice che lo scatto non è riuscito."""
        response = self._get_ok("exec_takemotion.cgi", com="starttake")
        self._get_ok("exec_takemotion.cgi", com="stoptake")
        esito = _xml_text(response.content, "take")
        if esito is not None and esito != "ok":
            raise ProtocolError("La fotocamera non ha scattato.", response.content)

    def start_movie(self) -> None:
        """Inizia un video lasciando acceso il live (che passa a 16:9)."""
        self._get_ok("exec_takemotion.cgi", com="startmovietake", liveview="on")

    def stop_movie(self) -> None:
        self._get_ok("exec_takemotion.cgi", com="stopmovietake")

    def rec_view(self) -> bytes:
        """L'immagine di revisione dell'ultimo scatto: circa 40-50 KB in 45 ms
        con il live acceso. Con il live spento la fotocamera non risponde."""
        response = self._get_ok("exec_takemisc.cgi", com="getrecview")
        if response.content[:2] != b"\xff\xd8":
            raise ProtocolError("Immagine di revisione non valida.", response.content)
        return response.content
```

5. In fondo al modulo, fuori dalla classe:

```python
def _xml_text(body: bytes, tag: str) -> str | None:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None
    nodo = root if root.tag == tag else root.find(f".//{tag}")
    return nodo.text.strip() if nodo is not None and nodo.text else None


def _focus_result(body: bytes) -> bool | None:
    esito = _xml_text(body, "affocus")
    return None if esito is None else esito == "ok"
```

- [ ] **Step 5: `describe_error` per i codici della fotocamera**

In `olympus_connect/gui/workers.py`, dentro `describe_error`, subito prima della riga `if isinstance(exc, ProtocolError):`:

```python
    if isinstance(exc, ProtocolError) and exc.code == 1004:
        return ("La fotocamera non accetta il comando in questo momento: "
                "funziona solo con il live attivo. Riprova.")
    if isinstance(exc, ProtocolError) and exc.code is not None:
        return f"La fotocamera ha rifiutato il comando (codice {exc.code})."
```

- [ ] **Step 6: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_protocol.py tests/test_workers.py -q && .venv/bin/python -m pytest -q`
Expected: tutti `passed`.

- [ ] **Step 7: Commit**

```bash
git add olympus_connect/core/errors.py olympus_connect/core/protocol.py olympus_connect/gui/workers.py tests/test_protocol.py tests/test_workers.py
git commit -m "feat(core): comandi della modalità scatto e codici d'errore della fotocamera

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `LiveViewWorker`

**Files:**
- Create: `olympus_connect/gui/liveview.py`
- Test: `tests/test_liveview_worker.py`

**Interfaces:**
- Consumes: `FrameAssembler` (Task 1); in test, `frame_packets`, `padded_jpeg` da `tests/live_helpers.py`.
- Produces: `CAMERA_LIVE = ("192.168.0.10", 16540)`, `RECV_BUFFER = 4 * 1024 * 1024`; classe `LiveViewWorker(QObject)`:
  - `__init__(camera=CAMERA_LIVE, bind_host="", stall_after=2.0, no_signal_after=3.0, clock=time.monotonic)`;
  - segnali `listening(int)`, `frame_ready()`, `stalled()`, `no_signal()`, `resumed()`;
  - slot `run()` (ciclo bloccante, da avviare con `thread.started`); metodi thread-safe `arm()`, `disarm()`, `take_frame() -> QImage | None`, `stop()`.

- [ ] **Step 1: Scrivi i test che falliscono**

Create `tests/test_liveview_worker.py`:

```python
"""Il ricevitore del live, con un finto mittente UDP su 127.0.0.1."""
import socket
import time

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QThread
from PyQt6.QtGui import QImage

from tests.live_helpers import frame_packets, padded_jpeg
from olympus_connect.gui.liveview import LiveViewWorker


def _jpeg(larghezza, altezza=30) -> bytes:
    immagine = QImage(larghezza, altezza, QImage.Format.Format_RGB32)
    immagine.fill(0xFF336699)
    dati = QByteArray()
    buffer = QBuffer(dati)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    immagine.save(buffer, "JPEG")
    return bytes(dati)


def _attendi(qapp, condizione, secondi=3.0):
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        qapp.processEvents()
        if condizione():
            return True
        time.sleep(0.01)
    return condizione()


@pytest.fixture
def live(qapp):
    """Un worker su un thread, e il finto mittente che fa da fotocamera."""
    fotocamera = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fotocamera.bind(("127.0.0.1", 0))
    fotocamera.settimeout(1.0)
    creati = []

    def make(**kw):
        worker = LiveViewWorker(camera=fotocamera.getsockname(), bind_host="127.0.0.1", **kw)
        eventi = {"porta": [], "fotogrammi": 0, "stalled": 0, "no_signal": 0, "resumed": 0}
        worker.listening.connect(eventi["porta"].append)
        worker.frame_ready.connect(lambda: eventi.__setitem__("fotogrammi", eventi["fotogrammi"] + 1))
        worker.stalled.connect(lambda: eventi.__setitem__("stalled", eventi["stalled"] + 1))
        worker.no_signal.connect(lambda: eventi.__setitem__("no_signal", eventi["no_signal"] + 1))
        worker.resumed.connect(lambda: eventi.__setitem__("resumed", eventi["resumed"] + 1))
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        thread.start()
        creati.append((worker, thread))
        assert _attendi(qapp, lambda: eventi["porta"]), "il worker non è in ascolto"
        return worker, eventi, ("127.0.0.1", eventi["porta"][0])

    yield make, fotocamera
    for worker, thread in creati:
        worker.stop()
        thread.quit()
        assert thread.wait(3000), "il ciclo di ricezione non si è fermato"
    fotocamera.close()


def _manda(fotocamera, destinazione, jpeg, seq):
    for p in frame_packets(jpeg, seq=seq):
        fotocamera.sendto(p, destinazione)
    return seq + len(frame_packets(jpeg, seq=seq))


def test_arm_opens_the_path_with_one_datagram_to_the_camera(qapp, live):
    make, fotocamera = live
    worker, _eventi, porta = make()
    worker.arm()
    dati, origine = fotocamera.recvfrom(100)
    assert dati == b"\x00" and origine == porta


def test_delivers_frames_and_keeps_only_the_latest(qapp, live):
    make, fotocamera = live
    worker, eventi, destinazione = make()
    seq = 1
    for larghezza in (40, 50, 60):
        seq = _manda(fotocamera, destinazione, _jpeg(larghezza), seq)
    assert _attendi(qapp, lambda: eventi["fotogrammi"] >= 1)
    time.sleep(0.2)
    qapp.processEvents()
    assert eventi["fotogrammi"] == 1, "senza take_frame non deve accumulare notifiche"
    assert worker.take_frame().width() == 60
    assert worker.take_frame() is None
    _manda(fotocamera, destinazione, _jpeg(70), seq)
    assert _attendi(qapp, lambda: eventi["fotogrammi"] == 2)
    assert worker.take_frame().width() == 70


def test_a_frame_rebuilt_from_padding_decodes(qapp, live):
    make, fotocamera = live
    worker, eventi, destinazione = make()
    _manda(fotocamera, destinazione, padded_jpeg(_jpeg(48), 30000), 1)
    assert _attendi(qapp, lambda: eventi["fotogrammi"] == 1)
    assert worker.take_frame().width() == 48


def test_no_signal_when_nothing_arrives_after_arming(qapp, live):
    make, _fotocamera = live
    worker, eventi, _ = make(no_signal_after=0.3)
    worker.arm()
    assert _attendi(qapp, lambda: eventi["no_signal"] == 1, 2.0)


def test_no_sentinel_before_arming(qapp, live):
    make, _fotocamera = live
    _worker, eventi, _ = make(no_signal_after=0.2, stall_after=0.2)
    time.sleep(0.6)
    qapp.processEvents()
    assert eventi["no_signal"] == 0 and eventi["stalled"] == 0


def test_stalled_then_resumed(qapp, live):
    make, fotocamera = live
    worker, eventi, destinazione = make(stall_after=0.3)
    worker.arm()
    seq = _manda(fotocamera, destinazione, _jpeg(40), 1)
    assert _attendi(qapp, lambda: eventi["fotogrammi"] == 1)
    worker.take_frame()
    assert _attendi(qapp, lambda: eventi["stalled"] == 1, 2.0)
    _manda(fotocamera, destinazione, _jpeg(40), seq)
    assert _attendi(qapp, lambda: eventi["resumed"] == 1)


def test_disarm_silences_the_sentinel(qapp, live):
    make, _fotocamera = live
    worker, eventi, _ = make(no_signal_after=0.2)
    worker.arm()
    worker.disarm()
    time.sleep(0.5)
    qapp.processEvents()
    assert eventi["no_signal"] == 0


def test_garbage_produces_no_frame(qapp, live):
    make, fotocamera = live
    _worker, eventi, destinazione = make()
    for dati in (b"", b"\x00" * 5, b"\x80" * 1500):
        fotocamera.sendto(dati, destinazione)
    time.sleep(0.3)
    qapp.processEvents()
    assert eventi["fotogrammi"] == 0
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_liveview_worker.py -q`
Expected: errore di raccolta, `ModuleNotFoundError: No module named 'olympus_connect.gui.liveview'`.

- [ ] **Step 3: Scrivi il worker**

Create `olympus_connect/gui/liveview.py`:

```python
"""Ricezione del live su un thread proprio.

Il ciclo di ricezione è bloccante e vive per tutta la sessione del
telecomando, quindi non riceve slot in coda: la finestra parla con il
worker solo attraverso metodi thread-safe (arm, disarm, take_frame, stop)
e i suoi segnali.

Vince l'ultimo fotogramma: il worker ne tiene uno solo in attesa e lo
notifica una volta; se la finestra è indietro, i fotogrammi più vecchi si
buttano invece di accumularsi in coda. Il ritardo non cresce mai.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage

from ..core.liveview import FrameAssembler

# La fotocamera spedisce il live sempre da qui (prova sul campo). Un
# datagramma mandato a questo indirizzo dalla porta su cui si ascolta fa
# accettare al firewall i pacchetti di risposta: nessuna regola necessaria.
CAMERA_LIVE = ("192.168.0.10", 16540)
RECV_BUFFER = 4 * 1024 * 1024      # a 650 pacchetti/s il buffer predefinito trabocca


class LiveViewWorker(QObject):
    listening = pyqtSignal(int)
    frame_ready = pyqtSignal()
    stalled = pyqtSignal()
    no_signal = pyqtSignal()
    resumed = pyqtSignal()

    def __init__(self, camera: tuple[str, int] = CAMERA_LIVE, bind_host: str = "",
                 stall_after: float = 2.0, no_signal_after: float = 3.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._camera = camera
        self._bind_host = bind_host
        self._stall_after = stall_after
        self._no_signal_after = no_signal_after
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: QImage | None = None
        self._pending = False
        self._sock: socket.socket | None = None
        self._armed_at: float | None = None

    # -- thread-safe, chiamabili dal thread della finestra --------------------

    def arm(self) -> None:
        """Da chiamare prima di ogni startliveview: apre il passaggio nel
        firewall e fa ripartire la sentinella."""
        self._armed_at = self._clock()
        sock = self._sock
        if sock is not None:
            try:
                sock.sendto(b"\x00", self._camera)
            except OSError:
                pass

    def disarm(self) -> None:
        """Il live si ferma di proposito: niente avvisi di live interrotto."""
        self._armed_at = None

    def take_frame(self) -> QImage | None:
        with self._lock:
            immagine, self._latest = self._latest, None
            self._pending = False
        return immagine

    def stop(self) -> None:
        self._stop.set()

    # -- ciclo di ricezione ---------------------------------------------------

    @pyqtSlot()
    def run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_BUFFER)
            except OSError:
                pass
            sock.bind((self._bind_host, 0))
            sock.settimeout(0.25)
        except OSError:
            self.no_signal.emit()
            return
        self._sock = sock
        self.listening.emit(sock.getsockname()[1])
        try:
            self._loop(sock)
        except Exception:
            pass                # nessuna eccezione esce dal ciclo
        finally:
            self._sock = None
            sock.close()

    def _loop(self, sock: socket.socket) -> None:
        assembler = FrameAssembler()
        ultimo: float | None = None
        fermo = muto = False
        while not self._stop.is_set():
            try:
                dati, _origine = sock.recvfrom(65535)
            except socket.timeout:
                dati = None
            except OSError:
                return
            adesso = self._clock()
            if dati is not None:
                jpeg = assembler.feed(dati)
                if jpeg is not None:
                    immagine = QImage.fromData(jpeg)
                    if not immagine.isNull():
                        if fermo or muto:
                            self.resumed.emit()
                            fermo = muto = False
                        ultimo = adesso
                        self._deliver(immagine)
            armato = self._armed_at
            if armato is None:
                fermo = muto = False
                continue
            if ultimo is None or ultimo < armato:
                if not muto and adesso - armato > self._no_signal_after:
                    muto = True
                    self.no_signal.emit()
            elif not fermo and adesso - ultimo > self._stall_after:
                fermo = True
                self.stalled.emit()

    def _deliver(self, immagine: QImage) -> None:
        with self._lock:
            self._latest = immagine
            notifica = not self._pending
            self._pending = True
        if notifica:
            self.frame_ready.emit()
```

- [ ] **Step 4: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_liveview_worker.py -q && .venv/bin/python -m pytest -q`
Expected: tutti `passed`. Ripeti `tests/test_liveview_worker.py` tre volte: deve passare sempre (nessun test lento o instabile).

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/gui/liveview.py tests/test_liveview_worker.py
git commit -m "feat(gui): ricezione del live su un thread, vince l'ultimo fotogramma

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `RemoteWorker`

**Files:**
- Modify: `olympus_connect/gui/workers.py` (import, classe nuova in fondo)
- Test: `tests/test_workers.py` (in coda)

**Interfaces:**
- Consumes: i metodi del client del Task 2; `describe_error`; `LIVE_QUALITY`.
- Produces: `RemoteWorker(client, quality: str = LIVE_QUALITY)`:
  - segnali `entered(str, object)` (modalità della ghiera, area di messa a fuoco `tuple | None`), `focused(object)` (`bool | None`), `shot(bytes)` (revisione, `b""` se non arriva), `movie_started()`, `movie_stopped()`, `left()`, `failed(str)`;
  - slot `enter(str, int)` (qualità, porta), `focus(int, int)`, `shoot()`, `start_movie()`, `stop_movie()`, `leave()`;
  - metodo `is_recording() -> bool`.

- [ ] **Step 1: Scrivi i test che falliscono**

In coda a `tests/test_workers.py`:

```python
# --- telecomando -----------------------------------------------------------------

class _RemoteClient:
    def __init__(self, fail=None):
        self.commands = {"exec_takemotion"}
        self.calls = []
        self._fail = fail or {}

    def _call(self, nome, *args):
        self.calls.append((nome, *args))
        if nome in self._fail:
            raise self._fail[nome]

    def enter_rec_mode(self, quality):
        self._call("enter_rec_mode", quality)

    def start_liveview(self, port):
        self._call("start_liveview", port)

    def stop_liveview(self):
        self._call("stop_liveview")

    def take_mode(self):
        self._call("take_mode")
        return "S"

    def touch_area(self):
        self._call("touch_area")
        return (96, 72, 448, 336)

    def assign_af(self, x, y):
        self._call("assign_af", x, y)
        return True

    def take_picture(self):
        self._call("take_picture")

    def rec_view(self):
        self._call("rec_view")
        return b"\xff\xd8rev"

    def start_movie(self):
        self._call("start_movie")

    def stop_movie(self):
        self._call("stop_movie")

    def ensure_play_mode(self):
        self._call("ensure_play_mode")


def _remote(client):
    from olympus_connect.gui.workers import RemoteWorker
    worker = RemoteWorker(client, "1024x0768")
    ev = {k: [] for k in ("entered", "focused", "shot", "started", "stopped", "left", "failed")}
    worker.entered.connect(lambda m, a: ev["entered"].append((m, a)))
    worker.focused.connect(ev["focused"].append)
    worker.shot.connect(ev["shot"].append)
    worker.movie_started.connect(lambda: ev["started"].append(True))
    worker.movie_stopped.connect(lambda: ev["stopped"].append(True))
    worker.left.connect(lambda: ev["left"].append(True))
    worker.failed.connect(ev["failed"].append)
    return worker, ev


def test_remote_enter_switches_mode_then_starts_the_live(qapp):
    client = _RemoteClient()
    worker, ev = _remote(client)
    worker.enter("1024x0768", 40123)
    assert client.calls[:2] == [("enter_rec_mode", "1024x0768"), ("start_liveview", 40123)]
    assert ev["entered"] == [("S", (96, 72, 448, 336))]


def test_remote_enter_failure_is_reported_not_raised(qapp):
    client = _RemoteClient(fail={"start_liveview": CameraNotReachable("x")})
    worker, ev = _remote(client)
    worker.enter("1024x0768", 1)
    assert ev["entered"] == [] and ev["failed"]


def test_remote_enter_survives_unreadable_dial_and_area(qapp):
    client = _RemoteClient(fail={"take_mode": ProtocolError("x"), "touch_area": ProtocolError("x")})
    worker, ev = _remote(client)
    worker.enter("1024x0768", 1)
    assert ev["entered"] == [("", None)]


def test_remote_focus_and_shoot(qapp):
    client = _RemoteClient()
    worker, ev = _remote(client)
    worker.focus(320, 240)
    worker.shoot()
    assert ("assign_af", 320, 240) in client.calls
    assert ev["focused"] == [True] and ev["shot"] == [b"\xff\xd8rev"]


def test_remote_shot_without_review_emits_empty(qapp):
    client = _RemoteClient(fail={"rec_view": ProtocolError("x")})
    worker, ev = _remote(client)
    worker.shoot()
    assert ev["shot"] == [b""] and ev["failed"] == []


def test_remote_refused_shot_is_a_failure(qapp):
    client = _RemoteClient(fail={"take_picture": ProtocolError("x", code=1004)})
    worker, ev = _remote(client)
    worker.shoot()
    assert ev["shot"] == [] and "live" in ev["failed"][0]


def test_remote_movie_start_and_stop(qapp):
    client = _RemoteClient()
    worker, ev = _remote(client)
    worker.start_movie()
    assert worker.is_recording() and ev["started"] == [True]
    worker.stop_movie()
    assert not worker.is_recording() and ev["stopped"] == [True]


def test_remote_leave_stops_a_recording_first_and_always_ends_in_play(qapp):
    client = _RemoteClient(fail={"stop_liveview": CameraNotReachable("x")})
    worker, ev = _remote(client)
    worker.start_movie()
    worker.leave()
    nomi = [c[0] for c in client.calls]
    assert nomi[-3:] == ["stop_movie", "stop_liveview", "ensure_play_mode"]
    assert ev["left"] == [True]


def test_remote_leave_emits_left_even_if_everything_fails(qapp):
    client = _RemoteClient(fail={n: CameraNotReachable("x") for n in
                                 ("stop_movie", "stop_liveview", "ensure_play_mode")})
    worker, ev = _remote(client)
    worker.leave()
    assert ev["left"] == [True]


def test_remote_client_without_commands_connects_first(qapp):
    client = _RemoteClient()
    client.commands = set()
    client.connect = lambda: client.calls.append(("connect",)) or "E-M10MarkIV"
    worker, _ev = _remote(client)
    worker.enter("1024x0768", 1)
    assert client.calls[0] == ("connect",)
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_workers.py -q -k remote`
Expected: failed con `ImportError: cannot import name 'RemoteWorker'`.

- [ ] **Step 3: Scrivi il worker**

In `olympus_connect/gui/workers.py`, all'import del protocollo (o, se non c'è, accanto agli altri import del core):

```python
from ..core.protocol import LIVE_QUALITY
```

In fondo al file:

```python
class RemoteWorker(QObject):
    """I comandi del telecomando, uno alla volta, su un thread proprio.

    Separato dal live: un comando che aspetta la fotocamera (uno stop del
    video dura più di un secondo e mezzo) non deve fermare l'immagine.
    Client proprio, come ogni worker: requests.Session non è thread-safe.
    """

    entered = pyqtSignal(str, object)     # modalità della ghiera, area di messa a fuoco | None
    focused = pyqtSignal(object)          # True / False / None
    shot = pyqtSignal(bytes)              # revisione; b"" se non arriva
    movie_started = pyqtSignal()
    movie_stopped = pyqtSignal()
    left = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, client, quality: str = LIVE_QUALITY) -> None:
        super().__init__()
        self._client = client
        self._quality = quality
        self._recording = False

    def is_recording(self) -> bool:
        return self._recording

    @pyqtSlot(str, int)
    def enter(self, quality: str, port: int) -> None:
        try:
            if not getattr(self._client, "commands", None):
                self._client.connect()
            self._client.enter_rec_mode(quality or self._quality)
            self._client.start_liveview(port)
        except Exception as exc:
            self.failed.emit(describe_error(exc))
            return
        modo, area = "", None
        try:
            modo = self._client.take_mode()
        except Exception:
            pass
        try:
            area = self._client.touch_area()
        except Exception:
            pass
        self.entered.emit(modo, area)

    @pyqtSlot(int, int)
    def focus(self, x: int, y: int) -> None:
        try:
            self.focused.emit(self._client.assign_af(x, y))
        except Exception as exc:
            self.failed.emit(describe_error(exc))

    @pyqtSlot()
    def shoot(self) -> None:
        try:
            self._client.take_picture()
        except Exception as exc:
            self.failed.emit(describe_error(exc))
            return
        try:
            revisione = self._client.rec_view()
        except Exception:
            revisione = b""            # lo scatto c'è; manca solo l'immagine da mostrare
        self.shot.emit(revisione)

    @pyqtSlot()
    def start_movie(self) -> None:
        try:
            self._client.start_movie()
        except Exception as exc:
            self.failed.emit(describe_error(exc))
            return
        self._recording = True
        self.movie_started.emit()

    @pyqtSlot()
    def stop_movie(self) -> None:
        try:
            self._client.stop_movie()
        except Exception as exc:
            self.failed.emit(describe_error(exc))
            return
        self._recording = False
        self.movie_stopped.emit()

    @pyqtSlot()
    def leave(self) -> None:
        """Ogni passo è tentato anche se il precedente fallisce: la
        fotocamera deve tornare in riproduzione in ogni caso."""
        if self._recording:
            try:
                self._client.stop_movie()
            except Exception:
                pass
            self._recording = False
        for passo in (self._client.stop_liveview, self._client.ensure_play_mode):
            try:
                passo()
            except Exception:
                pass
        self.left.emit()
```

- [ ] **Step 4: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_workers.py -q && .venv/bin/python -m pytest -q`
Expected: tutti `passed`.

- [ ] **Step 5: Commit**

```bash
git add olympus_connect/gui/workers.py tests/test_workers.py
git commit -m "feat(gui): RemoteWorker, i comandi del telecomando in fila su un thread

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Vista del live e pannello dei comandi (`gui/remote.py`)

**Files:**
- Create: `olympus_connect/gui/remote.py`
- Modify: `olympus_connect/gui/icons.py` (`_PATHS`: quattro icone nuove)
- Modify: `olympus_connect/gui/panels.py` (`VerticalLabel.set_text`, `CollapsiblePanel.set_title`)
- Test: `tests/test_remote_view.py`

**Interfaces:**
- Consumes: `theme` (`SUNKEN`, `LINE`, `TEXT`, `TEXT_DIM`, `TEXT_FAINT`, `ACCENT`, `OK`, `ERROR`, `SIZE_SMALL`, `SIZE_BODY`, `RADIUS`, `DURATION`), `icons.icon`, `SectionLabel`, `label_column_width` (da `gui/shot.py`).
- Produces:
  - `fit_rect(container: tuple[int, int], frame: tuple[int, int]) -> tuple[float, float, float, float]`;
  - `map_to_frame(pos: tuple[float, float], container: tuple[int, int], frame: tuple[int, int], area: tuple[int, int, int, int] | None) -> tuple[int, int] | None`;
  - `RemoteView(QWidget)`: segnali `focus_requested(int, int)`, `retry_requested()`; metodi `set_frame(QImage)`, `has_frame() -> bool`, `frame_size() -> tuple[int, int] | None`, `set_touch_area(area | None)`, `set_focus_enabled(bool)`, `show_af(x, y)`, `set_af_result(bool | None)`, `show_review(bytes) -> bool`, `is_reviewing() -> bool`, `show_message(text, retry=False)`, `message() -> str`, `clear()`; costante `REVIEW_MS = 2000`;
  - `RemotePanel(QWidget)`: segnali `shoot_requested()`, `movie_toggle_requested()`; metodi `set_take_mode(str)`, `take_mode_text() -> str`, `set_recording(bool)`, `is_recording() -> bool`, `set_busy(bool)`, `elapsed_text() -> str`;
  - icone `remote`, `shutter`, `record`, `stop`;
  - `CollapsiblePanel.set_title(text: str)`.

- [ ] **Step 1: Scrivi i test che falliscono**

Create `tests/test_remote_view.py`:

```python
"""Vista del live e pannello dei comandi."""
import time

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QPoint, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest

from olympus_connect.gui.remote import RemotePanel, RemoteView, fit_rect, map_to_frame


def _jpeg(w=64, h=48) -> bytes:
    immagine = QImage(w, h, QImage.Format.Format_RGB32)
    immagine.fill(0xFF808080)
    dati = QByteArray()
    buffer = QBuffer(dati)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    immagine.save(buffer, "JPEG")
    return bytes(dati)


# --- geometria -------------------------------------------------------------------

def test_fit_rect_letterboxes_a_wider_container():
    assert fit_rect((1000, 480), (640, 480)) == (180.0, 0.0, 640.0, 480.0)


def test_fit_rect_pillarboxes_a_taller_container():
    assert fit_rect((640, 1000), (640, 360)) == (0.0, 320.0, 640.0, 360.0)


def test_map_to_frame_through_the_bands():
    # contenitore 1280×480, fotogramma 640×480 scalato 1:1 al centro
    assert map_to_frame((640, 240), (1280, 480), (640, 480), None) == (320, 240)
    assert map_to_frame((100, 240), (1280, 480), (640, 480), None) is None  # sulla banda


def test_map_to_frame_scales_with_the_frame():
    # 1024×768 mostrato in 512×384: ogni pixel a schermo vale 2
    assert map_to_frame((256, 192), (512, 384), (1024, 768), None) == (512, 384)


def test_map_to_frame_clamps_to_the_touch_area():
    area = (96, 72, 448, 336)
    assert map_to_frame((0, 0), (640, 480), (640, 480), area) == (96, 72)
    assert map_to_frame((639, 479), (640, 480), (640, 480), area) == (544, 408)


# --- RemoteView ------------------------------------------------------------------

def test_click_requests_focus_in_frame_coordinates(qapp):
    vista = RemoteView()
    vista.resize(640, 480)
    vista.set_frame(QImage(640, 480, QImage.Format.Format_RGB32))
    ricevuti = []
    vista.focus_requested.connect(lambda x, y: ricevuti.append((x, y)))
    QTest.mouseClick(vista, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(320, 240))
    assert ricevuti == [(320, 240)]


def test_no_focus_without_a_frame_or_when_disabled(qapp):
    vista = RemoteView()
    vista.resize(640, 480)
    ricevuti = []
    vista.focus_requested.connect(lambda x, y: ricevuti.append((x, y)))
    QTest.mouseClick(vista, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(320, 240))
    vista.set_frame(QImage(640, 480, QImage.Format.Format_RGB32))
    vista.set_focus_enabled(False)
    QTest.mouseClick(vista, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(320, 240))
    assert ricevuti == []


def test_frame_size_follows_the_image(qapp):
    vista = RemoteView()
    vista.set_frame(QImage(640, 360, QImage.Format.Format_RGB32))
    assert vista.frame_size() == (640, 360)


def test_review_covers_the_live_for_two_seconds(qapp):
    vista = RemoteView()
    assert vista.show_review(_jpeg())
    assert vista.is_reviewing()
    fine = time.monotonic() + RemoteView.REVIEW_MS / 1000 + 0.5
    while time.monotonic() < fine and vista.is_reviewing():
        qapp.processEvents()
        time.sleep(0.02)
    assert not vista.is_reviewing()


def test_invalid_review_is_ignored(qapp):
    vista = RemoteView()
    assert not vista.show_review(b"non un jpeg")
    assert not vista.is_reviewing()


def test_a_frame_clears_the_message(qapp):
    vista = RemoteView()
    vista.show_message("Live interrotto", retry=True)
    assert vista.message() == "Live interrotto"
    vista.set_frame(QImage(64, 48, QImage.Format.Format_RGB32))
    assert vista.message() == ""


def test_retry_button_emits(qapp):
    vista = RemoteView()
    vista.resize(640, 480)
    vista.show()
    ricevuti = []
    vista.retry_requested.connect(lambda: ricevuti.append(True))
    vista.show_message("Live interrotto", retry=True)
    vista._retry.click()
    assert ricevuti == [True]
    vista.close()


# --- RemotePanel -----------------------------------------------------------------

def test_panel_buttons_emit(qapp):
    pannello = RemotePanel()
    eventi = []
    pannello.shoot_requested.connect(lambda: eventi.append("scatta"))
    pannello.movie_toggle_requested.connect(lambda: eventi.append("video"))
    pannello._shoot.click()
    pannello._movie.click()
    assert eventi == ["scatta", "video"]


def test_panel_recording_state_and_timer(qapp):
    pannello = RemotePanel()
    pannello.set_recording(True)
    assert pannello.is_recording()
    assert pannello._movie.text().strip() == "Ferma"
    assert pannello.elapsed_text() == "00:00"
    assert not pannello._shoot.isEnabled(), "niente foto durante un video"
    pannello.set_recording(False)
    assert pannello._movie.text().strip() == "Registra"
    assert pannello._shoot.isEnabled()


def test_panel_busy_disables_commands(qapp):
    pannello = RemotePanel()
    pannello.set_busy(True)
    assert not pannello._shoot.isEnabled() and not pannello._movie.isEnabled()
    pannello.set_busy(False)
    assert pannello._shoot.isEnabled() and pannello._movie.isEnabled()


def test_panel_shows_the_dial(qapp):
    pannello = RemotePanel()
    pannello.set_take_mode("S")
    assert pannello.take_mode_text() == "S"
    pannello.set_take_mode("")
    assert pannello.take_mode_text() == "—"
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_remote_view.py -q`
Expected: errore di raccolta, `ModuleNotFoundError: No module named 'olympus_connect.gui.remote'`.

- [ ] **Step 3: Icone e titolo del pannello**

In `olympus_connect/gui/icons.py`, dentro `_PATHS`, dopo la voce `"wifi"`:

```python
    # Telecomando: la fotocamera con un'onda, come «a distanza».
    "remote": (
        '<path d="M2.5 8.5h3l1-1.7h4.8l1 1.7h3v8h-12.8z"/>'
        '<circle cx="8.9" cy="12.5" r="2.2"/>'
        '<path d="M14.6 3.4a4.2 4.2 0 0 1 2.9 2.9"/>'
    ),
    "shutter": '<circle cx="10" cy="10" r="6.6"/><circle cx="10" cy="10" r="2.9"/>',
    "record": '<circle cx="10" cy="10" r="4.2"/><circle cx="10" cy="10" r="1.6"/>',
    "stop": '<rect x="5.6" y="5.6" width="8.8" height="8.8" rx="1.6"/>',
```

In `olympus_connect/gui/panels.py`, nella classe `VerticalLabel` aggiungi:

```python
    def set_text(self, text: str) -> None:
        self._text = text
        self.update()
```

e nella classe `CollapsiblePanel`, dopo `layout_body`:

```python
    def set_title(self, text: str) -> None:
        """Il titolo cambia con il contenuto: «Anteprima» o «Telecomando»."""
        self._title.setText(text)
        self._vertical_title.set_text(text)
```

- [ ] **Step 4: Scrivi la vista e il pannello**

Create `olympus_connect/gui/remote.py`:

```python
"""Il modo Telecomando: la vista del live e il pannello dei comandi.

La vista segue la dimensione reale di ogni fotogramma, non un 4:3 fisso:
durante una registrazione la fotocamera manda 640×360. Il clic diventa un
punto in coordinate del fotogramma, limitato all'area in cui la fotocamera
accetta la messa a fuoco col tocco.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QElapsedTimer, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
    pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QGridLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from . import icons, theme
from .panels import SectionLabel
from .shot import label_column_width

AF_BOX = 56          # lato del riquadro di messa a fuoco a schermo, in pixel


def fit_rect(container: tuple[int, int], frame: tuple[int, int]) -> tuple[float, float, float, float]:
    """Dove sta il fotogramma dentro il contenitore, con le bande: x, y, l, a."""
    cw, ch = container
    fw, fh = frame
    if cw <= 0 or ch <= 0 or fw <= 0 or fh <= 0:
        return 0.0, 0.0, 0.0, 0.0
    scala = min(cw / fw, ch / fh)
    w, h = fw * scala, fh * scala
    return (cw - w) / 2, (ch - h) / 2, w, h


def map_to_frame(pos: tuple[float, float], container: tuple[int, int],
                 frame: tuple[int, int],
                 area: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
    """Un punto del contenitore in coordinate del fotogramma; None sulle bande."""
    x0, y0, w, h = fit_rect(container, frame)
    px, py = pos
    if w <= 0 or not (x0 <= px < x0 + w and y0 <= py < y0 + h):
        return None
    fx = (px - x0) * frame[0] / w
    fy = (py - y0) * frame[1] / h
    if area is not None:
        ax, ay, aw, ah = area
        fx = min(max(fx, ax), ax + aw)
        fy = min(max(fy, ay), ay + ah)
    return int(round(fx)), int(round(fy))


class RemoteView(QWidget):
    focus_requested = pyqtSignal(int, int)
    retry_requested = pyqtSignal()

    REVIEW_MS = 2000

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._frame: QImage | None = None
        self._review: QPixmap | None = None
        self._review_opacity = 1.0
        self._message = ""
        self._area: tuple[int, int, int, int] | None = None
        self._focus_enabled = True
        self._af: tuple[int, int] | None = None
        self._af_result: bool | None = None
        self._af_pending = False

        self._retry = QPushButton(" Riprova", self)
        self._retry.setObjectName("ghost")
        self._retry.setIcon(icons.icon("retry", theme.TEXT, theme.LINE_STRONG, 18))
        self._retry.setIconSize(QSize(18, 18))
        self._retry.hide()
        self._retry.clicked.connect(self.retry_requested)

        self._review_timer = QTimer(self)
        self._review_timer.setSingleShot(True)
        self._review_timer.setInterval(self.REVIEW_MS)
        self._review_timer.timeout.connect(self._end_review)

        self._fade = QPropertyAnimation(self, b"reviewOpacity", self)
        self._fade.setDuration(theme.DURATION)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    # -- proprietà animata ----------------------------------------------------

    def _get_review_opacity(self) -> float:
        return self._review_opacity

    def _set_review_opacity(self, value: float) -> None:
        self._review_opacity = value
        self.update()

    reviewOpacity = pyqtProperty(float, fget=_get_review_opacity, fset=_set_review_opacity)

    # -- contenuto ------------------------------------------------------------

    def set_frame(self, image: QImage) -> None:
        self._frame = image
        if self._message:
            self._message = ""
            self._retry.hide()
        self.update()

    def has_frame(self) -> bool:
        return self._frame is not None

    def frame_size(self) -> tuple[int, int] | None:
        return None if self._frame is None else (self._frame.width(), self._frame.height())

    def set_touch_area(self, area: tuple[int, int, int, int] | None) -> None:
        self._area = area

    def set_focus_enabled(self, enabled: bool) -> None:
        self._focus_enabled = enabled
        if not enabled:
            self._af = None
            self.update()

    def show_af(self, x: int, y: int) -> None:
        self._af = (x, y)
        self._af_pending = True
        self._af_result = None
        self.update()

    def set_af_result(self, result: bool | None) -> None:
        self._af_pending = False
        self._af_result = result
        self.update()

    def show_review(self, data: bytes) -> bool:
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            return False
        self._review = pixmap
        self._fade.stop()
        self._fade.setStartValue(0.35)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._review_timer.start()
        self.update()
        return True

    def is_reviewing(self) -> bool:
        return self._review is not None

    def _end_review(self) -> None:
        self._review = None
        self.update()

    def show_message(self, text: str, retry: bool = False) -> None:
        self._message = text
        self._retry.setVisible(retry)
        self._place_retry()
        self.update()

    def message(self) -> str:
        return self._message

    def clear(self) -> None:
        self._frame = None
        self._review = None
        self._review_timer.stop()
        self._message = ""
        self._retry.hide()
        self._af = None
        self._area = None
        self._focus_enabled = True
        self.update()

    # -- interazione ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if (event.button() != Qt.MouseButton.LeftButton or self._frame is None
                or not self._focus_enabled or self._review is not None):
            super().mousePressEvent(event)
            return
        punto = map_to_frame((event.position().x(), event.position().y()),
                             (self.width(), self.height()),
                             (self._frame.width(), self._frame.height()), self._area)
        if punto is not None:
            self.focus_requested.emit(*punto)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_retry()

    def _place_retry(self) -> None:
        self._retry.adjustSize()
        self._retry.move((self.width() - self._retry.width()) // 2,
                         self.height() // 2 + 18)

    # -- disegno --------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.SUNKEN))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._frame is not None:
            x, y, w, h = fit_rect((self.width(), self.height()),
                                  (self._frame.width(), self._frame.height()))
            painter.drawImage(QRectF(x, y, w, h), self._frame)
            if self._af is not None:
                self._draw_af(painter, x, y, w / self._frame.width())

        if self._review is not None:
            x, y, w, h = fit_rect((self.width(), self.height()),
                                  (self._review.width(), self._review.height()))
            painter.setOpacity(self._review_opacity)
            painter.fillRect(self.rect(), QColor(theme.SUNKEN))
            painter.drawPixmap(QRectF(x, y, w, h).toRect(), self._review)
            painter.setOpacity(1.0)

        if self._message:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 140))
            painter.setPen(QColor(theme.TEXT))
            font = painter.font()
            font.setPixelSize(theme.SIZE_BODY)
            painter.setFont(font)
            testo = self.rect().adjusted(24, 0, -24, -40)
            painter.drawText(testo, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             self._message)
        painter.end()

    def _draw_af(self, painter: QPainter, x0: float, y0: float, scala: float) -> None:
        fx, fy = self._af
        cx, cy = x0 + fx * scala, y0 + fy * scala
        if self._af_pending:
            colore = theme.ACCENT
        elif self._af_result is True:
            colore = theme.OK
        elif self._af_result is False:
            colore = theme.ERROR
        else:
            colore = theme.TEXT
        penna = QPen(QColor(colore))
        penna.setWidthF(2.0)
        painter.setPen(penna)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(cx - AF_BOX / 2, cy - AF_BOX / 2, AF_BOX, AF_BOX))


def _value(text: str) -> QLabel:
    etichetta = QLabel(text)
    etichetta.setObjectName("value")
    return etichetta


class RemotePanel(QWidget):
    shoot_requested = pyqtSignal()
    movie_toggle_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        colonna = QVBoxLayout(self)
        colonna.setContentsMargins(0, 0, 0, 0)
        colonna.setSpacing(8)

        colonna.addWidget(SectionLabel("Scatto"))
        self._shoot = QPushButton(" Scatta")
        self._shoot.setObjectName("primary")
        self._shoot.setIcon(icons.icon("shutter", theme.ACCENT_INK, theme.TEXT_FAINT, 18))
        self._shoot.setIconSize(QSize(18, 18))
        self._shoot.setToolTip("Scatta (barra spaziatrice)")
        self._shoot.clicked.connect(self.shoot_requested)
        colonna.addWidget(self._shoot)

        self._movie = QPushButton()
        self._movie.setIconSize(QSize(18, 18))
        self._movie.setToolTip("Registra o ferma un video (tasto R)")
        self._movie.clicked.connect(self.movie_toggle_requested)
        colonna.addWidget(self._movie)

        self._elapsed_label = _value("00:00")
        self._elapsed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        colonna.addWidget(self._elapsed_label)

        colonna.addSpacing(8)
        colonna.addWidget(SectionLabel("Fotocamera"))
        griglia = QGridLayout()
        griglia.setContentsMargins(0, 0, 0, 0)
        griglia.setHorizontalSpacing(10)
        griglia.setColumnStretch(1, 1)
        griglia.setColumnMinimumWidth(0, label_column_width())
        modalita = QLabel("Modalità")
        modalita.setObjectName("faint")
        griglia.addWidget(modalita, 0, 0)
        self._take_mode = _value("—")
        griglia.addWidget(self._take_mode, 0, 1)
        colonna.addLayout(griglia)

        suggerimento = QLabel("Clic sull'immagine per mettere a fuoco.")
        suggerimento.setObjectName("faint")
        suggerimento.setWordWrap(True)
        colonna.addWidget(suggerimento)
        colonna.addStretch(1)

        self._recording = False
        self._busy = False
        self._clock = QElapsedTimer()
        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._tick)
        self._refresh()

    def set_take_mode(self, mode: str) -> None:
        self._take_mode.setText(mode or "—")

    def take_mode_text(self) -> str:
        return self._take_mode.text()

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        if recording:
            self._clock.start()
            self._ticker.start()
        else:
            self._ticker.stop()
        self._refresh()

    def is_recording(self) -> bool:
        return self._recording

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh()

    def elapsed_text(self) -> str:
        if not self._recording:
            return "00:00"
        secondi = self._clock.elapsed() // 1000
        return f"{secondi // 60:02d}:{secondi % 60:02d}"

    def _tick(self) -> None:
        self._elapsed_label.setText(self.elapsed_text())

    def _refresh(self) -> None:
        if self._recording:
            self._movie.setText(" Ferma")
            self._movie.setIcon(icons.icon("stop", theme.ERROR, theme.LINE_STRONG, 18))
        else:
            self._movie.setText(" Registra")
            self._movie.setIcon(icons.icon("record", theme.ERROR, theme.LINE_STRONG, 18))
        self._elapsed_label.setVisible(self._recording)
        self._elapsed_label.setText(self.elapsed_text())
        self._shoot.setEnabled(not self._busy and not self._recording)
        self._movie.setEnabled(not self._busy)
```

Se `label_column_width` non è esportata da `olympus_connect/gui/shot.py` con quel nome, cerca con `grep -n "def label_column_width" olympus_connect/gui/*.py` e importala dal modulo dove si trova.

- [ ] **Step 5: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_remote_view.py -q && .venv/bin/python -m pytest -q`
Expected: tutti `passed`.

- [ ] **Step 6: Commit**

```bash
git add olympus_connect/gui/remote.py olympus_connect/gui/icons.py olympus_connect/gui/panels.py tests/test_remote_view.py
git commit -m "feat(gui): vista del live e pannello dei comandi del telecomando

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Il modo Telecomando nella finestra

**Files:**
- Modify: `olympus_connect/gui/main_window.py`
- Test: `tests/test_main_window.py` (in coda)

**Interfaces:**
- Consumes: `LiveViewWorker` (Task 3), `RemoteWorker` (Task 4), `RemoteView`, `RemotePanel` (Task 5), `CollapsiblePanel.set_title` (Task 5), `LIVE_QUALITY` (Task 2).
- Produces in `MainWindow`:
  - parametro del costruttore `live_factory=LiveViewWorker`;
  - costante di modulo `VIEW_REMOTE = "telecomando"`;
  - segnali `remote_enter(str, int)`, `remote_focus(int, int)`, `remote_shoot()`, `remote_movie_start()`, `remote_movie_stop()`, `remote_leave()`;
  - attributi `_view_remote` (pulsante), `_remote_view`, `_remote_panel`, `_inspector_pages`, `_remote_active: bool`, `_downloading: bool`, `_shortcut_shoot`, `_shortcut_movie`;
  - metodi `enter_remote()`, `exit_remote(view: str)`, `choose_view(view: str)`, `_update_remote_button()`.

- [ ] **Step 1: Scrivi i test che falliscono**

In coda a `tests/test_main_window.py`:

```python
# --- telecomando ---------------------------------------------------------------------

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402


class _RemoteStubClient(_RecordingClient):
    """Client finto con i comandi della modalità scatto; registra tutto a
    livello di classe, perché ogni worker ne crea uno."""

    def enter_rec_mode(self, quality):
        _RecordingClient.chiamate.append(("enter_rec_mode", quality))

    def start_liveview(self, port):
        _RecordingClient.chiamate.append(("start_liveview", port))

    def stop_liveview(self):
        _RecordingClient.chiamate.append(("stop_liveview",))

    def take_mode(self):
        return "S"

    def touch_area(self):
        return (96, 72, 448, 336)

    def assign_af(self, x, y):
        _RecordingClient.chiamate.append(("assign_af", x, y))
        return True

    def take_picture(self):
        _RecordingClient.chiamate.append(("take_picture",))

    def rec_view(self):
        # Bytes preparati sul thread della GUI: QPixmap non si crea su un altro thread.
        _RecordingClient.chiamate.append(("rec_view",))
        return _RemoteStubClient.revisione

    def start_movie(self):
        _RecordingClient.chiamate.append(("start_movie",))

    def stop_movie(self):
        _RecordingClient.chiamate.append(("stop_movie",))

    def ensure_play_mode(self):
        _RecordingClient.chiamate.append(("ensure_play_mode",))


class _FakeLive(QObject):
    listening = pyqtSignal(int)
    frame_ready = pyqtSignal()
    stalled = pyqtSignal()
    no_signal = pyqtSignal()
    resumed = pyqtSignal()
    istanze: list = []

    def __init__(self):
        super().__init__()
        self.armato = 0
        self.fermato = False
        self.immagine = None
        _FakeLive.istanze.append(self)

    def run(self):
        self.listening.emit(5555)

    def arm(self):
        self.armato += 1

    def disarm(self):
        pass

    def take_frame(self):
        immagine, self.immagine = self.immagine, None
        return immagine

    def stop(self):
        self.fermato = True


def _telecomando(settings):
    _RecordingClient.chiamate = []
    _FakeLive.istanze = []
    _RemoteStubClient.revisione = _jpeg_valido()
    win = MainWindow(client_factory=_RemoteStubClient,
                     network_factory=lambda ssid: FakeNetwork(all_green()),
                     settings=settings, live_factory=_FakeLive)
    win.resize(1200, 700)
    win.show()
    win.on_connected("E-M10MarkIV")
    return win


@pytest.fixture
def remote_win(qapp, settings):
    win = _telecomando(settings)
    yield win
    win.close()


def test_remote_button_needs_a_connected_camera(windows, qapp, settings):
    win = windows(settings)
    assert not win._view_remote.isEnabled()
    win.on_connected("E-M10MarkIV")
    assert win._view_remote.isEnabled()
    win._downloading = True
    win._update_remote_button()
    assert not win._view_remote.isEnabled()


def test_entering_suspends_the_library_and_starts_the_session(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert win._remote_active and win._suspended
    assert win._views.currentWidget() is win._remote_view
    assert win._inspector_pages.currentWidget() is win._remote_panel
    assert not win._download_button.isEnabled()
    assert not any(b.isEnabled() for b in win._filter_boxes.values())
    assert _attendi(qapp, lambda: ("start_liveview", 5555) in _RecordingClient.chiamate)
    assert ("enter_rec_mode", "1024x0768") in _RecordingClient.chiamate
    assert _FakeLive.istanze[-1].armato == 1, "il datagramma va mandato prima di startliveview"
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")


def test_a_frame_reaches_the_view(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: _FakeLive.istanze)
    live = _FakeLive.istanze[-1]
    live.immagine = QImage(64, 48, QImage.Format.Format_RGB32)
    live.frame_ready.emit()
    assert _attendi(qapp, win._remote_view.has_frame)


def test_shoot_shows_the_review(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win._remote_panel._shoot.click()
    assert _attendi(qapp, win._remote_view.is_reviewing)
    assert ("take_picture",) in _RecordingClient.chiamate


def test_click_on_the_live_asks_for_focus(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win._remote_view.focus_requested.emit(320, 240)
    assert _attendi(qapp, lambda: ("assign_af", 320, 240) in _RecordingClient.chiamate)


def test_leaving_stops_the_video_returns_to_play_and_relists(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win._remote_panel._movie.click()
    assert _attendi(qapp, win._remote_panel.is_recording)
    win._view_grid.click()
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    nomi = [c[0] for c in _RecordingClient.chiamate]
    assert nomi.index("stop_movie") < nomi.index("stop_liveview") < nomi.index("ensure_play_mode")
    assert _FakeLive.istanze[-1].fermato
    assert win._views.currentWidget() is not win._remote_view
    assert win._inspector_pages.currentIndex() == 0
    assert not win._suspended
    assert win._list_worker is not None, "uscendo si rifà l'elenco con un client nuovo"


def test_shortcuts_exist_only_in_remote_mode(remote_win, qapp):
    win = remote_win
    assert win._shortcut_shoot.key().toString() == "Space"
    assert win._shortcut_movie.key().toString() == "R"
    assert not win._shortcut_shoot.isEnabled()
    win._view_remote.click()
    assert win._shortcut_shoot.isEnabled() and win._shortcut_movie.isEnabled()


def test_remote_is_not_remembered_as_startup_view(remote_win, qapp, settings):
    remote_win._view_remote.click()
    assert settings.value("view") != "telecomando"


def test_closing_in_remote_mode_returns_the_camera_to_play(qapp, settings):
    win = _telecomando(settings)
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win.close()
    assert ("ensure_play_mode",) in _RecordingClient.chiamate
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_main_window.py -q -k "remote or shortcut or closing"`
Expected: failed con `TypeError: MainWindow.__init__() got an unexpected keyword argument 'live_factory'` e `AttributeError: … '_view_remote'`.

- [ ] **Step 3: Import, costanti, segnali, stato**

In `olympus_connect/gui/main_window.py`:

1. Negli import Qt aggiungi `QEventLoop` e `QTimer` a `PyQt6.QtCore`, e `QKeySequence`, `QShortcut` a `PyQt6.QtGui` (crea la riga `from PyQt6.QtGui import …` se serve, accanto all'import di `QIcon` già presente).
2. Accanto agli altri import del pacchetto:

```python
from ..core.protocol import LIVE_QUALITY
from .liveview import LiveViewWorker
from .remote import RemotePanel, RemoteView
```

e aggiungi `RemoteWorker` all'import da `.workers`.

3. Dopo `VIEW_LABELS = …`:

```python
# Il telecomando non è una vista dell'elenco: non si ricorda come vista
# d'avvio, perché all'avvio la fotocamera non è collegata.
VIEW_REMOTE = "telecomando"
```

4. Nella classe, sotto `preview_needed = pyqtSignal(str)`:

```python
    # Comandi verso il RemoteWorker, che vive su un altro thread.
    remote_enter = pyqtSignal(str, int)
    remote_focus = pyqtSignal(int, int)
    remote_shoot = pyqtSignal()
    remote_movie_start = pyqtSignal()
    remote_movie_stop = pyqtSignal()
    remote_leave = pyqtSignal()
```

5. Il costruttore diventa `def __init__(self, client_factory=OlympusClient, network_factory=CameraNetwork, settings: QSettings | None = None, live_factory=LiveViewWorker) -> None:` e, dopo `self._suspended = False`:

```python
        self._live_factory = live_factory
        self._remote_active = False
        self._remote_leaving = False
        self._remote_worker: RemoteWorker | None = None
        self._live_worker = None
        self._remote_threads: list[QThread] = []
        self._remote_exit_view = VIEW_GRID
        self._live_port = 0
        self._downloading = False
```

- [ ] **Step 4: Pulsante, vista, pagine del pannello, scorciatoie**

1. In `_build_topbar`, dopo `riga.addWidget(self._view_table)`:

```python
        self._view_remote = QPushButton()
        self._view_remote.setObjectName("ghost")
        self._view_remote.setCheckable(True)
        self._view_remote.setIcon(icons.icon("remote", theme.TEXT_DIM, size=18))
        self._view_remote.setIconSize(QSize(18, 18))
        self._view_remote.setToolTip("Telecomando: live, scatto e video")
        self._view_remote.setEnabled(False)
        riga.addWidget(self._view_remote)
```

2. In `_build_content`, dopo `self._views.addWidget(self._build_empty_state())`:

```python
        self._remote_view = RemoteView()
        self._views.addWidget(self._remote_view)
```

3. In `_build_inspector` metti il contenuto esistente in una pagina e aggiungi la pagina del telecomando. Sostituisci la riga `corpo = pannello.layout_body()` con:

```python
        esterno = pannello.layout_body()
        self._inspector_pages = QStackedWidget()
        pagina = QWidget()
        corpo = QVBoxLayout(pagina)
        corpo.setContentsMargins(0, 0, 0, 0)
        self._inspector_pages.addWidget(pagina)
        self._remote_panel = RemotePanel()
        self._inspector_pages.addWidget(self._remote_panel)
        esterno.addWidget(self._inspector_pages)
```

Il resto del metodo resta com'è (aggiunge preview, dettagli, scatto e lo stretch a `corpo`, cioè alla pagina). Se la spaziatura fra gli elementi della pagina risulta diversa da prima, copia su `corpo` lo `setSpacing` che `layout_body()` usa in `gui/panels.py`.

4. In `_build_ui`, sostituisci le due righe `self._view_grid.clicked.connect(lambda: self.set_view(VIEW_GRID))` e `self._view_table.clicked.connect(lambda: self.set_view(VIEW_TABLE))` con:

```python
        self._view_grid.clicked.connect(lambda: self.choose_view(VIEW_GRID))
        self._view_table.clicked.connect(lambda: self.choose_view(VIEW_TABLE))
        self._view_remote.clicked.connect(self.enter_remote)

        self._remote_view.focus_requested.connect(self.on_remote_focus_requested)
        self._remote_view.retry_requested.connect(self.on_live_retry)
        self._remote_panel.shoot_requested.connect(self.on_remote_shoot)
        self._remote_panel.movie_toggle_requested.connect(self.on_remote_movie_toggle)

        self._shortcut_shoot = QShortcut(QKeySequence("Space"), self)
        self._shortcut_shoot.activated.connect(self.on_remote_shoot)
        self._shortcut_movie = QShortcut(QKeySequence("R"), self)
        self._shortcut_movie.activated.connect(self.on_remote_movie_toggle)
        for scorciatoia in (self._shortcut_shoot, self._shortcut_movie):
            scorciatoia.setEnabled(False)
```

- [ ] **Step 5: Stato dei comandi della libreria**

1. In `_update_selection_label`, come ultima riga del metodo (anche nel ramo senza selezione, prima del `return`), garantisci che durante il telecomando il download resti spento. Il modo più semplice: in testa al metodo

```python
        if self._remote_active:
            self._download_button.setEnabled(False)
            return
```

2. In `on_connected`, in fondo: `self._update_remote_button()`.
3. In `on_download_selected`, prima di `self._start_on_thread(worker, worker.run)`: `self._downloading = True` e `self._update_remote_button()`. In `on_resume`, dopo `self._download_worker.resume()`: `self._downloading = True` e `self._update_remote_button()`. In `on_cancel`, `on_paused` e `on_download_finished`, in fondo: `self._downloading = False` e `self._update_remote_button()`.
4. In `set_view`, dopo `self._view_table.setChecked(view == VIEW_TABLE)`: `self._view_remote.setChecked(False)`.

5. Nuovi metodi, in una sezione `# -- telecomando --` dopo `on_download_finished`:

```python
    # ------------------------------------------------------------------
    # telecomando
    # ------------------------------------------------------------------

    def _update_remote_button(self) -> None:
        self._view_remote.setEnabled(
            self._remote_active or (bool(self._connected_model) and not self._downloading))

    def _set_library_controls(self, enabled: bool) -> None:
        for pulsante in self._filter_boxes.values():
            pulsante.setEnabled(enabled)
        if enabled:
            self._update_selection_label()
        else:
            self._download_button.setEnabled(False)

    def choose_view(self, view: str) -> None:
        if self._remote_active:
            self.exit_remote(view)
        else:
            self.set_view(view)

    def enter_remote(self) -> None:
        if self._remote_active:
            self._view_remote.setChecked(True)
            return
        if not self._connected_model or self._downloading:
            self._view_remote.setChecked(False)
            return
        self._remote_active = True
        self._remote_exit_view = self.current_view()
        # Miniature e anteprima lavorano in riproduzione: in modalità scatto
        # le loro richieste fallirebbero.
        self._suspend_thumbnails(True)
        self._set_library_controls(False)
        self._view_grid.setChecked(False)
        self._view_table.setChecked(False)
        self._view_remote.setChecked(True)
        self._views.setCurrentWidget(self._remote_view)
        self._inspector_pages.setCurrentWidget(self._remote_panel)
        self._inspector.set_title("Telecomando")
        self._remote_view.clear()
        self._remote_view.show_message("Collegamento al live…")
        self._remote_panel.set_recording(False)
        self._remote_panel.set_busy(True)
        for scorciatoia in (self._shortcut_shoot, self._shortcut_movie):
            scorciatoia.setEnabled(True)
        self.statusBar().showMessage("Telecomando: passaggio in modalità scatto…")

        remote = RemoteWorker(self._client_factory(), LIVE_QUALITY)
        remote.entered.connect(self.on_remote_entered)
        remote.focused.connect(self.on_remote_focused)
        remote.shot.connect(self.on_remote_shot)
        remote.movie_started.connect(self.on_remote_movie_started)
        remote.movie_stopped.connect(self.on_remote_movie_stopped)
        remote.left.connect(self.on_remote_left)
        remote.failed.connect(self.on_remote_failed)
        coda = Qt.ConnectionType.QueuedConnection
        self.remote_enter.connect(remote.enter, coda)
        self.remote_focus.connect(remote.focus, coda)
        self.remote_shoot.connect(remote.shoot, coda)
        self.remote_movie_start.connect(remote.start_movie, coda)
        self.remote_movie_stop.connect(remote.stop_movie, coda)
        self.remote_leave.connect(remote.leave, coda)

        live = self._live_factory()
        live.listening.connect(self.on_live_listening)
        live.frame_ready.connect(self.on_live_frame)
        live.stalled.connect(self.on_live_stalled)
        live.no_signal.connect(self.on_live_no_signal)

        self._remote_worker = remote
        self._live_worker = live
        self._remote_threads = [self._start_on_thread(remote),
                                self._start_on_thread(live, live.run)]

    @pyqtSlot(int)
    def on_live_listening(self, port: int) -> None:
        self._live_port = port
        if self._live_worker is not None:
            self._live_worker.arm()          # prima di startliveview: apre il passaggio
        self.remote_enter.emit(LIVE_QUALITY, port)

    @pyqtSlot(str, object)
    def on_remote_entered(self, mode: str, area: object) -> None:
        self._remote_panel.set_take_mode(mode)
        self._remote_panel.set_busy(False)
        self._remote_view.set_touch_area(area)
        self.statusBar().showMessage("Telecomando attivo.")

    @pyqtSlot()
    def on_live_frame(self) -> None:
        if self._live_worker is None:
            return
        immagine = self._live_worker.take_frame()
        if immagine is not None:
            self._remote_view.set_frame(immagine)

    @pyqtSlot()
    def on_live_stalled(self) -> None:
        self._remote_view.show_message("Live interrotto.", retry=True)

    @pyqtSlot()
    def on_live_no_signal(self) -> None:
        self._remote_view.show_message(
            "Nessuna immagine dalla fotocamera. Se il firewall blocca il live, "
            "consenti i pacchetti UDP da 192.168.0.10 (vedi il README).", retry=True)

    def on_live_retry(self) -> None:
        if not self._remote_active or self._live_worker is None:
            return
        self._remote_view.show_message("Collegamento al live…")
        self._live_worker.arm()
        self.remote_enter.emit(LIVE_QUALITY, self._live_port)

    def on_remote_focus_requested(self, x: int, y: int) -> None:
        if not self._remote_active or self._remote_panel.is_recording():
            return
        self._remote_view.show_af(x, y)
        self.remote_focus.emit(x, y)

    @pyqtSlot(object)
    def on_remote_focused(self, result: object) -> None:
        self._remote_view.set_af_result(result)

    def on_remote_shoot(self) -> None:
        if (not self._remote_active or self._remote_leaving
                or not self._remote_panel._shoot.isEnabled()):
            return
        self._remote_panel.set_busy(True)
        self.statusBar().showMessage("Scatto…")
        self.remote_shoot.emit()

    @pyqtSlot(bytes)
    def on_remote_shot(self, review: bytes) -> None:
        self._remote_panel.set_busy(False)
        self._remote_view.show_review(review)
        self.statusBar().showMessage("Scatto eseguito.", 4000)

    def on_remote_movie_toggle(self) -> None:
        if (not self._remote_active or self._remote_leaving
                or not self._remote_panel._movie.isEnabled()):
            return
        self._remote_panel.set_busy(True)
        if self._remote_panel.is_recording():
            self.remote_movie_stop.emit()
        else:
            self.remote_movie_start.emit()

    @pyqtSlot()
    def on_remote_movie_started(self) -> None:
        self._remote_panel.set_busy(False)
        self._remote_panel.set_recording(True)
        self._remote_view.set_focus_enabled(False)
        self.statusBar().showMessage("Registrazione in corso.")

    @pyqtSlot()
    def on_remote_movie_stopped(self) -> None:
        self._remote_panel.set_busy(False)
        self._remote_panel.set_recording(False)
        self._remote_view.set_focus_enabled(True)
        self.statusBar().showMessage("Video salvato sulla scheda.", 4000)

    @pyqtSlot(str)
    def on_remote_failed(self, message: str) -> None:
        self._remote_panel.set_busy(False)
        self.statusBar().showMessage(message)
        if not self._remote_view.has_frame():
            self._remote_view.show_message(message, retry=True)

    def exit_remote(self, view: str) -> None:
        if not self._remote_active or self._remote_leaving:
            return
        self._remote_leaving = True
        self._remote_exit_view = view
        self._remote_panel.set_busy(True)
        self.statusBar().showMessage("Uscita dal telecomando…")
        if self._live_worker is not None:
            self._live_worker.disarm()
        self.remote_leave.emit()

    @pyqtSlot()
    def on_remote_left(self) -> None:
        self._teardown_remote()
        self._remote_active = False
        self._remote_leaving = False
        for scorciatoia in (self._shortcut_shoot, self._shortcut_movie):
            scorciatoia.setEnabled(False)
        self._remote_panel.set_recording(False)
        self._remote_panel.set_busy(False)
        self._remote_view.clear()
        self._inspector_pages.setCurrentIndex(0)
        self._inspector.set_title("Anteprima")
        self._set_library_controls(True)
        self._suspend_thumbnails(False)
        self.set_view(self._remote_exit_view)
        self._update_remote_button()
        # Un ListWorker nuovo ha un client nuovo, senza modalità ricordata:
        # ensure_play_mode rimette davvero la fotocamera in riproduzione, e
        # le foto e i video appena fatti compaiono nell'elenco.
        self.on_refresh()

    def _teardown_remote(self) -> None:
        if self._live_worker is not None:
            self._live_worker.stop()
        for thread in self._remote_threads:
            thread.quit()
            thread.wait(2000)
            if thread in self._threads:
                self._threads.remove(thread)
        self._remote_threads = []
        for segnale in (self.remote_enter, self.remote_focus, self.remote_shoot,
                        self.remote_movie_start, self.remote_movie_stop, self.remote_leave):
            try:
                segnale.disconnect()
            except TypeError:
                pass
        for worker in (self._remote_worker, self._live_worker):
            if worker is not None:
                worker.blockSignals(True)
        self._remote_worker = None
        self._live_worker = None
```

- [ ] **Step 6: Chiusura dell'app durante il telecomando**

In `closeEvent`, come primo blocco del metodo (prima di `if self._download_worker is not None:`):

```python
        if self._remote_active and self._remote_worker is not None:
            # La fotocamera deve tornare in riproduzione anche chiudendo,
            # ma senza tenere in ostaggio la chiusura: al massimo 3 s.
            attesa = QEventLoop()
            self._remote_worker.left.connect(attesa.quit)
            QTimer.singleShot(3000, attesa.quit)
            if not self._remote_leaving:
                self._remote_leaving = True
                if self._live_worker is not None:
                    self._live_worker.disarm()
                self.remote_leave.emit()
            attesa.exec()
            self._teardown_remote()
            self._remote_active = False
```

- [ ] **Step 7: Verifica che passino**

Run: `.venv/bin/python -m pytest tests/test_main_window.py -q && .venv/bin/python -m pytest -q`
Expected: tutti `passed`. Ripeti la suite completa tre volte: nessun crash nativo né test instabile.

- [ ] **Step 8: Guarda il modo a schermo**

Con uno script usa e getta nella cartella scratchpad (non nel repository), offscreen, come per la sezione Scatto: tema e font applicati come in `main()`, finestra 1366×740, `_RemoteStubClient` e `_FakeLive` dei test, entra nel telecomando, consegna un fotogramma 1024×768 grigio, poi uno 640×360, mostra il riquadro di messa a fuoco a (512, 384) con esito `True`, e salva due screenshot. Controlla: il fotogramma è centrato con le bande e cambia proporzione al 16:9; il pannello di destra si intitola «Telecomando» e mostra Scatta, Registra e la modalità; nessun testo tagliato a 1366 px.

- [ ] **Step 9: Commit**

```bash
git add olympus_connect/gui/main_window.py tests/test_main_window.py
git commit -m "feat(gui): il modo Telecomando nella finestra

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Dall'inizio alla fine con una fotocamera finta, documentazione

**Files:**
- Create: `tests/test_remote_e2e.py`
- Modify: `README.md`, `DESIGN.md`

**Interfaces:**
- Consumes: tutto; `frame_packets`, `padded_jpeg` da `tests/live_helpers.py`; le fixture vere `tests/fixtures/{commandlist.xml,caminfo.xml,imglist_dcim.csv,imglist_dcim_100olymp.csv}` e `tests/fixtures/remote/{camprop_takemode.xml,camprop_desclist.xml,af.txt}`.
- Produces: nessuna interfaccia.

- [ ] **Step 1: Scrivi il test**

Create `tests/test_remote_e2e.py`:

```python
"""Il telecomando con il client VERO, HTTP vero e UDP vero.

Una fotocamera finta risponde ai comandi con le risposte registrate dalla
E-M10 Mark IV e, alla richiesta startliveview, manda un live sintetico
alla porta richiesta, come fa la fotocamera vera.
"""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QSettings
from PyQt6.QtGui import QImage

from tests.live_helpers import frame_packets, padded_jpeg
from olympus_connect.core.network import Diagnosis, Rung
from olympus_connect.core.protocol import OlympusClient
from olympus_connect.gui.liveview import LiveViewWorker
from olympus_connect.gui.main_window import MainWindow

FIXTURES = Path(__file__).parent / "fixtures"


def _jpeg(w, h) -> bytes:
    immagine = QImage(w, h, QImage.Format.Format_RGB32)
    immagine.fill(0xFF406080)
    dati = QByteArray()
    buffer = QBuffer(dati)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    immagine.save(buffer, "JPEG")
    return bytes(dati)


class _Net:
    def __init__(self, ssid):
        self.ssid = ssid

    def diagnose(self):
        return Diagnosis([Rung("porta 80 della fotocamera", True, "risponde")])


def _attendi(qapp, condizione, secondi=10.0):
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        qapp.processEvents()
        if condizione():
            return True
        time.sleep(0.01)
    return condizione()


@pytest.fixture
def camera(qapp):
    stato = {"richieste": [], "invio": None, "modo": "play"}
    mittente = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mittente.bind(("127.0.0.1", 0))
    fermo = threading.Event()
    fotogramma = padded_jpeg(_jpeg(1024, 768), 60000)
    revisione = _jpeg(320, 240)

    def trasmetti(destinazione):
        seq = 1
        while not fermo.is_set():
            for p in frame_packets(fotogramma, seq=seq, size=(1024, 768)):
                mittente.sendto(p, destinazione)
            seq += len(frame_packets(fotogramma, seq=seq))
            time.sleep(1 / 30)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, body, ctype="text/xml"):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_GET(self):
            url = urlparse(self.path)
            nome = url.path.lstrip("/")
            qs = {k: v[0] for k, v in parse_qs(url.query).items()}
            stato["richieste"].append((nome, qs))
            if nome == "get_commandlist.cgi":
                return self._send((FIXTURES / "commandlist.xml").read_bytes())
            if nome == "get_caminfo.cgi":
                return self._send((FIXTURES / "caminfo.xml").read_bytes())
            if nome == "switch_cammode.cgi":
                stato["modo"] = qs.get("mode")
                return self._send(b"")
            if nome == "get_imglist.cgi":
                f = "imglist_dcim.csv" if qs["DIR"] == "/DCIM" else "imglist_dcim_100olymp.csv"
                return self._send((FIXTURES / f).read_bytes(), "text/plain")
            if nome == "get_camprop.cgi":
                f = "camprop_takemode.xml" if qs.get("propname") == "takemode" else "camprop_desclist.xml"
                return self._send((FIXTURES / "remote" / f).read_bytes())
            if nome == "exec_takemisc.cgi" and qs.get("com") == "startliveview":
                fermo.clear()
                destinazione = (self.client_address[0], int(qs["port"]))
                stato["invio"] = threading.Thread(target=trasmetti, args=(destinazione,), daemon=True)
                stato["invio"].start()
                return self._send(b'<?xml version="1.0"?><funcidlist/>')
            if nome == "exec_takemisc.cgi" and qs.get("com") == "stopliveview":
                fermo.set()
                return self._send(b"")
            if nome == "exec_takemisc.cgi" and qs.get("com") == "getrecview":
                return self._send(revisione, "image/jpeg")
            if nome == "exec_takemotion.cgi":
                if qs.get("com") == "assignafframe":
                    return self._send((FIXTURES / "remote" / "af.txt").read_bytes())
                if qs.get("com") == "starttake":
                    return self._send(b'<?xml version="1.0"?><response><take>ok</take></response>')
                return self._send(b"")
            if nome in ("get_thumbnail.cgi", "set_timeout.cgi", "get_exif.cgi", "get_screennail.cgi"):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stato["base_url"] = f"http://127.0.0.1:{server.server_address[1]}"
    stato["udp"] = mittente.getsockname()
    yield stato
    fermo.set()
    server.shutdown()
    server.server_close()
    mittente.close()


def test_remote_shows_the_live_shoots_and_returns_to_the_library(qapp, camera, tmp_path):
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
        assert _attendi(qapp, lambda: finestra._view_remote.isEnabled()), "nessuna connessione"

        finestra._view_remote.click()
        assert _attendi(qapp, finestra._remote_view.has_frame), "il live non è arrivato alla vista"
        assert finestra._remote_view.frame_size() == (1024, 768)
        assert finestra._remote_panel.take_mode_text() == "S"

        finestra._remote_panel._shoot.click()
        assert _attendi(qapp, finestra._remote_view.is_reviewing), "nessuna revisione dopo lo scatto"

        finestra._view_grid.click()
        assert _attendi(qapp, lambda: not finestra._remote_active), "uscita non completata"
        assert _attendi(qapp, lambda: finestra._model.rowCount() == 1161), "elenco non rifatto"
        modi = [qs.get("mode") for nome, qs in camera["richieste"] if nome == "switch_cammode.cgi"]
        assert "rec" in modi and modi[-1] == "play"
    finally:
        finestra.close()
        qapp.processEvents()
```

- [ ] **Step 2: Verifica che passi**

Run: `.venv/bin/python -m pytest tests/test_remote_e2e.py -q`
Expected: `1 passed`.

- [ ] **Step 3: Verifica che fallisca senza la modifica**

Commenta temporaneamente in `main_window.py` la riga `live.frame_ready.connect(self.on_live_frame)`.
Run: `.venv/bin/python -m pytest tests/test_remote_e2e.py -q`
Expected: FAIL con «il live non è arrivato alla vista». Ripristina la riga; rilancia: `1 passed`; `git diff olympus_connect/` vuoto.

- [ ] **Step 4: Suite completa**

Run: `.venv/bin/python -m pytest -q` (tre volte)
Expected: sempre tutti `passed`; annota il totale.

- [ ] **Step 5: Documentazione**

`README.md`:
- nella tabella «Cosa funziona, verificato sul campo», dopo la riga dei dati di scatto:

```markdown
| Telecomando: live, messa a fuoco col tocco, scatto, video | ✅ live a 1024×768 e 30 fotogrammi/s, nessuna regola del firewall |
```

- nella sezione «Uso», dopo l'elenco numerato, un paragrafo:

```markdown
### Telecomando

Con la fotocamera collegata, il pulsante **Telecomando** nella barra in alto mostra il live al centro della finestra. Un clic sull'immagine mette a fuoco in quel punto; **Scatta** (o la barra spaziatrice) scatta e mostra la foto per due secondi; **Registra** (o il tasto R) avvia e ferma un video. Tornando a Miniature o a Elenco la fotocamera torna in riproduzione e l'elenco si aggiorna con i file nuovi. Le impostazioni di scatto (tempi, diaframmi, ISO) si cambiano sulla fotocamera.

Non serve aprire porte nel firewall: prima di avviare il live il PC manda un pacchetto alla fotocamera, e il firewall accetta le risposte. Se il live non arriva comunque, l'app lo dice e suggerisce la regola da aggiungere.
```

- nello schema della struttura, sotto `core/`, dopo `exif.py`: `│   ├── liveview.py     pacchetti RTP del live, ricomposizione dei fotogrammi`; sotto `gui/`, dopo `shot.py`: `│   ├── liveview.py     ricezione del live su un thread` e `│   ├── remote.py       vista del live e comandi del telecomando`;
- nella sezione «Test», il numero dei test con quello dello Step 4.

`DESIGN.md`, dopo la descrizione dell'ispettore nella sezione del layout, una voce:

```markdown
- **Remote mode** (third top-bar toggle, enabled only with a connected camera and no transfer): the content area becomes the live view on `{colors.sunken}`, letterboxed to the frame's real size (16:9 while recording); a click focuses there and draws a 56 px focus box (accent while pending, ok/error for the result); the review image covers the live for 2 s with the standard 180 ms fade from 0.35. The inspector, retitled «Telecomando», holds a primary **Scatta** button, a **Registra/Ferma** toggle with a red record glyph and a tabular `mm:ss` timer, and the dial mode read-only. Library controls (filters, download) are disabled in this mode.
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_remote_e2e.py README.md DESIGN.md
git status --short
git commit -m "test: telecomando dall'inizio alla fine con una fotocamera finta; docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Prova con la fotocamera vera (con l'utente)

Non è codice: è la verifica che i test non possono fare. Il controllore guida l'utente e annota l'esito.

**Files:**
- Modify: `docs/field-report-2026-09-24.md` (in coda), e il codice solo se la prova trova un difetto (in quel caso, un fix con test, revisionato come un task).

- [ ] **Step 1: L'utente prova il telecomando**

Con la fotocamera in Wi-Fi e collegata (dal pulsante Connetti dell'app), ghiera su P, A, S o M:

```bash
.venv/bin/python -m olympus_connect
```

Sequenza da provare e riferire:
1. Telecomando: il live compare entro 2 s ed è fluido?
2. Clic al centro, poi vicino a ciascuno dei quattro angoli dell'immagine: il riquadro compare dove si è cliccato, diventa verde, e la fotocamera mette a fuoco **in quel punto** (guardando il monitor della fotocamera o il live)?
3. Scatta: si sente lo scatto, la foto compare per 2 s, poi torna il live?
4. Registra 5 secondi, poi Ferma: il live passa a 16:9 durante la registrazione e torna dopo? Il tempo scorre?
5. Torna a Miniature: la fotocamera torna in riproduzione e l'elenco mostra la foto e il video nuovi?
6. Rientra nel telecomando e chiudi l'app dal live: alla riapertura la fotocamera è in riproduzione (elenco leggibile)?

- [ ] **Step 2: Il punto delicato: le coordinate della messa a fuoco a 1024×768**

La prova sul campo ha misurato la messa a fuoco solo con il live a 640×480. Se al punto 2 il fuoco cade **sistematicamente spostato verso l'alto a sinistra** (per esempio un clic nell'angolo in basso a destra mette a fuoco al centro), la fotocamera usa coordinate 640×480 anche con il live a 1024×768: il fix è convertire il punto in `on_remote_focus_requested` moltiplicando per `640 / larghezza` e `480 / altezza` del fotogramma corrente prima di emettere `remote_focus`, e convertire l'area di `touch_area` al contrario prima di `set_touch_area`, con un test in `tests/test_main_window.py`. Se il fuoco cade dove si è cliccato, nessun cambiamento.

- [ ] **Step 3: Annota l'esito**

In coda a `docs/field-report-2026-09-24.md`, una sezione «Telecomando con l'app (2026-11-DD)» con l'esito dei sei punti e, per il punto 2, il sistema di coordinate verificato. Nessun'immagine e nessun seriale.

```bash
git add docs/field-report-2026-09-24.md
git commit -m "docs: prova del telecomando con la fotocamera vera

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
