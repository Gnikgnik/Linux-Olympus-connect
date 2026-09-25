from datetime import datetime
from pathlib import Path

import pytest

from olympus_connect.core.downloader import (
    Job, JobState, Outcome, download, pending, plan,
)
from olympus_connect.core.errors import CameraAsleep
from olympus_connect.core.model import MediaFile, MediaKind
from olympus_connect.core.protocol import OlympusClient
from tests.conftest import FlakyCamera

PAYLOAD = bytes(range(256)) * 1600   # 409600 byte: la caduta a metà lascia 3 chunk
#                                     completi da 64 KiB sul disco, come su un file reale


def media(name="P9240123.JPG", size=1000, kind=MediaKind.JPEG) -> MediaFile:
    return MediaFile(f"/DCIM/100OLYMP/{name}", size,
                     datetime(2026, 9, 24, 18, 5, 0), kind)


def test_plan_marks_missing_file_as_new(tmp_path):
    jobs = plan([media()], tmp_path)
    assert jobs[0].state is JobState.NEW
    assert jobs[0].dest == tmp_path / "2026/2026-09-24/P9240123.JPG"


def test_plan_marks_existing_file_with_same_size_as_present(tmp_path):
    dest = tmp_path / "2026/2026-09-24/P9240123.JPG"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x" * 1000)

    assert plan([media(size=1000)], tmp_path)[0].state is JobState.PRESENT


def test_plan_marks_existing_file_with_different_size_as_incomplete(tmp_path):
    # Dimensione diversa significa file non corrispondente: va riscaricato.
    dest = tmp_path / "2026/2026-09-24/P9240123.JPG"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x" * 42)

    assert plan([media(size=1000)], tmp_path)[0].state is JobState.INCOMPLETE


def test_plan_puts_undated_file_in_senza_data(tmp_path):
    undated = MediaFile("/DCIM/100OLYMP/ODD.JPG", 5, None, MediaKind.JPEG)
    assert plan([undated], tmp_path)[0].dest == tmp_path / "senza-data/ODD.JPG"


def test_plan_does_not_create_directories(tmp_path):
    # plan() deve essere una pura ispezione: nessun effetto sul disco.
    plan([media()], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_plan_preserves_input_order(tmp_path):
    # L'ordinamento è responsabilità di pending(), non di plan().
    jobs = plan([media("B.JPG"), media("A.JPG")], tmp_path)
    assert [j.media.name for j in jobs] == ["B.JPG", "A.JPG"]


def test_pending_excludes_present_jobs(tmp_path):
    jobs = [
        Job(media("A.JPG"), tmp_path / "A.JPG", JobState.NEW),
        Job(media("B.JPG"), tmp_path / "B.JPG", JobState.PRESENT),
        Job(media("C.JPG"), tmp_path / "C.JPG", JobState.INCOMPLETE),
    ]
    assert {j.media.name for j in pending(jobs)} == {"A.JPG", "C.JPG"}


def test_pending_orders_smallest_first(tmp_path):
    # Senza Range un'interruzione costa il file in corso: conviene che il
    # file in corso sia il più piccolo possibile il più a lungo possibile.
    jobs = [
        Job(media("BIG.MOV", 944_000_000), tmp_path / "BIG.MOV", JobState.NEW),
        Job(media("SMALL.ORF", 20_000_000), tmp_path / "SMALL.ORF", JobState.NEW),
        Job(media("MID.MOV", 129_000_000), tmp_path / "MID.MOV", JobState.NEW),
    ]
    assert [j.media.name for j in pending(jobs)] == ["SMALL.ORF", "MID.MOV", "BIG.MOV"]


def test_plan_on_real_card_listing(tmp_path, fixture_bytes):
    """La scheda reale: 1161 file, tutti nuovi su una destinazione vuota."""
    from olympus_connect.core.model import parse_imglist

    files, _ = parse_imglist(
        fixture_bytes("imglist_dcim_100olymp.csv").decode("ascii", "replace")
    )
    jobs = plan(files, tmp_path)

    assert len(jobs) == 1161
    assert all(j.state is JobState.NEW for j in jobs)
    # 27,9 GB da trasferire: il salto dei duplicati non è un'ottimizzazione.
    assert sum(j.media.size for j in pending(jobs)) > 27_000_000_000
    # E l'ordine mette per ultimo il MOV da 944 MB.
    assert pending(jobs)[-1].media.size == max(f.size for f in files)


# --- trasferimento ---------------------------------------------------------

def job_for(tmp_path, size=len(PAYLOAD)) -> Job:
    return Job(media(size=size), tmp_path / "out" / "P9240123.JPG", JobState.NEW)


def part_of(job: Job) -> Path:
    return job.dest.with_suffix(job.dest.suffix + ".part")


def test_download_writes_file_and_reports_progress(tmp_path):
    with FlakyCamera(PAYLOAD) as camera:
        client = OlympusClient(base_url=camera.base_url)
        seen: list[tuple[int, int]] = []
        job = job_for(tmp_path)

        outcome = download(client, job,
                           on_progress=lambda w, t: seen.append((w, t)))
        assert outcome is Outcome.DONE

    assert job.dest.read_bytes() == PAYLOAD
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))


def test_download_creates_parent_directories(tmp_path):
    with FlakyCamera(PAYLOAD) as camera:
        job = job_for(tmp_path)
        download(OlympusClient(base_url=camera.base_url), job)
    assert job.dest.parent.is_dir()


def test_download_leaves_no_part_file_on_success(tmp_path):
    with FlakyCamera(PAYLOAD) as camera:
        job = job_for(tmp_path)
        download(OlympusClient(base_url=camera.base_url), job)
    assert list(job.dest.parent.glob("*.part")) == []


def test_download_sets_mtime_to_shot_time(tmp_path):
    with FlakyCamera(PAYLOAD) as camera:
        job = job_for(tmp_path)
        download(OlympusClient(base_url=camera.base_url), job)
    assert job.dest.stat().st_mtime == pytest.approx(
        job.media.shot_at.timestamp(), abs=1
    )


def test_download_resumes_from_part_file_when_range_works(tmp_path):
    # Ramo per altri modelli: la E-M10 IV non supporta Range (Task 0), ma il
    # rilevamento è a runtime e questo percorso va comunque protetto.
    job = job_for(tmp_path)
    part = part_of(job)
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:40000])

    with FlakyCamera(PAYLOAD) as camera:
        download(OlympusClient(base_url=camera.base_url), job)
        assert camera.requests[0][1] == "bytes=40000-"

    assert job.dest.read_bytes() == PAYLOAD


def test_download_restarts_when_server_ignores_range(tmp_path):
    # Il comportamento reale: il .part parziale va buttato, non appeso.
    job = job_for(tmp_path)
    part = part_of(job)
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:40000])

    with FlakyCamera(PAYLOAD, honour_range=False) as camera:
        download(OlympusClient(base_url=camera.base_url), job)

    assert job.dest.read_bytes() == PAYLOAD


def test_download_recovers_from_drop_at_half_with_range(tmp_path):
    job = job_for(tmp_path)
    with FlakyCamera(PAYLOAD, drop_once=True) as camera:
        client = OlympusClient(base_url=camera.base_url)
        assert download(client, job, sleep=lambda s: None) is Outcome.DONE
        assert len(camera.requests) == 2
        assert camera.requests[1][1] is not None   # il retry ha chiesto un Range

    assert job.dest.read_bytes() == PAYLOAD


def test_download_recovers_from_drop_at_half_without_range(tmp_path):
    # **Il test che conta**: è lo scenario reale della E-M10 Mark IV.
    # La connessione cade a metà e il server ignora il Range: il recupero
    # deve ripartire da zero e produrre comunque un file corretto, non
    # 50 KB di spazzatura seguiti dal file intero.
    job = job_for(tmp_path)
    with FlakyCamera(PAYLOAD, honour_range=False, drop_once=True) as camera:
        client = OlympusClient(base_url=camera.base_url)
        assert download(client, job, sleep=lambda s: None) is Outcome.DONE

    assert job.dest.read_bytes() == PAYLOAD
    assert list(job.dest.parent.glob("*.part")) == []


def test_download_discards_part_file_larger_than_expected(tmp_path):
    # Un .part più grande del file atteso è spazzatura: va buttato.
    job = job_for(tmp_path)
    part = part_of(job)
    part.parent.mkdir(parents=True)
    part.write_bytes(b"q" * (len(PAYLOAD) + 5000))

    with FlakyCamera(PAYLOAD) as camera:
        download(OlympusClient(base_url=camera.base_url), job)

    assert job.dest.read_bytes() == PAYLOAD


def test_download_cancels_between_chunks_and_keeps_part(tmp_path):
    job = job_for(tmp_path)
    with FlakyCamera(PAYLOAD) as camera:
        client = OlympusClient(base_url=camera.base_url)
        assert download(client, job, should_cancel=lambda: True) is Outcome.CANCELLED

    assert not job.dest.exists()
    assert part_of(job).exists()   # il lavoro fatto resta sul disco


def test_download_raises_camera_asleep_after_exhausting_retries(tmp_path):
    class AlwaysDropping(OlympusClient):
        def open_stream(self, camera_path, offset=0):
            raise CameraAsleep("connessione caduta")

    job = job_for(tmp_path)
    with pytest.raises(CameraAsleep):
        download(AlwaysDropping(base_url="http://127.0.0.1:1"), job,
                 sleep=lambda s: None)


def test_download_backs_off_with_expected_delays(tmp_path):
    slept: list[float] = []

    class AlwaysDropping(OlympusClient):
        def open_stream(self, camera_path, offset=0):
            raise CameraAsleep("connessione caduta")

    job = job_for(tmp_path)
    with pytest.raises(CameraAsleep):
        download(AlwaysDropping(base_url="http://127.0.0.1:1"), job,
                 sleep=slept.append)

    assert slept == [2, 4, 8]


def test_download_raises_when_size_does_not_match(tmp_path):
    # La fotocamera manda meno byte di quanti la imglist dichiarava: il file
    # non deve mai prendere il nome definitivo.
    job = job_for(tmp_path, size=len(PAYLOAD) + 999)
    with FlakyCamera(PAYLOAD) as camera:
        with pytest.raises(CameraAsleep):
            download(OlympusClient(base_url=camera.base_url), job,
                     sleep=lambda s: None)
    assert not job.dest.exists()
