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


@pytest.mark.parametrize("magic, order", [
    (b"IIRO", "<"), (b"IIRS", "<"), (b"MMOR", ">"),
])
def test_reads_the_olympus_orf_header(magic, order):
    tiff = build_tiff(IFD0, EXIF, order)
    assert parse_exif(magic + tiff[4:]) == ATTESO


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
        if ("Exif.Photo.PixelXDimension" not in valori
                and "Exif.Image.ImageWidth" in valori):
            assert info.width == int(valori["Exif.Image.ImageWidth"]), nome
            assert info.height == int(valori["Exif.Image.ImageLength"]), nome
        if "Exif.Photo.Flash" in valori:
            assert info.flash_fired is bool(int(valori["Exif.Photo.Flash"]) & 1), nome


# --- formattazione -----------------------------------------------------------

from olympus_connect.core.exif import FormattedShot, format_shot  # noqa: E402


def _riga(info, etichetta):
    return dict(format_shot(info).rows).get(etichetta)


@pytest.mark.parametrize("tempo,testo", [
    (Fraction(1, 4000), "1/4000 s"),
    (Fraction(1, 60), "1/60 s"),
    (Fraction(10, 600), "1/60 s"),
    (Fraction(1, 3), "0,3 s"),
    (Fraction(1, 4), "1/4 s"),
    (Fraction(3, 10), "0,3 s"),
    (Fraction(2, 5), "0,4 s"),
    (Fraction(1, 2), "0,5 s"),
    (Fraction(3, 5), "0,6 s"),
    (Fraction(4, 5), "0,8 s"),
    (Fraction(10, 13), "0,8 s"),
    (Fraction(1, 8), "1/8 s"),
    (Fraction(1, 3200), "1/3200 s"),
    (Fraction(1), "1 s"),
    (Fraction(5, 2), "2,5 s"),
    (Fraction(30), "30 s"),
])
def test_exposure_time(tempo, testo):
    assert format_shot(ShotInfo(exposure_time=tempo)).headline == testo


def test_a_zero_exposure_time_is_left_out_without_raising():
    shot = format_shot(ShotInfo(exposure_time=Fraction(0), iso=100))
    assert shot.headline == "ISO 100"


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
