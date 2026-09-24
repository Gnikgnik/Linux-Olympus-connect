"""Client HTTP per il protocollo OPC delle fotocamere Olympus.

La fotocamera in modalità access point espone un server HTTP su
192.168.0.10. Verificato sul campo (E-M10 Mark IV, firmware 4.50): risponde
anche senza lo User-Agent di OI.Share, ma non serve un indice di directory,
quindi senza conoscere gli endpoint CGI non c'è modo di arrivare ai file.
"""
from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from contextlib import contextmanager
from typing import Iterator

import requests

from .errors import CameraAsleep, CameraNotReachable, OlympusError, ProtocolError
from .model import MediaFile, parse_imglist

BASE_URL = "http://192.168.0.10"

HEADERS = {
    "User-Agent": "OI.Share v2",   # non obbligatorio, ma è ciò che manda OI.Share
    "Host": "192.168.0.10",
}

TIMEOUT = (3, 15)   # (connect, read) in secondi


class OlympusClient:
    def __init__(self, base_url: str = BASE_URL,
                 session: requests.Session | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self.commands: set[str] = set()
        self.model: str = ""
        self._mode: str | None = None

    # -- livello trasporto ------------------------------------------------

    def _get(self, path: str, *, stream: bool = False,
             headers: dict[str, str] | None = None, **params) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            return self._session.get(
                url, headers={**HEADERS, **(headers or {})},
                params=params, timeout=TIMEOUT, stream=stream,
            )
        except requests.exceptions.ConnectionError as exc:
            raise CameraNotReachable(
                "La fotocamera non risponde su 192.168.0.10."
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise CameraAsleep(
                "La fotocamera non ha risposto entro il tempo previsto."
            ) from exc

    def _get_ok(self, path: str, **params) -> requests.Response:
        response = self._get(path, **params)
        if response.status_code != 200:
            raise ProtocolError(
                f"{path} ha risposto HTTP {response.status_code}.", response.content
            )
        return response

    @staticmethod
    def _xml(response: requests.Response) -> ElementTree.Element:
        try:
            return ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise ProtocolError(
                f"Risposta XML non interpretabile da {response.url}.", response.content
            ) from exc

    # -- identificazione --------------------------------------------------

    def connect(self) -> str:
        """Interroga la fotocamera su cosa supporta. Restituisce il modello."""
        root = self._xml(self._get_ok("get_commandlist.cgi"))
        self.commands = {
            elem.attrib["name"].strip()
            for elem in root.iter("cgi") if "name" in elem.attrib
        }
        if not self.commands:
            raise ProtocolError("get_commandlist.cgi non elenca alcun comando.")

        info = self._xml(self._get_ok("get_caminfo.cgi"))
        model = info.findtext("model")
        self.model = model.strip() if model else "modello sconosciuto"
        return self.model

    def ensure_play_mode(self) -> None:
        """La fotocamera elenca i file solo in modalità riproduzione."""
        if self._mode == "play":
            return
        self._get_ok("switch_cammode.cgi", mode="play")
        self._mode = "play"

    def keep_awake(self, seconds: int = 3600) -> bool:
        """Chiede alla fotocamera di non spegnere l'access point.

        Restituisce False se la fotocamera non dichiara set_timeout o se la
        richiesta fallisce: è un'ottimizzazione, non un requisito, e non
        deve mai impedire di scaricare.

        Sulla E-M10 Mark IV con firmware 4.50 restituisce sempre False: il
        comando è dichiarato ma risponde HTTP 520 errorcode 1006 (misurato,
        vedi docs/field-report-2026-09-24.md). Il metodo resta per gli
        altri modelli.
        """
        if "set_timeout" not in self.commands:
            return False
        try:
            self._get_ok("set_timeout.cgi", timeoutsec=seconds)
            return True
        except OlympusError:
            return False

    # -- elenco e miniature -----------------------------------------------

    def list_media(self, directory: str = "/DCIM") -> list[MediaFile]:
        """Elenca ricorsivamente i file multimediali sulla scheda."""
        self.ensure_play_mode()
        found: list[MediaFile] = []
        pending = [directory]
        seen: set[str] = set()

        while pending:
            current = pending.pop()
            if current in seen:        # difesa contro directory cicliche
                continue
            seen.add(current)

            response = self._get("get_imglist.cgi", DIR=current)
            if response.status_code == 404:
                continue               # directory vuota, non un errore
            if response.status_code != 200:
                raise ProtocolError(
                    f"get_imglist.cgi su {current} ha risposto "
                    f"HTTP {response.status_code}.", response.content
                )

            files, subdirs = parse_imglist(response.text)
            found.extend(files)
            pending.extend(subdirs)

        return found

    def thumbnail(self, camera_path: str) -> bytes:
        """Miniatura JPEG. Verificata funzionante su JPG, ORF e MOV (8-13 KB)."""
        return self._get_ok("get_thumbnail.cgi", DIR=camera_path).content
