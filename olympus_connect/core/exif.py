"""Dati di scatto dall'EXIF standard.

Verificato sulle foto della E-M10 Mark IV: tutti i campi mostrati stanno
nell'EXIF standard (IFD0 e sotto-IFD EXIF), compreso il nome
dell'obiettivo. La sezione proprietaria Olympus non viene letta.

Il lettore accetta le forme in cui l'EXIF può arrivare: TIFF nudo (anche
con l'intestazione ORF della fotocamera, IIRO, IIRS o MMOR), blocco che
inizia con «Exif\\0\\0», o JPEG intero. Non solleva mai
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

# intestazioni TIFF: quella standard e le varianti ORF della fotocamera
_TIFF_HEADERS = (b"II*\x00", b"MM\x00*", b"IIRO", b"IIRS", b"MMOR")

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
    if data[:4] in _TIFF_HEADERS:
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
