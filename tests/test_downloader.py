from datetime import datetime
from pathlib import Path

from olympus_connect.core.downloader import Job, JobState, pending, plan
from olympus_connect.core.model import MediaFile, MediaKind


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
