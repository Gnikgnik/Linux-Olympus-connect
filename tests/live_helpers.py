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
