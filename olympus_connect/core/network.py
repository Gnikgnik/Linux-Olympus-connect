"""Gestione della rete della fotocamera: diagnostica e profilo NetworkManager.

Tutti i comandi di sistema passano da un runner iniettabile, così i test
verificano gli argv emessi senza toccare la rete reale.
"""
from __future__ import annotations

import re
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from .errors import NetworkUnavailable

# Gli access point delle fotocamere Olympus/OM System hanno l'SSID nella
# forma <modello>-P-<seriale>, per esempio E-M10MKIV-P-BJGA85301. Serve solo
# a offrirli per primi nell'elenco: un falso negativo costa un po' di
# scorrimento, non un errore.
_CAMERA_SSID = re.compile(r"^(E-M\d|E-PL?\d|OM-\d|TG-\d|STYLUS)", re.IGNORECASE)


def looks_like_camera(ssid: str) -> bool:
    return bool(ssid) and _CAMERA_SSID.match(ssid) is not None

CAMERA_IP = "192.168.0.10"
CAMERA_PORT = 80
COMMAND_TIMEOUT = 10
PROFILE_PREFIX = "olympus-connect-"

# Impostazioni che rendono usabile l'AP della fotocamera su Linux:
# never-default evita che la fotocamera diventi la default route, la metrica
# alta la mette dietro alle reti vere, autoconnect disattivato evita agganci
# a sorpresa, ipv6 disattivato elimina rumore di router advertisement.
#
# Nota: con una sola interfaccia Wi-Fi never-default non conserva Internet,
# perché non esiste un secondo percorso da instradare. Serve a non
# stravolgere la tabella di routing e a non far riagganciare altre reti a
# metà trasferimento. Con ethernet o tethering attivi fa invece esattamente
# ciò che promette.
PROFILE_SETTINGS = [
    ("ipv4.never-default", "yes"),
    ("ipv4.route-metric", "900"),
    ("ipv6.method", "disabled"),
    ("connection.autoconnect", "no"),
]


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

    def _checked(self, *args: str) -> CommandResult:
        result = self._nmcli(*args)
        if result.returncode != 0:
            raise NetworkUnavailable(
                result.stderr.strip() or
                f"nmcli {' '.join(args)} è terminato con codice {result.returncode}."
            )
        return result

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

    def scan_ssids(self) -> list[str]:
        """Le reti Wi-Fi visibili, quelle che sembrano fotocamere per prime.

        Serve perché l'SSID della fotocamera è lungo e scomodo da digitare.
        Una scansione fallita restituisce una lista vuota invece di
        sollevare: l'utente deve poter comunque inserire l'SSID a mano.
        """
        result = self._nmcli("-t", "-f", "SIGNAL,SSID", "device", "wifi", "list")
        if result.returncode != 0:
            return []

        seen: list[str] = []
        for line in result.stdout.splitlines():
            signal, _, ssid = line.partition(":")
            ssid = ssid.replace("\\:", ":").strip()
            if not ssid or ssid in seen:
                continue         # reti nascoste e access point duplicati
            seen.append(ssid)

        # nmcli già ordina per potenza; qui basta portare avanti le
        # fotocamere mantenendo stabile il resto.
        return sorted(seen, key=lambda s: not looks_like_camera(s))

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

    # -- profilo NetworkManager -------------------------------------------

    def profile_exists(self) -> bool:
        result = self._nmcli("-t", "-f", "NAME", "connection", "show")
        return self.profile_name in result.stdout.splitlines()

    def ensure_profile(self, password: str | None = None) -> None:
        """Crea o aggiorna il nostro profilo. Non tocca nessun altro profilo.

        La password viene passata a NetworkManager, che la conserva: questa
        applicazione non la scrive mai su disco.
        """
        if not self.ssid:
            raise NetworkUnavailable("SSID della fotocamera non configurato.")

        settings = [part for setting in PROFILE_SETTINGS for part in setting]

        if not self.profile_exists():
            if not password:
                raise NetworkUnavailable(
                    f"Profilo «{self.profile_name}» assente: serve la password "
                    "del Wi-Fi della fotocamera per crearlo."
                )
            self._checked(
                "connection", "add",
                "type", "wifi",
                "con-name", self.profile_name,
                "ssid", self.ssid,
                "--",
                "802-11-wireless.ssid", self.ssid,
                "wifi-sec.key-mgmt", "wpa-psk",
                "wifi-sec.psk", password,
                *settings,
            )
            return

        # Profilo esistente: riallinea le impostazioni, che potrebbero essere
        # state create da una versione precedente o modificate a mano.
        self._checked("connection", "modify", self.profile_name, *settings)
        if password:
            self._checked(
                "connection", "modify", self.profile_name,
                "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password,
            )

    def connect(self) -> None:
        self._checked("connection", "up", self.profile_name)

    def disconnect(self) -> None:
        self._checked("connection", "down", self.profile_name)
