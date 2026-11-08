import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Va impostato prima che Qt inizializzi la piattaforma: i test GUI non
# richiedono un display, e senza questo falliscono in CI o via SSH.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """Un solo QApplication per sessione: Qt non ne ammette due."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class FlakyCamera:
    """Server HTTP locale che imita la fotocamera, inclusi i suoi difetti.

    I due difetti sono indipendenti e vanno combinabili, perché la
    combinazione è il caso reale: la E-M10 Mark IV **ignora il Range**, e
    una caduta a metà su quel firmware costa l'intero file.

    honour_range=False  risponde sempre 200 col file intero, come fa la
                        fotocamera reale
    drop_once=True      chiude la connessione a metà del corpo, una sola
                        volta: il tentativo successivo va a buon fine
    """

    def __init__(self, payload: bytes, *, honour_range: bool = True,
                 drop_once: bool = False) -> None:
        self.payload = payload
        self.honour_range = honour_range
        self.drop_once = drop_once
        self.dropped = False
        self.requests: list[tuple[str, str | None]] = []

        camera = self

        class Handler(BaseHTTPRequestHandler):
            # HTTP/1.1 perché serve il Content-Length per far rilevare al
            # client il corpo troncato, ma ogni risposta chiude la
            # connessione: in keep-alive l'handler resterebbe in attesa
            # della richiesta successiva e shutdown() non rientrerebbe.
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass   # silenzio nei test

            def do_GET(self):
                rng = self.headers.get("Range")
                camera.requests.append((self.path, rng))

                start = 0
                if rng and camera.honour_range:
                    start = int(rng.removeprefix("bytes=").split("-")[0])
                    self.send_response(206)
                    self.send_header(
                        "Content-Range",
                        f"bytes {start}-{len(camera.payload) - 1}"
                        f"/{len(camera.payload)}",
                    )
                else:
                    self.send_response(200)

                body = camera.payload[start:]
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

                if camera.drop_once and not camera.dropped:
                    camera.dropped = True
                    self.wfile.write(body[: len(body) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                    return

                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def fixture_bytes():
    """Legge una fixture registrata dalla fotocamera reale (Task 0)."""
    def _read(name: str) -> bytes:
        path = FIXTURES / name
        if not path.exists():
            pytest.skip(f"fixture {name} assente: eseguire diag.py con la fotocamera")
        return path.read_bytes()
    return _read


@pytest.fixture(autouse=True)
def _finestre_distrutte_nel_thread_principale():
    """Dopo ogni test, le finestre chiuse muoiono qui e non a caso altrove.

    Senza questo una finestra chiusa resta viva finché la raccolta dei rifiuti
    di Python non la trova, e può trovarla mentre gira un thread di lavoro:
    il C++ della finestra verrebbe distrutto fuori dal thread della GUI, con
    un'animazione ancora registrata. Il risultato era un crash raro e casuale.
    """
    yield
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication
    if QApplication.instance() is None:
        return
    for finestra in QApplication.topLevelWidgets():
        if not finestra.isVisible():
            finestra.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
