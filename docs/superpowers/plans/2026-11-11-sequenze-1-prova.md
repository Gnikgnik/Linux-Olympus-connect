# Sequenze, parte 1: prova sul campo — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** misurare sulla fotocamera vera il tempo fra due scatti, se la scheda si legge in modalità scatto, se il live si spegne e si riaccende fra gli scatti senza rientrare in rec, e se la fotocamera risponde dopo cinque minuti ferma; ricavarne le decisioni del cancello per la parte 2.

**Architecture:** uno strumento `tools/prova_sequenza.py` (nel repository, come `tools/prova_impostazioni.py`) che gira **senza Internet** mentre il PC è collegato alla fotocamera e scrive il referto in `diag-downloads/prova_sequenza.txt`. Le funzioni che non toccano la fotocamera hanno test, e la prova intera gira nei test contro una fotocamera finta (richieste HTTP simulate, live simulato, attese accorciate). È il cancello della specifica `docs/superpowers/specs/2026-11-11-sequenze-design.md`.

**Tech Stack:** Python 3.12+, requests, `socket`/`threading`, `xml.etree`; pytest con requests-mock.

## Global Constraints

- I test si lanciano con `.venv/bin/python -m pytest` (`pythonpath = ["."]` già configurato; `tools` si importa come `from tools import prova_sequenza`).
- Nessuna nuova dipendenza. `exec_erase.cgi` non va mai invocato.
- La prova **non cambia nessuna impostazione** della fotocamera. Scatta circa 18 foto e non salva sul PC nessuna immagine (dell'anteprima annota solo la dimensione).
- Alla fine, in un blocco `finally`, il live si ferma e la fotocamera torna in riproduzione, anche dopo un errore o un'interruzione.
- Il datagramma di apertura del firewall va a `192.168.0.10` sulla **porta di ricezione con i byte scambiati** (`((p & 0xFF) << 8) | (p >> 8)`), come l'app, e si rimanda prima di ogni `startliveview`.
- Lo strumento non lo esegue mai un agente: lo esegue l'utente, collegato alla fotocamera e quindi senza Internet.
- Nessun numero di serie e nessuna immagine in un commit. `diag-downloads/` è ignorata da git.
- Ogni messaggio di commit termina con la riga `Co-Authored-By: Claude <noreply@anthropic.com>`.

---

### Task 1: Lo strumento di prova

**Files:**
- Create: `tools/prova_sequenza.py`
- Test: `tests/test_prova_sequenza.py`

**Interfaces:**
- Consumes: niente dal pacchetto `olympus_connect` (lo strumento è autonomo, come `tools/prova_impostazioni.py`). Fixture esistenti: `tests/fixtures/imglist_dcim.csv`, `tests/fixtures/imglist_dcim_100olymp.csv` (1161 file).
- Produces: il referto `diag-downloads/prova_sequenza.txt`, che finisce con la sezione `== ESITO ==`: una riga per ognuno dei quattro punti del cancello (`1. ritmo`, `2. elenco in modalità scatto`, `3. …live spento…`, `4. dopo l'attesa…`) e la riga `foto scattate dalla prova`. Il Task 2 legge quelle righe.

**Nota:** una bozza dei due file esiste già nell'albero di lavoro, scritta e fatta girare mentre si stendeva questo piano (21 test verdi, serie completa a 611). Se i file ci sono e coincidono con il codice qui sotto, i passi 1–3 si riducono a controllarlo; i passi 4–5 valgono comunque.

- [ ] **Step 1: Scrivi i test**

`tests/test_prova_sequenza.py`:

```python
"""Le funzioni dello strumento di prova delle sequenze che non toccano la fotocamera."""
from pathlib import Path

import pytest

from tools import prova_sequenza as ps
from tools.prova_sequenza import (
    Esiti, Scatto, autoscatto, codice_errore, ha_scattato, intervallo_minimo, mediana,
    nuovi, per_estensione, porta_sorgente, posa_lunga, riassunto, voci,
)

FIXTURES = Path(__file__).parent / "fixtures"
OK = (b'<?xml version="1.0"?><response><take>ok</take><affocus>ok</affocus>'
      b'<afframepoint>0296x0224</afframepoint><afframesize>0046x0032</afframesize></response>')
RIFIUTO = b'<?xml version="1.0"?><result>error</result><errorcode>1004</errorcode>'


def test_reads_the_real_listings():
    assert voci((FIXTURES / "imglist_dcim.csv").read_text()) == [("/DCIM", "100OLYMP")]
    file = voci((FIXTURES / "imglist_dcim_100olymp.csv").read_text())
    assert file[0] == ("/DCIM/100OLYMP", "P2100001.ORF")
    assert len(file) == 1161


def test_garbage_listing_is_empty():
    assert voci("VER_100\n\nnon una riga\n") == []


def test_new_files_keep_the_card_order():
    assert nuovi(["a.JPG", "a.ORF"], ["a.JPG", "a.ORF", "b.JPG", "b.ORF"]) == ["b.JPG", "b.ORF"]
    assert nuovi([], []) == []


def test_extension_summary():
    assert per_estensione(["b.JPG", "b.ORF", "c.jpg"]) == "JPG 2, ORF 1"
    assert per_estensione([]) == "nessuno"


def test_slow_shutters_and_self_timers_are_recognised():
    assert posa_lunga('60"') and posa_lunga('1.3"') and posa_lunga("livebulb")
    assert not posa_lunga("250") and not posa_lunga("1.3")
    assert autoscatto("lowvib-customselftimer") and not autoscatto("lowvib-normal")


def test_reads_the_shot_response():
    assert ha_scattato(OK)
    assert not ha_scattato(b"<response><take>ng</take></response>")
    assert not ha_scattato(b"") and not ha_scattato(b"non xml")
    assert codice_errore(RIFIUTO) == "1004"
    assert codice_errore(OK) is None


def test_median_and_minimum_interval():
    assert mediana([3.0, 1.0, 2.0]) == 2.0 and mediana([]) is None
    assert intervallo_minimo(0.6) == 2      # 0,6 + 1 di margine → 2
    assert intervallo_minimo(1.0) == 2
    assert intervallo_minimo(1.2) == 3
    assert intervallo_minimo(4.5) == 8
    assert intervallo_minimo(5000) == 1800


def test_byte_swapped_port():
    assert porta_sorgente(40000) == 16540


def test_shot_cycle_adds_the_requests():
    assert Scatto(True, "200", OK, 0.5, 0.02, 0.1, 300000).ciclo == pytest.approx(0.62)
    assert Scatto(True, "200", OK, 0.5, 0.02, None, 0).ciclo == pytest.approx(0.52)


def test_summary_of_a_complete_run():
    e = Esiti(con_anteprima=[1.8, 1.6, 2.3], senza=[1.5, 1.4, 1.6],
              stato_elenco_rec="200", nuovi_in_rec=["a.JPG", "a.ORF", "b.JPG", "b.ORF"],
              scatto_a_live_spento="rifiutato (HTTP 520, errorcode 1004)",
              riacceso=True, tempo_riaccensione=0.84, scatto_dopo_riaccensione=True,
              modo_invariato=True, lungo_acceso="scatto ok in 0.4 s", live_dopo_attesa=True,
              lungo_spento="live riacceso in 0.9 s, scatto ok in 0.5 s",
              foto=19, nuovi_sulla_scheda=["a.JPG", "a.ORF"])
    assert riassunto(e) == [
        "== ESITO ==",
        "1. ritmo: ciclo mediano 1.8 s con anteprima (min 1.6, max 2.3), 1.5 s senza; "
        "intervallo minimo proposto 3 s; l'anteprima si chiede sempre (guadagno 0.3 s)",
        "2. elenco in modalità scatto: risponde, 4 file nuovi dopo gli scatti (JPG 2, ORF 2)",
        "3. scatto a live spento: rifiutato (HTTP 520, errorcode 1004); "
        "live riacceso senza rientro: sì in 0.8 s; scatto dopo: sì; modalità invariata: sì",
        "4. dopo l'attesa con il live acceso: scatto ok in 0.4 s (live ancora attivo: sì); "
        "con il live spento: live riacceso in 0.9 s, scatto ok in 0.5 s",
        "foto scattate dalla prova: 19; file nuovi sulla scheda: 2 (JPG 1, ORF 1)",
    ]


def test_summary_drops_the_review_when_it_costs_more_than_half_a_second():
    riga = riassunto(Esiti(con_anteprima=[2.4, 2.4], senza=[1.6, 1.6]))[1]
    assert "intervallo minimo proposto 3 s" in riga          # da 1,6 s, non da 2,4
    assert "sotto i 5 s l'anteprima non si chiede (guadagno 0.8 s)" in riga


def test_summary_of_a_run_that_stopped_early():
    assert riassunto(Esiti()) == [
        "== ESITO ==",
        "1. ritmo: non misurato",
        "2. elenco in modalità scatto: non provato",
        "3. live spento fra gli scatti: non provato",
        "4. dopo l'attesa con il live acceso: non provato; con il live spento: non provato",
        "foto scattate dalla prova: 0; file nuovi sulla scheda: non letti",
    ]


def test_summary_of_the_unlucky_branches():
    righe = riassunto(Esiti(stato_elenco_rec="520", scatto_a_live_spento="riuscito",
                            riacceso=False, scatto_dopo_riaccensione=True, modo_invariato=False))
    assert righe[2] == "2. elenco in modalità scatto: non risponde (HTTP 520)"
    assert righe[3] == ("3. scatto a live spento: riuscito; live riacceso senza rientro: "
                        "no, serve il rientro; scatto dopo: sì; modalità invariata: no")
    assert riassunto(Esiti(stato_elenco_rec="200"))[2] == \
        "2. elenco in modalità scatto: risponde ma non vede le foto nuove"


def test_a_shot_sends_start_stop_and_review(requests_mock):
    requests_mock.get(f"{ps.BASE}/exec_takemotion.cgi", content=OK)
    requests_mock.get(f"{ps.BASE}/exec_takemisc.cgi", content=b"\xff\xd8jpeg")
    s = ps.scatta(True)
    assert s.ok and s.stato == "200" and s.byte == 6 and s.anteprima is not None
    storia = requests_mock.request_history
    assert [r.qs["com"][0] for r in storia] == ["starttake", "stoptake", "getrecview"]
    assert storia[0].timeout == ps.TIMEOUT_SCATTO and storia[1].timeout == ps.TIMEOUT


def test_a_refused_shot_keeps_its_code_and_skips_the_review(requests_mock):
    requests_mock.get(f"{ps.BASE}/exec_takemotion.cgi", status_code=520, content=RIFIUTO)
    s = ps.scatta(True)
    assert not s.ok and s.stato == "520" and codice_errore(s.corpo) == "1004"
    assert s.anteprima is None
    assert [r.qs["com"][0] for r in requests_mock.request_history] == ["starttake", "stoptake"]


def test_listing_reads_the_last_folder(requests_mock):
    requests_mock.get(f"{ps.BASE}/get_imglist.cgi?DIR=%2FDCIM",
                      text="VER_100\n/DCIM,100OLYMP,0,16,23714,17744\n"
                           "/DCIM,101OLYMP,0,16,23714,17744\n")
    requests_mock.get(f"{ps.BASE}/get_imglist.cgi?DIR=%2FDCIM%2F101OLYMP",
                      text="VER_100\n/DCIM/101OLYMP,PB110001.JPG,5,0,1,1\n"
                           "/DCIM/101OLYMP,PB110001.ORF,9,0,1,1\n")
    stato, nomi, secondi = ps.elenco_file()
    assert stato == "200" and nomi == ["PB110001.JPG", "PB110001.ORF"] and secondi >= 0


def test_listing_reports_a_refusal(requests_mock):
    requests_mock.get(f"{ps.BASE}/get_imglist.cgi", status_code=520)
    assert ps.elenco_file()[:2] == ("520", [])


# -- la prova intera contro una fotocamera finta ---------------------------------

class _Finta:
    """Una fotocamera finta: risponde alle richieste e tiene lo stato del live."""

    def __init__(self, tempo="250", modo="A", riaccende_senza_rientro=True):
        self.tempo, self.modo = tempo, modo
        self.riaccende_senza_rientro = riaccende_senza_rientro
        self.live = False
        self.appena_rientrata = False
        self.file = ["P0000001.JPG", "P0000001.ORF"]
        self.richieste: list[str] = []

    def rispondi(self, request, context):
        q = {k: v[0] for k, v in request.qs.items()}
        nome = request.path.rsplit("/", 1)[-1]
        self.richieste.append(nome + ":" + (q.get("com") or q.get("mode") or q.get("dir") or ""))
        if nome == "switch_cammode.cgi":
            self.live = False
            self.appena_rientrata = q["mode"] == "rec"
            return b""
        if nome == "get_imglist.cgi":
            if q["dir"] == "/dcim":
                return b"VER_100\n/DCIM,100OLYMP,0,16,1,1\n"
            return ("VER_100\n" + "".join(f"/DCIM/100OLYMP,{n},5,0,1,1\n" for n in self.file)).encode()
        if nome == "get_camprop.cgi":
            valore = {"takemode": self.modo, "shutspeedvalue": self.tempo,
                      "drivemode": "lowvib-normal", "noisereduction": "auto"}[q["propname"]]
            return f'<?xml version="1.0"?><get><value>{valore}</value></get>'.encode()
        if nome == "exec_takemisc.cgi":
            if q["com"] == "startliveview":
                self.live = self.riaccende_senza_rientro or self.appena_rientrata
                self.appena_rientrata = False
            elif q["com"] == "stopliveview":
                self.live = False
            elif q["com"] == "getrecview":
                return b"\xff\xd8anteprima"
            return b""
        if q["com"] == "starttake":
            if not self.live:
                context.status_code = 520
                return RIFIUTO
            n = len(self.file) // 2 + 1
            self.file += [f"P{n:07d}.JPG", f"P{n:07d}.ORF"]
            return OK
        return b""


@pytest.fixture
def finta(requests_mock, monkeypatch, tmp_path):
    camera = _Finta()

    class Svuota:
        porta = 40000

        def __init__(self):
            self._n = 0

        @property
        def pacchetti(self):
            if camera.live:
                self._n += 1
            return self._n

        def apri(self):
            pass

        def chiudi(self):
            pass

    import re
    import time
    requests_mock.get(re.compile(re.escape(ps.BASE) + "/.*"), content=camera.rispondi)
    vero = time.sleep
    monkeypatch.setattr(ps.time, "sleep", lambda s: vero(min(s, 0.002)))
    monkeypatch.setattr(ps, "Svuota", Svuota)
    monkeypatch.setattr(ps, "ATTESA_LUNGA", 0.01)
    monkeypatch.setattr(ps, "ATTESA_LIVE", 0.05)
    monkeypatch.setattr(ps, "CARTELLA", tmp_path)
    monkeypatch.setattr(ps, "REFERTO", tmp_path / "prova_sequenza.txt")
    monkeypatch.setattr(ps, "_righe", [])
    return camera


def test_a_whole_run_against_a_fake_camera(finta, tmp_path):
    assert ps.main() == 0
    referto = (tmp_path / "prova_sequenza.txt").read_text()
    assert "2. elenco in modalità scatto: risponde, 30 file nuovi dopo gli scatti (JPG 15, ORF 15)" in referto
    assert ("3. scatto a live spento: rifiutato (HTTP 520, errorcode 1004); "
            "live riacceso senza rientro: sì in ") in referto
    assert "scatto dopo: sì; modalità invariata: sì" in referto
    assert "(live ancora attivo: sì); con il live spento: live riacceso in " in referto
    assert "foto scattate dalla prova: 18; file nuovi sulla scheda: 36 (JPG 18, ORF 18)" in referto
    assert "FERMO" not in referto and "ERRORE" not in referto
    # alla fine: live spento, riproduzione, elenco
    assert finta.richieste[-4:] == ["exec_takemisc.cgi:stopliveview", "switch_cammode.cgi:play",
                                    "get_imglist.cgi:/dcim", "get_imglist.cgi:/dcim/100olymp"]
    assert not finta.live


def test_a_camera_that_needs_the_full_reentry(finta, tmp_path):
    finta.riaccende_senza_rientro = False
    assert ps.main() == 0
    referto = (tmp_path / "prova_sequenza.txt").read_text()
    assert "live riacceso senza rientro: no, serve il rientro; scatto dopo: sì" in referto
    assert "attesa con il live spento saltata" in referto
    assert "con il live spento: non provato" in referto
    assert "foto scattate dalla prova: 17;" in referto
    assert finta.richieste[-3] == "switch_cammode.cgi:play"


def test_a_slow_shutter_stops_before_any_shot(finta, tmp_path):
    finta.modo, finta.tempo = "M", '60"'
    assert ps.main() == 1
    referto = (tmp_path / "prova_sequenza.txt").read_text()
    assert 'FERMO: il tempo 60" è troppo lungo' in referto
    assert "foto scattate dalla prova: 0; file nuovi sulla scheda: 0 (nessuno)" in referto
    assert "exec_takemotion.cgi:starttake" not in finta.richieste
    assert "switch_cammode.cgi:play" in finta.richieste[-3:]


def test_an_unreachable_camera_stops_at_once(requests_mock, monkeypatch, tmp_path):
    import re

    import requests
    requests_mock.get(re.compile(re.escape(ps.BASE) + "/.*"), exc=requests.ConnectTimeout)
    monkeypatch.setattr(ps, "CARTELLA", tmp_path)
    monkeypatch.setattr(ps, "REFERTO", tmp_path / "prova_sequenza.txt")
    monkeypatch.setattr(ps, "_righe", [])
    assert ps.main() == 1
    referto = (tmp_path / "prova_sequenza.txt").read_text()
    assert "FERMO: la fotocamera non risponde; nessuna foto scattata" in referto
    assert "1. ritmo: non misurato" in referto
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest -q tests/test_prova_sequenza.py`
Expected: errore di raccolta, `ModuleNotFoundError: No module named 'tools.prova_sequenza'` (se la bozza dello strumento non c'è ancora).

- [ ] **Step 3: Scrivi lo strumento**

`tools/prova_sequenza.py`:

```python
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
```

- [ ] **Step 4: Verifica**

Run: `.venv/bin/python -m pytest -q tests/test_prova_sequenza.py`
Expected: `21 passed`.

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`
Expected: `611 passed` (590 di prima più 21).

Run: `git check-ignore -q diag-downloads/prova_sequenza.txt && echo ignorato`
Expected: `ignorato`.

- [ ] **Step 5: Commit**

```bash
git add tools/prova_sequenza.py tests/test_prova_sequenza.py
git commit -m "feat(tools): prova sul campo delle sequenze (ritmo, elenco in rec, live spento, attese)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Esecuzione sul campo e decisioni (con l'utente)

**Files:**
- Modify: `docs/field-report-2026-09-24.md` (in coda), `docs/superpowers/specs/2026-11-11-sequenze-design.md` (nuova sezione «Esito della prova» subito dopo «Prova sul campo (cancello)», e stato in testa)

**Interfaces:**
- Consumes: `diag-downloads/prova_sequenza.txt` prodotto dallo strumento del Task 1, con la sezione `== ESITO ==`.
- Produces: la sezione «Esito della prova» della specifica, con i valori che la parte 2 userà: intervallo minimo, anteprima sotto i 5 s (sì/no), regola per riconoscere le foto, live spento fra gli scatti (sì/no e anticipo), richieste fra gli scatti (sì/no), file per scatto.

Non è codice. Il controllore guida l'utente; **mentre il PC è collegato alla fotocamera non c'è Internet**: l'utente esegue, torna in linea, riferisce.

- [ ] **Step 1: L'utente esegue la prova**

Stanza illuminata, fotocamera appoggiata e puntata su qualcosa che si possa mettere a fuoco, ghiera su A o P, scatto singolo, batteria carica. Con la fotocamera in Wi-Fi:

```bash
nmcli connection up olympus-connect-E-M10MKIV-P-<seriale>
.venv/bin/python tools/prova_sequenza.py
nmcli connection down olympus-connect-E-M10MKIV-P-<seriale>
```

Dura circa 13 minuti, di cui dieci di attesa senza toccare la fotocamera. Poi torna in linea e lo dice. Le circa 18 foto della prova si possono cancellare dalla fotocamera.

- [ ] **Step 2: Leggi il referto**

Run: `cat diag-downloads/prova_sequenza.txt`

Controlla che non ci siano righe `FERMO` o `ERRORE IMPREVISTO` (se ci sono: correggi la causa indicata e fai ripetere la prova) e che la chiusura dica `ritorno in riproduzione 200`. Dalle righe di `== ESITO ==` e dai singoli scatti annota:
1. ciclo mediano con e senza anteprima, intervallo minimo proposto, se sotto i 5 s l'anteprima si toglie; se i tempi di `starttake` superano i 3 s la scena era buia e la misura va ripetuta con più luce;
2. se l'elenco risponde in modalità scatto e vede le foto nuove, e quanti file nascono per scatto (dalla riga finale: file nuovi diviso foto scattate);
3. se lo scatto a live spento è rifiutato, se il live si riaccende senza rientro e in quanto tempo, se la modalità resta quella di prima;
4. se dopo cinque minuti la fotocamera scatta ancora, con il live acceso e con il live spento, e se il live era ancora attivo alla fine dell'attesa.

- [ ] **Step 3: Annota l'esito nel resoconto**

In coda a `docs/field-report-2026-09-24.md` aggiungi la sezione «Sequenze: prova sul campo (AAAA-MM-GG)» con la data vera, una tabella dei quattro punti (misura, valore letto nel referto) e le righe del referto che contano, senza numeri di serie.

- [ ] **Step 4: Annota le decisioni nella specifica**

In `docs/superpowers/specs/2026-11-11-sequenze-design.md`, subito dopo la sezione «Prova sul campo (cancello)», aggiungi «## Esito della prova» con la data e, per ogni riga della tabella «Cosa decide ogni esito», l'esito avuto e la conseguenza che ne segue, con i numeri: intervallo minimo offerto; anteprima sotto i 5 s; regola di riconoscimento delle foto (istantanea all'avvio, oppure foto nuove dall'ingresso nel telecomando); live spento fra gli scatti con il suo anticipo in secondi (tempo misurato più 3 s, minimo 5 s), oppure live sempre acceso; richieste fra gli scatti (nessuna, oppure lettura ogni 30 s). Aggiorna la riga **Stato** in testa: «approvato in sessione; cancello superato il AAAA-MM-GG, esiti nella sezione "Esito della prova"». Se un esito non rientra in nessuna riga della tabella (per esempio lo scatto a live spento riesce), fermati e parlane con l'utente prima di scrivere.

- [ ] **Step 5: Controllo dei seriali e commit**

```bash
while read -r s; do [ -n "$s" ] && git diff | grep -qaF -- "$s" && echo TROVATO; done < diag-downloads/seriali.txt; echo fine
```

Expected: solo `fine`.

```bash
git add docs/field-report-2026-09-24.md docs/superpowers/specs/2026-11-11-sequenze-design.md
git commit -m "docs: esito della prova sul campo delle sequenze

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Dopo questo piano

Con il cancello superato si scrive la parte 2 (`docs/superpowers/plans/AAAA-MM-GG-sequenze-2-app.md`) sui dati veri: `core/sequence.py`, `core/timelapse.py`, `core/power.py`, `plan_flat` nello scarico, `gui/sequence.py` (selettori, regista, finestra di fine sequenza), `TimelapseWorker`, i collegamenti nella finestra, i test, la prova finale e la documentazione.
