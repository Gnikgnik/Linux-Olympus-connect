"""Gestione della rete della fotocamera: diagnostica e profilo NetworkManager.

Tutti i comandi di sistema passano da un runner iniettabile, così i test
verificano gli argv emessi senza toccare la rete reale.
"""
from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from .errors import NetworkUnavailable

CAMERA_IP = "192.168.0.10"
CAMERA_PORT = 80
COMMAND_TIMEOUT = 10
PROFILE_PREFIX = "olympus-connect-"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], CommandResult]


def run_command(argv: list[str]) -> CommandResult:
    completed = subprocess.run(
        argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def probe_camera_port() -> bool:
    try:
        with socket.create_connection((CAMERA_IP, CAMERA_PORT), timeout=3):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class Rung:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Diagnosis:
    rungs: list[Rung] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(rung.ok for rung in self.rungs)

    @property
    def problem(self) -> str | None:
        """Nome del primo gradino fallito: la causa più a monte."""
        for rung in self.rungs:
            if not rung.ok:
                return rung.name
        return None


class CameraNetwork:
    def __init__(self, ssid: str = "", runner: Runner = run_command,
                 probe_tcp: Callable[[], bool] = probe_camera_port) -> None:
        self.ssid = ssid
        self._run = runner
        self._probe_tcp = probe_tcp

    @property
    def profile_name(self) -> str:
        """Il nostro profilo NetworkManager. Non tocchiamo nessun altro profilo."""
        return f"{PROFILE_PREFIX}{self.ssid}"

    def _nmcli(self, *args: str) -> CommandResult:
        try:
            return self._run(["nmcli", *args])
        except FileNotFoundError as exc:
            raise NetworkUnavailable(
                "nmcli non è disponibile: la gestione automatica della rete "
                "è disattivata."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise NetworkUnavailable(
                "nmcli non ha risposto entro 10 secondi."
            ) from exc

    # -- lettura dello stato ----------------------------------------------

    def wifi_devices(self) -> list[str]:
        result = self._nmcli("-t", "-f", "DEVICE,TYPE,STATE", "device")
        return [ln.split(":")[0] for ln in result.stdout.splitlines()
                if ":wifi:" in ln]

    def active_connection(self, device: str) -> str | None:
        """Nome del profilo NetworkManager attivo sul dispositivo, se connesso.

        Il campo ACTIVE di `nmcli device wifi` riporta 'no' per tutte le reti
        anche quando una è connessa (misurato sul sistema reale), quindi non
        è utilizzabile: si legge invece GENERAL.CONNECTION da
        `nmcli device show`.
        """
        result = self._nmcli("-t", "-f", "GENERAL.CONNECTION,GENERAL.STATE",
                             "device", "show", device)
        name, state = None, ""
        for line in result.stdout.splitlines():
            key, _, value = line.partition(":")
            if key == "GENERAL.CONNECTION":
                name = value or None
            elif key == "GENERAL.STATE":
                state = value
        return name if state.startswith("100") else None

    def is_onlink(self) -> tuple[bool, str]:
        """La fotocamera è raggiungibile direttamente, senza gateway?

        `ip route get` riesce sempre grazie alla default route, quindi il
        codice di ritorno non distingue «sono sull'AP della fotocamera» da
        «sono sul Wi-Fi di casa»: misurato, entrambi danno
        `192.168.0.10 via 192.168.1.254 dev wlan0`. La differenza è la
        presenza di ' via '.
        """
        result = self._run(["ip", "-o", "route", "get", CAMERA_IP])
        route = " ".join(result.stdout.split())
        if result.returncode != 0:
            return False, result.stderr.strip() or "nessuna rotta"
        return " via " not in f" {route} ", route

    # -- scala diagnostica ------------------------------------------------

    def diagnose(self) -> Diagnosis:
        """Un gradino per ogni causa distinta, dalla più a monte alla più a valle."""
        rungs: list[Rung] = []

        devices = self.wifi_devices()
        rungs.append(Rung(
            "dispositivo Wi-Fi", bool(devices),
            ", ".join(devices) if devices else "nessuna interfaccia Wi-Fi trovata",
        ))
        if not devices:
            return Diagnosis(rungs)

        active = next(
            (c for c in (self.active_connection(d) for d in devices) if c), None
        )

        if self.ssid:
            # Collegandosi col nostro profilo il nome attivo è il profilo,
            # non l'SSID: entrambi valgono come «rete giusta».
            on_camera = active in (self.ssid, self.profile_name)
            rungs.append(Rung(
                "rete della fotocamera", on_camera,
                f"collegato a «{active}»" if active else "nessuna rete Wi-Fi attiva",
            ))
            if not on_camera:
                return Diagnosis(rungs)

        onlink, route = self.is_onlink()
        rungs.append(Rung(
            "fotocamera sulla rete locale", onlink,
            route if onlink else f"la rotta passa da un gateway: {route}",
        ))
        if not onlink:
            return Diagnosis(rungs)

        reachable = self._probe_tcp()
        rungs.append(Rung(
            "porta 80 della fotocamera", reachable,
            "risponde" if reachable else
            "nessuna risposta: il Wi-Fi della fotocamera è attivo?",
        ))
        return Diagnosis(rungs)
