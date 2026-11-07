#!/usr/bin/env python3
"""Prova delle impostazioni dal PC (telecomando, pezzo D).

Da eseguire con il PC collegato alla fotocamera, quindi senza Internet:

    .venv/bin/python tools/prova_impostazioni.py

Verifica il formato del comando set_camprop (POST) e cosa diventa
regolabile in P, A, S e M. Non scatta e non registra. Ogni impostazione
toccata torna al valore originale, anche se qualcosa va storto. Scrive il
referto in diag-downloads/prova_impostazioni.txt e gli elenchi grezzi in
diag-downloads/impostazioni/.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import requests

BASE = "http://192.168.0.10"
CAMERA = "192.168.0.10"
UA = {"User-Agent": "OI.Share v2"}
TIMEOUT = (3, 15)
CARTELLA = Path("diag-downloads")
REFERTO = CARTELLA / "prova_impostazioni.txt"
ELENCHI = CARTELLA / "impostazioni"
TOCCATE = ("takemode", "isospeedvalue", "focalvalue", "shutspeedvalue",
           "expcomp", "wbvalue", "drivemode")
ESPOSIZIONE = ("shutspeedvalue", "focalvalue", "isospeedvalue", "expcomp")

_righe: list[str] = []


# -- funzioni pure ------------------------------------------------------------

def parse_desclist(body: bytes) -> dict[str, tuple[str, str, list[str]]]:
    try:
        radice = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return {}
    out: dict[str, tuple[str, str, list[str]]] = {}
    for desc in radice.iter("desc"):
        nome = (desc.findtext("propname") or "").strip()
        if not nome:
            continue
        out[nome] = ((desc.findtext("attribute") or "").strip(),
                     (desc.findtext("value") or "").strip(),
                     (desc.findtext("enum") or "").split())
    return out


def altro_valore(valori: list[str], attuale: str, escludi: tuple[str, ...] = ()) -> str | None:
    """Un valore ammesso diverso dall'attuale: il successivo, o il precedente."""
    candidati = [v for v in valori if v != attuale and v not in escludi]
    if not candidati:
        return None
    if attuale in valori:
        i = valori.index(attuale)
        for v in valori[i + 1:] + valori[:i][::-1]:
            if v in candidati:
                return v
    return candidati[0]


def porta_sorgente(porta: int) -> int:
    """La fotocamera spedisce il live dalla porta di ricezione con i byte scambiati."""
    return ((porta & 0xFF) << 8) | (porta >> 8)


def ordine_ripristino(originali: dict[str, str]) -> list[tuple[str, str]]:
    """In M ogni impostazione di esposizione è regolabile: si ripristina lì
    e si rimette la modalità per ultima."""
    if "takemode" not in originali:
        return list(originali.items())
    resto = [(k, v) for k, v in originali.items() if k != "takemode"]
    return [("takemode", "M"), *resto, ("takemode", originali["takemode"])]


# -- rete -----------------------------------------------------------------------

def scrivi(testo: str) -> None:
    print(testo)
    _righe.append(testo)
    REFERTO.write_text("\n".join(_righe) + "\n")


def _get(path: str, **q) -> tuple[str, bytes]:
    try:
        r = requests.get(f"{BASE}/{path}", headers=UA, params=q, timeout=TIMEOUT)
        return str(r.status_code), r.content
    except requests.RequestException as e:
        return type(e).__name__, b""


def _post(path: str, corpo: bytes, tipo: str | None = None, **q) -> tuple[str, bytes]:
    intestazioni = dict(UA)
    if tipo:
        intestazioni["Content-Type"] = tipo
    try:
        r = requests.post(f"{BASE}/{path}", headers=intestazioni, params=q,
                          data=corpo, timeout=TIMEOUT)
        return str(r.status_code), r.content
    except requests.RequestException as e:
        return type(e).__name__, b""


def _corto(corpo: bytes) -> str:
    return " ".join(corpo[:200].decode("utf-8", "replace").split()) or "(vuoto)"


def leggi(nome: str) -> str:
    _stato, corpo = _get("get_camprop.cgi", com="get", propname=nome)
    try:
        return (ElementTree.fromstring(corpo).findtext("value") or "").strip()
    except ElementTree.ParseError:
        return "?"


def elenco(etichetta: str) -> dict[str, tuple[str, str, list[str]]]:
    stato, corpo = _get("get_camprop.cgi", com="desc", propname="desclist")
    (ELENCHI / f"desclist_{etichetta}.xml").write_bytes(corpo)
    scrivi(f"  desclist {etichetta}: HTTP {stato}, {len(corpo)} byte")
    return parse_desclist(corpo)


def _xml(valore: str) -> bytes:
    return f'<?xml version="1.0"?><set><value>{valore}</value></set>'.encode()


FORMATI = (
    ("xml con dichiarazione", lambda v: (_xml(v), None, {})),
    ("xml senza dichiarazione", lambda v: (f"<set><value>{v}</value></set>".encode(), None, {})),
    ("xml con Content-Type text/xml", lambda v: (_xml(v), "text/xml", {})),
    ("value nella query", lambda v: (b"", None, {"value": v})),
)


def imposta(formato, nome: str, valore: str) -> tuple[str, bytes]:
    corpo, tipo, extra = formato(valore)
    return _post("set_camprop.cgi", corpo, tipo, com="set", propname=nome, **extra)


class Svuota:
    """Riceve e butta il live: senza, il buffer si riempie mentre la prova lavora."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self.sock.bind(("", 0))
        self.sock.settimeout(0.25)
        self.porta = self.sock.getsockname()[1]
        self.pacchetti = 0
        self._fermo = threading.Event()
        self._filo = threading.Thread(target=self._giro, daemon=True)
        self._filo.start()

    def apri(self) -> None:
        try:
            self.sock.sendto(b"\x00", (CAMERA, porta_sorgente(self.porta)))
        except OSError:
            pass

    def _giro(self) -> None:
        while not self._fermo.is_set():
            try:
                self.sock.recvfrom(65535)
                self.pacchetti += 1
            except socket.timeout:
                continue
            except OSError:
                return

    def chiudi(self) -> None:
        self._fermo.set()
        self.sock.close()
        self._filo.join(1)


# -- la prova -------------------------------------------------------------------

def _riassunto(e: dict, nome: str) -> str:
    if nome not in e:
        return f"{nome}: assente"
    attributo, valore, valori = e[nome]
    estremi = f"{valori[0]}…{valori[-1]}" if valori else "-"
    return f"{nome}: {attributo}, valore {valore}, {len(valori)} valori ({estremi})"


def main() -> int:
    ELENCHI.mkdir(parents=True, exist_ok=True)
    scrivi(f"Prova impostazioni {time.strftime('%Y-%m-%d %H:%M:%S')}")
    svuota = Svuota()
    originali: dict[str, str] = {}
    formato_ok = None
    try:
        scrivi(f"play {_get('switch_cammode.cgi', mode='play')[0]}, "
               f"rec {_get('switch_cammode.cgi', mode='rec', lvqty='1024x0768')[0]}")
        svuota.apri()
        scrivi(f"startliveview porta {svuota.porta}: "
               f"{_get('exec_takemisc.cgi', com='startliveview', port=svuota.porta)[0]}")
        time.sleep(1.0)
        scrivi(f"live: {svuota.pacchetti} pacchetti nel primo secondo")

        iniziale = elenco("iniziale")
        originali = {n: iniziale[n][1] for n in TOCCATE if n in iniziale}
        scrivi(f"originali: {originali}")

        # 1. formato del comando
        scrivi("== formato di set_camprop ==")
        iso_orig = originali.get("isospeedvalue", "")
        bersaglio = altro_valore(iniziale.get("isospeedvalue", ("", "", []))[2], iso_orig,
                                 escludi=("Auto", "Low"))
        for nome, formato in FORMATI:
            stato, corpo = imposta(formato, "isospeedvalue", bersaglio or "")
            dopo = leggi("isospeedvalue")
            scrivi(f"  {nome}: HTTP {stato}, risposta {_corto(corpo)}, "
                   f"riletto {dopo} (bersaglio {bersaglio})")
            if dopo == bersaglio:
                formato_ok = formato
                scrivi(f"  -> formato funzionante: {nome}")
                break
        if formato_ok is None:
            scrivi("CANCELLO: nessun formato ha cambiato l'ISO")
            return 1
        stato, _ = imposta(formato_ok, "isospeedvalue", iso_orig)
        scrivi(f"  ISO rimesso a {iso_orig}: HTTP {stato}, riletto {leggi('isospeedvalue')}")

        # 2. cosa è regolabile in P, A, S, M
        scrivi("== modalità ==")
        for modo in ("P", "A", "S", "M"):
            stato, corpo = imposta(formato_ok, "takemode", modo)
            scrivi(f"takemode {modo}: HTTP {stato} {_corto(corpo)}, riletto {leggi('takemode')}")
            e = elenco(modo)
            for nome in ESPOSIZIONE:
                scrivi("  " + _riassunto(e, nome))
            time.sleep(0.3)

        # 3. richieste che la modalità non consente
        scrivi("== richieste non consentite ==")
        for modo, nome, valore in (("S", "focalvalue", None), ("A", "shutspeedvalue", None),
                                   ("A", "focalvalue", "1.0")):
            imposta(formato_ok, "takemode", modo)
            e = parse_desclist(_get("get_camprop.cgi", com="desc", propname="desclist")[1])
            attuale = e.get(nome, ("", "", []))[1]
            valore = valore or altro_valore(e.get(nome, ("", "", []))[2], attuale) or attuale
            stato, corpo = imposta(formato_ok, nome, valore)
            scrivi(f"  in {modo}, {nome}={valore}: HTTP {stato} {_corto(corpo)}, "
                   f"riletto {leggi(nome)} (prima {attuale})")

        # 4. bilanciamento e modalità di scatto
        scrivi("== bilanciamento e scatto ==")
        for nome, escludi in (("wbvalue", ()), ("drivemode", ("lowvib-selftimer", "customselftimer",
                                                          "lowvib-customselftimer",
                                                          "silent-customselftimer"))):
            valori = iniziale.get(nome, ("", "", []))[2]
            valore = altro_valore(valori, originali.get(nome, ""), escludi=escludi)
            if valore is None:
                scrivi(f"  {nome}: nessun altro valore da provare")
                continue
            stato, corpo = imposta(formato_ok, nome, valore)
            scrivi(f"  {nome}={valore}: HTTP {stato} {_corto(corpo)}, riletto {leggi(nome)}")
        return 0
    finally:
        scrivi("== ripristino ==")
        if formato_ok is not None:
            for nome, valore in ordine_ripristino(originali):
                stato, _ = imposta(formato_ok, nome, valore)
                scrivi(f"  {nome}={valore}: HTTP {stato}, riletto {leggi(nome)}")
        scrivi(f"stopliveview {_get('exec_takemisc.cgi', com='stopliveview')[0]}")
        svuota.chiudi()
        scrivi(f"ritorno in riproduzione {_get('switch_cammode.cgi', mode='play')[0]}")
        scrivi(f"live ricevuto in tutto: {svuota.pacchetti} pacchetti")


if __name__ == "__main__":
    sys.exit(main())
