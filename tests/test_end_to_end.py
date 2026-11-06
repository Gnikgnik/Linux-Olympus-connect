"""Il client VERO, via HTTP vero, contro una finta fotocamera locale.

Esiste per un difetto preciso: dopo il ridisegno miniature e anteprima non
arrivavano più, e nessun test se ne accorgeva. I test della finestra usano
client finti, e gli screenshot iniettavano le miniature direttamente nel
modello: il percorso reale (OlympusClient nuovo per ogni worker, che non ha
mai interrogato la fotocamera) non veniva mai esercitato. Qui sì.

Il server risponde con le risposte registrate dalla E-M10 Mark IV.
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PyQt6.QtCore import QItemSelectionModel, QSettings

from olympus_connect.core.network import Diagnosis, Rung
from olympus_connect.core.protocol import OlympusClient
from olympus_connect.gui.main_window import MainWindow

FIXTURES = Path(__file__).parent / "fixtures"


def _jpeg(width=160, height=120) -> bytes:
    from PyQt6.QtCore import QBuffer, QByteArray
    from PyQt6.QtGui import QPixmap
    pixmap = QPixmap(width, height)
    pixmap.fill()
    dati = QByteArray()
    buffer = QBuffer(dati)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "JPEG")
    return bytes(dati)


@pytest.fixture
def fake_camera(qapp):
    miniatura, anteprima = _jpeg(), _jpeg(1600, 1200)
    richieste: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, body: bytes, ctype: str) -> None:
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
            richieste.append(nome)
            if nome == "get_commandlist.cgi":
                return self._send((FIXTURES / "commandlist.xml").read_bytes(), "text/xml")
            if nome == "get_caminfo.cgi":
                return self._send((FIXTURES / "caminfo.xml").read_bytes(), "text/xml")
            if nome in ("switch_cammode.cgi", "set_timeout.cgi"):
                return self._send(b"", "text/plain")
            if nome == "get_imglist.cgi":
                cartella = parse_qs(url.query)["DIR"][0]
                f = "imglist_dcim.csv" if cartella == "/DCIM" else "imglist_dcim_100olymp.csv"
                return self._send((FIXTURES / f).read_bytes(), "text/plain")
            if nome == "get_exif.cgi":
                return self._send((FIXTURES / "exif" / "orf.bin").read_bytes(),
                                  "application/octet-stream")
            if nome == "get_thumbnail.cgi":
                return self._send(miniatura, "image/jpeg")
            if nome == "get_screennail.cgi":
                return self._send(anteprima, "image/jpeg")
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.richieste = richieste
    server.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    yield server
    server.shutdown()
    server.server_close()


class _Net:
    def __init__(self, ssid):
        self.ssid = ssid

    def diagnose(self):
        return Diagnosis([Rung("porta 80 della fotocamera", True, "risponde")])


def _attendi(qapp, condizione, secondi=10.0) -> bool:
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        qapp.processEvents()
        if condizione():
            return True
        time.sleep(0.01)
    return condizione()


def test_real_client_loads_listing_thumbnails_and_preview(qapp, fake_camera,
                                                          tmp_path):
    impostazioni = QSettings(str(tmp_path / "e2e.ini"), QSettings.Format.IniFormat)
    impostazioni.setValue("dest_root", str(tmp_path / "dest"))
    finestra = MainWindow(
        client_factory=lambda: OlympusClient(base_url=fake_camera.base_url),
        network_factory=_Net,
        settings=impostazioni,
    )
    try:
        finestra.resize(1366, 740)
        finestra.show()
        finestra.on_refresh()

        assert _attendi(qapp, lambda: finestra._model.rowCount() == 1161), \
            "l'elenco della scheda non è arrivato"
        assert _attendi(qapp, lambda: len(finestra._model._thumbnails) > 0), \
            "nessuna miniatura: le richieste non raggiungono il worker"

        finestra._grid.selectionModel().select(
            finestra._model.index(5, 0),
            QItemSelectionModel.SelectionFlag.Select |
            QItemSelectionModel.SelectionFlag.Rows)
        assert _attendi(qapp, finestra._preview.has_image), \
            f"anteprima assente: {finestra._preview.text()!r}"

        # Virtualizzazione: si chiedono le miniature di ciò che si vede, non
        # 1161 richieste alla fotocamera.
        miniature = fake_camera.richieste.count("get_thumbnail.cgi")
        assert 0 < miniature < 100
    finally:
        finestra.close()
        qapp.processEvents()


def test_real_client_shows_shot_data_of_the_selected_raw(qapp, fake_camera, tmp_path):
    if not (FIXTURES / "exif" / "orf.bin").exists():
        pytest.skip("fixture EXIF assente: eseguire diag.py con la fotocamera")
    impostazioni = QSettings(str(tmp_path / "e2e.ini"), QSettings.Format.IniFormat)
    impostazioni.setValue("dest_root", str(tmp_path / "dest"))
    finestra = MainWindow(
        client_factory=lambda: OlympusClient(base_url=fake_camera.base_url),
        network_factory=_Net,
        settings=impostazioni,
    )
    try:
        finestra.resize(1366, 740)
        finestra.show()
        finestra.on_refresh()
        assert _attendi(qapp, lambda: finestra._model.rowCount() == 1161)

        modello = finestra._model
        riga = next(r for r in range(modello.rowCount())
                    if modello.job_at(modello.index(r, 0)).media.name.upper()
                    .endswith(".ORF"))
        finestra._grid.selectionModel().select(
            finestra._model.index(riga, 0),
            QItemSelectionModel.SelectionFlag.Select |
            QItemSelectionModel.SelectionFlag.Rows)

        assert _attendi(qapp, lambda: not finestra._shot.isHidden()), \
            "la sezione Scatto non è comparsa"
        assert not finestra._shot.is_unavailable(), \
            "la sezione dice «non disponibili» sui byte veri della fotocamera"
        assert "ISO" in finestra._shot.headline_text()
        assert "get_exif.cgi" in fake_camera.richieste
    finally:
        finestra.close()
        qapp.processEvents()
