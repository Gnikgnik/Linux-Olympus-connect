"""Gli helper puri della prova del telecomando in diag.py.

La prova vera gira solo con la fotocamera; questi pezzi no, e da loro
dipende lo strumento che ricava le fixture dai pacchetti registrati.
"""
import struct

from diag import leggi_pacchetti, rtp_frame_size, rtp_summary, statistiche_stabili, rtp_tlvs, salva_pacchetti


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


def _tlv(ident, valore):
    return struct.pack(">HH", ident, len(valore) // 4) + valore


def _con_tlv():
    ext = _tlv(1, b"\x00\x00\x00\x07") + _tlv(2, b"\x00" * 8) \
        + _tlv(26, struct.pack(">II", 640, 480))
    return _rtp(ext=(0x0001, ext))


def test_tlvs_of_the_extension_and_frame_size():
    pacchetto = _con_tlv()
    tlvs = rtp_tlvs(pacchetto)
    assert [i for i, _v in tlvs] == [1, 2, 26]
    assert tlvs[0][1] == b"\x00\x00\x00\x07"
    assert rtp_frame_size(pacchetto) == (640, 480)


def test_no_extension_means_no_tlvs_and_no_size():
    assert rtp_tlvs(_rtp()) == []
    assert rtp_frame_size(_rtp()) is None
    ext = _tlv(1, b"\x00" * 4)
    assert rtp_frame_size(_rtp(ext=(1, ext))) is None


def test_tlvs_never_raise_on_truncation():
    pacchetto = _con_tlv()
    for n in range(len(pacchetto) + 1):
        assert isinstance(rtp_tlvs(pacchetto[:n]), list)
        rtp_frame_size(pacchetto[:n])
    assert rtp_tlvs(b"") == [] and rtp_frame_size(b"") is None


def test_tlv_overrunning_the_extension_is_malformed():
    ext = struct.pack(">HH", 26, 50) + b"\x00" * 8     # dichiara 200 byte, ne ha 8
    assert rtp_tlvs(_rtp(ext=(1, ext))) == []


def test_overflowing_extension_never_exposes_the_whole_packet():
    # l'estensione dichiara 400 byte ma il pacchetto è molto più corto:
    # l'intestazione da registrare è quella fissa più le 4 byte dell'estensione.
    pacchetto = struct.pack(">BBHII", 0x90, 96, 1, 2, 3) + struct.pack(">HH", 1, 100) \
        + b"\xff\xd8" + b"\x11" * 60
    s = rtp_summary(pacchetto)
    assert s["header_len"] == 12 + 4
    assert s["ext_overflow"] is True
    assert rtp_summary(_rtp(ext=(1, b"\x00" * 8)))["ext_overflow"] is False
    con_csrc = bytearray(pacchetto)
    con_csrc[0] |= 0x02
    con_csrc[12:12] = b"\x00" * 8
    assert rtp_summary(bytes(con_csrc))["header_len"] == 12 + 8 + 4


def test_steady_state_stats_exclude_the_backlog_and_count_gaps():
    # 30 fps a 90 kHz: +3000 per fotogramma; 3 pacchetti per fotogramma.
    pacchetti, seq = [], 0
    for f in range(60):
        for k in range(3):
            pacchetti.append((0.5 + f / 30 + k * 0.001, ("x", 1),
                              _rtp(seq=seq, ts=1000 + 3000 * f, marker=int(k == 2))))
            seq += 1
    # arretrato letto subito (t < 0.2) con timestamp e sequenza lontani
    arretrato = [(0.002, ("x", 1), _rtp(seq=60000 + i, ts=5)) for i in range(5)]
    del pacchetti[10]                        # un pacchetto perso nel regime
    st = statistiche_stabili(arretrato + pacchetti, 0.2, 10.0)
    assert st["pacchetti"] == len(pacchetti)
    assert st["fotogrammi"] == 60
    assert abs(st["fps"] - 30.0) < 0.01
    assert st["salti"] == 1


def test_steady_state_stats_on_nothing():
    st = statistiche_stabili([], 0.0, 1.0)
    assert st["pacchetti"] == 0 and st["fps"] is None and st["salti"] == 0
