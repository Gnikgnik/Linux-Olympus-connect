"""Il ricevitore del live, con un finto mittente UDP su 127.0.0.1."""
import socket
import time

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QThread
from PyQt6.QtGui import QImage

from tests.live_helpers import frame_packets, padded_jpeg
from olympus_connect.gui.liveview import LiveViewWorker


def _jpeg(larghezza, altezza=30) -> bytes:
    immagine = QImage(larghezza, altezza, QImage.Format.Format_RGB32)
    immagine.fill(0xFF336699)
    dati = QByteArray()
    buffer = QBuffer(dati)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    immagine.save(buffer, "JPEG")
    return bytes(dati)


def _attendi(qapp, condizione, secondi=3.0):
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        qapp.processEvents()
        if condizione():
            return True
        time.sleep(0.01)
    return condizione()


@pytest.fixture
def live(qapp):
    """Un worker su un thread, e il finto mittente che fa da fotocamera."""
    fotocamera = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fotocamera.bind(("127.0.0.1", 0))
    fotocamera.settimeout(1.0)
    creati = []

    def make(**kw):
        worker = LiveViewWorker(camera=fotocamera.getsockname(), bind_host="127.0.0.1", **kw)
        eventi = {"porta": [], "fotogrammi": 0, "stalled": 0, "no_signal": 0, "resumed": 0}
        worker.listening.connect(eventi["porta"].append)
        worker.frame_ready.connect(lambda: eventi.__setitem__("fotogrammi", eventi["fotogrammi"] + 1))
        worker.stalled.connect(lambda: eventi.__setitem__("stalled", eventi["stalled"] + 1))
        worker.no_signal.connect(lambda: eventi.__setitem__("no_signal", eventi["no_signal"] + 1))
        worker.resumed.connect(lambda: eventi.__setitem__("resumed", eventi["resumed"] + 1))
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        thread.start()
        creati.append((worker, thread))
        assert _attendi(qapp, lambda: eventi["porta"]), "il worker non è in ascolto"
        return worker, eventi, ("127.0.0.1", eventi["porta"][0])

    yield make, fotocamera
    for worker, thread in creati:
        worker.stop()
        thread.quit()
        assert thread.wait(3000), "il ciclo di ricezione non si è fermato"
    fotocamera.close()


def _manda(fotocamera, destinazione, jpeg, seq):
    for p in frame_packets(jpeg, seq=seq):
        fotocamera.sendto(p, destinazione)
    return seq + len(frame_packets(jpeg, seq=seq))


def test_arm_opens_the_path_with_one_datagram_to_the_camera(qapp, live):
    make, fotocamera = live
    worker, _eventi, porta = make()
    worker.arm()
    dati, origine = fotocamera.recvfrom(100)
    assert dati == b"\x00" and origine == porta


def test_delivers_frames_and_keeps_only_the_latest(qapp, live):
    make, fotocamera = live
    worker, eventi, destinazione = make()
    seq = 1
    for larghezza in (40, 50, 60):
        seq = _manda(fotocamera, destinazione, _jpeg(larghezza), seq)
    assert _attendi(qapp, lambda: eventi["fotogrammi"] >= 1)
    time.sleep(0.2)
    qapp.processEvents()
    assert eventi["fotogrammi"] == 1, "senza take_frame non deve accumulare notifiche"
    assert worker.take_frame().width() == 60
    assert worker.take_frame() is None
    _manda(fotocamera, destinazione, _jpeg(70), seq)
    assert _attendi(qapp, lambda: eventi["fotogrammi"] == 2)
    assert worker.take_frame().width() == 70


def test_a_frame_rebuilt_from_padding_decodes(qapp, live):
    make, fotocamera = live
    worker, eventi, destinazione = make()
    _manda(fotocamera, destinazione, padded_jpeg(_jpeg(48), 30000), 1)
    assert _attendi(qapp, lambda: eventi["fotogrammi"] == 1)
    assert worker.take_frame().width() == 48


def test_no_signal_when_nothing_arrives_after_arming(qapp, live):
    make, _fotocamera = live
    worker, eventi, _ = make(no_signal_after=0.3)
    worker.arm()
    assert _attendi(qapp, lambda: eventi["no_signal"] == 1, 2.0)


def test_no_sentinel_before_arming(qapp, live):
    make, _fotocamera = live
    _worker, eventi, _ = make(no_signal_after=0.2, stall_after=0.2)
    time.sleep(0.6)
    qapp.processEvents()
    assert eventi["no_signal"] == 0 and eventi["stalled"] == 0


def test_stalled_then_resumed(qapp, live):
    make, fotocamera = live
    worker, eventi, destinazione = make(stall_after=0.3)
    worker.arm()
    seq = _manda(fotocamera, destinazione, _jpeg(40), 1)
    assert _attendi(qapp, lambda: eventi["fotogrammi"] == 1)
    worker.take_frame()
    assert _attendi(qapp, lambda: eventi["stalled"] == 1, 2.0)
    _manda(fotocamera, destinazione, _jpeg(40), seq)
    assert _attendi(qapp, lambda: eventi["resumed"] == 1)


def test_disarm_silences_the_sentinel(qapp, live):
    make, _fotocamera = live
    worker, eventi, _ = make(no_signal_after=0.2)
    worker.arm()
    worker.disarm()
    time.sleep(0.5)
    qapp.processEvents()
    assert eventi["no_signal"] == 0


def test_garbage_produces_no_frame(qapp, live):
    make, fotocamera = live
    _worker, eventi, destinazione = make()
    for dati in (b"", b"\x00" * 5, b"\x80" * 1500):
        fotocamera.sendto(dati, destinazione)
    time.sleep(0.3)
    qapp.processEvents()
    assert eventi["fotogrammi"] == 0
