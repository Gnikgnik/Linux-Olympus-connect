#!/usr/bin/env python3
"""Strumento diagnostico per la connessione alla fotocamera Olympus.

Eseguire con la fotocamera accesa in modalità Wi-Fi e il PC collegato
al suo access point:

    python3 diag.py

Con l'opzione --telecomando prova invece la modalità scatto (visione dal
vivo a tre qualità, messa a fuoco, scatto, video e ripresa del live dopo
il video): scatta davvero una foto e gira un video di 3 secondi.
Ghiera su P, A, S, M o iAuto.

Risponde a quattro domande:
  - la fotocamera risponde solo con lo User-Agent di OI.Share?
  - RAW (.ORF) e video (.MOV) sono scaricabili via GET?
  - il server supporta le richieste Range (ripresa dei trasferimenti)?
  - get_exif restituisce i dati di scatto, e in che forma? L'anteprima li contiene?

Registra inoltre l'output grezzo della fotocamera in tests/fixtures/,
che diventa la base dei test automatici.
"""
from __future__ import annotations

import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

BASE = "http://192.168.0.10"
CAMERA_IP = "192.168.0.10"
UA = {"User-Agent": "OI.Share v2", "Host": "192.168.0.10"}
TIMEOUT = (3, 15)
CHUNK = 65536
FIXTURES = Path("tests/fixtures")
REPORT = Path("diag-report.txt")


class Tee:
    """Scrive contemporaneamente su stdout e sul file di referto.

    Questo strumento viene eseguito mentre il PC è collegato all'access point
    della fotocamera e quindi senza rete: l'output è l'unico risultato del
    test, e non deve poter andare perso.
    """

    def __init__(self, path: Path) -> None:
        self._handle = path.open("w", encoding="utf-8")

    def write(self, text: str) -> int:
        sys.__stdout__.write(text)
        self._handle.write(text)
        return len(text)

    def flush(self) -> None:
        sys.__stdout__.flush()
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


def step(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def sh(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=10)


def wifi_devices() -> list[str]:
    out = sh(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"]).stdout
    return [ln.split(":")[0] for ln in out.splitlines() if ":wifi:" in ln]


def active_connection(device: str) -> str | None:
    """Nome del profilo NetworkManager attivo sul dispositivo, se connesso.

    Usa `nmcli device show`: il campo ACTIVE di `nmcli device wifi` riporta
    'no' per tutte le reti anche quando una è connessa, quindi non è usabile.
    """
    out = sh(["nmcli", "-t", "-f", "GENERAL.CONNECTION,GENERAL.STATE",
              "device", "show", device]).stdout
    name, state = None, ""
    for line in out.splitlines():
        key, _, value = line.partition(":")
        if key == "GENERAL.CONNECTION":
            name = value or None
        elif key == "GENERAL.STATE":
            state = value
    return name if state.startswith("100") else None


def is_onlink(ip: str) -> tuple[bool, str]:
    """La rotta verso ip è diretta (nessun gateway intermedio)?

    `ip route get` riesce sempre grazie alla default route, quindi il solo
    codice di ritorno non dice niente: quando si è collegati all'AP della
    fotocamera la rotta non contiene 'via'.
    """
    result = sh(["ip", "-o", "route", "get", ip])
    route = " ".join(result.stdout.split())
    if result.returncode != 0:
        return False, result.stderr.strip() or "nessuna rotta"
    return " via " not in f" {route} ", route


def ladder() -> bool:
    """Scala diagnostica: un gradino per ogni causa distinta."""
    print("\n== Scala diagnostica ==")

    devices = wifi_devices()
    if not step("dispositivo Wi-Fi presente", bool(devices), ", ".join(devices)):
        return False

    connections = {d: active_connection(d) for d in devices}
    connected = {d: c for d, c in connections.items() if c}
    step("rete Wi-Fi attiva", bool(connected),
         ", ".join(f"{d}→{c}" for d, c in connected.items()) or "nessuna")

    onlink, route = is_onlink(CAMERA_IP)
    if not step(f"{CAMERA_IP} raggiungibile direttamente (on-link)", onlink, route):
        print(f"\n  La rotta verso {CAMERA_IP} passa da un gateway: il PC non è")
        print("  sull'access point della fotocamera. Collegalo alla rete Wi-Fi")
        print("  mostrata dalla fotocamera e riprova.")
        return False

    tcp_ok = False
    detail = ""
    try:
        with socket.create_connection((CAMERA_IP, 80), timeout=3):
            tcp_ok, detail = True, "accetta"
    except OSError as exc:
        detail = str(exc)
    if not step("porta 80 della fotocamera", tcp_ok, detail):
        print("\n  Il PC è sulla rete giusta ma la fotocamera non accetta")
        print("  connessioni: probabilmente ha spento il Wi-Fi per inattività.")
        return False

    # Il gradino decisivo: la fotocamera risponde senza lo User-Agent di OI.Share?
    print("\n== Verifica dell'header User-Agent ==")
    for label, headers in (("con User-Agent OI.Share", UA), ("senza User-Agent", {})):
        try:
            r = requests.get(f"{BASE}/get_caminfo.cgi", headers=headers, timeout=TIMEOUT)
            step(f"get_caminfo.cgi {label}", r.status_code == 200,
                 f"HTTP {r.status_code}, {len(r.content)} byte")
        except requests.RequestException as exc:
            step(f"get_caminfo.cgi {label}", False, type(exc).__name__)

    return True


def get(path: str, **params) -> requests.Response:
    r = requests.get(f"{BASE}/{path.lstrip('/')}", headers=UA,
                     params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def record() -> list[list[str]]:
    """Salva l'output grezzo della fotocamera e restituisce le righe dei file."""
    print("\n== Registrazione fixture ==")
    FIXTURES.mkdir(parents=True, exist_ok=True)

    for name, path in (("commandlist.xml", "get_commandlist.cgi"),
                       ("caminfo.xml", "get_caminfo.cgi")):
        body = get(path).content
        (FIXTURES / name).write_bytes(body)
        print(f"  salvato {name} ({len(body)} byte)")

    get("switch_cammode.cgi", mode="play")
    print("  switch_cammode mode=play OK")

    rows: list[list[str]] = []
    pending = ["/DCIM"]
    seen: set[str] = set()
    files: list[list[str]] = []

    while pending:
        directory = pending.pop()
        if directory in seen:
            continue
        seen.add(directory)

        try:
            body = get("get_imglist.cgi", DIR=directory).content
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                print(f"  {directory}: vuota (404)")
                continue
            raise

        label = directory.strip("/").replace("/", "_").lower() or "root"
        (FIXTURES / f"imglist_{label}.csv").write_bytes(body)
        print(f"  salvato imglist_{label}.csv ({len(body)} byte) per {directory}")

        for line in body.decode("ascii", "replace").splitlines():
            parts = line.split(",")
            if len(parts) != 6:
                continue
            rows.append(parts)
            attrib = int(parts[3])
            if attrib & 16:
                pending.append(f"{parts[0]}/{parts[1]}")
            elif not attrib & (2 | 4 | 8):
                files.append(parts)

    return files


def probe_downloads(files: list[list[str]]) -> None:
    """Verifica: RAW scaricabile? MOV scaricabile? Range supportato?"""
    print("\n== Download e supporto Range ==")

    by_ext: dict[str, list[str]] = {}
    for parts in files:
        by_ext.setdefault(Path(parts[1]).suffix.upper(), []).append(
            f"{parts[0]}/{parts[1]}"
        )

    print("  estensioni sulla scheda: "
          + (", ".join(f"{k} x{len(v)}" for k, v in sorted(by_ext.items())) or "nessuna"))

    out = Path("diag-downloads")
    out.mkdir(exist_ok=True)

    for ext in (".JPG", ".ORF", ".MOV"):
        paths = by_ext.get(ext)
        if not paths:
            print(f"  [SALTATO] nessun file {ext} sulla scheda")
            continue
        camera_path = paths[0]

        try:
            r = requests.get(f"{BASE}{camera_path}", headers=UA,
                             timeout=TIMEOUT, stream=True)
            body = b"".join(r.iter_content(CHUNK))
            ok = r.status_code == 200 and len(body) > 0
            step(f"download {ext}", ok,
                 f"{camera_path}: HTTP {r.status_code}, {len(body)} byte")
            if not ok:
                continue
            (out / Path(camera_path).name).write_bytes(body)
        except requests.RequestException as exc:
            step(f"download {ext}", False, f"{camera_path}: {type(exc).__name__}")
            continue

        try:
            r = requests.get(f"{BASE}{camera_path}", headers=dict(UA, Range="bytes=1024-"),
                             timeout=TIMEOUT, stream=True)
            got = sum(len(c) for c in r.iter_content(CHUNK))
            step(f"Range su {ext}", r.status_code == 206,
                 f"HTTP {r.status_code}, "
                 f"Content-Range={r.headers.get('Content-Range', 'assente')}, "
                 f"ricevuti {got} byte (attesi {len(body) - 1024})")
        except requests.RequestException as exc:
            step(f"Range su {ext}", False, type(exc).__name__)


def probe_previews(files: list[list[str]]) -> None:
    """Le miniature funzionano per RAW e video, non solo per i JPEG?

    Su questa scheda il 98% dei file sono ORF: se get_thumbnail non li
    copre, una griglia di miniature è inutile e serve un'altra strategia.
    """
    print("\n== Miniature e anteprime ==")

    first: dict[str, str] = {}
    for parts in files:
        ext = Path(parts[1]).suffix.upper()
        first.setdefault(ext, f"{parts[0]}/{parts[1]}")

    out = Path("diag-downloads")
    out.mkdir(exist_ok=True)

    for endpoint in ("get_thumbnail", "get_screennail"):
        for ext in (".JPG", ".ORF", ".MOV"):
            camera_path = first.get(ext)
            if not camera_path:
                print(f"  [SALTATO] {endpoint} {ext}: nessun file")
                continue
            try:
                r = requests.get(f"{BASE}/{endpoint}.cgi", headers=UA,
                                 params={"DIR": camera_path}, timeout=TIMEOUT)
                body = r.content
                is_jpeg = body[:2] == b"\xff\xd8"
                step(f"{endpoint} {ext}", r.status_code == 200 and is_jpeg,
                     f"HTTP {r.status_code}, {len(body)} byte, "
                     f"{'JPEG valido' if is_jpeg else 'non è un JPEG'}")
                if r.status_code == 200 and is_jpeg:
                    (out / f"{endpoint}{ext.lower()}.jpg").write_bytes(body)
            except requests.RequestException as exc:
                step(f"{endpoint} {ext}", False, type(exc).__name__)


def _exif_shape(body: bytes) -> str:
    """In che forma get_exif restituisce i dati: decide come leggerli."""
    if body[:4] in (b"II*\x00", b"MM\x00*"):
        return "TIFF nudo"
    if body[:6] == b"Exif\x00\x00":
        return "blocco Exif"
    if body[:2] == b"\xff\xd8":
        return "JPEG"
    if body[:4] in (b"IIRO", b"IIRS", b"MMOR"):
        return "ORF (TIFF Olympus)"
    return "sconosciuta"


def probe_exif(files: list[list[str]]) -> None:
    """Da dove si leggono i dati di scatto?

    Due fonti possibili (specifica 2026-11-06-dati-di-scatto-design.md):
    A, get_exif.cgi, una richiesta dedicata; B, l'EXIF che la fotocamera
    potrebbe copiare dentro il JPEG dell'anteprima. Questo passaggio
    registra entrambe, così la scelta si fa sui byte veri.
    """
    import time

    print("\n== Dati di scatto ==")

    first: dict[str, str] = {}
    for parts in files:
        first.setdefault(Path(parts[1]).suffix.upper(), f"{parts[0]}/{parts[1]}")

    out = FIXTURES / "exif"
    out.mkdir(parents=True, exist_ok=True)
    sorgenti: list[str] = []

    for ext in (".JPG", ".ORF", ".MOV"):
        camera_path = first.get(ext)
        if not camera_path:
            print(f"  [SALTATO] get_exif {ext}: nessun file")
            continue
        try:
            inizio = time.monotonic()
            r = requests.get(f"{BASE}/get_exif.cgi", headers=UA,
                             params={"DIR": camera_path}, timeout=TIMEOUT)
            ms = (time.monotonic() - inizio) * 1000
            body = r.content
            forma = _exif_shape(body)
            step(f"get_exif {ext}", r.status_code == 200 and forma != "sconosciuta",
                 f"HTTP {r.status_code}, {r.headers.get('Content-Type', '?')}, "
                 f"{len(body)} byte, {ms:.0f} ms, forma: {forma}, "
                 f"inizio: {body[:16].hex(' ')}")
            nome = f"{ext[1:].lower()}.bin"
            (out / nome).write_bytes(body)
            sorgenti.append(f"{nome} {camera_path}")
        except requests.RequestException as exc:
            step(f"get_exif {ext}", False, type(exc).__name__)

    camera_path = first.get(".ORF")
    if camera_path:
        try:
            r = requests.get(f"{BASE}/get_screennail.cgi", headers=UA,
                             params={"DIR": camera_path}, timeout=TIMEOUT)
            body = r.content
            posizione = body.find(b"Exif\x00\x00", 0, 65536)
            step("EXIF dentro l'anteprima di un ORF",
                 r.status_code == 200 and posizione >= 0,
                 f"HTTP {r.status_code}, {len(body)} byte, "
                 + (f"blocco Exif alla posizione {posizione}" if posizione >= 0
                    else "nessun blocco Exif"))
            (out / "screennail_orf.jpg").write_bytes(body)
            sorgenti.append(f"screennail_orf.jpg {camera_path}")
        except requests.RequestException as exc:
            step("EXIF dentro l'anteprima di un ORF", False, type(exc).__name__)

    (out / "sorgenti.txt").write_text("\n".join(sorgenti) + "\n")


def probe_keepalive() -> None:
    """set_timeout permette di evitare che la fotocamera si addormenti?

    Senza supporto Range un trasferimento interrotto costa l'intero file, e
    sulla scheda ci sono video da oltre 900 MB: allungare il timeout di
    inattività è la mitigazione più efficace disponibile.

    Il valore vale per la sessione Wi-Fi corrente e si azzera spegnendo
    la fotocamera.
    """
    print("\n== Keep-alive (set_timeout) ==")

    def try_timeout(label: str, seconds) -> bool:
        try:
            r = requests.get(f"{BASE}/set_timeout.cgi", headers=UA,
                             params={"timeoutsec": seconds}, timeout=TIMEOUT)
            body = " ".join(r.text.split())
            return step(f"set_timeout {label}", r.status_code == 200,
                        f"HTTP {r.status_code}: {body[:90] or '(vuoto)'}")
        except requests.RequestException as exc:
            return step(f"set_timeout {label}", False, type(exc).__name__)

    # In modalità play tutti i valori rispondono HTTP 520 errorcode 1006
    # (misurato il 2026-09-24). Due ipotesi sul perché, provate qui.
    print("  -- in modalità play --")
    for seconds in (300, 1800, 3600):
        try_timeout(f"timeoutsec={seconds} (play)", seconds)

    print("  -- valori limite, in modalità play --")
    for value in (0, 1, 60, 65535):
        try_timeout(f"timeoutsec={value} (play)", value)

    print("  -- in modalità rec --")
    try:
        requests.get(f"{BASE}/switch_cammode.cgi", headers=UA,
                     params={"mode": "rec"}, timeout=TIMEOUT)
        for seconds in (300, 1800):
            try_timeout(f"timeoutsec={seconds} (rec)", seconds)
    except requests.RequestException as exc:
        step("switch_cammode mode=rec", False, type(exc).__name__)
    finally:
        # Tornare sempre in play: è la modalità in cui la fotocamera
        # elenca i file, e lasciarla in rec confonderebbe l'uso successivo.
        try:
            requests.get(f"{BASE}/switch_cammode.cgi", headers=UA,
                         params={"mode": "play"}, timeout=TIMEOUT)
            print("  ripristinata modalità play")
        except requests.RequestException:
            print("  ATTENZIONE: impossibile ripristinare la modalità play")

    try:
        r = requests.get(f"{BASE}/get_unusedcapacity.cgi", headers=UA, timeout=TIMEOUT)
        step("get_unusedcapacity", r.status_code == 200,
             f"HTTP {r.status_code}: {r.text.strip()[:80]}")
    except requests.RequestException as exc:
        step("get_unusedcapacity", False, type(exc).__name__)


# -- telecomando --------------------------------------------------------------

LIVE_PORT = 40000           # fissa: una regola del firewall vale anche al secondo tentativo
LIVE_SECONDS = 3.0
CAMERA_LIVE_PORT = 16540    # la porta da cui la fotocamera invia il live
REMOTE_OUT = Path("diag-downloads/telecomando")


def rtp_summary(packet: bytes) -> dict | None:
    """I campi dell'intestazione RTP (RFC 3550) di un pacchetto del live.

    Non interpreta il contenuto: serve a scoprire come la fotocamera spezza
    i fotogrammi. None per un pacchetto più corto dell'intestazione fissa.
    """
    if len(packet) < 12:
        return None
    b0, b1 = packet[0], packet[1]
    seq, timestamp, ssrc = struct.unpack(">HII", packet[2:12])
    csrc = b0 & 0x0F
    pos = 12 + 4 * csrc
    extension = (b0 >> 4) & 1
    ext_profile = ext_len = None
    ext_overflow = False
    header_cap = None
    if extension and len(packet) >= pos + 4:
        ext_profile, words = struct.unpack(">HH", packet[pos:pos + 4])
        ext_len = 4 * words
        if pos + 4 + ext_len > len(packet):
            # Estensione che sfora: non si sa dove finisce l'intestazione.
            # Si registra solo la parte fissa, mai il pacchetto intero
            # (conterrebbe il fotogramma).
            ext_overflow = True
            header_cap = pos + 4
        pos += 4 + ext_len
    payload = packet[pos:] if pos <= len(packet) else b""
    return {
        "version": b0 >> 6, "padding": (b0 >> 5) & 1, "extension": extension,
        "csrc": csrc, "marker": b1 >> 7, "payload_type": b1 & 0x7F,
        "seq": seq, "timestamp": timestamp, "ssrc": ssrc,
        "ext_profile": ext_profile, "ext_len": ext_len,
        "header_len": header_cap if header_cap is not None else min(pos, len(packet)),
        "ext_overflow": ext_overflow, "payload_len": len(payload),
        "payload_soi": payload[:2] == b"\xff\xd8",
        "payload_eoi": payload[-2:] == b"\xff\xd9",
    }


def rtp_tlvs(packet: bytes) -> list[tuple[int, bytes]]:
    """I TLV dell'estensione RTP: (id, valore). [] se manca o è malformata.

    Ogni TLV: id (2 byte), lunghezza in parole da 32 bit (2 byte), valore.
    Non solleva mai.
    """
    try:
        if len(packet) < 12 or not (packet[0] >> 4) & 1:
            return []
        pos = 12 + 4 * (packet[0] & 0x0F)
        if len(packet) < pos + 4:
            return []
        _profilo, words = struct.unpack(">HH", packet[pos:pos + 4])
        fine = pos + 4 + 4 * words
        if fine > len(packet):
            return []
        pos += 4
        out: list[tuple[int, bytes]] = []
        while pos + 4 <= fine:
            ident, n = struct.unpack(">HH", packet[pos:pos + 4])
            if pos + 4 + 4 * n > fine:
                return []
            out.append((ident, packet[pos + 4:pos + 4 + 4 * n]))
            pos += 4 + 4 * n
        return out
    except Exception:
        return []


def rtp_frame_size(packet: bytes) -> tuple[int, int] | None:
    """Larghezza e altezza del fotogramma dal TLV 26, None se assente."""
    for ident, valore in rtp_tlvs(packet):
        if ident == 26 and len(valore) >= 8:
            return struct.unpack(">II", valore[:8])
    return None


def salva_pacchetti(path: Path, pacchetti: list[tuple[float, tuple[str, int], bytes]]) -> None:
    """Registra i pacchetti grezzi: tempo relativo al primo, lunghezza, byte."""
    inizio = pacchetti[0][0] if pacchetti else 0.0
    with path.open("wb") as f:
        for t, _origine, dati in pacchetti:
            f.write(struct.pack(">dI", t - inizio, len(dati)) + dati)


def leggi_pacchetti(path: Path) -> list[tuple[float, bytes]]:
    dati = path.read_bytes()
    out: list[tuple[float, bytes]] = []
    pos = 0
    while pos + 12 <= len(dati):
        t, n = struct.unpack(">dI", dati[pos:pos + 12])
        out.append((t, dati[pos + 12:pos + 12 + n]))
        pos += 12 + n
    return out


def _cgi(path: str, **params) -> tuple[int, bytes, float]:
    """Una richiesta alla fotocamera: (stato, corpo, millisecondi). Non solleva."""
    inizio = time.monotonic()
    try:
        r = requests.get(f"{BASE}/{path}", headers=UA, params=params, timeout=TIMEOUT)
        return r.status_code, r.content, (time.monotonic() - inizio) * 1000
    except requests.RequestException as exc:
        return 0, type(exc).__name__.encode(), (time.monotonic() - inizio) * 1000


def _riporta(nome: str, risposta: tuple[int, bytes, float]) -> bool:
    stato, corpo, ms = risposta
    if corpo[:2] == b"\xff\xd8":
        testo = f"JPEG {len(corpo)} byte"
    else:
        testo = " ".join(corpo[:160].decode("utf-8", "replace").split()) or "(vuoto)"
    return step(nome, stato == 200, f"HTTP {stato}, {ms:.0f} ms: {testo}")


class Ricevitore:
    """Legge il socket in un thread di sfondo, già attivo prima di startliveview.

    Il thread è daemon e finisce quando il socket si chiude (OSError), quando
    si chiama stop() o quando passa la scadenza: nessuna chiamata HTTP
    bloccante della prova lascia il socket senza lettore.
    """

    def __init__(self, sock: socket.socket, scadenza: float):
        self.sock = sock
        self.pacchetti: list[tuple[float, tuple[str, int], bytes]] = []
        self._fine = threading.Event()
        self._scadenza = time.monotonic() + scadenza
        self._thread = threading.Thread(target=self._corpo, daemon=True)
        self._thread.start()

    def _corpo(self) -> None:
        try:
            self.sock.settimeout(0.25)
            while not self._fine.is_set() and time.monotonic() < self._scadenza:
                try:
                    dati, origine = self.sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                self.pacchetti.append((time.monotonic(), origine, dati))
        except OSError:
            pass

    def stop(self) -> list[tuple[float, tuple[str, int], bytes]]:
        self._fine.set()
        self._thread.join(timeout=2.0)
        return list(self.pacchetti)


def statistiche_stabili(pacchetti, t_da: float, t_a: float) -> dict:
    """Fotogrammi al secondo e salti di sequenza nel solo regime stabile.

    Considera i pacchetti ricevuti fra t_da e t_a (tempi monotonic); i
    fotogrammi sono i timestamp RTP distinti, a 90 kHz. L'arretrato letto
    subito dopo startliveview resta fuori.
    """
    sommari = [s for t, _o, p in pacchetti if t_da <= t <= t_a
               for s in (rtp_summary(p),) if s]
    stamp: list[int] = []
    for s in sommari:
        if s["timestamp"] not in stamp:
            stamp.append(s["timestamp"])
    fps = None
    if len(stamp) >= 2:
        durata = sum((b - a) % 2**32 for a, b in zip(stamp, stamp[1:])) / 90000
        fps = (len(stamp) - 1) / durata if durata > 0 else None
    salti = sum(1 for a, b in zip(sommari, sommari[1:])
                if (b["seq"] - a["seq"]) % 65536 != 1)
    return {"pacchetti": len(sommari), "fotogrammi": len(stamp), "fps": fps, "salti": salti}


def _dimensioni(pacchetti, t_da: float, t_a: float) -> str:
    viste = sorted({rtp_frame_size(p) for t, _o, p in pacchetti
                    if t_da <= t <= t_a and rtp_frame_size(p)})
    return ", ".join(f"{w}×{h}" for w, h in viste) or "nessuna"


def _regole_firewall() -> None:
    print("    Probabile blocco del firewall. Per riprovare, apri la porta")
    print("    solo per la fotocamera, riesegui, poi richiudila:")
    print(f"      sudo ufw allow in from 192.168.0.10 proto udp to any port {LIVE_PORT}")
    print(f"      sudo ufw delete allow in from 192.168.0.10 proto udp to any port {LIVE_PORT}")


def _avvia_live(sock: socket.socket, nome: str) -> tuple[bool, float]:
    """Pacchetto iniziale verso la fotocamera, poi startliveview.

    Il pacchetto è il tentativo di aprire il percorso di ritorno nel
    firewall senza regola. Restituisce (riuscito, istante del ritorno).
    """
    try:
        sock.sendto(b"\x00", (CAMERA_IP, CAMERA_LIVE_PORT))
    except OSError:
        pass
    ok = _riporta(f"startliveview {nome}",
                  _cgi("exec_takemisc.cgi", com="startliveview", port=LIVE_PORT))
    return ok, time.monotonic()


def _analizza(nome: str, pacchetti, t_ritorno: float, t_stop: float) -> None:
    secondi = max(t_stop - t_ritorno, 0.001)
    if not pacchetti:
        step(f"live {nome}", False, "nessun pacchetto")
        return
    sommari = [s for s in (rtp_summary(p) for _t, _o, p in pacchetti) if s]
    dimensioni = [len(p) for _t, _o, p in pacchetti]
    origini = sorted({f"{o[0]}:{o[1]}" for _t, o, _p in pacchetti})
    arretrato = sum(1 for t, _o, _p in pacchetti if t < t_ritorno + 0.2)
    st = statistiche_stabili(pacchetti, t_ritorno + 0.2, t_stop)
    fps = f"{st['fps']:.2f}" if st["fps"] is not None else "n/d"
    step(f"live {nome}", True,
         f"{len(pacchetti)} pacchetti, dimensioni {min(dimensioni)}–{max(dimensioni)} byte, "
         f"origine {', '.join(origini)}")
    print(f"    regime stabile (dopo 0,2 s dal ritorno di startliveview): "
          f"{st['pacchetti']} pacchetti, {st['fotogrammi']} fotogrammi, "
          f"{fps} fotogrammi/s, salti di sequenza {st['salti']}")
    print(f"    pacchetti nei primi 0,2 s (arretrato): {arretrato}")
    print(f"    dimensione dal flusso: {_dimensioni(pacchetti, 0, float('inf'))}")
    print(f"    marcatori RTP {sum(1 for s in sommari if s['marker'])}, "
          f"estensioni che sforano {sum(1 for s in sommari if s['ext_overflow'])}, "
          f"RTP non leggibili {len(pacchetti) - len(sommari)}")
    con_estensione = [(s, p) for s, (_t, _o, p) in zip(sommari, pacchetti)
                      if s["extension"] and not s["ext_overflow"]]
    if con_estensione:
        s, p = con_estensione[0]
        print(f"    prima estensione: profilo 0x{s['ext_profile']:04x}, {s['ext_len']} byte, "
              f"TLV {[i for i, _v in rtp_tlvs(p)]}")


def _live_prova(sock: socket.socket, nome: str, qualita: str, secondi: float,
                attivi: list, senza_regola: bool = False) -> bool:
    """Un live di prova: ricevitore attivo, avvio, ascolto, stop, scarico."""
    ricevitore = Ricevitore(sock, secondi + 15.0)
    attivi.append(ricevitore)
    _avvia_live(sock, nome)
    t_ritorno = time.monotonic()
    time.sleep(secondi)
    t_stop = time.monotonic()
    _riporta(f"stopliveview {nome}", _cgi("exec_takemisc.cgi", com="stopliveview"))
    time.sleep(0.5)                      # scarica i pacchetti in coda dopo lo stop
    pacchetti = ricevitore.stop()
    attivi.remove(ricevitore)
    etichetta = (f"{qualita} — live senza regola (pacchetto iniziale verso :{CAMERA_LIVE_PORT})"
                 if senza_regola else qualita)
    _analizza(etichetta, pacchetti, t_ritorno, t_stop)
    salva_pacchetti(REMOTE_OUT / f"live_{qualita}.bin", pacchetti)
    if senza_regola:
        if pacchetti:
            step("firewall", True, "nessuna regola del firewall necessaria")
        else:
            _regole_firewall()
    return bool(pacchetti)


def probe_remote() -> None:
    """La modalità scatto: live, messa a fuoco, scatto, revisione, video.

    Gira solo con --telecomando: scatta davvero una foto e gira un video di
    3 secondi. Pacchetti e immagini restano in diag-downloads/, perché
    mostrano ciò che la fotocamera inquadra.
    """
    print("\n== Telecomando (modalità scatto, prova v2) ==")
    print("  Scatta una foto e gira un video di 3 secondi.")
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", LIVE_PORT))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        effettivo = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    except OSError as exc:
        step(f"porta UDP {LIVE_PORT}", False, f"{type(exc).__name__}: {exc}")
        return
    step("buffer di ricezione", True,
         f"richiesto 4194304 byte, effettivo {effettivo} byte "
         "(il kernel lo limita a net.core.rmem_max e lo raddoppia)")

    attivi: list[Ricevitore] = []
    try:
        _riporta("switch_cammode rec 0640x0480",
                 _cgi("switch_cammode.cgi", mode="rec", lvqty="0640x0480"))

        for nome, params in (("takemode", {"com": "get", "propname": "takemode"}),
                             ("desclist", {"com": "desc", "propname": "desclist"})):
            risposta = _cgi("get_camprop.cgi", **params)
            _riporta(f"get_camprop {nome}", risposta)
            (REMOTE_OUT / f"camprop_{nome}.xml").write_bytes(risposta[1])

        # rec→rec non cambia la qualità (prima prova): si passa dalla
        # riproduzione ogni volta.
        for i, qualita in enumerate(("0640x0480", "1024x0768", "1280x0960")):
            _riporta("switch_cammode play", _cgi("switch_cammode.cgi", mode="play"))
            _riporta(f"switch_cammode rec {qualita}",
                     _cgi("switch_cammode.cgi", mode="rec", lvqty=qualita))
            _live_prova(sock, qualita, qualita, LIVE_SECONDS, attivi, senza_regola=(i == 0))

        _riporta("switch_cammode play", _cgi("switch_cammode.cgi", mode="play"))
        _riporta("switch_cammode rec 0640x0480 (scatto)",
                 _cgi("switch_cammode.cgi", mode="rec", lvqty="0640x0480"))

        # Messa a fuoco e scatto vanno dati con il live attivo (con il live
        # fermo la prima prova ha avuto errorcode 1004). Il ricevitore
        # continua a leggere per tutto il blocco: niente arretrato.
        scatto = Ricevitore(sock, 60.0)
        attivi.append(scatto)
        _avvia_live(sock, "(scatto)")

        risposta = _cgi("exec_takemotion.cgi", com="assignafframe", point="0320x0240")
        _riporta("assignafframe 0320x0240", risposta)
        (REMOTE_OUT / "af.txt").write_bytes(risposta[1])
        _riporta("releaseafframe", _cgi("exec_takemotion.cgi", com="releaseafframe"))

        _riporta("starttake", _cgi("exec_takemotion.cgi", com="starttake"))
        time.sleep(0.5)
        _riporta("stoptake", _cgi("exec_takemotion.cgi", com="stoptake"))
        time.sleep(1.0)

        # L'immagine di revisione si chiede prima a live acceso e, se non
        # arriva, di nuovo a live fermo.
        revisione = (("getrecview", "recview.jpg"), ("getlastjpg", "lastjpg.jpg"))
        arrivate: dict[str, bool] = {}
        for com, file in revisione:
            risposta = _cgi("exec_takemisc.cgi", com=com)
            arrivate[com] = _riporta(f"{com} (live acceso)", risposta)
            if risposta[1][:2] == b"\xff\xd8":
                (REMOTE_OUT / file).write_bytes(risposta[1])

        _riporta("stopliveview (scatto)", _cgi("exec_takemisc.cgi", com="stopliveview"))
        time.sleep(0.5)                  # scarico dei pacchetti in coda
        pacchetti_scatto = scatto.stop()
        attivi.remove(scatto)
        step("live durante messa a fuoco e scatto", bool(pacchetti_scatto),
             f"{len(pacchetti_scatto)} pacchetti; dimensione dal flusso: "
             f"{_dimensioni(pacchetti_scatto, 0, float('inf'))}")
        salva_pacchetti(REMOTE_OUT / "live_scatto.bin", pacchetti_scatto)

        for com, file in revisione:
            if arrivate[com]:
                continue
            risposta = _cgi("exec_takemisc.cgi", com=com)
            _riporta(f"{com} (live fermo)", risposta)
            if risposta[1][:2] == b"\xff\xd8":
                (REMOTE_OUT / file).write_bytes(risposta[1])

        # Video con il live attivo e già in ascolto: il live continua durante
        # la registrazione, e riprende dopo lo stop?
        video = Ricevitore(sock, 60.0)
        attivi.append(video)
        _avvia_live(sock, "(video)")
        time.sleep(1.0)
        inizio_video = time.monotonic()
        risposta = _cgi("exec_takemotion.cgi", com="startmovietake", liveview="on")
        if not _riporta("startmovietake liveview=on", risposta):
            _riporta("startmovietake (senza liveview)",
                     _cgi("exec_takemotion.cgi", com="startmovietake"))
        time.sleep(3.0)
        t_stop_inviato = time.monotonic()
        _riporta("stopmovietake", _cgi("exec_takemotion.cgi", com="stopmovietake"))
        t_stop_tornato = time.monotonic()
        time.sleep(3.0)                  # ascolto dopo il ritorno di stopmovietake
        t_fine = time.monotonic()
        raccolti = video.pacchetti[:]
        durante = sum(1 for t, _o, _p in raccolti if inizio_video <= t <= t_stop_inviato)
        dopo = sum(1 for t, _o, _p in raccolti if t > t_stop_tornato)
        step("live durante la registrazione", durante > 0,
             f"{durante} pacchetti in {t_stop_inviato - inizio_video:.1f} s di registrazione")
        step("live dopo stopmovietake", dopo > 0,
             f"{dopo} pacchetti nei 3 s dopo il ritorno dello stop")
        print(f"    dimensione dal flusso: prima {_dimensioni(raccolti, 0, inizio_video)}; "
              f"durante {_dimensioni(raccolti, inizio_video, t_stop_inviato)}; "
              f"dopo {_dimensioni(raccolti, t_stop_tornato, t_fine)}")
        if dopo == 0:
            _avvia_live(sock, "(dopo il video)")
            t_riavvio = time.monotonic()
            time.sleep(2.0)
            raccolti = video.pacchetti[:]
            riavviati = sum(1 for t, _o, _p in raccolti if t > t_riavvio)
            step("live riavviato dopo il video", riavviati > 0,
                 f"live riavviato dopo il video: {riavviati} pacchetti; dimensione dal "
                 f"flusso: {_dimensioni(raccolti, t_riavvio, float('inf'))}")
        raccolti = video.stop()
        attivi.remove(video)
        salva_pacchetti(REMOTE_OUT / "live_video.bin", raccolti)
    finally:
        # Se un'eccezione arriva a metà video, la registrazione va chiusa
        # prima di tutto il resto. Se non c'è nessun video in corso la
        # fotocamera risponde con un errore, che qui non conta.
        _cgi("exec_takemotion.cgi", com="stopmovietake")
        _cgi("exec_takemisc.cgi", com="stopliveview")
        for ricevitore in attivi:
            ricevitore.stop()
        sock.close()
        stato, _corpo, _ms = _cgi("switch_cammode.cgi", mode="play")
        print("  ripristinata modalità play" if stato == 200
              else "  ATTENZIONE: impossibile ripristinare la modalità play")

    stato, corpo, ms = _cgi("get_imglist.cgi", DIR="/DCIM/100OLYMP")
    nomi = [r.split(",")[1] for r in corpo.decode("ascii", "replace").splitlines()
            if r.count(",") == 5]
    step("nuovi file sulla scheda", stato == 200 and bool(nomi),
         f"HTTP {stato}, {len(nomi)} file, ultimi: {', '.join(nomi[-2:])}")


def run(telecomando: bool = False) -> int:
    from datetime import datetime

    print(f"Olympus Connect — diagnostica ({datetime.now():%Y-%m-%d %H:%M:%S})")
    if not ladder():
        return 1
    if telecomando:
        # Niente registrazione delle fixture e niente altre prove: questa
        # esecuzione serve solo alla modalità scatto, e non deve riscrivere
        # tests/fixtures/ con i dati veri della scheda.
        probe_remote()
        print("\nFatto. Risposte e pacchetti in diag-downloads/telecomando/.")
        return 0
    try:
        files = record()
    except requests.RequestException as exc:
        print(f"\nErrore durante la registrazione: {exc}")
        return 1
    print(f"\n  {len(files)} file trovati sulla scheda")
    probe_downloads(files)
    probe_previews(files)
    probe_exif(files)
    probe_keepalive()
    print("\nFatto. Fixture in tests/fixtures/ (dati di scatto in "
          "tests/fixtures/exif/), campioni in diag-downloads/.")
    return 0


def main() -> int:
    from datetime import datetime

    # Copia con marca temporale oltre al referto "corrente": ogni esecuzione
    # avviene offline e il suo output è un dato che non va perso.
    stamped = REPORT.with_name(f"diag-report-{datetime.now():%Y%m%d-%H%M%S}.txt")

    tee = Tee(REPORT)
    sys.stdout = tee
    try:
        return run(telecomando="--telecomando" in sys.argv[1:])
    except Exception as exc:                     # il referto deve sopravvivere
        print(f"\nErrore inatteso: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc(file=tee)
        return 2
    finally:
        sys.stdout = sys.__stdout__
        tee.close()
        stamped.write_bytes(REPORT.read_bytes())
        print(f"\nReferto salvato in {REPORT} e in {stamped}")


if __name__ == "__main__":
    sys.exit(main())
