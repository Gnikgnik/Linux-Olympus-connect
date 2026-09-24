from datetime import datetime
from pathlib import Path

import pytest

from olympus_connect.core.model import (
    MediaFile, MediaKind, classify, decode_fat_datetime, parse_imglist,
)


def test_decode_fat_datetime_reference_value():
    # 19594 = 2018-04-10, 45820 = 22:23:56 (secondi in unità da 2)
    assert decode_fat_datetime(19594, 45820) == datetime(2018, 4, 10, 22, 23, 56)


def test_decode_fat_datetime_matches_real_camera_row():
    # Riga reale: /DCIM/100OLYMP,P2100001.ORF,19578383,0,23626,28090
    # EXIF letto da exiv2 sullo stesso file: 2026-02-10 13:45:53.
    # Lo scarto di 1 s è la granularità a 2 secondi del formato FAT.
    assert decode_fat_datetime(23626, 28090) == datetime(2026, 2, 10, 13, 45, 52)


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


def test_parse_imglist_keeps_file_with_invalid_date():
    # Una data non valida non deve far perdere il file.
    text = "/DCIM/100OLYMP,ODD.JPG,1,0,0,0\r\n"
    files, _ = parse_imglist(text)
    assert files[0].shot_at is None


def test_parse_imglist_ignores_non_numeric_fields():
    text = "/DCIM/100OLYMP,BAD.JPG,non-un-numero,0,19594,45820\r\n"
    files, _ = parse_imglist(text)
    assert files == []
