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
