"""Montaggio del video di una sequenza con ffmpeg.

Le foto entrano come collegamenti simbolici numerati in una cartella
temporanea: così ffmpeg le legge nell'ordine della sequenza qualunque nome
abbiano sulla scheda, e i collegamenti non dipendono dal file system della
cartella di destinazione. Niente Qt.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

RESOLUTIONS = {"Full HD": (1920, 1080), "4K": (3840, 2160)}
FRAME_RATES = (24, 25, 30)
DEFAULT_RESOLUTION = "Full HD"
DEFAULT_FRAME_RATE = 25
VIDEO_NAME = "timelapse.mp4"
FRAME_PATTERN = "%06d.jpg"


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def link_frames(files: Sequence[Path], folder: Path) -> int:
    """Un collegamento numerato per foto (000001.jpg, 000002.jpg…), nell'ordine dato.
    La cartella deve essere vuota: è quella temporanea di un solo montaggio."""
    for numero, file in enumerate(files, 1):
        (folder / (FRAME_PATTERN % numero)).symlink_to(Path(file).resolve())
    return len(files)


def build_command(ffmpeg: str, frames_dir: Path, fps: int, size: tuple[int, int],
                  out: Path) -> list[str]:
    """H.264 in MP4, ritagliato al centro nel formato chiesto (le foto sono 4:3).

    I JPEG hanno i colori in BT.601 a gamma piena; i lettori si aspettano
    BT.709 a gamma ridotta per l'alta definizione. Il filtro converte e il
    video lo dichiara, altrimenti colori e contrasto si spostano di poco.
    """
    larghezza, altezza = size
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-framerate", str(fps), "-i", str(Path(frames_dir) / FRAME_PATTERN),
        "-vf", (f"scale={larghezza}:{altezza}:force_original_aspect_ratio=increase"
                f":out_color_matrix=bt709:out_range=tv,crop={larghezza}:{altezza}"),
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
        "-f", "mp4", str(out),
    ]


def parse_progress(line: str) -> int | None:
    """Il numero di fotogrammi scritti, da una riga «frame=N» di -progress."""
    chiave, _, valore = line.strip().partition("=")
    if chiave != "frame":
        return None
    try:
        return int(valore)
    except ValueError:
        return None
