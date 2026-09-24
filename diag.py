#!/usr/bin/env python3
"""Strumento diagnostico per la connessione alla fotocamera Olympus.

Eseguire con la fotocamera accesa in modalità Wi-Fi e il PC collegato
al suo access point:

    python3 diag.py

Risponde a tre domande:
  - la fotocamera risponde solo con lo User-Agent di OI.Share?
  - RAW (.ORF) e video (.MOV) sono scaricabili via GET?
  - il server supporta le richieste Range (ripresa dei trasferimenti)?

Registra inoltre l'output grezzo della fotocamera in tests/fixtures/,
che diventa la base dei test automatici.
"""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import requests

BASE = "http://192.168.0.10"
CAMERA_IP = "192.168.0.10"
UA = {"User-Agent": "OI.Share v2", "Host": "192.168.0.10"}
TIMEOUT = (3, 15)
CHUNK = 65536
FIXTURES = Path("tests/fixtures")


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


def main() -> int:
    print("Olympus Connect — diagnostica")
    if not ladder():
        return 1
    try:
        files = record()
    except requests.RequestException as exc:
        print(f"\nErrore durante la registrazione: {exc}")
        return 1
    print(f"\n  {len(files)} file trovati sulla scheda")
    probe_downloads(files)
    print("\nFatto. Fixture in tests/fixtures/, campioni in diag-downloads/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
