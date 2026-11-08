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
