#!/usr/bin/env python3
"""Prova delle sequenze (telecomando, pezzo E1).

Da eseguire con il PC collegato alla fotocamera, quindi senza Internet, in
una stanza illuminata e con la ghiera su A o P:

    .venv/bin/python tools/prova_sequenza.py

Misura quanto passa fra due scatti, se la scheda si può leggere in modalità
scatto, se il live si può spegnere e riaccendere fra gli scatti, e se la
fotocamera risponde ancora dopo cinque minuti ferma. Non cambia nessuna
impostazione. Scatta circa 18 foto. Dura circa 13 minuti. Alla fine la
fotocamera torna in riproduzione, anche se qualcosa va storto. Scrive il
referto in diag-downloads/prova_sequenza.txt.
"""
from __future__ import annotations

import re
import socket
import statistics
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
TIMEOUT_SCATTO = (3, 60)
QUALITA = "1024x0768"
CARTELLA = Path("diag-downloads")
REFERTO = CARTELLA / "prova_sequenza.txt"
INTERVALLI = (2, 3, 5, 8, 10, 15, 20, 30, 45, 60, 120, 180, 300, 600, 900, 1800)
SCATTI_CON_ANTEPRIMA = 10
SCATTI_SENZA = 5
ATTESA_LUNGA = 300.0       # secondi fermi prima di riprovare a scattare
ATTESA_LIVE = 10.0         # secondi concessi al live per ripartire
_ERRORCODE = re.compile(rb"<errorcode>\s*(\d+)\s*</errorcode>")

_righe: list[str] = []


# -- funzioni pure ------------------------------------------------------------

def porta_sorgente(porta: int) -> int:
    """La fotocamera spedisce il live dalla porta di ricezione con i byte scambiati."""
    return ((porta & 0xFF) << 8) | (porta >> 8)


def voci(testo: str) -> list[tuple[str, str]]:
    """(cartella, nome) per ogni riga di una risposta di get_imglist."""
    out: list[tuple[str, str]] = []
    for riga in testo.splitlines():
        campi = riga.strip().split(",")
        if len(campi) >= 6 and campi[0].startswith("/"):
            out.append((campi[0], campi[1]))
    return out


def nuovi(prima: list[str], dopo: list[str]) -> list[str]:
    """I nomi che prima non c'erano, nell'ordine della scheda."""
    visti = set(prima)
    return [nome for nome in dopo if nome not in visti]


def per_estensione(nomi: list[str]) -> str:
    conti: dict[str, int] = {}
    for nome in nomi:
        estensione = nome.rsplit(".", 1)[-1].upper() if "." in nome else "?"
        conti[estensione] = conti.get(estensione, 0) + 1
    return ", ".join(f"{e} {n}" for e, n in sorted(conti.items())) or "nessuno"


def posa_lunga(tempo: str) -> bool:
    """I tempi in secondi (con le virgolette) e le pose Live: troppo lenti
    per misurare il ritmo."""
    return tempo.endswith('"') or tempo.startswith("live")


def autoscatto(scatto: str) -> bool:
    return "selftimer" in scatto


def ha_scattato(corpo: bytes) -> bool:
    try:
        return (ElementTree.fromstring(corpo).findtext("take") or "").strip() == "ok"
    except ElementTree.ParseError:
        return False


def codice_errore(corpo: bytes) -> str | None:
    trovato = _ERRORCODE.search(corpo or b"")
    return trovato.group(1).decode() if trovato else None


def mediana(valori: list[float]) -> float | None:
    return statistics.median(valori) if valori else None


def intervallo_minimo(ciclo: float) -> int:
    """Il primo intervallo dell'elenco che lascia un secondo di margine al ciclo."""
    for valore in INTERVALLI:
        if valore >= ciclo + 1.0:
            return valore
    return INTERVALLI[-1]


@dataclass
class Scatto:
    ok: bool
    stato: str                 # stato HTTP di starttake, o il nome dell'errore
    corpo: bytes
    start: float               # secondi per la risposta a starttake
    stop: float                # secondi per la risposta a stoptake
    anteprima: float | None    # secondi per getrecview, se chiesta
    byte: int                  # dimensione dell'anteprima

    @property
    def ciclo(self) -> float:
        return self.start + self.stop + (self.anteprima or 0.0)


@dataclass
class Esiti:
    con_anteprima: list[float] = field(default_factory=list)   # cicli, in secondi
    senza: list[float] = field(default_factory=list)
    stato_elenco_rec: str | None = None      # stato HTTP dell'elenco in modalità scatto
    nuovi_in_rec: list[str] = field(default_factory=list)
    scatto_a_live_spento: str | None = None  # «rifiutato (…)» oppure «riuscito»
    riacceso: bool | None = None
    tempo_riaccensione: float | None = None
    scatto_dopo_riaccensione: bool | None = None
    modo_invariato: bool | None = None
    lungo_acceso: str | None = None          # scatto dopo l'attesa con il live acceso
    live_dopo_attesa: bool | None = None
    lungo_spento: str | None = None          # scatto dopo l'attesa con il live spento
    foto: int = 0
    nuovi_sulla_scheda: list[str] | None = None


def _si_no(valore: bool | None) -> str:
    return "non provato" if valore is None else ("sì" if valore else "no")


def riassunto(e: Esiti) -> list[str]:
    """Le righe finali del referto, una per ogni punto del cancello."""
    righe = ["== ESITO =="]
    con, senza = mediana(e.con_anteprima), mediana(e.senza)
    if con is None:
        righe.append("1. ritmo: non misurato")
    else:
        guadagno = con - senza if senza is not None else 0.0
        toglie = guadagno > 0.5
        utile = senza if toglie and senza is not None else con
        righe.append(
            f"1. ritmo: ciclo mediano {con:.1f} s con anteprima "
            f"(min {min(e.con_anteprima):.1f}, max {max(e.con_anteprima):.1f})"
            + (f", {senza:.1f} s senza" if senza is not None else "")
            + f"; intervallo minimo proposto {intervallo_minimo(utile)} s; "
            + ("sotto i 5 s l'anteprima non si chiede" if toglie
               else "l'anteprima si chiede sempre")
            + f" (guadagno {guadagno:.1f} s)")
    if e.stato_elenco_rec is None:
        righe.append("2. elenco in modalità scatto: non provato")
    elif e.stato_elenco_rec != "200":
        righe.append(f"2. elenco in modalità scatto: non risponde (HTTP {e.stato_elenco_rec})")
    elif not e.nuovi_in_rec:
        righe.append("2. elenco in modalità scatto: risponde ma non vede le foto nuove")
    else:
        righe.append(f"2. elenco in modalità scatto: risponde, {len(e.nuovi_in_rec)} file nuovi "
                     f"dopo gli scatti ({per_estensione(e.nuovi_in_rec)})")
    if e.scatto_a_live_spento is None:
        righe.append("3. live spento fra gli scatti: non provato")
    else:
        if e.riacceso is None:
            riaccensione = "non provato"
        elif e.riacceso:
            riaccensione = f"sì in {e.tempo_riaccensione:.1f} s"
        else:
            riaccensione = "no, serve il rientro"
        righe.append(f"3. scatto a live spento: {e.scatto_a_live_spento}; "
                     f"live riacceso senza rientro: {riaccensione}; "
                     f"scatto dopo: {_si_no(e.scatto_dopo_riaccensione)}; "
                     f"modalità invariata: {_si_no(e.modo_invariato)}")
    acceso = e.lungo_acceso or "non provato"
    if e.live_dopo_attesa is not None:
        acceso += f" (live ancora attivo: {_si_no(e.live_dopo_attesa)})"
    righe.append(f"4. dopo l'attesa con il live acceso: {acceso}; "
                 f"con il live spento: {e.lungo_spento or 'non provato'}")
    scheda = ("non letti" if e.nuovi_sulla_scheda is None else
              f"{len(e.nuovi_sulla_scheda)} ({per_estensione(e.nuovi_sulla_scheda)})")
    righe.append(f"foto scattate dalla prova: {e.foto}; file nuovi sulla scheda: {scheda}")
    return righe


# -- scrittura del referto e rete -------------------------------------------------

def scrivi(testo: str) -> None:
    _righe.append(testo)
    try:
        print(testo, flush=True)
    except Exception:
        pass
    try:
        REFERTO.write_text("\n".join(_righe) + "\n")
    except Exception:
        pass


def _sicuro(descrizione: str, passo: Callable[[], object]) -> object:
    """Esegue un passo di chiusura: un errore non ferma gli altri."""
    try:
        return passo()
    except Exception:
        scrivi(f"{descrizione}: errore\n{traceback.format_exc()}")
        return None


def _get(path: str, attesa: tuple[float, float] = TIMEOUT, **q) -> tuple[str, bytes]:
    try:
        r = requests.get(f"{BASE}/{path}", headers=UA, params=q, timeout=attesa)
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


def elenco_file() -> tuple[str, list[str], float]:
    """Stato HTTP, nomi dei file dell'ultima cartella di /DCIM, secondi impiegati."""
    inizio = time.monotonic()
    stato, corpo = _get("get_imglist.cgi", DIR="/DCIM")
    if stato != "200":
        return stato, [], time.monotonic() - inizio
    cartelle = sorted(nome for _cartella, nome in voci(corpo.decode("utf-8", "replace")))
    if not cartelle:
        return "200 senza cartelle", [], time.monotonic() - inizio
    stato, corpo = _get("get_imglist.cgi", DIR=f"/DCIM/{cartelle[-1]}")
    nomi = [nome for _cartella, nome in voci(corpo.decode("utf-8", "replace"))]
    return stato, nomi, time.monotonic() - inizio


def scatta(con_anteprima: bool, attesa: tuple[float, float] = TIMEOUT_SCATTO) -> Scatto:
    """Uno scatto come lo fa l'app: starttake, stoptake e, se chiesta, l'anteprima."""
    t0 = time.monotonic()
    stato, corpo = _get("exec_takemotion.cgi", attesa, com="starttake")
    t1 = time.monotonic()
    _get("exec_takemotion.cgi", com="stoptake")
    t2 = time.monotonic()
    anteprima: float | None = None
    byte = 0
    if con_anteprima and stato == "200":
        _stato, immagine = _get("exec_takemisc.cgi", com="getrecview")
        anteprima, byte = time.monotonic() - t2, len(immagine)
    return Scatto(stato == "200" and ha_scattato(corpo), stato, corpo, t1 - t0, t2 - t1,
                  anteprima, byte)


def _riga(etichetta: str, s: Scatto) -> str:
    anteprima = (f", anteprima {s.anteprima:.2f}s ({s.byte} byte)"
                 if s.anteprima is not None else "")
    esito = "ok" if s.ok else f"NON RIUSCITO: {_corto(s.corpo)}"
    return (f"  {etichetta}: starttake {s.stato} in {s.start:.2f}s, stoptake {s.stop:.2f}s"
            f"{anteprima}, ciclo {s.ciclo:.2f}s, {esito}")


def _esito_scatto(s: Scatto) -> str:
    if s.ok:
        return f"scatto ok in {s.start:.1f} s"
    codice = codice_errore(s.corpo)
    return f"scatto non riuscito ({s.stato}" + (f", errorcode {codice})" if codice else ")")


class Svuota:
    """Riceve e butta il live, contando i pacchetti."""

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
        """Il datagramma che apre il firewall al live."""
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


def attendi_live(svuota: Svuota) -> float | None:
    """Secondi fino al primo pacchetto nuovo del live, o None se non arriva
    entro ATTESA_LIVE."""
    entro = ATTESA_LIVE
    base = svuota.pacchetti
    inizio = time.monotonic()
    while time.monotonic() - inizio < entro:
        if svuota.pacchetti > base:
            return time.monotonic() - inizio
        time.sleep(0.02)
    return None


def attesa_lunga(svuota: Svuota, secondi: float, etichetta: str) -> int:
    """Aspetta senza mandare richieste. Ritorna i pacchetti dell'ultimo tratto."""
    fine = time.monotonic() + secondi
    ultimo = 0
    while (resto := fine - time.monotonic()) > 0:
        base = svuota.pacchetti
        time.sleep(min(60.0, resto))
        ultimo = svuota.pacchetti - base
        scrivi(f"  {etichetta}: mancano {max(0.0, fine - time.monotonic()) / 60:.0f} min, "
               f"{ultimo} pacchetti nell'ultimo tratto")
    return ultimo


def accendi_live(svuota: Svuota) -> tuple[bool, float | None]:
    """startliveview senza rientrare in rec. Ritorna (ripartito, secondi)."""
    svuota.apri()
    inizio = time.monotonic()
    stato, _ = _get("exec_takemisc.cgi", com="startliveview", port=svuota.porta)
    arrivato = attendi_live(svuota)
    secondi = time.monotonic() - inizio if arrivato is not None else None
    scrivi(f"  startliveview {stato}: " + (f"primo pacchetto dopo {secondi:.2f}s"
                                           if secondi is not None else
                                           f"nessun pacchetto in {ATTESA_LIVE:.0f}s"))
    return arrivato is not None, secondi


def rientra(svuota: Svuota) -> bool:
    """Il rientro completo dell'app: play, rec, live."""
    scrivi(f"  rientro: play {_get('switch_cammode.cgi', mode='play')[0]}, "
           f"rec {_get('switch_cammode.cgi', mode='rec', lvqty=QUALITA)[0]}")
    return accendi_live(svuota)[0]


# -- la prova -------------------------------------------------------------------

def _prova(e: Esiti, svuota: Svuota) -> int:
    scrivi(f"rec {_get('switch_cammode.cgi', mode='rec', lvqty=QUALITA)[0]}")
    svuota.apri()
    scrivi(f"startliveview porta {svuota.porta}: "
           f"{_get('exec_takemisc.cgi', com='startliveview', port=svuota.porta)[0]}")
    time.sleep(1.0)
    scrivi(f"live: {svuota.pacchetti} pacchetti nel primo secondo")
    if svuota.pacchetti == 0:
        scrivi("FERMO: il live non arriva; nessuna foto scattata")
        return 1

    modo, tempo, scatto = leggi("takemode"), leggi("shutspeedvalue"), leggi("drivemode")
    scrivi(f"modalità {modo}, tempo {tempo}, scatto {scatto}, "
           f"riduzione rumore {leggi('noisereduction')}")
    if autoscatto(scatto):
        scrivi("FERMO: la fotocamera è in autoscatto; scegli lo scatto singolo e rilancia")
        return 1
    if modo in ("M", "S") and posa_lunga(tempo):
        scrivi(f"FERMO: il tempo {tempo} è troppo lungo per misurare il ritmo; "
               "metti la ghiera su A o P, o scegli un tempo rapido, e rilancia")
        return 1

    scrivi("== 2a. elenco in modalità scatto, prima degli scatti ==")
    stato_prima, prima_rec, secondi = elenco_file()
    scrivi(f"  HTTP {stato_prima}, {len(prima_rec)} file, {secondi:.2f}s")

    scrivi("== 1. ritmo ==")
    for i in range(SCATTI_CON_ANTEPRIMA):
        s = scatta(True)
        scrivi(_riga(f"con anteprima {i + 1}", s))
        if s.ok:
            e.foto += 1
            e.con_anteprima.append(s.ciclo)
    for i in range(SCATTI_SENZA):
        s = scatta(False)
        scrivi(_riga(f"senza anteprima {i + 1}", s))
        if s.ok:
            e.foto += 1
            e.senza.append(s.ciclo)
    if e.foto == 0:
        scrivi("FERMO: nessuno scatto riuscito")
        return 1

    scrivi("== 2b. elenco in modalità scatto, dopo gli scatti ==")
    stato_dopo, dopo_rec, secondi = elenco_file()
    scrivi(f"  HTTP {stato_dopo}, {len(dopo_rec)} file, {secondi:.2f}s")
    e.stato_elenco_rec = stato_dopo if stato_prima == "200" else stato_prima
    if stato_prima == "200" and stato_dopo == "200":
        e.nuovi_in_rec = nuovi(prima_rec, dopo_rec)
        scrivi(f"  file nuovi visti: {len(e.nuovi_in_rec)} ({per_estensione(e.nuovi_in_rec)})")
    base = svuota.pacchetti
    time.sleep(1.0)
    scrivi(f"  live dopo l'elenco: {svuota.pacchetti - base} pacchetti in un secondo")

    scrivi("== 3. live spento fra gli scatti ==")
    scrivi(f"  stopliveview {_get('exec_takemisc.cgi', com='stopliveview')[0]}")
    time.sleep(1.0)
    base = svuota.pacchetti
    time.sleep(1.0)
    scrivi("  live fermo: " + ("sì" if svuota.pacchetti == base
                                else "no, arrivano ancora pacchetti"))
    s = scatta(False, (3, 20))
    scrivi(_riga("scatto a live spento", s))
    if s.ok:
        e.foto += 1
        e.scatto_a_live_spento = "riuscito"
    else:
        codice = codice_errore(s.corpo)
        e.scatto_a_live_spento = (f"rifiutato (HTTP {s.stato}"
                                  + (f", errorcode {codice})" if codice else ")"))
    e.riacceso, e.tempo_riaccensione = accendi_live(svuota)
    if not e.riacceso and not rientra(svuota):
        scrivi("FERMO: il live non riparte nemmeno con il rientro")
        return 1
    s = scatta(True)
    scrivi(_riga("scatto dopo la riaccensione", s))
    e.foto += s.ok
    e.scatto_dopo_riaccensione = s.ok
    modo_dopo = leggi("takemode")
    e.modo_invariato = modo_dopo == modo
    scrivi(f"  modalità dopo la riaccensione: {modo_dopo} (prima {modo})")

    scrivi("== 4. intervalli lunghi ==")
    scrivi(f"  attesa di {ATTESA_LUNGA / 60:.0f} minuti con il live acceso: "
           "non toccare la fotocamera")
    ultimo = attesa_lunga(svuota, ATTESA_LUNGA, "live acceso")
    e.live_dopo_attesa = ultimo > 0
    s = scatta(True)
    scrivi(_riga("scatto dopo l'attesa con il live acceso", s))
    e.foto += s.ok
    e.lungo_acceso = _esito_scatto(s)
    if not e.riacceso:
        scrivi("  attesa con il live spento saltata: il live non si riaccende senza rientro")
        return 0
    scrivi(f"  stopliveview {_get('exec_takemisc.cgi', com='stopliveview')[0]}")
    scrivi(f"  attesa di {ATTESA_LUNGA / 60:.0f} minuti con il live spento: "
           "non toccare la fotocamera")
    attesa_lunga(svuota, ATTESA_LUNGA, "live spento")
    ripartito, secondi = accendi_live(svuota)
    if not ripartito:
        e.lungo_spento = "il live non riparte senza rientro"
        if not rientra(svuota):
            e.lungo_spento = "la fotocamera non risponde più"
            return 0
    s = scatta(True)
    scrivi(_riga("scatto dopo l'attesa con il live spento", s))
    e.foto += s.ok
    if ripartito:
        e.lungo_spento = f"live riacceso in {secondi:.1f} s, {_esito_scatto(s)}"
    else:
        e.lungo_spento += f"; dopo il rientro, {_esito_scatto(s)}"
    return 0


def _chiusura(e: Esiti, svuota: Svuota | None, prima_play: list[str] | None) -> None:
    _sicuro("stopliveview", lambda: scrivi(
        f"stopliveview {_get('exec_takemisc.cgi', com='stopliveview')[0]}"))
    if svuota is not None:
        _sicuro("chiusura del live", svuota.chiudi)
    _sicuro("ritorno in riproduzione", lambda: scrivi(
        f"ritorno in riproduzione {_get('switch_cammode.cgi', mode='play')[0]}"))

    def scheda() -> None:
        stato, dopo, secondi = elenco_file()
        scrivi(f"elenco in riproduzione alla fine: HTTP {stato}, {len(dopo)} file, {secondi:.2f}s")
        if stato == "200" and prima_play is not None:
            e.nuovi_sulla_scheda = nuovi(prima_play, dopo)
    _sicuro("elenco finale", scheda)
    for riga in riassunto(e):
        scrivi(riga)


def main() -> int:
    try:
        CARTELLA.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    scrivi(f"Prova sequenza {time.strftime('%Y-%m-%d %H:%M:%S')}")
    e = Esiti()
    svuota: Svuota | None = None
    prima_play: list[str] | None = None
    try:
        scrivi(f"play {_get('switch_cammode.cgi', mode='play')[0]}")
        stato, nomi, secondi = elenco_file()
        scrivi(f"elenco in riproduzione all'inizio: HTTP {stato}, {len(nomi)} file, {secondi:.2f}s")
        if stato != "200":
            scrivi("FERMO: la fotocamera non risponde; nessuna foto scattata")
            return 1
        prima_play = nomi
        svuota = Svuota()
        return _prova(e, svuota)
    except Exception:
        scrivi("ERRORE IMPREVISTO:\n" + traceback.format_exc())
        return 1
    finally:
        scrivi("== chiusura ==")
        _chiusura(e, svuota, prima_play)


if __name__ == "__main__":
    sys.exit(main())
