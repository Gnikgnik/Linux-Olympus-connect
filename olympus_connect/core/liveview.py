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
TLV_LENS = 9
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
        self._pending_lens: tuple[float, float] | None = None
        self.frame_size: tuple[int, int] | None = None
        self.lens_limits: tuple[float, float] | None = None

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
            self._pending_lens = p.lens_limits()
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
        self.lens_limits = self._pending_lens
        return jpeg
