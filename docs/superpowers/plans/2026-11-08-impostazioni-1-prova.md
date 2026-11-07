# Impostazioni dal PC, parte 1: prova sul campo — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** verificare sulla fotocamera vera il formato del comando `set_camprop` (POST) e cosa diventa regolabile in P, A, S e M, ripristinando tutte le impostazioni; ricavarne fixture e regole per la parte 2.

**Architecture:** uno strumento `tools/prova_impostazioni.py` (nel repository, come `tools/anonimizza_exif.py`) che gira **senza Internet** mentre il PC è collegato alla fotocamera e scrive il referto in `diag-downloads/prova_impostazioni.txt` e gli elenchi grezzi in `diag-downloads/impostazioni/`. Le funzioni pure dello strumento hanno test. È il cancello della specifica `2026-11-08-impostazioni-dal-pc-design.md`.

**Tech Stack:** Python 3.12+, requests, `socket`/`threading`, `xml.etree`; pytest con requests-mock.

## Global Constraints

- I test si lanciano con `.venv/bin/python -m pytest` (`pythonpath = ["."]` già configurato).
- Nessuna nuova dipendenza. `exec_erase.cgi` non va mai invocato. La prova **non scatta e non registra**.
- Ogni proprietà toccata torna al valore originale in un blocco `finally`; alla fine il live si ferma e la fotocamera torna in riproduzione.
- Il datagramma di apertura del firewall va a `192.168.0.10` sulla **porta di ricezione con i byte scambiati** (`((p & 0xFF) << 8) | (p >> 8)`), come l'app.
- Lo strumento non lo esegue mai un agente: lo esegue l'utente, collegato alla fotocamera e quindi senza Internet.
- Nessun numero di serie e nessuna immagine in un commit.
- Ogni messaggio di commit termina con la riga `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### Task 1: Lo strumento di prova

**Files:**
- Create: `tools/prova_impostazioni.py`
- Test: `tests/test_prova_impostazioni.py`

**Interfaces:**
- Produces: in `tools/prova_impostazioni.py` le funzioni pure `parse_desclist(body: bytes) -> dict[str, tuple[str, str, list[str]]]` (nome → attributo, valore, valori ammessi), `altro_valore(valori: list[str], attuale: str, escludi: tuple[str, ...] = ()) -> str | None`, `porta_sorgente(porta: int) -> int`, `ordine_ripristino(originali: dict[str, str]) -> list[tuple[str, str]]`; la funzione `main() -> int` che esegue la prova. File prodotti all'esecuzione: `diag-downloads/prova_impostazioni.txt`, `diag-downloads/impostazioni/desclist_iniziale.xml`, `desclist_P.xml`, `desclist_A.xml`, `desclist_S.xml`, `desclist_M.xml`.

- [ ] **Step 1: Scrivi i test delle funzioni pure**

Create `tests/test_prova_impostazioni.py`:

```python
"""Le funzioni pure dello strumento di prova delle impostazioni."""
from pathlib import Path

from tools.prova_impostazioni import (
    altro_valore, ordine_ripristino, parse_desclist, porta_sorgente,
)

DESCLIST = (Path(__file__).parent / "fixtures" / "remote" / "camprop_desclist.xml").read_bytes()


def test_reads_the_real_desclist():
    elenco = parse_desclist(DESCLIST)
    attributo, valore, valori = elenco["isospeedvalue"]
    assert attributo == "getset" and valore == "200"
    assert valori[:3] == ["Auto", "Low", "200"]
    assert elenco["touchactiveframe"][0] == "get"
    assert elenco["takemode"][1] == "S"


def test_garbage_desclist_is_empty():
    assert parse_desclist(b"non xml") == {}


def test_altro_valore_picks_a_different_allowed_value():
    assert altro_valore(["Auto", "Low", "200", "250"], "200", escludi=("Auto", "Low")) == "250"
    assert altro_valore(["normal", "lowvib-selftimer"], "normal", escludi=("lowvib-selftimer",)) is None
    assert altro_valore(["a"], "a") is None


def test_porta_sorgente_swaps_the_bytes():
    assert porta_sorgente(40000) == 16540
    assert porta_sorgente(35199) == 32649


def test_ripristino_restores_the_mode_last_after_going_to_manual():
    passi = ordine_ripristino({"takemode": "S", "isospeedvalue": "200", "focalvalue": "5.6"})
    assert passi[0] == ("takemode", "M")
    assert passi[-1] == ("takemode", "S")
    assert set(passi[1:-1]) == {("isospeedvalue", "200"), ("focalvalue", "5.6")}


def test_ripristino_without_mode_is_just_the_values():
    assert ordine_ripristino({"wbvalue": "0"}) == [("wbvalue", "0")]
```

- [ ] **Step 2: Verifica che falliscano**

Run: `.venv/bin/python -m pytest tests/test_prova_impostazioni.py -q`
Expected: errore di raccolta, `ModuleNotFoundError: No module named 'tools.prova_impostazioni'`. Se l'errore è invece `No module named 'tools'`, crea `tools/__init__.py` vuoto e rilancia.

- [ ] **Step 3: Scrivi lo strumento**

Create `tools/prova_impostazioni.py`:

```python
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
```

- [ ] **Step 4: Verifica**

Run: `.venv/bin/python -m pytest tests/test_prova_impostazioni.py -q && .venv/bin/python -m py_compile tools/prova_impostazioni.py && .venv/bin/python -m pytest -q`
Expected: `6 passed`; nessun errore di compilazione; suite completa verde.

Non eseguire `tools/prova_impostazioni.py`: lo esegue l'utente collegato alla fotocamera.

- [ ] **Step 5: Commit**

```bash
git add tools/prova_impostazioni.py tests/test_prova_impostazioni.py
git commit -m "feat(tools): prova sul campo delle impostazioni dal PC

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(con `tools/__init__.py` se è servito allo Step 2).

---

### Task 2: Esecuzione sul campo, fixture, decisioni (con l'utente)

**Files:**
- Create: `tests/fixtures/remote/desclist_P.xml`, `desclist_A.xml`, `desclist_S.xml`, `desclist_M.xml` (copiati da `diag-downloads/impostazioni/` dopo il controllo)
- Modify: `docs/superpowers/specs/2026-11-08-impostazioni-dal-pc-design.md` (paragrafo «Cancello» e regole di `is_automatic`), `docs/field-report-2026-09-24.md` (in coda)

- [ ] **Step 1: L'utente esegue la prova**

Ghiera su una modalità qualunque fra P, A, S, M. Con la fotocamera in Wi-Fi:

```bash
nmcli connection up olympus-connect-E-M10MKIV-P-<seriale>
.venv/bin/python tools/prova_impostazioni.py
nmcli connection down olympus-connect-E-M10MKIV-P-<seriale>
```

Poi torna in linea e lo dice. Dura circa 30 secondi.

- [ ] **Step 2: Leggi il referto**

Run: `cat diag-downloads/prova_impostazioni.txt`
Annota: il formato funzionante (o il cancello), per ogni modalità attributo/valore/numero di valori delle quattro proprietà di esposizione, l'esito delle tre richieste non consentite, bilanciamento e modalità di scatto, l'esito di ogni ripristino.

- [ ] **Step 3: Fixture, dopo il controllo dei seriali**

```bash
for f in diag-downloads/impostazioni/desclist_{P,A,S,M}.xml; do
  while read -r s; do [ -n "$s" ] && grep -qaF "$s" "$f" && echo "TROVATO in $f"; done < diag-downloads/seriali.txt
done; echo fine
cp diag-downloads/impostazioni/desclist_{P,A,S,M}.xml tests/fixtures/remote/
```

Expected: solo `fine`. Se compare `TROVATO`, non copiare quel file e annotalo.

- [ ] **Step 4: Annota esito e regole**

Nella specifica, sostituisci il paragrafo che inizia con «**Cancello.**» con l'esito (data, **passa** o **stop**, formato funzionante con l'esempio esatto di richiesta, risposta a un rifiuto con il suo `errorcode`, effetto di `focalvalue=1.0` in A); nella descrizione di `is_automatic` sostituisci «regola di partenza (da confermare con la prova)» con la regola osservata per P, A, S, M (da attributo/valori di `desclist` o dai rifiuti). In coda al resoconto sul campo, una sezione «Impostazioni dal PC (2026-11-DD)» con le righe del referto.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/remote/ docs/superpowers/specs/2026-11-08-impostazioni-dal-pc-design.md docs/field-report-2026-09-24.md
git status --short
git commit -m "test: elenchi veri delle impostazioni per P, A, S, M; esito della prova

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Dopo questo piano

Con l'esito **passa**, si scrive la parte 2 (`docs/superpowers/plans/2026-11-DD-impostazioni-2-pannello.md`) sui dati veri: `core/camprops.py`, `camera_props`/`set_prop` nel client, i due comandi nel `RemoteWorker`, `gui/props.py`, l'integrazione nel pannello, i test e la documentazione.
