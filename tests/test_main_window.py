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


class _StubClient:
    """Client finto: i test non devono aprire connessioni HTTP reali."""

    def connect(self):
        return "E-M10MarkIV"

    def keep_awake(self, seconds=3600):
        return False

    def ensure_play_mode(self):
        pass

    def list_media(self, directory="/DCIM"):
        return []

    def thumbnail(self, camera_path):
        return b""


@pytest.fixture
def windows(qapp):
    """Crea finestre e le chiude a fine test.

    Senza la chiusura i QThread avviati dai worker sopravvivono alla
    distruzione della finestra e il processo va in segfault.
    """
    created = []

    def make(settings, diagnosis=None, client_factory=_StubClient,
             network_factory=None):
        win = MainWindow(
            client_factory=client_factory,
            network_factory=network_factory or
            (lambda ssid: FakeNetwork(diagnosis or all_green())),
            settings=settings,
        )
        created.append(win)
        return win

    yield make

    for win in created:
        win.close()


class FakeNetwork:
    def __init__(self, diagnosis, ssids=None):
        self._diagnosis = diagnosis
        self._ssids = ssids if ssids is not None else []
        self.ssid = "CAM"

    def diagnose(self):
        return self._diagnosis

    def scan_ssids(self):
        return list(self._ssids)


def all_green():
    return Diagnosis([Rung("porta 80 della fotocamera", True, "risponde")])


def all_red():
    return Diagnosis([Rung("dispositivo Wi-Fi", False, "nessuna interfaccia")])




def job(name, state, tmp_path, size=100, kind=MediaKind.JPEG):
    m = MediaFile(f"/DCIM/100OLYMP/{name}", size,
                  datetime(2026, 9, 24, 12, 0, 0), kind)
    return Job(m, tmp_path / name, state)


# --- costruzione e impostazioni --------------------------------------------

def test_window_constructs(windows, qapp, settings):
    assert windows(settings).windowTitle()


def test_window_uses_default_destination_when_unset(windows, qapp, settings):
    assert windows(settings).dest_root() == DEFAULT_DEST
    assert DEFAULT_DEST == Path.home() / "Immagini" / "Olympus"


def test_window_remembers_destination(windows, qapp, settings, tmp_path):
    settings.setValue("dest_root", str(tmp_path / "Scatti"))
    assert windows(settings).dest_root() == tmp_path / "Scatti"


def test_window_remembers_filters(windows, qapp, settings):
    first = windows(settings)
    first._filter_raw.setChecked(False)
    first._only_new.setChecked(True)

    second = windows(settings)
    assert second._filter_raw.isChecked() is False
    assert second._only_new.isChecked() is True
    assert second._filter_jpeg.isChecked() is True


def test_window_remembers_ssid(windows, qapp, settings):
    settings.setValue("ssid", "E-M10MKIV-P-XXXXXXXXX")
    assert windows(settings).camera_ssid() == "E-M10MKIV-P-XXXXXXXXX"


# --- stato della rete ------------------------------------------------------

def test_failed_diagnosis_shows_first_failing_rung(windows, qapp, settings):
    win = windows(settings, all_red())
    win.refresh_status()
    assert "Wi-Fi" in win.statusBar().currentMessage()


def test_green_diagnosis_reports_reachable(windows, qapp, settings):
    win = windows(settings, all_green())
    win.refresh_status()
    assert "raggiungibile" in win.statusBar().currentMessage().lower()


def test_network_unavailable_does_not_crash_the_window(windows, qapp, settings):
    from olympus_connect.core.errors import NetworkUnavailable

    class Broken:
        ssid = ""

        def diagnose(self):
            raise NetworkUnavailable("nmcli assente")

    win = windows(settings, network_factory=lambda ssid: Broken())
    win.refresh_status()
    assert "rete" in win.statusBar().currentMessage().lower()


# --- elenco e riepilogo ----------------------------------------------------

def test_summary_counts_new_and_present(windows, qapp, settings, tmp_path):
    win = windows(settings)
    win.on_listed([
        job("A.JPG", JobState.NEW, tmp_path),
        job("B.JPG", JobState.NEW, tmp_path),
        job("C.JPG", JobState.PRESENT, tmp_path),
    ])

    message = win.statusBar().currentMessage()
    assert "2 da scaricare" in message
    assert "1 già" in message


def test_listing_populates_the_grid(windows, qapp, settings, tmp_path):
    win = windows(settings)
    win.on_listed([job("A.JPG", JobState.NEW, tmp_path)])
    assert win._model.rowCount() == 1


def test_connected_signal_shows_model_in_status(windows, qapp, settings):
    win = windows(settings)
    win.on_connected("E-M10MarkIV")
    assert "E-M10MarkIV" in win.statusBar().currentMessage()


def test_failed_listing_shows_message_without_dialog(windows, qapp, settings):
    win = windows(settings)
    win.on_failed("La fotocamera non risponde su 192.168.0.10.")
    assert "non risponde" in win.statusBar().currentMessage()


# --- filtri ----------------------------------------------------------------

def test_filters_propagate_to_the_model(windows, qapp, settings, tmp_path):
    win = windows(settings)
    win.on_listed([
        job("A.JPG", JobState.NEW, tmp_path, kind=MediaKind.JPEG),
        job("B.ORF", JobState.NEW, tmp_path, kind=MediaKind.RAW),
    ])
    win._filter_jpeg.setChecked(False)

    assert [j.media.name for j in win._model.visible_jobs()] == ["B.ORF"]


def test_only_new_filter_hides_present_files(windows, qapp, settings, tmp_path):
    win = windows(settings)
    win.on_listed([
        job("A.JPG", JobState.NEW, tmp_path),
        job("B.JPG", JobState.PRESENT, tmp_path),
    ])
    win._only_new.setChecked(True)

    assert [j.media.name for j in win._model.visible_jobs()] == ["A.JPG"]


# --- download --------------------------------------------------------------

def test_download_without_selection_warns_in_status_bar(windows, qapp, settings, tmp_path):
    win = windows(settings)
    win.on_listed([job("A.JPG", JobState.NEW, tmp_path)])
    win.on_download_selected()
    assert "selezionat" in win.statusBar().currentMessage().lower()


def test_each_worker_gets_its_own_client(windows, qapp, settings, tmp_path):
    """requests.Session non è thread-safe.

    Condividere un OlympusClient fra ListWorker e ThumbWorker, che girano su
    QThread diversi, faceva arrivare le miniature troncate — una striscia
    invece dell'immagine. Ogni worker deve avere la propria sessione HTTP.
    """
    created = []

    def factory():
        client = _StubClient()
        created.append(client)
        return client

    win = windows(settings, client_factory=factory)
    win.on_refresh()
    win.on_listed([job("A.JPG", JobState.NEW, tmp_path)])

    assert len(created) >= 2
    assert win._list_worker._client is not win._thumb_worker._client


def test_thumbnails_are_suspended_during_download(windows, qapp, settings, tmp_path):
    win = windows(settings)
    win.on_listed([job("A.JPG", JobState.NEW, tmp_path)])

    win._suspend_thumbnails(True)
    assert win._thumb_worker._suspended
    win._suspend_thumbnails(False)
    assert not win._thumb_worker._suspended


# --- scelta della vista ----------------------------------------------------

def test_default_view_is_the_thumbnail_grid(windows, qapp, settings):
    win = windows(settings)
    assert win.current_view() == "griglia"


def test_view_choice_is_remembered(windows, qapp, settings):
    first = windows(settings)
    first.set_view("elenco")

    second = windows(settings)
    assert second.current_view() == "elenco"


def test_switching_view_changes_the_visible_widget(windows, qapp, settings, tmp_path):
    win = windows(settings)
    # Con l'elenco vuoto a schermo c'è lo stato vuoto, non le viste: per
    # verificare il selettore servono dei file.
    win.on_listed([job("A.JPG", JobState.NEW, tmp_path)])
    win.set_view("elenco")
    assert win._views.currentWidget() is win._table
    win.set_view("griglia")
    assert win._views.currentWidget() is win._grid


def test_both_views_share_one_model(windows, qapp, settings, tmp_path):
    # Un solo elenco caricato dalla fotocamera, due modi di guardarlo.
    win = windows(settings)
    win.on_listed([job("A.JPG", JobState.NEW, tmp_path)])
    assert win._grid.model() is win._table.model()
    assert win._table.model().rowCount() == 1


def test_download_reads_selection_from_the_active_view(windows, qapp, settings,
                                                      tmp_path):
    from PyQt6.QtCore import QItemSelectionModel

    win = windows(settings)
    win.on_listed([job("A.JPG", JobState.NEW, tmp_path)])
    win.set_view("elenco")

    win._table.selectionModel().select(
        win._model.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select |
        QItemSelectionModel.SelectionFlag.Rows,
    )
    assert [j.media.name for j in win.selected_jobs()] == ["A.JPG"]


def test_detailed_view_is_sorted_by_date(windows, qapp, settings, tmp_path):
    from datetime import datetime as dt

    def dated(name, when):
        m = MediaFile(f"/DCIM/100OLYMP/{name}", 10, when, MediaKind.JPEG)
        return Job(m, tmp_path / name, JobState.NEW)

    win = windows(settings)
    win.set_view("elenco")
    win.on_listed([dated("NUOVA.JPG", dt(2026, 9, 6)),
                   dated("VECCHIA.JPG", dt(2020, 1, 1))])

    assert [j.media.name for j in win._model.visible_jobs()] == \
        ["VECCHIA.JPG", "NUOVA.JPG"]


# --- scelta dell'SSID ------------------------------------------------------

def test_ask_ssid_offers_the_scanned_networks(windows, qapp, settings, monkeypatch):
    """L'SSID della fotocamera è lungo: va scelto da un elenco, non digitato."""
    reti = ["E-M10MKIV-P-XXXXXXXXX", "casa-wifi"]
    win = windows(settings,
                  network_factory=lambda ssid: FakeNetwork(all_green(), reti))

    offerti = {}

    def fake_get_item(parent, title, label, items, current, editable):
        offerti["items"] = list(items)
        offerti["editable"] = editable
        return items[0], True

    monkeypatch.setattr(
        "olympus_connect.gui.main_window.QInputDialog.getItem", fake_get_item
    )
    assert win.ask_camera_ssid() == "E-M10MKIV-P-XXXXXXXXX"
    assert offerti["items"] == reti
    # Deve restare modificabile: una rete nascosta o non ancora visibile
    # va potuta scrivere a mano.
    assert offerti["editable"] is True


def test_ask_ssid_falls_back_to_text_input_when_scan_is_empty(
        windows, qapp, settings, monkeypatch):
    win = windows(settings,
                  network_factory=lambda ssid: FakeNetwork(all_green(), []))

    monkeypatch.setattr(
        "olympus_connect.gui.main_window.QInputDialog.getText",
        lambda *a, **k: ("SCRITTO-A-MANO", True),
    )
    assert win.ask_camera_ssid() == "SCRITTO-A-MANO"


def test_ask_ssid_returns_none_when_cancelled(windows, qapp, settings, monkeypatch):
    win = windows(settings,
                  network_factory=lambda ssid: FakeNetwork(all_green(), ["A"]))
    monkeypatch.setattr(
        "olympus_connect.gui.main_window.QInputDialog.getItem",
        lambda *a, **k: ("", False),
    )
    assert win.ask_camera_ssid() is None


def test_ask_ssid_survives_a_failing_scan(windows, qapp, settings, monkeypatch):
    from olympus_connect.core.errors import NetworkUnavailable

    class NoScan:
        ssid = ""

        def diagnose(self):
            return all_green()

        def scan_ssids(self):
            raise NetworkUnavailable("nmcli assente")

    win = windows(settings, network_factory=lambda ssid: NoScan())
    monkeypatch.setattr(
        "olympus_connect.gui.main_window.QInputDialog.getText",
        lambda *a, **k: ("A-MANO", True),
    )
    assert win.ask_camera_ssid() == "A-MANO"


def test_paused_enables_retry_and_disables_cancel(windows, qapp, settings):
    win = windows(settings)
    win.on_paused("La fotocamera si è disconnessa.")
    assert win._resume_button.isEnabled()
    assert not win._cancel_button.isEnabled()


def test_retry_button_is_labelled_riprova(windows, qapp, settings):
    # Questo firmware non supporta il Range: il pulsante non deve chiamarsi
    # "Riprendi", perché non riprende — riparte da zero.
    assert windows(settings)._resume_button.text() == "Riprova"


def test_progress_bars_survive_a_full_card(windows, qapp, settings):
    """Regressione: QProgressBar lavora su interi a 32 bit.

    I 23,48 GB di RAW della scheda di riferimento, in byte, sforano il
    limite: passarli direttamente faceva sollevare OverflowError e chiudere
    l'applicazione proprio quando si avvia il download più lungo.
    """
    win = windows(settings)
    win._update_total_bar(2_480_000_000, 23_480_000_000)      # non deve sollevare
    win._update_file_bar(12_400_000, 20_600_000)
    assert win._total_bar.maximum() <= 2_147_483_647
    assert 0 < win._total_bar.value() <= win._total_bar.maximum()
    # Circa il 10,6% di 23,48 GB.
    frazione = win._total_bar.value() / win._total_bar.maximum()
    assert 0.09 < frazione < 0.12


def test_progress_bar_handles_zero_total(windows, qapp, settings):
    win = windows(settings)
    win._update_total_bar(0, 0)
    assert win._total_bar.maximum() >= 1
