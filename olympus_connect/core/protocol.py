"""Client HTTP per il protocollo OPC delle fotocamere Olympus.

La fotocamera in modalità access point espone un server HTTP su
192.168.0.10. Verificato sul campo (E-M10 Mark IV, firmware 4.50): risponde
anche senza lo User-Agent di OI.Share, ma non serve un indice di directory,
quindi senza conoscere gli endpoint CGI non c'è modo di arrivare ai file.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import requests

from .errors import CameraAsleep, CameraNotReachable, OlympusError, ProtocolError
from .model import CHUNK_SIZE, MediaFile, parse_imglist

BASE_URL = "http://192.168.0.10"

HEADERS = {
    "User-Agent": "OI.Share v2",   # non obbligatorio, ma è ciò che manda OI.Share
    "Host": "192.168.0.10",
}

TIMEOUT = (3, 15)   # (connect, read) in secondi

# Misurato sul campo: 30 fotogrammi al secondo a 640×480 e a 1024×768,
# circa 15 a 1280×960. La più alta che resta fluida.
LIVE_QUALITY = "1024x0768"

_ERRORCODE = re.compile(rb"<errorcode>\s*(\d+)\s*</errorcode>")


@dataclass
class Stream:
    """Un trasferimento in corso.

    resumed è True solo se il server ha risposto 206: è l'unico segnale
    affidabile che il Range è stato onorato e che si sta riprendendo
    invece di ricominciare. La E-M10 Mark IV risponde sempre 200, quindi
    in pratica resumed è sempre False su questa fotocamera.
    """
    total: int
    resumed: bool
    chunks: Iterator[bytes]


def _total_from_content_range(value: str) -> int | None:
    # "bytes 500-999/1000" -> 1000
    _, _, denominator = value.partition("/")
    return int(denominator) if denominator.isdigit() else None


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
            trovato = _ERRORCODE.search(response.content or b"")
            codice = int(trovato.group(1)) if trovato else None
            dettaglio = f", codice {codice}." if codice is not None else "."
            raise ProtocolError(
                f"{path} ha risposto HTTP {response.status_code}{dettaglio}",
                response.content, code=codice,
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

    def has_screennail(self) -> bool:
        """La fotocamera genera anteprime grandi?

        Verificato sulla E-M10 Mark IV: caminfo dichiara
        GetImageScreennailSupport e l'endpoint restituisce circa 1 MB anche
        per un ORF. Dove manca, l'anteprima si disattiva invece di produrre
        un errore a ogni selezione.
        """
        return "get_screennail" in self.commands

    def screennail(self, camera_path: str) -> bytes:
        """Anteprima JPEG grande: permette di giudicare uno scatto prima di
        scaricarlo, cosa che una miniatura da 9 KB non consente."""
        return self._get_ok("get_screennail.cgi", DIR=camera_path).content

    def has_exif(self) -> bool:
        """La fotocamera restituisce i dati di scatto di un file?

        Dichiarato dalla E-M10 Mark IV; formato e tempi misurati sul campo,
        vedi la specifica 2026-11-06-dati-di-scatto-design.md. Dove manca,
        la sezione dei dati di scatto non compare.
        """
        return "get_exif" in self.commands

    def exif(self, camera_path: str) -> bytes:
        """I dati di scatto grezzi di un file, da leggere con parse_exif."""
        return self._get_ok("get_exif.cgi", DIR=camera_path).content

    # -- modalità scatto (telecomando) --------------------------------------

    def enter_rec_mode(self, quality: str = LIVE_QUALITY) -> None:
        """Modalità scatto con la qualità del live richiesta.

        Verificato sul campo: un mode=rec mandato quando la fotocamera è già
        in rec ignora lvqty. Per cambiare qualità si passa dalla riproduzione.
        """
        self._get_ok("switch_cammode.cgi", mode="play")
        self._get_ok("switch_cammode.cgi", mode="rec", lvqty=quality)
        self._mode = "rec"

    def start_liveview(self, port: int) -> None:
        self._get_ok("exec_takemisc.cgi", com="startliveview", port=port)

    def stop_liveview(self) -> None:
        self._get_ok("exec_takemisc.cgi", com="stopliveview")

    def take_mode(self) -> str:
        """La modalità della ghiera: P, A, S, M, iAuto…"""
        root = self._xml(self._get_ok("get_camprop.cgi", com="get", propname="takemode"))
        return (root.findtext("value") or "").strip()

    def touch_area(self) -> tuple[int, int, int, int] | None:
        """L'area del live in cui la messa a fuoco col tocco è accettata:
        (x, y, larghezza, altezza). La fotocamera la descrive come
        «0096-0072_0448x0336» nella voce touchactiveframe di desclist."""
        root = self._xml(self._get_ok("get_camprop.cgi", com="desc", propname="desclist"))
        for desc in root.iter("desc"):
            if (desc.findtext("propname") or "").strip() != "touchactiveframe":
                continue
            trovato = re.fullmatch(r"\s*(\d+)-(\d+)_(\d+)x(\d+)\s*", desc.findtext("value") or "")
            if trovato:
                x, y, w, h = (int(g) for g in trovato.groups())
                return x, y, w, h
        return None

    def assign_af(self, x: int, y: int) -> bool | None:
        """Mette a fuoco nel punto (x, y) del live. Solo con il live acceso.

        True a fuoco, False messa a fuoco fallita, None se la risposta non
        dice l'esito."""
        response = self._get_ok("exec_takemotion.cgi", com="assignafframe",
                                point=f"{x:04d}x{y:04d}")
        return _focus_result(response.content)

    def release_af(self) -> None:
        self._get_ok("exec_takemotion.cgi", com="releaseafframe")

    def take_picture(self) -> None:
        """Scatta. Solo con il live acceso. stoptake segue sempre starttake,
        anche quando la fotocamera dice che lo scatto non è riuscito."""
        response = self._get_ok("exec_takemotion.cgi", com="starttake")
        self._get_ok("exec_takemotion.cgi", com="stoptake")
        esito = _xml_text(response.content, "take")
        if esito is not None and esito != "ok":
            raise ProtocolError("La fotocamera non ha scattato.", response.content)

    def start_movie(self) -> None:
        """Inizia un video lasciando acceso il live (che passa a 16:9)."""
        self._get_ok("exec_takemotion.cgi", com="startmovietake", liveview="on")

    def stop_movie(self) -> None:
        self._get_ok("exec_takemotion.cgi", com="stopmovietake")

    def rec_view(self) -> bytes:
        """L'immagine di revisione dell'ultimo scatto: circa 40-50 KB in 45 ms
        con il live acceso. Con il live spento la fotocamera non risponde."""
        response = self._get_ok("exec_takemisc.cgi", com="getrecview")
        if response.content[:2] != b"\xff\xd8":
            raise ProtocolError("Immagine di revisione non valida.", response.content)
        return response.content

    # -- download ---------------------------------------------------------

    @contextmanager
    def open_stream(self, camera_path: str, offset: int = 0) -> Iterator[Stream]:
        """Scarica un file in streaming, opzionalmente riprendendo da offset.

        Il chiamante deve controllare `stream.resumed` prima di appendere a
        un file parziale: se il server ha ignorato il Range il corpo parte
        da zero, e appenderlo produrrebbe un file corrotto.
        """
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        response = self._get(camera_path, stream=True, headers=headers)
        try:
            if response.status_code not in (200, 206):
                raise ProtocolError(
                    f"{camera_path} ha risposto HTTP {response.status_code}.",
                    response.content,
                )

            resumed = response.status_code == 206
            total: int | None = None
            if resumed:
                total = _total_from_content_range(
                    response.headers.get("Content-Range", "")
                )
            if total is None:
                length = response.headers.get("Content-Length")
                total = int(length) if length and length.isdigit() else 0
                if resumed:
                    total += offset

            yield Stream(total=total, resumed=resumed,
                         chunks=response.iter_content(CHUNK_SIZE))
        finally:
            response.close()


def _xml_text(body: bytes, tag: str) -> str | None:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None
    nodo = root if root.tag == tag else root.find(f".//{tag}")
    return nodo.text.strip() if nodo is not None and nodo.text else None


def _focus_result(body: bytes) -> bool | None:
    esito = _xml_text(body, "affocus")
    return None if esito is None else esito == "ok"
