"""Vista del live e pannello dei comandi."""
import time

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QPoint, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest

from olympus_connect.gui.remote import RemotePanel, RemoteView, fit_rect, map_to_frame


def _jpeg(w=64, h=48) -> bytes:
    immagine = QImage(w, h, QImage.Format.Format_RGB32)
    immagine.fill(0xFF808080)
    dati = QByteArray()
    buffer = QBuffer(dati)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    immagine.save(buffer, "JPEG")
    return bytes(dati)


# --- geometria -------------------------------------------------------------------

def test_fit_rect_letterboxes_a_wider_container():
    assert fit_rect((1000, 480), (640, 480)) == (180.0, 0.0, 640.0, 480.0)


def test_fit_rect_pillarboxes_a_taller_container():
    assert fit_rect((640, 1000), (640, 360)) == (0.0, 320.0, 640.0, 360.0)


def test_map_to_frame_through_the_bands():
    # contenitore 1280×480, fotogramma 640×480 scalato 1:1 al centro
    assert map_to_frame((640, 240), (1280, 480), (640, 480), None) == (320, 240)
    assert map_to_frame((100, 240), (1280, 480), (640, 480), None) is None  # sulla banda


def test_map_to_frame_scales_with_the_frame():
    # 1024×768 mostrato in 512×384: ogni pixel a schermo vale 2
    assert map_to_frame((256, 192), (512, 384), (1024, 768), None) == (512, 384)


def test_map_to_frame_clamps_to_the_touch_area():
    area = (96, 72, 448, 336)
    assert map_to_frame((0, 0), (640, 480), (640, 480), area) == (96, 72)
    assert map_to_frame((639, 479), (640, 480), (640, 480), area) == (543, 407)


def test_map_to_frame_never_leaves_the_frame():
    assert map_to_frame((639.7, 479.7), (640, 480), (640, 480), None) == (639, 479)


def test_touch_area_is_cut_to_a_16_9_frame():
    assert map_to_frame((639, 359), (640, 360), (640, 360), (96, 72, 448, 336)) == (543, 359)


# --- RemoteView ------------------------------------------------------------------

def test_click_requests_focus_in_frame_coordinates(qapp):
    vista = RemoteView()
    vista.resize(640, 480)
    vista.set_frame(QImage(640, 480, QImage.Format.Format_RGB32))
    ricevuti = []
    vista.focus_requested.connect(lambda x, y: ricevuti.append((x, y)))
    QTest.mouseClick(vista, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(320, 240))
    assert ricevuti == [(320, 240)]


def test_no_focus_without_a_frame_or_when_disabled(qapp):
    vista = RemoteView()
    vista.resize(640, 480)
    ricevuti = []
    vista.focus_requested.connect(lambda x, y: ricevuti.append((x, y)))
    QTest.mouseClick(vista, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(320, 240))
    vista.set_frame(QImage(640, 480, QImage.Format.Format_RGB32))
    vista.set_focus_enabled(False)
    QTest.mouseClick(vista, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(320, 240))
    assert ricevuti == []


def test_frame_size_follows_the_image(qapp):
    vista = RemoteView()
    vista.set_frame(QImage(640, 360, QImage.Format.Format_RGB32))
    assert vista.frame_size() == (640, 360)


def test_review_covers_the_live_for_two_seconds(qapp):
    vista = RemoteView()
    assert vista.show_review(_jpeg())
    assert vista.is_reviewing()
    fine = time.monotonic() + RemoteView.REVIEW_MS / 1000 + 0.5
    while time.monotonic() < fine and vista.is_reviewing():
        qapp.processEvents()
        time.sleep(0.02)
    assert not vista.is_reviewing()


def test_invalid_review_is_ignored(qapp):
    vista = RemoteView()
    assert not vista.show_review(b"non un jpeg")
    assert not vista.is_reviewing()


def test_a_frame_clears_the_message(qapp):
    vista = RemoteView()
    vista.show_message("Live interrotto", retry=True)
    assert vista.message() == "Live interrotto"
    vista.set_frame(QImage(64, 48, QImage.Format.Format_RGB32))
    assert vista.message() == ""


def test_retry_button_emits(qapp):
    vista = RemoteView()
    vista.resize(640, 480)
    vista.show()
    ricevuti = []
    vista.retry_requested.connect(lambda: ricevuti.append(True))
    vista.show_message("Live interrotto", retry=True)
    vista._retry.click()
    assert ricevuti == [True]
    vista.close()


# --- RemotePanel -----------------------------------------------------------------

def test_panel_buttons_emit(qapp):
    pannello = RemotePanel()
    eventi = []
    pannello.shoot_requested.connect(lambda: eventi.append("scatta"))
    pannello.movie_toggle_requested.connect(lambda: eventi.append("video"))
    pannello._shoot.click()
    pannello._movie.click()
    assert eventi == ["scatta", "video"]


def test_panel_recording_state_and_timer(qapp):
    pannello = RemotePanel()
    pannello.set_recording(True)
    assert pannello.is_recording()
    assert pannello._movie.text().strip() == "Ferma"
    assert pannello.elapsed_text() == "00:00"
    assert not pannello._shoot.isEnabled(), "niente foto durante un video"
    pannello.set_recording(False)
    assert pannello._movie.text().strip() == "Registra"
    assert pannello._shoot.isEnabled()


def test_panel_busy_disables_commands(qapp):
    pannello = RemotePanel()
    pannello.set_busy(True)
    assert not pannello._shoot.isEnabled() and not pannello._movie.isEnabled()
    pannello.set_busy(False)
    assert pannello._shoot.isEnabled() and pannello._movie.isEnabled()


def test_panel_shows_the_dial(qapp):
    pannello = RemotePanel()
    pannello.set_take_mode("S")
    assert pannello.take_mode_text() == "S"
    pannello.set_take_mode("")
    assert pannello.take_mode_text() == "—"


def test_af_pending_is_public(qapp):
    view = RemoteView()
    assert not view.af_pending()
    view.show_af(10, 10)
    assert view.af_pending()
    view.set_af_result(False)
    assert not view.af_pending()


def test_panel_has_the_settings_section_and_disables_it(qapp):
    pannello = RemotePanel()
    assert pannello.props is not None
    pannello.set_busy(True)
    assert pannello.props._enabled is False
    pannello.set_busy(False)
    assert pannello.props._enabled is True
    pannello.set_recording(True)
    assert pannello.props._enabled is False
