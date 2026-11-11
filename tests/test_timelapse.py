"""Il comando di ffmpeg, i collegamenti numerati e la lettura dell'avanzamento."""
import subprocess
from pathlib import Path

import pytest
from PyQt6.QtGui import QImage

from olympus_connect.core.timelapse import (
    DEFAULT_FRAME_RATE, DEFAULT_RESOLUTION, FRAME_RATES, RESOLUTIONS, build_command,
    find_ffmpeg, link_frames, parse_progress,
)


def test_choices():
    assert RESOLUTIONS == {"Full HD": (1920, 1080), "4K": (3840, 2160)}
    assert FRAME_RATES == (24, 25, 30)
    assert DEFAULT_RESOLUTION in RESOLUTIONS and DEFAULT_FRAME_RATE in FRAME_RATES


def test_command():
    comando = build_command("/usr/bin/ffmpeg", Path("/tmp/f"), 30, (3840, 2160), Path("/x/t.mp4.part"))
    assert comando[0] == "/usr/bin/ffmpeg" and comando[-1] == "/x/t.mp4.part"
    testo = " ".join(comando)
    assert "-framerate 30 -i /tmp/f/%06d.jpg" in testo
    assert ("scale=3840:2160:force_original_aspect_ratio=increase:out_color_matrix=bt709"
            ":out_range=tv,crop=3840:2160") in testo
    assert "-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv" in testo
    assert "-c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p" in testo
    assert "-progress pipe:1" in testo and "-f mp4" in testo and "-nostdin" in testo


def test_frames_are_linked_in_the_given_order(tmp_path):
    foto = tmp_path / "foto"
    foto.mkdir()
    nomi = ["PB119999.JPG", "PB110001.JPG", "PB110002.JPG"]      # ordine della sequenza, non alfabetico
    for nome in nomi:
        (foto / nome).write_bytes(nome.encode())
    cartella = tmp_path / "fotogrammi"
    cartella.mkdir()
    assert link_frames([foto / n for n in nomi], cartella) == 3
    assert sorted(p.name for p in cartella.iterdir()) == ["000001.jpg", "000002.jpg", "000003.jpg"]
    assert (cartella / "000001.jpg").read_bytes() == b"PB119999.JPG"
    assert (cartella / "000003.jpg").is_symlink()


@pytest.mark.parametrize("riga, atteso", [
    ("frame=12\n", 12), ("frame=0", 0), ("fps=25.0", None), ("frame=N/A", None),
    ("progress=end", None), ("", None),
])
def test_progress_lines(riga, atteso):
    assert parse_progress(riga) == atteso


def test_a_real_video_from_three_frames(tmp_path):
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        pytest.skip("ffmpeg non installato")
    foto = []
    for i, colore in enumerate((0xFF203040, 0xFF806020, 0xFF20A060)):
        immagine = QImage(640, 480, QImage.Format.Format_RGB32)      # 4:3, come la fotocamera
        immagine.fill(colore)
        percorso = tmp_path / f"P{i}.JPG"
        assert immagine.save(str(percorso), "JPEG")
        foto.append(percorso)
    cartella = tmp_path / "fotogrammi"
    cartella.mkdir()
    link_frames(foto, cartella)
    video = tmp_path / "timelapse.mp4"
    esito = subprocess.run(build_command(ffmpeg, cartella, 25, (1920, 1080), video),
                           capture_output=True, text=True, timeout=120)
    assert esito.returncode == 0, esito.stderr
    assert video.stat().st_size > 1000
    assert [parse_progress(r) for r in esito.stdout.splitlines() if r.startswith("frame=")][-1] == 3
    import shutil
    ffprobe = shutil.which("ffprobe")
    if ffprobe is not None:                 # il filtro sbagliato darebbe un altro formato
        letto = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,pix_fmt,color_range,color_space,nb_frames",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=60).stdout.strip()
        assert letto == "1920,1080,yuv420p,tv,bt709,3", letto
