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
import traceback
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

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


def valori_modo(elenco_: dict[str, tuple[str, str, list[str]]]) -> dict[str, str]:
    """Le impostazioni di esposizione scrivibili (getset) in una modalità."""
    return {n: valore for n, (attributo, valore, _valori) in elenco_.items()
            if attributo == "getset" and n in ESPOSIZIONE}


def piano_ripristino(modo_originale: str, per_modo: dict[str, dict[str, str]],
                     globali: dict[str, str]) -> list[tuple[str, str]]:
    """Ogni modalità visitata in ordine, con i suoi valori; poi la modalità
    originale con i suoi valori; poi le impostazioni globali non già coperte;
    per ultima la modalità originale."""
    passi: list[tuple[str, str]] = []
    for modo, valori in per_modo.items():
        if modo != modo_originale:
            passi += [("takemode", modo), *valori.items()]
    passi += [("takemode", modo_originale), *per_modo.get(modo_originale, {}).items()]
    coperti = {nome for valori in per_modo.values() for nome in valori}
    passi += [(nome, valore) for nome, valore in globali.items()
              if nome not in coperti and nome != "takemode"]
    passi.append(("takemode", modo_originale))
    return passi


# -- rete -----------------------------------------------------------------------

def scrivi(testo: str) -> None:
    _righe.append(testo)
    try:
        print(testo)
    except Exception:
        pass
    try:
        REFERTO.write_text("\n".join(_righe) + "\n")
    except Exception:
        pass


def _sicuro(descrizione: str, passo: Callable[[], object]) -> object:
    """Esegue un passo di ripristino: un errore non ferma gli altri."""
    try:
        return passo()
    except Exception:
        scrivi(f"{descrizione}: errore\n{traceback.format_exc()}")
        return None


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


# -- stato e passi della prova ----------------------------------------------------

@dataclass
class Stato:
    """Quello che la prova ha letto e può aver cambiato, per il ripristino."""
    svuota: Svuota | None = None
    formato: Callable[[str], tuple[bytes, str | None, dict]] | None = None
    originali: dict[str, str] = field(default_factory=dict)
    per_modo: dict[str, dict[str, str]] = field(default_factory=dict)


def cambia_modo(stato: Stato, modo: str) -> bool:
    if stato.formato is not None:
        imposta(stato.formato, "takemode", modo)
    else:
        for _etichetta, formato in FORMATI:
            imposta(formato, "takemode", modo)
            if leggi("takemode") == modo:
                stato.formato = formato
                break
    letto = leggi("takemode")
    scrivi(f"takemode {modo}: riletto {letto}")
    time.sleep(0.3)
    return letto == modo


def cerca_formato_iso(stato: Stato, bersaglio: str) -> bool:
    for etichetta, formato in FORMATI:
        stato_http, corpo = imposta(formato, "isospeedvalue", bersaglio)
        dopo = leggi("isospeedvalue")
        scrivi(f"  {etichetta}: HTTP {stato_http}, risposta {_corto(corpo)}, "
               f"riletto {dopo} (bersaglio {bersaglio})")
        if dopo == bersaglio:
            stato.formato = formato
            scrivi(f"  -> formato funzionante: {etichetta}")
            return True
    return False


def _riassunto(e: dict, nome: str) -> str:
    if nome not in e:
        return f"{nome}: assente"
    attributo, valore, valori = e[nome]
    estremi = f"{valori[0]}…{valori[-1]}" if valori else "-"
    return f"{nome}: {attributo}, valore {valore}, {len(valori)} valori ({estremi})"


# -- ripristino -----------------------------------------------------------------

def _ripristina_iso(stato: Stato, iso: str) -> None:
    """Senza un formato funzionante: prova ogni formato finché l'ISO torna."""
    if leggi("isospeedvalue") == iso:
        return
    for etichetta, formato in FORMATI:
        imposta(formato, "isospeedvalue", iso)
        if leggi("isospeedvalue") == iso:
            stato.formato = formato
            scrivi(f"ISO rimesso a {iso} con {etichetta}")
            return
    scrivi(f"ISO NON rimesso a {iso}: nessun formato ha funzionato")


def _esegui_piano(stato: Stato, piano: list[tuple[str, str]]) -> None:
    for nome, valore in piano:
        def passo(nome=nome, valore=valore):
            stato_http, _ = imposta(stato.formato, nome, valore)
            scrivi(f"  {nome}={valore}: HTTP {stato_http}, riletto {leggi(nome)}")
        _sicuro(f"ripristino {nome}={valore}", passo)


def _verifica(stato: Stato) -> list[str]:
    """Confronta la lettura con gli originali (in modalità originale)."""
    mancano = []
    for nome, valore in stato.originali.items():
        letto = leggi(nome)
        if letto != valore:
            mancano.append(f"{nome} (atteso {valore}, letto {letto})")
    return mancano


def ripristina_tutto(stato: Stato) -> list[str]:
    """Riporta ogni impostazione toccata al valore originale.
    Ritorna quelle che, rilette, non tornano."""
    orig = stato.originali.get("takemode")
    iso = stato.originali.get("isospeedvalue")
    if stato.formato is None and iso is not None:
        _sicuro("ripristino ISO", lambda: _ripristina_iso(stato, iso))
    if stato.formato is None and orig is not None:
        _sicuro("ripristino modalità", lambda: cambia_modo(stato, orig)
                if leggi("takemode") != orig else None)
    if stato.formato is not None and orig is not None:
        globali = {n: v for n, v in stato.originali.items() if n != "takemode"}
        _esegui_piano(stato, piano_ripristino(orig, stato.per_modo, globali))
    return _verifica(stato)


def _riepilogo_ripristino(stato: Stato) -> None:
    try:
        mancano = ripristina_tutto(stato)
    except Exception:
        scrivi(f"errore durante il ripristino\n{traceback.format_exc()}")
        mancano = ["ripristino interrotto da un errore"]
    if mancano:
        scrivi("RIPRISTINO INCOMPLETO: " + "; ".join(mancano))
    else:
        scrivi("ripristino completo")


# -- la prova -------------------------------------------------------------------

def _prova(stato: Stato, svuota: Svuota) -> int:
    scrivi(f"play {_get('switch_cammode.cgi', mode='play')[0]}, "
           f"rec {_get('switch_cammode.cgi', mode='rec', lvqty='1024x0768')[0]}")
    svuota.apri()
    scrivi(f"startliveview porta {svuota.porta}: "
           f"{_get('exec_takemisc.cgi', com='startliveview', port=svuota.porta)[0]}")
    time.sleep(1.0)
    scrivi(f"live: {svuota.pacchetti} pacchetti nel primo secondo")

    iniziale = elenco("iniziale")
    if not iniziale:
        scrivi("FERMO: l'elenco iniziale è vuoto o illeggibile; nulla è stato modificato")
        return 1
    stato.originali = {n: iniziale[n][1] for n in TOCCATE if n in iniziale}
    scrivi(f"originali: {stato.originali}")
    orig = stato.originali.get("takemode")
    if orig is None or "isospeedvalue" not in stato.originali:
        scrivi("FERMO: modalità o ISO originali non letti; nulla è stato modificato")
        return 1
    stato.per_modo[orig] = valori_modo(iniziale)

    # 1. formato del comando
    scrivi("== formato di set_camprop ==")
    desc_iso = iniziale["isospeedvalue"]
    if desc_iso[0] != "getset":
        scrivi(f"ISO con attributo {desc_iso[0]!r} in {orig}: passo a M per provarlo")
        if not cambia_modo(stato, "M"):
            scrivi("CANCELLO: ISO non modificabile e modalità M non raggiunta")
            return 1
        desc_iso = parse_desclist(_get("get_camprop.cgi", com="desc", propname="desclist")[1]) \
            .get("isospeedvalue", ("", "", []))
        if desc_iso[0] != "getset":
            scrivi("CANCELLO: ISO non modificabile in questa modalità")
            return 1
    iso_qui = desc_iso[1]
    bersaglio = altro_valore(desc_iso[2], iso_qui, escludi=("Auto", "Low"))
    if bersaglio is None:
        scrivi("CANCELLO: nessun altro valore ISO da provare")
        return 1
    if not cerca_formato_iso(stato, bersaglio):
        scrivi("CANCELLO: nessun formato ha cambiato l'ISO")
        return 1
    stato_http, _ = imposta(stato.formato, "isospeedvalue", iso_qui)
    scrivi(f"  ISO rimesso a {iso_qui}: HTTP {stato_http}, riletto {leggi('isospeedvalue')}")

    # 2. cosa è regolabile in P, A, S, M
    scrivi("== modalità ==")
    for modo in ("P", "A", "S", "M"):
        if not cambia_modo(stato, modo):
            scrivi(f"modalità {modo} non raggiunta: la modalità non è stata visitata")
            continue
        e = elenco(modo)
        stato.per_modo.setdefault(modo, valori_modo(e))
        for nome in ESPOSIZIONE:
            scrivi("  " + _riassunto(e, nome))

    # 3. richieste che la modalità non consente
    scrivi("== richieste non consentite ==")
    for modo, nome, valore in (("S", "focalvalue", None), ("A", "shutspeedvalue", None),
                               ("A", "focalvalue", "1.0")):
        if not cambia_modo(stato, modo):
            scrivi(f"  modalità {modo} non raggiunta: {nome} non provato")
            continue
        e = parse_desclist(_get("get_camprop.cgi", com="desc", propname="desclist")[1])
        attributo, attuale, valori = e.get(nome, ("", "", []))
        if valore is None:
            valore = altro_valore(valori, attuale)
            if valore is None:
                scrivi(f"  in {modo}, {nome}: nessun altro valore ammesso (attuale {attuale}), "
                       "caso saltato: non è una prova di rifiuto")
                continue
        stato_http, corpo = imposta(stato.formato, nome, valore)
        scrivi(f"  in {modo}, {nome}={valore}: HTTP {stato_http} {_corto(corpo)}, "
               f"riletto {leggi(nome)} (prima {attuale})")

    # 4. bilanciamento e modalità di scatto
    scrivi("== bilanciamento e scatto ==")
    for nome, escludi in (("wbvalue", ()), ("drivemode", ("lowvib-selftimer", "customselftimer",
                                                      "lowvib-customselftimer",
                                                      "silent-customselftimer"))):
        valori = iniziale.get(nome, ("", "", []))[2]
        valore = altro_valore(valori, stato.originali.get(nome, ""), escludi=escludi)
        if valore is None:
            scrivi(f"  {nome}: nessun altro valore da provare")
            continue
        stato_http, corpo = imposta(stato.formato, nome, valore)
        scrivi(f"  {nome}={valore}: HTTP {stato_http} {_corto(corpo)}, riletto {leggi(nome)}")
    return 0


def main() -> int:
    try:
        ELENCHI.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    scrivi(f"Prova impostazioni {time.strftime('%Y-%m-%d %H:%M:%S')}")
    stato = Stato()
    try:
        stato.svuota = Svuota()
        return _prova(stato, stato.svuota)
    except Exception:
        scrivi("ERRORE IMPREVISTO:\n" + traceback.format_exc())
        return 1
    finally:
        scrivi("== ripristino ==")
        _riepilogo_ripristino(stato)
        _sicuro("stopliveview", lambda: scrivi(
            f"stopliveview {_get('exec_takemisc.cgi', com='stopliveview')[0]}"))
        if stato.svuota is not None:
            _sicuro("chiusura del live", stato.svuota.chiudi)
        _sicuro("ritorno in riproduzione", lambda: scrivi(
            f"ritorno in riproduzione {_get('switch_cammode.cgi', mode='play')[0]}"))
        pacchetti = stato.svuota.pacchetti if stato.svuota is not None else 0
        scrivi(f"live ricevuto in tutto: {pacchetti} pacchetti")


if __name__ == "__main__":
    sys.exit(main())
