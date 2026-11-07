"""Il telecomando con il client VERO, HTTP vero e UDP vero.

Una fotocamera finta risponde ai comandi con le risposte registrate dalla
E-M10 Mark IV e, alla richiesta startliveview, manda un live sintetico
alla porta richiesta, come fa la fotocamera vera.
"""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QSettings
from PyQt6.QtGui import QImage

from tests.live_helpers import frame_packets, padded_jpeg
from olympus_connect.core.network import Diagnosis, Rung
from olympus_connect.core.protocol import OlympusClient
from olympus_connect.gui.liveview import LiveViewWorker
from olympus_connect.gui.main_window import MainWindow

FIXTURES = Path(__file__).parent / "fixtures"


def _jpeg(w, h) -> bytes:
    immagine = QImage(w, h, QImage.Format.Format_RGB32)
    immagine.fill(0xFF406080)
    dati = QByteArray()
    buffer = QBuffer(dati)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    immagine.save(buffer, "JPEG")
    return bytes(dati)


class _Net:
    def __init__(self, ssid):
        self.ssid = ssid

    def diagnose(self):
        return Diagnosis([Rung("porta 80 della fotocamera", True, "risponde")])


def _attendi(qapp, condizione, secondi=10.0):
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        qapp.processEvents()
        if condizione():
            return True
        time.sleep(0.01)
    return condizione()


@pytest.fixture
def camera(qapp):
    stato = {"richieste": [], "invio": None, "modo": "play"}
    mittente = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mittente.bind(("127.0.0.1", 0))
    fermo = threading.Event()
    fotogramma = padded_jpeg(_jpeg(1024, 768), 60000)
    revisione = _jpeg(320, 240)

    def trasmetti(destinazione):
        seq = 1
        while not fermo.is_set():
            for p in frame_packets(fotogramma, seq=seq, size=(1024, 768)):
                mittente.sendto(p, destinazione)
            seq += len(frame_packets(fotogramma, seq=seq))
            time.sleep(1 / 30)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, body, ctype="text/xml"):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_GET(self):
            url = urlparse(self.path)
            nome = url.path.lstrip("/")
            qs = {k: v[0] for k, v in parse_qs(url.query).items()}
            stato["richieste"].append((nome, qs))
            if nome == "get_commandlist.cgi":
                return self._send((FIXTURES / "commandlist.xml").read_bytes())
            if nome == "get_caminfo.cgi":
                return self._send((FIXTURES / "caminfo.xml").read_bytes())
            if nome == "switch_cammode.cgi":
                stato["modo"] = qs.get("mode")
                return self._send(b"")
            if nome == "get_imglist.cgi":
                f = "imglist_dcim.csv" if qs["DIR"] == "/DCIM" else "imglist_dcim_100olymp.csv"
                return self._send((FIXTURES / f).read_bytes(), "text/plain")
            if nome == "get_camprop.cgi":
                f = "camprop_takemode.xml" if qs.get("propname") == "takemode" else "camprop_desclist.xml"
                return self._send((FIXTURES / "remote" / f).read_bytes())
            if nome == "exec_takemisc.cgi" and qs.get("com") == "startliveview":
                fermo.clear()
                destinazione = (self.client_address[0], int(qs["port"]))
                stato["invio"] = threading.Thread(target=trasmetti, args=(destinazione,), daemon=True)
                stato["invio"].start()
                return self._send(b'<?xml version="1.0"?><funcidlist/>')
            if nome == "exec_takemisc.cgi" and qs.get("com") == "stopliveview":
                fermo.set()
                return self._send(b"")
            if nome == "exec_takemisc.cgi" and qs.get("com") == "getrecview":
                return self._send(revisione, "image/jpeg")
            if nome == "exec_takemotion.cgi":
                if qs.get("com") == "assignafframe":
                    return self._send((FIXTURES / "remote" / "af.txt").read_bytes())
                if qs.get("com") == "starttake":
                    return self._send(b'<?xml version="1.0"?><response><take>ok</take></response>')
                return self._send(b"")
            if nome in ("get_thumbnail.cgi", "set_timeout.cgi", "get_exif.cgi", "get_screennail.cgi"):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stato["base_url"] = f"http://127.0.0.1:{server.server_address[1]}"
    stato["udp"] = mittente.getsockname()
    yield stato
    fermo.set()
    if stato["invio"] is not None:
        stato["invio"].join(timeout=2)
    server.shutdown()
    server.server_close()
    mittente.close()


def test_remote_shows_the_live_shoots_and_returns_to_the_library(qapp, camera, tmp_path):
    impostazioni = QSettings(str(tmp_path / "e2e.ini"), QSettings.Format.IniFormat)
    impostazioni.setValue("dest_root", str(tmp_path / "dest"))
    finestra = MainWindow(
        client_factory=lambda: OlympusClient(base_url=camera["base_url"]),
        network_factory=_Net,
        settings=impostazioni,
        live_factory=lambda: LiveViewWorker(camera=camera["udp"], bind_host="127.0.0.1"),
    )
    try:
        finestra.resize(1366, 740)
        finestra.show()
        finestra.on_refresh()
        assert _attendi(qapp, lambda: finestra._model.rowCount() == 1161), "elenco non arrivato"
        assert _attendi(qapp, lambda: finestra._view_remote.isEnabled()), "nessuna connessione"

        finestra._view_remote.click()
        assert _attendi(qapp, finestra._remote_view.has_frame), "il live non è arrivato alla vista"
        assert finestra._remote_view.frame_size() == (1024, 768)
        assert _attendi(qapp, lambda: finestra._remote_panel.take_mode_text() == "S"), "modo di scatto non letto"

        finestra._remote_panel._shoot.click()
        assert _attendi(qapp, finestra._remote_view.is_reviewing), "nessuna revisione dopo lo scatto"

        finestra._view_grid.click()
        assert _attendi(qapp, lambda: not finestra._remote_active), "uscita non completata"
        assert _attendi(qapp, lambda: finestra._model.rowCount() == 1161), "elenco non rifatto"
        modi = [qs.get("mode") for nome, qs in camera["richieste"] if nome == "switch_cammode.cgi"]
        assert "rec" in modi and modi[-1] == "play"
    finally:
        finestra.close()
        qapp.processEvents()
