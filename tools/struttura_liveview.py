#!/usr/bin/env python3
"""Ricava dai pacchetti del live registrati le fixture senza immagini.

I pacchetti grezzi in diag-downloads/telecomando/ contengono i fotogrammi
del live, cioè ciò che la fotocamera inquadrava: non vanno in git. Qui si
tiene solo la struttura: l'intestazione RTP vera di ogni pacchetto, estensione
compresa, e la lunghezza del contenuto.

Uso, dalla cartella del progetto:

    .venv/bin/python tools/struttura_liveview.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from diag import leggi_pacchetti, rtp_summary  # noqa: E402

SORGENTE = Path("diag-downloads/telecomando")
DESTINAZIONE = Path("tests/fixtures/liveview")
REGISTRAZIONI = {"live_0640x0480.bin": "0640x0480",
                 "live_1024x0768.bin": "1024x0768",
                 "live_video.bin": "video"}


def struttura(pacchetti: list[tuple[float, bytes]], qualita: str) -> dict:
    righe = []
    scartati = 0
    for t, dati in pacchetti:
        s = rtp_summary(dati)
        if s is None:
            continue
        if s["ext_overflow"]:
            # l'intestazione non è delimitabile: niente rischio di portare
            # dati del fotogramma nella fixture
            scartati += 1
            continue
        righe.append({"t": round(t, 4),
                      "intestazione": dati[:s["header_len"]].hex(),
                      "payload_len": s["payload_len"],
                      "soi": s["payload_soi"], "eoi": s["payload_eoi"]})
    if scartati:
        print(f"  {qualita}: {scartati} pacchetti con estensione che sfora, saltati")
    secondi = round(pacchetti[-1][0], 3) if pacchetti else 0.0
    return {"qualita": qualita, "secondi": secondi, "pacchetti": righe}


def main() -> int:
    DESTINAZIONE.mkdir(parents=True, exist_ok=True)
    scritti = 0
    for nome, qualita in REGISTRAZIONI.items():
        sorgente = SORGENTE / nome
        if not sorgente.exists():
            print(f"  {nome}: assente")
            continue
        pacchetti = leggi_pacchetti(sorgente)
        if not pacchetti:
            print(f"  {nome}: vuoto, nessuna fixture")
            continue
        dati = struttura(pacchetti, qualita)
        (DESTINAZIONE / f"{qualita}.json").write_text(
            json.dumps(dati, indent=1) + "\n")
        print(f"  {qualita}.json: {len(dati['pacchetti'])} pacchetti")
        scritti += 1
    return 0 if scritti else 1


if __name__ == "__main__":
    sys.exit(main())
