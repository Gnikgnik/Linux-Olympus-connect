"""Smoke test: la finestra si costruisce, le impostazioni persistono, i
filtri si propagano al modello, e uno stato diagnostico fallito produce un
messaggio comprensibile in barra di stato."""
from datetime import datetime
from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings

from olympus_connect.core.downloader import Job, JobState
from olympus_connect.core.model import MediaFile, MediaKind
from olympus_connect.core.network import Diagnosis, Rung
from olympus_connect.gui.main_window import DEFAULT_DEST, MainWindow


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "test.ini"), QSettings.Format.IniFormat)


class FakeNetwork:
    def __init__(self, diagnosis):
        self._diagnosis = diagnosis
        self.ssid = "CAM"

    def diagnose(self):
        return self._diagnosis


def all_green():
    return Diagnosis([Rung("porta 80 della fotocamera", True, "risponde")])


def all_red():
    return Diagnosis([Rung("dispositivo Wi-Fi", False, "nessuna interfaccia")])


def window(settings, diagnosis=None):
    return MainWindow(
        network_factory=lambda ssid: FakeNetwork(diagnosis or all_green()),
        settings=settings,
    )


def job(name, state, tmp_path, size=100, kind=MediaKind.JPEG):
    m = MediaFile(f"/DCIM/100OLYMP/{name}", size,
                  datetime(2026, 9, 24, 12, 0, 0), kind)
    return Job(m, tmp_path / name, state)


# --- costruzione e impostazioni --------------------------------------------

def test_window_constructs(qapp, settings):
    assert window(settings).windowTitle()


def test_window_uses_default_destination_when_unset(qapp, settings):
    assert window(settings).dest_root() == DEFAULT_DEST
    assert DEFAULT_DEST == Path.home() / "Immagini" / "Olympus"


def test_window_remembers_destination(qapp, settings, tmp_path):
    settings.setValue("dest_root", str(tmp_path / "Scatti"))
    assert window(settings).dest_root() == tmp_path / "Scatti"


def test_window_remembers_filters(qapp, settings):
    first = window(settings)
    first._filter_raw.setChecked(False)
    first._only_new.setChecked(True)

    second = window(settings)
    assert second._filter_raw.isChecked() is False
    assert second._only_new.isChecked() is True
    assert second._filter_jpeg.isChecked() is True


def test_window_remembers_ssid(qapp, settings):
    settings.setValue("ssid", "E-M10MKIV-P-BJGA85301")
    assert window(settings).camera_ssid() == "E-M10MKIV-P-BJGA85301"


# --- stato della rete ------------------------------------------------------

def test_failed_diagnosis_shows_first_failing_rung(qapp, settings):
    win = window(settings, all_red())
    win.refresh_status()
    assert "Wi-Fi" in win.statusBar().currentMessage()


def test_green_diagnosis_reports_reachable(qapp, settings):
    win = window(settings, all_green())
    win.refresh_status()
    assert "raggiungibile" in win.statusBar().currentMessage().lower()


def test_network_unavailable_does_not_crash_the_window(qapp, settings):
    from olympus_connect.core.errors import NetworkUnavailable

    class Broken:
        ssid = ""

        def diagnose(self):
            raise NetworkUnavailable("nmcli assente")

    win = MainWindow(network_factory=lambda ssid: Broken(), settings=settings)
    win.refresh_status()
    assert "rete" in win.statusBar().currentMessage().lower()


# --- elenco e riepilogo ----------------------------------------------------

def test_summary_counts_new_and_present(qapp, settings, tmp_path):
    win = window(settings)
    win.on_listed([
        job("A.JPG", JobState.NEW, tmp_path),
        job("B.JPG", JobState.NEW, tmp_path),
        job("C.JPG", JobState.PRESENT, tmp_path),
    ])

    message = win.statusBar().currentMessage()
    assert "2 da scaricare" in message
    assert "1 già" in message


def test_listing_populates_the_grid(qapp, settings, tmp_path):
    win = window(settings)
    win.on_listed([job("A.JPG", JobState.NEW, tmp_path)])
    assert win._model.rowCount() == 1


def test_connected_signal_shows_model_in_status(qapp, settings):
    win = window(settings)
    win.on_connected("E-M10MarkIV")
    assert "E-M10MarkIV" in win.statusBar().currentMessage()


def test_failed_listing_shows_message_without_dialog(qapp, settings):
    win = window(settings)
    win.on_failed("La fotocamera non risponde su 192.168.0.10.")
    assert "non risponde" in win.statusBar().currentMessage()


# --- filtri ----------------------------------------------------------------

def test_filters_propagate_to_the_model(qapp, settings, tmp_path):
    win = window(settings)
    win.on_listed([
        job("A.JPG", JobState.NEW, tmp_path, kind=MediaKind.JPEG),
        job("B.ORF", JobState.NEW, tmp_path, kind=MediaKind.RAW),
    ])
    win._filter_jpeg.setChecked(False)

    assert [j.media.name for j in win._model.visible_jobs()] == ["B.ORF"]


def test_only_new_filter_hides_present_files(qapp, settings, tmp_path):
    win = window(settings)
    win.on_listed([
        job("A.JPG", JobState.NEW, tmp_path),
        job("B.JPG", JobState.PRESENT, tmp_path),
    ])
    win._only_new.setChecked(True)

    assert [j.media.name for j in win._model.visible_jobs()] == ["A.JPG"]


# --- download --------------------------------------------------------------

def test_download_without_selection_warns_in_status_bar(qapp, settings, tmp_path):
    win = window(settings)
    win.on_listed([job("A.JPG", JobState.NEW, tmp_path)])
    win.on_download_selected()
    assert "selezionat" in win.statusBar().currentMessage().lower()


def test_paused_enables_retry_and_disables_cancel(qapp, settings):
    win = window(settings)
    win.on_paused("La fotocamera si è disconnessa.")
    assert win._resume_button.isEnabled()
    assert not win._cancel_button.isEnabled()


def test_retry_button_is_labelled_riprova(qapp, settings):
    # Questo firmware non supporta il Range: il pulsante non deve chiamarsi
    # "Riprendi", perché non riprende — riparte da zero.
    assert window(settings)._resume_button.text() == "Riprova"
