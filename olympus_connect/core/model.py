"""Dati puri: nessun I/O, nessuna rete, nessun Qt."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

# Bit di attributo nel formato imglist della fotocamera (convenzione FAT).
# ATTR_READONLY compare nei dati reali (P9061187.ORF) ed è un file normale:
# non va scartato.
ATTR_READONLY = 1
ATTR_HIDDEN = 2
ATTR_SYSTEM = 4
ATTR_VOLUME = 8
ATTR_DIRECTORY = 16

ATTR_SKIP = ATTR_HIDDEN | ATTR_SYSTEM | ATTR_VOLUME

CHUNK_SIZE = 65536


class MediaKind(Enum):
    JPEG = "jpeg"
    RAW = "raw"
    MOVIE = "movie"
    OTHER = "other"


_EXTENSIONS = {
    ".JPG": MediaKind.JPEG,
    ".JPEG": MediaKind.JPEG,
    ".ORF": MediaKind.RAW,
    ".MOV": MediaKind.MOVIE,
    ".MP4": MediaKind.MOVIE,
}


def classify(name: str) -> MediaKind:
    return _EXTENSIONS.get(Path(name).suffix.upper(), MediaKind.OTHER)


def decode_fat_datetime(date: int, time: int) -> datetime:
    """Decodifica i campi data/ora di get_imglist.cgi (campi di bit FAT).

    I secondi sono memorizzati in unità da due, quindi il risultato può
    differire di un secondo dall'EXIF dello stesso file: verificato contro
    P2100001.ORF, che la fotocamera dichiara alle 13:45:52 e l'EXIF alle
    13:45:53. È la granularità del formato, non un difetto da correggere.

    Solleva ValueError se i campi non descrivono una data reale: alcune
    fotocamere emettono zeri per file senza timestamp valido.
    """
    return datetime(
        1980 + (date >> 9),
        (date >> 5) & 0x0F,
        date & 0x1F,
        time >> 11,
        (time >> 5) & 0x3F,
        2 * (time & 0x1F),
    )


@dataclass(frozen=True)
class MediaFile:
    camera_path: str   # es. "/DCIM/100OLYMP/P9240123.JPG"
    size: int          # byte
    shot_at: datetime | None
    kind: MediaKind

    @property
    def name(self) -> str:
        return self.camera_path.rsplit("/", 1)[-1]

    @property
    def dest_relpath(self) -> Path:
        if self.shot_at is None:
            return Path("senza-data") / self.name
        return Path(f"{self.shot_at:%Y}") / f"{self.shot_at:%Y-%m-%d}" / self.name


def parse_imglist(text: str) -> tuple[list[MediaFile], list[str]]:
    """Interpreta la risposta CSV di get_imglist.cgi.

    Restituisce (file trovati, sottodirectory da esplorare a loro volta).
    Righe malformate vengono ignorate: la fotocamera premette alla risposta
    una riga di versione (`VER_100`) e può emettere righe vuote.
    """
    files: list[MediaFile] = []
    subdirs: list[str] = []

    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) != 6:
            continue
        directory, name = parts[0], parts[1]
        try:
            size, attrib, date, time = (int(p) for p in parts[2:])
        except ValueError:
            continue

        path = f"{directory}/{name}"

        if attrib & ATTR_DIRECTORY:
            subdirs.append(path)
            continue
        if attrib & ATTR_SKIP:
            continue

        try:
            shot_at: datetime | None = decode_fat_datetime(date, time)
        except ValueError:
            shot_at = None   # non perdere il file per una data illeggibile

        files.append(MediaFile(path, size, shot_at, classify(name)))

    return files, subdirs
