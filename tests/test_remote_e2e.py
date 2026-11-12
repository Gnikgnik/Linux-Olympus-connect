"""Il telecomando con il client VERO, HTTP vero e UDP vero.

Una fotocamera finta risponde ai comandi con le risposte registrate dalla
E-M10 Mark IV e, alla richiesta startliveview, manda un live sintetico
alla porta richiesta, come fa la fotocamera vera.
"""
import socket
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QSettings
from PyQt6.QtGui import QImage

from tests.live_helpers import frame_packets, padded_jpeg
from olympus_connect.core.network import Diagnosis, Rung
from olympus_connect.core.protocol import OlympusClient
from olympus_connect.core.timelapse import find_ffmpeg
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
    stato = {"richieste": [], "invio": None, "modo": "play", "valori": {},
             "scatti": 0, "nuovi": [], "elenchi": []}
    mittente = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mittente.bind(("127.0.0.1", 0))
    fermo = threading.Event()
    fotogramma = padded_jpeg(_jpeg(1024, 768), 60000)
    revisione = _jpeg(320, 240)
    foto = _jpeg(640, 480)                      # 4:3, come la fotocamera
    grezzo = b"ORF finto " * 50

    def trasmetti(destinazione):
        seq = 1
        while not fermo.is_set():
            for p in frame_packets(fotogramma, seq=seq, size=(1024, 768)):
                mittente.sendto(p, destinazione)
            seq += len(frame_packets(fotogramma, seq=seq))
            time.sleep(1 / 30)

    def desclist():
        radice = ET.fromstring((FIXTURES / "remote" / "camprop_desclist.xml").read_bytes())
        for desc in radice.iter("desc"):
            nome = desc.findtext("propname")
            if nome in stato["valori"]:
                desc.find("value").text = stato["valori"][nome]
        return b'<?xml version="1.0"?>' + ET.tostring(radice)

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
                if qs["DIR"] == "/DCIM":
                    return self._send((FIXTURES / "imglist_dcim.csv").read_bytes(), "text/plain")
                stato["elenchi"].append(stato["modo"])
                righe = (FIXTURES / "imglist_dcim_100olymp.csv").read_bytes()
                for nome_file, contenuto in stato["nuovi"]:
                    righe += f"/DCIM/100OLYMP,{nome_file},{len(contenuto)},0,23626,28090\r\n".encode()
                return self._send(righe, "text/plain")
            if nome.startswith("DCIM/"):
                for nome_file, contenuto in stato["nuovi"]:
                    if nome == f"DCIM/100OLYMP/{nome_file}":
                        return self._send(contenuto, "application/octet-stream")
            if nome == "get_camprop.cgi":
                if qs.get("propname") == "takemode":
                    return self._send((FIXTURES / "remote" / "camprop_takemode.xml").read_bytes())
                return self._send(desclist())
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
                    stato["scatti"] += 1
                    n = stato["scatti"]
                    stato["nuovi"] += [(f"PB11{n:04d}.JPG", foto), (f"PB11{n:04d}.ORF", grezzo)]
                    return self._send(b'<?xml version="1.0"?><response><take>ok</take></response>')
                return self._send(b"")
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            url = urlparse(self.path)
            nome = url.path.lstrip("/")
            qs = {k: v[0] for k, v in parse_qs(url.query).items()}
            corpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if nome == "set_camprop.cgi":
                stato["valori"][qs["propname"]] = ET.fromstring(corpo).findtext("value")
                stato["richieste"].append((nome, qs))
                return self._send(b'<?xml version="1.0"?><response/>')
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

        def elenco_dopo_il_play():
            r = camera["richieste"]
            play = [i for i, (n, q) in enumerate(r) if n == "switch_cammode.cgi" and q.get("mode") == "play"]
            return any(n == "get_imglist.cgi" for n, _ in r[play[-1] + 1:]) if play else False

        assert _attendi(qapp, elenco_dopo_il_play), "l'elenco non è stato richiesto di nuovo dopo l'uscita"
    finally:
        finestra.close()
        qapp.processEvents()


def test_a_setting_changed_from_the_pc_reaches_the_camera(qapp, camera, tmp_path):
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
        assert _attendi(qapp, lambda: finestra._model.rowCount() == 1161)
        assert _attendi(qapp, lambda: finestra._view_remote.isEnabled())
        finestra._view_remote.click()
        sezione = finestra._remote_panel.props
        assert _attendi(qapp, lambda: sezione.row("isospeedvalue") is not None), \
            "le impostazioni non sono arrivate al pannello"
        sezione.row("isospeedvalue").step(1)
        assert _attendi(qapp, lambda: camera["valori"].get("isospeedvalue") == "250"), \
            "la modifica non è arrivata alla fotocamera"
        assert _attendi(qapp, lambda: sezione.row("isospeedvalue").confirmed_value() == "250"), \
            "il pannello non ha riletto il nuovo valore"
        finestra._view_grid.click()
        assert _attendi(qapp, lambda: not finestra._remote_active)
    finally:
        finestra.close()
        qapp.processEvents()


class _Blocco:
    """Al posto di SleepInhibitor: un test non deve bloccare la sospensione del PC."""

    def start(self, seconds):
        return True

    def stop(self):
        pass


def test_a_sequence_ends_in_a_folder_with_its_jpegs_and_a_video(qapp, camera, tmp_path, monkeypatch):
    if find_ffmpeg() is None:
        pytest.skip("ffmpeg non installato")
    monkeypatch.setattr(MainWindow, "SNAPSHOT_SETTLE", 0.0)
    # Se il test fallisce a sequenza in corso, la chiusura chiederebbe conferma
    # con una finestra modale e il test resterebbe appeso invece di fallire.
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: True)
    impostazioni = QSettings(str(tmp_path / "e2e.ini"), QSettings.Format.IniFormat)
    impostazioni.setValue("dest_root", str(tmp_path / "dest"))
    finestra = MainWindow(
        client_factory=lambda: OlympusClient(base_url=camera["base_url"]),
        network_factory=_Net,
        settings=impostazioni,
        live_factory=lambda: LiveViewWorker(camera=camera["udp"], bind_host="127.0.0.1"),
        inhibitor_factory=_Blocco,
    )
    try:
        finestra.resize(1366, 740)
        finestra.show()
        finestra.on_refresh()
        assert _attendi(qapp, lambda: finestra._model.rowCount() == 1161), "elenco non arrivato"
        assert _attendi(qapp, lambda: finestra._view_remote.isEnabled())
        finestra._view_remote.click()
        assert _attendi(qapp, finestra._remote_view.has_frame), "il live non è arrivato"
        assert _attendi(qapp, lambda: finestra._remote_panel.props.row("isospeedvalue") is not None)

        finestra.on_sequence_start(1, 3)                 # tre scatti: a 0, 1 e 2 secondi
        assert _attendi(qapp, lambda: finestra._sequence_dialog is not None, 30.0), \
            "la finestra di fine sequenza non è comparsa"
        assert camera["scatti"] == 3
        assert "rec" in camera["elenchi"], "l'istantanea non è stata presa in modalità scatto"
        assert camera["modo"] == "play", "a fine sequenza la fotocamera non è tornata in riproduzione"
        dialog = finestra._sequence_dialog
        assert dialog.message().startswith("Sequenza finita: 3 foto.")

        dialog._download.click()
        cartelle = lambda: [p for p in (tmp_path / "dest").glob("Sequenza *") if p.is_dir()]  # noqa: E731
        assert _attendi(qapp, lambda: len(cartelle()) == 1, 20.0), "la cartella non è stata creata"
        video = cartelle()[0] / "timelapse.mp4"
        assert _attendi(qapp, video.exists, 60.0), "il video non è stato montato"
        assert _attendi(qapp, lambda: finestra.statusBar().currentMessage().startswith("Video pronto"))
        nomi = sorted(p.name for p in cartelle()[0].iterdir())
        assert nomi == ["PB110001.JPG", "PB110002.JPG", "PB110003.JPG", "timelapse.mp4"]
        assert video.stat().st_size > 1000
        assert not list((tmp_path / "dest").rglob("*.ORF")), "i RAW dovevano restare sulla scheda"
    finally:
        finestra.close()
        qapp.processEvents()
