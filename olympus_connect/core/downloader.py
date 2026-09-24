"""Pianificazione e trasferimento dei file dalla fotocamera al disco."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from .model import MediaFile


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
