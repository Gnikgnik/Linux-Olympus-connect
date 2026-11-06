#!/usr/bin/env python3
"""Toglie i numeri di serie dalle fixture EXIF registrate da diag.py.

L'EXIF contiene il seriale del corpo (BodySerialNumber), spesso quello
dell'obiettivo, e altri ancora nella sezione proprietaria Olympus. I byte
vanno sovrascritti a parità di lunghezza: offset e puntatori interni
restano validi, quindi le fixture restano leggibili come quelle vere.

Uso, dalla cartella del progetto, PRIMA di riportare caminfo.xml alla
versione anonimizzata (lo script ne legge i seriali veri):

    .venv/bin/python tools/anonimizza_exif.py

Oltre a caminfo.xml e ai campioni, i seriali si cercano anche nei file
interi in diag-downloads/ (.ORF, .JPG): exiv2 non vede il MakerNote del
campione troncato, ma lo vede nel file completo, e lì stanno il seriale
interno del corpo e quello dell'obiettivo, che ricompaiono identici nel
campione.

Esce con 1, senza scrivere nulla, se exiv2 manca, se fallisce su un file
intero, se un ORF intero non ha alcuna chiave seriale o se non trova
nessun seriale: in questi casi non si può garantire l'anonimizzazione.
Non stampa mai i valori dei seriali, solo conteggi e nomi di file.

Scrive anche tests/fixtures/exif/attesi.json con i valori letti da exiv2,
che i test confrontano con parse_exif.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

CARTELLA = Path("tests/fixtures/exif")
CAMINFO = Path("tests/fixtures/caminfo.xml")
CHIAVI = (
    "Exif.Photo.ExposureTime", "Exif.Photo.FNumber",
    "Exif.Photo.ISOSpeedRatings", "Exif.Photo.FocalLength",
    "Exif.Photo.LensModel", "Exif.Photo.ExposureBiasValue",
    "Exif.Photo.ExposureProgram", "Exif.Photo.WhiteBalance",
    "Exif.Photo.Flash", "Exif.Photo.PixelXDimension",
    "Exif.Photo.PixelYDimension", "Exif.Image.ImageWidth",
    "Exif.Image.ImageLength",
)


def come_jpeg(dati: bytes) -> bytes:
    """exiv2 legge con sicurezza i JPEG: avvolge TIFF nudo e blocco Exif
    in un JPEG minimo con il solo segmento APP1."""
    if dati[:2] == b"\xff\xd8":
        return dati
    if dati[:6] != b"Exif\x00\x00":
        dati = b"Exif\x00\x00" + dati
    return (b"\xff\xd8\xff\xe1" + struct.pack(">H", len(dati) + 2)
            + dati + b"\xff\xd9")


def exiv2(dati: bytes) -> dict[str, str]:
    # Gli ORF (IIRO, IIRS, MMOR) vanno letti come tali: avvolti in un JPEG
    # exiv2 non li riconosce.
    orf = dati[:4] in (b"IIRO", b"IIRS", b"MMOR")
    with tempfile.NamedTemporaryFile(suffix=".orf" if orf else ".jpg") as f:
        f.write(dati if orf else come_jpeg(dati))
        f.flush()
        r = subprocess.run(["exiv2", "-q", "-Pkv", f.name],
                           capture_output=True, text=True, errors="replace")
    valori: dict[str, str] = {}
    for riga in r.stdout.splitlines():
        chiave, _, valore = riga.partition(" ")
        valori[chiave] = valore.strip()
    return valori


def seriali(campioni: dict[str, bytes], problemi: list[str]) -> set[str]:
    trovati: set[str] = set()
    if CAMINFO.exists():
        radice = ElementTree.fromstring(CAMINFO.read_bytes())
        for nodo in radice.iter():
            if re.fullmatch(r"Serial\d+", nodo.tag) and (nodo.text or "").strip():
                trovati.add(nodo.text.strip())
    for dati in campioni.values():
        for chiave, valore in exiv2(dati).items():
            if "serial" in chiave.lower() and len(valore.strip()) >= 4:
                trovati.add(valore.strip())
    # File interi: exiv2 ne legge il MakerNote, che nel campione troncato no.
    # Qui il controllo è severo: un errore di exiv2 o un ORF senza alcuna
    # chiave "serial" significa che un seriale potrebbe sfuggire.
    cartella = Path("diag-downloads")
    if cartella.is_dir():
        for p in sorted(cartella.iterdir()):
            if p.suffix.lower() in (".orf", ".jpg"):
                r = subprocess.run(["exiv2", "-q", "-Pkv", str(p)],
                                   capture_output=True, text=True, errors="replace")
                if r.returncode != 0:
                    problemi.append(f"exiv2 ha fallito su {p.name}")
                chiavi_serial = 0
                for riga in r.stdout.splitlines():
                    chiave, _, valore = riga.partition(" ")
                    if "serial" in chiave.lower():
                        chiavi_serial += 1
                        if len(valore.strip()) >= 4:
                            trovati.add(valore.strip())
                if p.suffix.lower() == ".orf" and chiavi_serial == 0:
                    problemi.append(f"nessuna chiave seriale in {p.name}")
    # I segnaposto già anonimi non sono seriali da cercare.
    return {s for s in trovati if set(s) - {"X", "Y", "0"}}


def main() -> int:
    if shutil.which("exiv2") is None:
        print("ERRORE: exiv2 non è installato; nessun file modificato.")
        return 1
    campioni = {p.name: p.read_bytes() for p in sorted(CARTELLA.iterdir())
                if p.suffix == ".bin"}
    if not campioni:
        print("Nessuna fixture in tests/fixtures/exif/: eseguire diag.py.")
        return 1

    problemi: list[str] = []
    da_togliere = seriali(campioni, problemi)
    print(f"Seriali da togliere: {len(da_togliere)}")
    if problemi:
        for problema in problemi:
            print(f"ATTENZIONE: {problema}")
        print("Nessun file modificato: i seriali potrebbero sfuggire.")
        return 1
    if not da_togliere:
        print("ERRORE: nessun seriale trovato: impossibile garantire "
              "l'anonimizzazione. Nessun file modificato.")
        return 1

    for nome, dati in campioni.items():
        sostituzioni = 0
        for seriale in da_togliere:
            vero = seriale.encode("ascii", "replace")
            sostituzioni += dati.count(vero)
            dati = dati.replace(vero, b"0" * len(vero))
        (CARTELLA / nome).write_bytes(dati)
        campioni[nome] = dati
        print(f"  {nome}: {sostituzioni} sostituzioni")

    rimasti = [(n, s) for n, d in campioni.items() for s in da_togliere
               if s.encode("ascii", "replace") in d]
    if rimasti:
        # Mai stampare i valori: solo conteggio e nomi dei file.
        nomi = sorted({n for n, _ in rimasti})
        print(f"ERRORE: {len(rimasti)} seriali ancora presenti in: "
              f"{', '.join(nomi)}")
        return 1

    attesi = {}
    for nome, dati in campioni.items():
        letti = exiv2(dati)
        valori = {k: letti[k] for k in CHIAVI if k in letti}
        if valori:
            attesi[nome] = valori
    (CARTELLA / "attesi.json").write_text(
        json.dumps(attesi, indent=2, ensure_ascii=False) + "\n")
    print(f"attesi.json: {sorted(attesi)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
