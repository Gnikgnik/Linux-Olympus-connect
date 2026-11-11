"""Pianificazione e trasferimento dei file dalla fotocamera al disco."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

import requests

from .errors import CameraAsleep, IncompleteDownload
from .model import MediaFile

BACKOFF_SECONDS = (2, 4, 8)


class JobState(Enum):
    NEW = "new"                # assente a destinazione
    PRESENT = "present"        # già presente con la stessa dimensione
    INCOMPLETE = "incomplete"  # presente ma di dimensione diversa


@dataclass(frozen=True)
class Job:
    media: MediaFile
    dest: Path
    state: JobState


def plan(files: Iterable[MediaFile], dest_root: Path) -> list[Job]:
    """Confronta l'elenco della fotocamera col disco. Non scrive nulla.

    Un file col nome definitivo è per costruzione integro (vedi download():
    il rename è atomico e avviene solo a trasferimento verificato), quindi
    nome + dimensione bastano a riconoscere un file già scaricato.
    """
    jobs: list[Job] = []
    for media in files:
        dest = dest_root / media.dest_relpath
        if not dest.exists():
            state = JobState.NEW
        elif dest.stat().st_size == media.size:
            state = JobState.PRESENT
        else:
            state = JobState.INCOMPLETE
        jobs.append(Job(media, dest, state))
    return jobs


def plan_flat(files: Iterable[MediaFile], folder: Path) -> list[Job]:
    """Come plan(), ma tutti i file in una sola cartella, con il nome della
    fotocamera: è la cartella dedicata di una sequenza."""
    jobs: list[Job] = []
    for media in files:
        dest = Path(folder) / media.name
        if not dest.exists():
            state = JobState.NEW
        elif dest.stat().st_size == media.size:
            state = JobState.PRESENT
        else:
            state = JobState.INCOMPLETE
        jobs.append(Job(media, dest, state))
    return jobs


def pending(jobs: Iterable[Job]) -> list[Job]:
    """I job da trasferire, dal più piccolo al più grande.

    Questo firmware non supporta le richieste Range: un'interruzione costa
    l'intero file in corso. Mettendo i file piccoli per primi, il lavoro a
    rischio in ogni istante è quasi sempre minimo — chiudere 1141 ORF da
    20 MB prima di attaccare un MOV da 944 MB è strettamente migliore che
    il contrario.
    """
    return sorted(
        (j for j in jobs if j.state is not JobState.PRESENT),
        key=lambda j: j.media.size,
    )


class Outcome(Enum):
    DONE = "done"
    CANCELLED = "cancelled"


def _part_path(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".part")


def download(
    client,
    job: Job,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> Outcome:
    """Trasferisce un file, ritentando dopo le disconnessioni.

    La fotocamera spegne l'access point dopo un periodo di inattività, e su
    un file grande il sintomo è una connessione chiusa a metà. Questo
    firmware non supporta le richieste Range, quindi il tentativo
    successivo riparte da zero; dove il Range è supportato riprende dal
    punto raggiunto, e il ramo è coperto dai test.

    Il rename al nome definitivo è atomico e avviene solo dopo la verifica
    della dimensione: un file col nome finale è quindi sempre integro, ed è
    questo che rende sicura la regola dei duplicati di plan().
    """
    attempts = retries + 1
    for attempt in range(attempts):
        try:
            return _attempt(client, job, on_progress, should_cancel)
        except (CameraAsleep, IncompleteDownload,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError) as exc:
            if attempt == attempts - 1:
                raise CameraAsleep(
                    f"Trasferimento di {job.media.name} interrotto dopo "
                    f"{attempts} tentativi."
                ) from exc
            sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])

    raise AssertionError("non raggiungibile")


def _attempt(
    client,
    job: Job,
    on_progress: Callable[[int, int], None] | None,
    should_cancel: Callable[[], bool] | None,
) -> Outcome:
    expected = job.media.size
    part = _part_path(job.dest)
    job.dest.parent.mkdir(parents=True, exist_ok=True)

    offset = part.stat().st_size if part.exists() else 0
    if offset >= expected:
        offset = 0   # .part incoerente o già completo: ricomincia

    with client.open_stream(job.media.camera_path, offset) as stream:
        resuming = offset > 0 and stream.resumed
        written = offset if resuming else 0
        # "wb" tronca: se il Range non è stato onorato, il .part parziale
        # viene buttato invece di far crescere un file corrotto.
        with open(part, "ab" if resuming else "wb") as handle:
            for chunk in stream.chunks:
                if should_cancel is not None and should_cancel():
                    handle.flush()
                    return Outcome.CANCELLED
                handle.write(chunk)
                written += len(chunk)
                if on_progress is not None:
                    on_progress(written, expected)

    if written != expected:
        raise IncompleteDownload(
            f"{job.media.name}: ricevuti {written} byte su {expected} attesi."
        )

    os.replace(part, job.dest)
    if job.media.shot_at is not None:
        stamp = job.media.shot_at.timestamp()
        os.utime(job.dest, (stamp, stamp))
    return Outcome.DONE
