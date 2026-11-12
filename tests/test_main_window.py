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


# --- percorso reale di miniature e anteprima --------------------------------
#
# Regressione: gli screenshot iniettavano le miniature direttamente nel
# modello, quindi il percorso di richiesta attraverso i worker non era mai
# esercitato. Si è rotto senza che nessuna prova se ne accorgesse. Questi
# test passano dai worker veri, sui loro thread, come fa l'applicazione.

def _attendi(qapp, condizione, secondi=3.0):
    import time
    fine = time.monotonic() + secondi
    while time.monotonic() < fine:
        qapp.processEvents()
        if condizione():
            return True
        time.sleep(0.01)
    return condizione()


class _RecordingClient:
    """Ogni worker crea il proprio client: le chiamate vanno registrate a
    livello di classe per vederle tutte."""

    chiamate: list = []

    def __init__(self):
        self.commands = set()

    def connect(self):
        self.commands = {"get_imglist", "get_thumbnail", "get_screennail"}
        return "E-M10MarkIV"

    def keep_awake(self, seconds=3600):
        return False

    def ensure_play_mode(self):
        pass

    def list_media(self, directory="/DCIM"):
        return []

    def thumbnail(self, camera_path):
        _RecordingClient.chiamate.append(("thumbnail", camera_path))
        return _jpeg_valido()

    def has_screennail(self):
        return "get_screennail" in self.commands

    def screennail(self, camera_path):
        _RecordingClient.chiamate.append(("screennail", camera_path))
        return _jpeg_valido()


def _jpeg_valido() -> bytes:
    from PyQt6.QtCore import QBuffer, QByteArray
    from PyQt6.QtGui import QPixmap
    pixmap = QPixmap(40, 30)
    pixmap.fill()
    dati = QByteArray()
    buffer = QBuffer(dati)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "JPEG")
    return bytes(dati)


def test_thumbnails_are_actually_fetched_after_listing(windows, qapp, settings,
                                                       tmp_path):
    """La griglia chiedeva le miniature prima che il worker esistesse: le
    richieste partivano nel vuoto, venivano segnate come fatte, e non si
    ripetevano mai. Risultato: nessuna miniatura, nessun errore."""
    _RecordingClient.chiamate = []
    win = windows(settings, client_factory=_RecordingClient)
    win.resize(1200, 700)
    win.show()
    jobs = [job(f"P{i}.ORF", JobState.NEW, tmp_path, kind=MediaKind.RAW)
            for i in range(8)]
    win.on_listed(jobs)

    assert _attendi(qapp, lambda: len(win._model._thumbnails) > 0), \
        "nessuna miniatura è arrivata nel modello"
    richieste = [c for c in _RecordingClient.chiamate if c[0] == "thumbnail"]
    assert richieste, "il client non ha mai ricevuto una richiesta di miniatura"


def test_preview_is_actually_fetched_on_selection(windows, qapp, settings,
                                                  tmp_path):
    """Il worker dell'anteprima aveva un client proprio che non aveva mai
    chiamato connect(): capacità vuote, quindi «questa fotocamera non genera
    anteprime grandi» anche su una fotocamera che le genera."""
    from PyQt6.QtCore import QItemSelectionModel

    _RecordingClient.chiamate = []
    win = windows(settings, client_factory=_RecordingClient)
    win.resize(1200, 700)
    win.show()
    win.on_listed([job("A.ORF", JobState.NEW, tmp_path, kind=MediaKind.RAW)])
    qapp.processEvents()

    win._grid.selectionModel().select(
        win._model.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select |
        QItemSelectionModel.SelectionFlag.Rows)

    assert _attendi(qapp, win._preview.has_image), \
        f"anteprima assente; testo mostrato: {win._preview.text()!r}"
    assert ("screennail", "/DCIM/100OLYMP/A.ORF") in _RecordingClient.chiamate


# --- dati di scatto ---------------------------------------------------------------

from fractions import Fraction  # noqa: E402

from olympus_connect.core.exif import ShotInfo  # noqa: E402

_INFO = ShotInfo(exposure_time=Fraction(1, 60), f_number=Fraction(7, 2),
                 iso=6400, focal_length=Fraction(14),
                 lens="LUMIX G VARIO 14-42/F3.5-5.6 II", exposure_program=4)


def _seleziona(win, righe):
    from PyQt6.QtCore import QItemSelectionModel
    modello = win._grid.selectionModel()
    modello.clearSelection()
    for riga in righe:
        modello.select(win._model.index(riga, 0),
                       QItemSelectionModel.SelectionFlag.Select |
                       QItemSelectionModel.SelectionFlag.Rows)


def _finestra_con(windows, settings, tmp_path, *nomi):
    win = windows(settings)
    win.resize(1200, 700)
    win.show()
    kinds = {".ORF": MediaKind.RAW, ".MOV": MediaKind.MOVIE, ".JPG": MediaKind.JPEG}
    win.on_listed([job(n, JobState.NEW, tmp_path, kind=kinds[n[-4:]]) for n in nomi])
    return win


def test_shot_section_is_hidden_until_data_arrives(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    assert win._shot.isHidden()


def test_shot_section_shows_headline_and_rows(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    assert not win._shot.isHidden()
    assert win._shot.headline_text() == "1/60 s   f/3,5   ISO 6400   14 mm"
    assert win._shot.rows() == [
        ("Obiettivo", "LUMIX G VARIO 14-42/F3.5-5.6 II"),
        ("Modalità", "Priorità dei tempi"),
    ]


def test_shot_data_for_a_file_no_longer_selected_is_ignored(
        windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF", "B.ORF")
    _seleziona(win, [0])
    _seleziona(win, [1])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    assert win._shot.isHidden()


def test_missing_shot_data_shows_the_unavailable_line(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", None)
    assert not win._shot.isHidden()
    assert win._shot.is_unavailable()
    assert win._shot.rows() == []


def test_changing_selection_hides_the_previous_shot(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF", "B.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    _seleziona(win, [1])
    assert win._shot.isHidden()


def test_multiple_selection_hides_the_shot(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF", "B.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    _seleziona(win, [0, 1])
    assert win._shot.isHidden()


def test_starting_a_download_hides_the_shot(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    win._suspend_thumbnails(True)
    assert win._shot.isHidden()


def test_a_shot_with_only_rows_hides_the_empty_headline(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", ShotInfo(white_balance=0))
    assert win._shot.headline_text() == ""
    assert win._shot.rows() == [("Bilanciamento", "Auto")]


def test_a_model_reset_clears_shot_and_details(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    assert not win._shot.isHidden()
    assert win._detail_values["Nome"].text() == "A.ORF"
    win.on_listed([job("A.ORF", JobState.NEW, tmp_path, kind=MediaKind.RAW)])
    assert win._shot.isHidden()
    assert win._detail_values["Nome"].text() == "—"


def test_a_shot_arriving_while_suspended_is_ignored(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win._suspend_thumbnails(True)
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    assert win._shot.isHidden()


def test_shot_and_details_values_start_at_the_same_x(windows, qapp, settings, tmp_path):
    win = _finestra_con(windows, settings, tmp_path, "A.ORF")
    _seleziona(win, [0])
    win.on_shot_ready("/DCIM/100OLYMP/A.ORF", _INFO)
    # Niente processEvents qui: il vero worker risponderebbe «non disponibili»
    # e svuoterebbe le righe. La geometria si calcola a mano.
    win._inspector.layout().activate()
    win._shot.layout().activate()
    dettaglio = win._detail_values["Nome"]
    riga = win._shot._grid.itemAtPosition(0, 1).widget()
    x_dettaglio = dettaglio.mapTo(win, dettaglio.rect().topLeft()).x()
    x_scatto = riga.mapTo(win, riga.rect().topLeft()).x()
    assert x_dettaglio == x_scatto


# --- telecomando ---------------------------------------------------------------------

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402


class _RemoteStubClient(_RecordingClient):
    """Client finto con i comandi della modalità scatto; registra tutto a
    livello di classe, perché ogni worker ne crea uno."""

    def enter_rec_mode(self, quality):
        _RecordingClient.chiamate.append(("enter_rec_mode", quality))

    def start_liveview(self, port):
        _RecordingClient.chiamate.append(("start_liveview", port))

    def stop_liveview(self):
        _RecordingClient.chiamate.append(("stop_liveview",))

    def take_mode(self):
        return "S"

    def touch_area(self):
        return (96, 72, 448, 336)

    def assign_af(self, x, y):
        _RecordingClient.chiamate.append(("assign_af", x, y))
        return True

    def take_picture(self, read_timeout=None):
        _RecordingClient.chiamate.append(("take_picture",))

    def rec_view(self):
        # Bytes preparati sul thread della GUI: QPixmap non si crea su un altro thread.
        _RecordingClient.chiamate.append(("rec_view",))
        return _RemoteStubClient.revisione

    def start_movie(self):
        _RecordingClient.chiamate.append(("start_movie",))

    def stop_movie(self):
        _RecordingClient.chiamate.append(("stop_movie",))

    def ensure_play_mode(self):
        _RecordingClient.chiamate.append(("ensure_play_mode",))


class _FakeLive(QObject):
    listening = pyqtSignal(int)
    frame_ready = pyqtSignal()
    stalled = pyqtSignal()
    no_signal = pyqtSignal()
    resumed = pyqtSignal()
    lens_changed = pyqtSignal(object)
    istanze: list = []

    def __init__(self):
        super().__init__()
        self.armato = 0
        self.fermato = False
        self.immagine = None
        _FakeLive.istanze.append(self)

    def run(self):
        self.listening.emit(5555)

    def arm(self):
        self.armato += 1

    def disarm(self):
        pass

    def take_frame(self):
        immagine, self.immagine = self.immagine, None
        return immagine

    def stop(self):
        self.fermato = True


def _telecomando(settings):
    _RecordingClient.chiamate = []
    _FakeLive.istanze = []
    _RemoteStubClient.revisione = _jpeg_valido()
    win = MainWindow(client_factory=_RemoteStubClient,
                     network_factory=lambda ssid: FakeNetwork(all_green()),
                     settings=settings, live_factory=_FakeLive)
    win.resize(1200, 700)
    win.show()
    win.on_connected("E-M10MarkIV")
    return win


@pytest.fixture
def remote_win(qapp, settings):
    win = _telecomando(settings)
    yield win
    win.close()


def test_remote_button_needs_a_connected_camera(windows, qapp, settings):
    win = windows(settings)
    assert not win._view_remote.isEnabled()
    win.on_connected("E-M10MarkIV")
    assert win._view_remote.isEnabled()
    win._downloading = True
    win._update_remote_button()
    assert not win._view_remote.isEnabled()


def test_entering_suspends_the_library_and_starts_the_session(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert win._remote_active and win._suspended
    assert win._views.currentWidget() is win._remote_view
    assert win._inspector_pages.currentWidget() is win._remote_panel
    assert not win._download_button.isEnabled()
    assert not any(b.isEnabled() for b in win._filter_boxes.values())
    assert _attendi(qapp, lambda: ("start_liveview", 5555) in _RecordingClient.chiamate)
    assert ("enter_rec_mode", "1024x0768") in _RecordingClient.chiamate
    assert _FakeLive.istanze[-1].armato == 1, "il datagramma va mandato prima di startliveview"
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")


def test_a_frame_reaches_the_view(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: _FakeLive.istanze)
    live = _FakeLive.istanze[-1]
    live.immagine = QImage(64, 48, QImage.Format.Format_RGB32)
    live.frame_ready.emit()
    assert _attendi(qapp, win._remote_view.has_frame)


def test_shoot_shows_the_review(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win._remote_panel._shoot.click()
    assert _attendi(qapp, win._remote_view.is_reviewing)
    assert ("take_picture",) in _RecordingClient.chiamate


def test_click_on_the_live_asks_for_focus(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win._remote_view.focus_requested.emit(320, 240)
    assert _attendi(qapp, lambda: ("assign_af", 320, 240) in _RecordingClient.chiamate)


def test_retry_while_recording_does_not_reenter(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win._remote_panel._movie.click()
    assert _attendi(qapp, win._remote_panel.is_recording)
    prima = [c for c in _RecordingClient.chiamate if c[0] == "enter_rec_mode"]
    win._remote_view.retry_requested.emit()
    _attendi(qapp, lambda: False, 0.3)
    dopo = [c for c in _RecordingClient.chiamate if c[0] == "enter_rec_mode"]
    assert dopo == prima
    assert "ferma il video" in win._remote_view.message()
    assert win._remote_entered


def test_a_failure_clears_the_pending_af_box(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win._remote_view.show_af(10, 10)
    assert win._remote_view.af_pending()
    win.on_remote_failed("La fotocamera non risponde.")
    assert not win._remote_view.af_pending()


def test_failed_reentry_after_a_stall_offers_retry_again(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win._remote_view.set_frame(QImage(64, 48, QImage.Format.Format_RGB32))
    win._remote_entered = False          # come dopo Riprova, prima della risposta
    win.on_remote_failed("La fotocamera non risponde.")
    assert win._remote_view.message() == "La fotocamera non risponde."
    assert win._remote_view._retry.isVisible()


def test_focus_and_entry_are_logged(remote_win, qapp, caplog):
    win = remote_win
    with caplog.at_level("DEBUG", logger="olympus_connect.remote"):
        win._view_remote.click()
        assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
        win._remote_view.set_frame(QImage(64, 48, QImage.Format.Format_RGB32))
        win._remote_view.focus_requested.emit(320, 240)
        assert _attendi(qapp, lambda: ("assign_af", 320, 240) in _RecordingClient.chiamate)
    assert "(96, 72, 448, 336)" in caplog.text
    assert "320" in caplog.text and "(64, 48)" in caplog.text


def test_main_configures_logging_only_with_the_env_var(monkeypatch):
    from olympus_connect.gui import main_window
    chiamate = []
    monkeypatch.setattr(main_window.logging, "basicConfig", lambda **kw: chiamate.append(kw))
    monkeypatch.delenv("OLYMPUS_DEBUG", raising=False)
    main_window.configure_logging()
    assert chiamate == []
    monkeypatch.setenv("OLYMPUS_DEBUG", "1")
    main_window.configure_logging()
    assert chiamate and chiamate[0]["level"] == main_window.logging.DEBUG


def test_leaving_stops_the_video_returns_to_play_and_relists(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win._remote_panel._movie.click()
    assert _attendi(qapp, win._remote_panel.is_recording)
    win._view_grid.click()
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    nomi = [c[0] for c in _RecordingClient.chiamate]
    assert nomi.index("stop_movie") < nomi.index("stop_liveview") < nomi.index("ensure_play_mode")
    assert _FakeLive.istanze[-1].fermato
    assert win._views.currentWidget() is not win._remote_view
    assert win._inspector_pages.currentIndex() == 0
    assert not win._suspended
    assert win._list_worker is not None, "uscendo si rifà l'elenco con un client nuovo"


def test_shortcuts_exist_only_in_remote_mode(remote_win, qapp):
    win = remote_win
    assert win._shortcut_shoot.key().toString() == "Space"
    assert win._shortcut_movie.key().toString() == "R"
    assert not win._shortcut_shoot.isEnabled()
    win._view_remote.click()
    assert win._shortcut_shoot.isEnabled() and win._shortcut_movie.isEnabled()


def test_remote_is_not_remembered_as_startup_view(remote_win, qapp, settings):
    remote_win._view_remote.click()
    assert settings.value("view") != "telecomando"


def test_closing_in_remote_mode_returns_the_camera_to_play(qapp, settings):
    win = _telecomando(settings)
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    win.close()
    assert ("ensure_play_mode",) in _RecordingClient.chiamate


# --- telecomando: correzioni della revisione ----------------------------------------------

import time  # noqa: E402


def test_inspector_pages_show_the_panel_background(qapp, settings):
    """Stack e pagina nuovi non vanno dipinti con lo sfondo della finestra."""
    from olympus_connect.gui import theme
    qapp.setStyleSheet(theme.STYLESHEET)
    win = _telecomando(settings)
    try:
        def colore():
            qapp.processEvents()
            pagine = win._inspector_pages
            punto = pagine.mapTo(win, pagine.rect().bottomLeft())
            immagine = win.grab().toImage()
            return immagine.pixelColor(punto.x() + 6, punto.y() - 6).name()
        assert colore() == theme.PANEL.lower()
        win._view_remote.click()
        assert colore() == theme.PANEL.lower()
    finally:
        win.close()
        qapp.setStyleSheet("")


def test_remote_toggle_is_disabled_while_a_listing_is_in_flight(remote_win, qapp):
    win = remote_win
    assert win._view_remote.isEnabled()
    win.on_refresh()
    assert not win._view_remote.isEnabled()
    assert _attendi(qapp, lambda: win._view_remote.isEnabled())


def test_listing_controls_are_disabled_in_remote_mode_and_restored(remote_win, qapp):
    win = remote_win
    win._show_transfer(True, con_riprova=True)
    win._view_remote.click()
    assert not win._refresh_button.isEnabled()
    assert not win._connect_button.isEnabled()
    assert not win._resume_button.isEnabled()
    win._view_grid.click()
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    assert win._refresh_button.isEnabled() and win._connect_button.isEnabled()
    assert win._resume_button.isEnabled()


def test_a_listing_arriving_in_remote_mode_leaves_the_view_alone(remote_win, qapp):
    win = remote_win
    win._view_remote.click()
    win.on_listed([])
    assert win._views.currentWidget() is win._remote_view
    assert win._thumb_worker is None and win._preview_worker is None


def test_resume_that_finishes_inside_the_call_leaves_downloading_false(remote_win, qapp):
    win = remote_win

    class _Scarico:
        def resume(self):
            win.on_download_finished()      # arriva dentro la chiamata, come dal vero

    win._download_worker = _Scarico()
    win.on_resume()
    win._download_worker = None        # il finto non sa farsi cancellare alla chiusura
    assert not win._downloading
    assert _attendi(qapp, lambda: win._view_remote.isEnabled())


class _SlowClient(_RemoteStubClient):
    def stop_liveview(self):
        time.sleep(2.0)
        super().stop_liveview()

    def ensure_play_mode(self):
        time.sleep(2.0)
        super().ensure_play_mode()


def test_closing_is_bounded_with_a_slow_camera(qapp, settings):
    from olympus_connect.gui import main_window
    _RecordingClient.chiamate = []
    _FakeLive.istanze = []
    _RemoteStubClient.revisione = _jpeg_valido()
    win = MainWindow(client_factory=_SlowClient,
                     network_factory=lambda ssid: FakeNetwork(all_green()),
                     settings=settings, live_factory=_FakeLive)
    win.show()
    win.on_connected("E-M10MarkIV")
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
    inizio = time.monotonic()
    win.close()
    assert time.monotonic() - inizio < 3.5
    # il thread staccato finisce da solo: lo si aspetta, perché non muoia vivo
    for thread, _worker in list(main_window._DETACHED):
        assert thread.wait(8000)
    main_window._DETACHED.clear()


class _LeaveClient(_RemoteStubClient):
    def stop_liveview(self):
        time.sleep(0.5)
        super().stop_liveview()


def test_retry_listening_and_focus_are_ignored_while_leaving(qapp, settings):
    _RecordingClient.chiamate = []
    _FakeLive.istanze = []
    _RemoteStubClient.revisione = _jpeg_valido()
    win = MainWindow(client_factory=_LeaveClient,
                     network_factory=lambda ssid: FakeNetwork(all_green()),
                     settings=settings, live_factory=_FakeLive)
    win.show()
    win.on_connected("E-M10MarkIV")
    try:
        win._view_remote.click()
        assert _attendi(qapp, lambda: win._remote_panel.take_mode_text() == "S")
        win.exit_remote("griglia")
        win._remote_view.retry_requested.emit()
        win.on_live_listening(5555)
        win._remote_view.focus_requested.emit(10, 10)
        assert not win._remote_view._retry.isVisible()
        assert _attendi(qapp, lambda: not win._remote_active, 5.0)
        _attendi(qapp, lambda: False, 0.3)
        nomi = [c[0] for c in _RecordingClient.chiamate]
        assert nomi.count("enter_rec_mode") == 1
        assert "assign_af" not in nomi
    finally:
        win.close()


class _FailingClient(_RemoteStubClient):
    def start_liveview(self, port):
        raise RuntimeError("niente live")


def test_commands_stay_disabled_when_entering_fails(qapp, settings):
    _RecordingClient.chiamate = []
    _FakeLive.istanze = []
    win = MainWindow(client_factory=_FailingClient,
                     network_factory=lambda ssid: FakeNetwork(all_green()),
                     settings=settings, live_factory=_FakeLive)
    win.show()
    win.on_connected("E-M10MarkIV")
    try:
        win._view_remote.click()
        falliti = []
        win._remote_worker.failed.connect(falliti.append)
        assert _attendi(qapp, lambda: falliti)
        _attendi(qapp, lambda: False, 0.2)
        assert not win._remote_panel._shoot.isEnabled()
        win._shortcut_shoot.activated.emit()
        _attendi(qapp, lambda: False, 0.2)
        assert ("take_picture",) not in _RecordingClient.chiamate
    finally:
        win.close()


# --- impostazioni dal PC ------------------------------------------------------------

from olympus_connect.core.camprops import parse_desclist  # noqa: E402

_DESCLIST = (Path(__file__).parent / "fixtures" / "remote" / "desclist_A.xml").read_bytes()


class _PropsStubClient(_RemoteStubClient):
    valori: dict = {}

    def camera_props(self):
        _RecordingClient.chiamate.append(("camera_props",))
        props = parse_desclist(_DESCLIST)
        for nome, valore in _PropsStubClient.valori.items():
            p = props[nome]
            props[nome] = type(p)(p.name, valore, p.values, p.settable)
        return props

    def set_prop(self, name, value):
        _RecordingClient.chiamate.append(("set_prop", name, value))
        _PropsStubClient.valori[name] = value


@pytest.fixture
def props_win(qapp, settings):
    _RecordingClient.chiamate = []
    _FakeLive.istanze = []
    _PropsStubClient.valori = {}
    _RemoteStubClient.revisione = _jpeg_valido()
    win = MainWindow(client_factory=_PropsStubClient,
                     network_factory=lambda ssid: FakeNetwork(all_green()),
                     settings=settings, live_factory=_FakeLive)
    win.resize(1200, 700)
    win.show()
    win.on_connected("E-M10MarkIV")
    yield win
    win.close()


def test_entering_reads_the_settings(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("isospeedvalue") is not None)
    assert win._remote_panel.props.row("isospeedvalue").confirmed_value() == "200"


def test_a_change_reaches_the_camera_and_is_confirmed(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("isospeedvalue") is not None)
    win._remote_panel.props.row("isospeedvalue").step(1)
    assert _attendi(qapp, lambda: ("set_prop", "isospeedvalue", "250") in _RecordingClient.chiamate)
    assert _attendi(qapp, lambda: win._remote_panel.props.row("isospeedvalue").confirmed_value() == "250")


def test_settings_are_disabled_while_recording(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("isospeedvalue") is not None)
    win._remote_panel._movie.click()
    assert _attendi(qapp, win._remote_panel.is_recording)
    assert not win._remote_panel.props.row("isospeedvalue").isEnabled()


def test_lens_limits_reach_the_aperture_row(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("focalvalue") is not None)
    _FakeLive.istanze[-1].lens_changed.emit((3.5, 22.0))
    assert _attendi(qapp, lambda: "1.0" not in win._remote_panel.props.row("focalvalue").menu_values())


def test_failed_read_shows_unavailable(props_win, qapp, monkeypatch):
    def rompi(self):
        raise RuntimeError("x")
    monkeypatch.setattr(_PropsStubClient, "camera_props", rompi)
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.message() != "")


def test_an_unreadable_list_shows_unavailable(props_win, qapp, monkeypatch):
    monkeypatch.setattr(_PropsStubClient, "camera_props", lambda self: {})
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.message() == "Impostazioni non disponibili")
    assert win._remote_panel.props.names() == []
    assert win.statusBar().currentMessage() == "Elenco delle impostazioni illeggibile"


def test_reentering_starts_without_the_old_settings(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("focalvalue") is not None)
    _FakeLive.istanze[-1].lens_changed.emit((3.5, 22.0))
    assert _attendi(qapp, lambda: "1.0" not in win._remote_panel.props.row("focalvalue").menu_values())
    win._view_grid.click()
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    assert _attendi(qapp, win._view_remote.isEnabled, 5.0)   # la scheda è stata riletta
    win._view_remote.click()
    assert win._remote_active
    assert win._remote_panel.props.names() == []      # niente righe della sessione prima
    assert _attendi(qapp, lambda: win._remote_panel.props.row("focalvalue") is not None)
    # nessun fotogramma del live ancora: l'obiettivo è ignoto, resta il solo valore attuale
    assert win._remote_panel.props.row("focalvalue").menu_values() == ["3.5"]


def test_no_read_is_sent_when_entered_arrives_while_leaving(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_entered)
    assert _attendi(qapp, lambda: ("camera_props",) in _RecordingClient.chiamate)
    letture = _RecordingClient.chiamate.count(("camera_props",))
    win._remote_leaving = True
    try:
        win.on_remote_entered("A", None)
        assert not _attendi(qapp, lambda: _RecordingClient.chiamate.count(("camera_props",)) > letture, 0.3)
    finally:
        win._remote_leaving = False


# --- pose lunghe -------------------------------------------------------------------

import threading  # noqa: E402


class _LongShotClient(_PropsStubClient):
    """Uno scatto che resta in corso finché il test non lo rilascia."""
    rilascio = threading.Event()
    errore: Exception | None = None
    attese: list = []

    def take_picture(self, read_timeout=None):
        _RecordingClient.chiamate.append(("take_picture",))
        _LongShotClient.attese.append(read_timeout)
        _LongShotClient.rilascio.wait(5)
        if _LongShotClient.errore is not None:
            raise _LongShotClient.errore


@pytest.fixture
def long_win(qapp, settings):
    _RecordingClient.chiamate = []
    _FakeLive.istanze = []
    _PropsStubClient.valori = {"takemode": "M", "shutspeedvalue": '60"'}
    _LongShotClient.rilascio = threading.Event()
    _LongShotClient.errore = None
    _LongShotClient.attese = []
    _RemoteStubClient.revisione = _jpeg_valido()
    win = MainWindow(client_factory=_LongShotClient,
                     network_factory=lambda ssid: FakeNetwork(all_green()),
                     settings=settings, live_factory=_FakeLive)
    win.resize(1200, 700)
    win.show()
    win.on_connected("E-M10MarkIV")
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("shutspeedvalue") is not None)
    win._remote_view.set_frame(QImage(64, 48, QImage.Format.Format_RGB32))
    try:
        yield win
    finally:
        _LongShotClient.rilascio.set()
        win.close()


def _posa(win, qapp):
    win._remote_panel._shoot.click()
    assert _attendi(qapp, lambda: win._remote_view.message().startswith("Posa in corso"))


def test_a_long_exposure_shows_the_countdown(long_win, qapp):
    win = long_win
    _posa(win, qapp)
    assert win._remote_view.message() == "Posa in corso: ancora circa 120 s"
    assert not win._remote_view._retry.isVisible()
    assert win.statusBar().currentMessage() == "Posa in corso: ancora circa 120 s"
    assert _LongShotClient.attese == [pytest.approx(147.0)]
    win._on_exposure_tick()
    assert win._remote_view.message() == "Posa in corso: ancora circa 119 s"
    assert win.statusBar().currentMessage() == "Posa in corso: ancora circa 119 s"
    win._exposure_left = 1
    win._on_exposure_tick()
    assert win._remote_view.message() == "Quasi finito…"
    assert win.statusBar().currentMessage() == "Quasi finito…"
    assert not win._exposure_timer.isActive()


def test_a_stalled_live_during_the_exposure_offers_no_retry(long_win, qapp):
    win = long_win
    _posa(win, qapp)
    live = _FakeLive.istanze[-1]
    live.stalled.emit()
    live.no_signal.emit()
    qapp.processEvents()
    assert win._remote_view.message().startswith("Posa in corso")
    assert not win._remote_view._retry.isVisible()


def test_retry_is_ignored_during_the_exposure(long_win, qapp):
    win = long_win
    _posa(win, qapp)
    prima = _RecordingClient.chiamate.count(("enter_rec_mode", "1024x0768"))
    win.on_live_retry()
    _attendi(qapp, lambda: False, 0.2)
    assert _RecordingClient.chiamate.count(("enter_rec_mode", "1024x0768")) == prima
    assert win._remote_view.message().startswith("Posa in corso")


def test_after_the_shot_a_still_stalled_live_restarts_after_the_review(long_win, qapp):
    # sul campo: dopo una posa da 60" la fotocamera non riprende il live da sola
    win = long_win
    win._remote_view._review_timer.setInterval(300)
    _posa(win, qapp)
    _FakeLive.istanze[-1].stalled.emit()
    qapp.processEvents()
    prima = _RecordingClient.chiamate.count(("enter_rec_mode", "1024x0768"))
    _LongShotClient.rilascio.set()
    assert _attendi(qapp, win._remote_view.is_reviewing)
    assert not win._remote_view._retry.isVisible()      # l'anteprima resta scoperta
    assert not win._exposure_timer.isActive()
    assert _attendi(qapp, lambda: _RecordingClient.chiamate.count(
        ("enter_rec_mode", "1024x0768")) == prima + 1, 3.0)


def test_a_live_back_during_the_review_needs_no_restart(long_win, qapp):
    win = long_win
    win._remote_view._review_timer.setInterval(300)
    _posa(win, qapp)
    live = _FakeLive.istanze[-1]
    live.stalled.emit()
    qapp.processEvents()
    prima = _RecordingClient.chiamate.count(("enter_rec_mode", "1024x0768"))
    _LongShotClient.rilascio.set()
    assert _attendi(qapp, win._remote_view.is_reviewing)
    live.resumed.emit()
    assert _attendi(qapp, lambda: not win._remote_view.is_reviewing(), 3.0)
    _attendi(qapp, lambda: False, 0.2)
    assert _RecordingClient.chiamate.count(("enter_rec_mode", "1024x0768")) == prima
    assert win._remote_view.message() == ""


def test_after_a_failed_shot_a_still_stalled_live_offers_retry(long_win, qapp):
    from olympus_connect.core.errors import CameraAsleep
    win = long_win
    _LongShotClient.errore = CameraAsleep("La fotocamera non ha risposto entro il tempo previsto.")
    _posa(win, qapp)
    _FakeLive.istanze[-1].stalled.emit()
    qapp.processEvents()
    _LongShotClient.rilascio.set()
    assert _attendi(qapp, lambda: win._remote_view.message() == "Live interrotto.")
    assert win._remote_view._retry.isVisible()
    assert not win._exposure_timer.isActive()


def test_after_the_shot_a_resumed_live_clears_the_countdown(long_win, qapp):
    win = long_win
    _posa(win, qapp)
    live = _FakeLive.istanze[-1]
    live.stalled.emit()
    live.resumed.emit()
    qapp.processEvents()
    _LongShotClient.rilascio.set()
    assert _attendi(qapp, win._remote_view.is_reviewing)
    assert _attendi(qapp, lambda: win._remote_view.message() == "")
    assert not win._remote_view._retry.isVisible()
    assert not win._exposure_timer.isActive()


def test_a_stall_after_the_shot_still_offers_retry(long_win, qapp):
    win = long_win
    _posa(win, qapp)
    _LongShotClient.rilascio.set()
    assert _attendi(qapp, win._remote_view.is_reviewing)
    win._remote_view._review_timer.setInterval(300)
    win._remote_view._review_timer.start()
    _FakeLive.istanze[-1].stalled.emit()
    assert _attendi(qapp, lambda: win._remote_view.message() == "Live interrotto.")
    assert not win._remote_view._retry.isVisible()      # non sopra l'anteprima
    assert _attendi(qapp, win._remote_view._retry.isVisible, 3.0)


def test_leaving_during_the_exposure_stops_the_countdown(long_win, qapp):
    win = long_win
    _posa(win, qapp)
    win.exit_remote("griglia")
    assert not win._exposure_timer.isActive()
    assert win._remote_view.message() == "Uscita dal telecomando…"
    _FakeLive.istanze[-1].stalled.emit()
    _LongShotClient.rilascio.set()
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    assert not win._remote_view._retry.isVisible()


def test_closing_during_the_exposure_stops_the_countdown(long_win, qapp):
    win = long_win
    _posa(win, qapp)
    _LongShotClient.rilascio.set()
    win.close()
    assert not win._exposure_timer.isActive()


def test_a_live_reentry_puts_back_the_mode_chosen_from_the_pc(props_win, qapp):
    # sul campo: rientrare in rec rimette la modalità della ghiera fisica
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("takemode") is not None)
    assert _attendi(qapp, lambda: win._remote_entered)
    win._remote_panel.props.row("takemode").choose("M")
    assert _attendi(qapp, lambda: win._remote_panel.props.row("takemode").confirmed_value() == "M")
    _PropsStubClient.valori["takemode"] = "A"          # la ghiera fisica è su A
    win.on_live_retry()
    assert _attendi(qapp, lambda: _RecordingClient.chiamate.count(("set_prop", "takemode", "M")) == 2)
    assert _attendi(qapp, lambda: win._remote_panel.props.row("takemode").confirmed_value() == "M")


def test_entering_the_remote_follows_the_dial(props_win, qapp):
    win = props_win
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("takemode") is not None)
    _attendi(qapp, lambda: False, 0.3)
    assert not [c for c in _RecordingClient.chiamate if c[:2] == ("set_prop", "takemode")]


# --- sequenze ------------------------------------------------------------------------

import time as _time  # noqa: E402


def _media_seq(nome, kind):
    return MediaFile(f"/DCIM/100OLYMP/{nome}", 100, datetime(2026, 11, 11, 12, 0, 0), kind)


class _FintoBlocco:
    """Al posto di SleepInhibitor: i test non devono bloccare la sospensione del PC."""
    istanze: list = []

    def __init__(self):
        self.eventi = []
        _FintoBlocco.istanze.append(self)

    def start(self, seconds):
        self.eventi.append(("start", seconds))
        return True

    def stop(self):
        self.eventi.append(("stop",))


class _SequenceStubClient(_PropsStubClient):
    """La scheda finta guadagna un JPG e un ORF a ogni scatto riuscito."""
    file: list = []
    guasti = 0                   # i primi N scatti falliscono
    istantanea_rotta = False     # l'elenco in modalità scatto fallisce
    elenco_rotto = 0             # i primi N elenchi in riproduzione falliscono

    def list_media(self, directory="/DCIM", switch_to_play=True):
        _RecordingClient.chiamate.append(("list_media", switch_to_play))
        if not switch_to_play and _SequenceStubClient.istantanea_rotta:
            raise RuntimeError("elenco in modalità scatto non riuscito")
        if switch_to_play and _SequenceStubClient.elenco_rotto > 0:
            _SequenceStubClient.elenco_rotto -= 1
            raise RuntimeError("elenco non riuscito")
        return list(_SequenceStubClient.file)

    def take_picture(self, read_timeout=None):
        _RecordingClient.chiamate.append(("take_picture",))
        if _SequenceStubClient.guasti > 0:
            _SequenceStubClient.guasti -= 1
            raise RuntimeError("scatto non riuscito")
        n = len(_SequenceStubClient.file) // 2 + 1
        _SequenceStubClient.file = _SequenceStubClient.file + [
            _media_seq(f"P{n:07d}.JPG", MediaKind.JPEG), _media_seq(f"P{n:07d}.ORF", MediaKind.RAW)]


def _scatti():
    return _RecordingClient.chiamate.count(("take_picture",))


@pytest.fixture
def seq_win(qapp, settings, tmp_path, monkeypatch):
    _RecordingClient.chiamate = []
    _FakeLive.istanze = []
    _FintoBlocco.istanze = []
    _PropsStubClient.valori = {}
    _SequenceStubClient.file = [_media_seq("P0000001.JPG", MediaKind.JPEG),
                                _media_seq("P0000001.ORF", MediaKind.RAW)]
    _SequenceStubClient.guasti = 0
    _SequenceStubClient.istantanea_rotta = False
    _SequenceStubClient.elenco_rotto = 0
    _RemoteStubClient.revisione = _jpeg_valido()
    settings.setValue("dest_root", str(tmp_path / "dest"))
    monkeypatch.setattr(MainWindow, "SNAPSHOT_SETTLE", 0.0)
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: True)
    win = MainWindow(client_factory=_SequenceStubClient,
                     network_factory=lambda ssid: FakeNetwork(all_green()),
                     settings=settings, live_factory=_FakeLive, inhibitor_factory=_FintoBlocco)
    win.resize(1200, 700)
    win.show()
    win.on_connected("E-M10MarkIV")
    win._view_remote.click()
    assert _attendi(qapp, lambda: win._remote_panel.props.row("isospeedvalue") is not None)
    win._remote_view.set_frame(QImage(64, 48, QImage.Format.Format_RGB32))
    yield win
    win.close()


def test_the_sequence_section_remembers_its_values(windows, qapp, settings):
    settings.setValue("sequence/interval", 10)
    settings.setValue("sequence/duration", 600)
    win = windows(settings)
    sezione = win._remote_panel.sequence
    assert (sezione.interval(), sezione.duration()) == (10, 600)
    sezione._interval.step(1)
    sezione._duration.step(-1)
    assert int(settings.value("sequence/interval")) == 15
    assert int(settings.value("sequence/duration")) == 300
    assert sezione.summary_text() == "20 scatti · video di meno di 1 s a 25 fps"


def test_the_summary_follows_the_exposure_read_from_the_camera(seq_win, qapp):
    win = seq_win
    sezione = win._remote_panel.sequence
    assert _attendi(qapp, lambda: sezione.summary_text() == "360 scatti · video di 14 s a 25 fps")
    _PropsStubClient.valori = {"takemode": "M", "shutspeedvalue": '60"'}
    win.remote_read_props.emit()
    assert _attendi(qapp, lambda: sezione.summary_text() == (
        "la posa dura più dell'intervallo: scatti uno dopo l'altro, circa 15 in 30 min"))


def test_start_takes_the_snapshot_then_shoots_at_the_interval(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert win._remote_panel.sequence.is_running()
    assert _attendi(qapp, lambda: ("list_media", False) in _RecordingClient.chiamate)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert win._sequence_before == {"/DCIM/100OLYMP/P0000001.JPG", "/DCIM/100OLYMP/P0000001.ORF"}
    assert _FintoBlocco.istanze[-1].eventi == [("start", 300)]
    assert not win._remote_panel._shoot.isEnabled() and not win._remote_panel._movie.isEnabled()
    assert not win._remote_panel.props.row("isospeedvalue").isEnabled()
    assert _attendi(qapp, lambda: _scatti() >= 2, 3.0), "il secondo scatto non è partito"
    assert _attendi(qapp, lambda: win._remote_panel.sequence.progress_texts()[0].startswith("Scatto 2"))


def test_stop_ends_the_sequence_and_leaves_the_remote(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert _attendi(qapp, lambda: not win._shot_in_flight)
    win._remote_panel.sequence._button.click()            # Ferma
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    assert win._sequence_result is not None and win._sequence_result.taken >= 1
    assert not win._sequence.is_running() and not win._remote_panel.sequence.is_running()
    assert _FintoBlocco.istanze[-1].eventi[-1] == ("stop",)
    nomi = [c[0] for c in _RecordingClient.chiamate]
    assert "stop_liveview" in nomi and "ensure_play_mode" in nomi


def test_the_sequence_ends_by_itself_at_the_duration(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 2)                           # due scatti: a 0 e a 1 s
    assert _attendi(qapp, lambda: not win._remote_active, 6.0)
    assert win._sequence_result.taken == 2 and not win._sequence_result.aborted
    assert _scatti() == 2


def test_focus_clicks_and_the_shoot_shortcut_are_ignored_during_a_sequence(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert _attendi(qapp, lambda: not win._shot_in_flight)
    prima = _scatti()
    win.on_remote_focus_requested(10, 10)
    win._shortcut_shoot.activated.emit()
    _attendi(qapp, lambda: False, 0.3)
    assert not [c for c in _RecordingClient.chiamate if c[0] == "assign_af"]
    assert _scatti() == prima                              # il prossimo è fra un secondo


def test_a_failed_shot_is_counted_and_the_sequence_goes_on(seq_win, qapp):
    win = seq_win
    _SequenceStubClient.guasti = 1
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 2, 3.0)
    assert _attendi(qapp, lambda: win._remote_panel.sequence.progress_texts()[1].endswith("1 saltato"))
    assert win._sequence.is_running()


def test_three_failed_shots_end_the_sequence_as_aborted(seq_win, qapp):
    win = seq_win
    _SequenceStubClient.guasti = 99
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: win._sequence_result is not None, 6.0)
    assert win._sequence_result.aborted and win._sequence_result.skipped == 3
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    assert _scatti() == 3


def test_the_snapshot_waits_after_a_manual_shot(seq_win, qapp, monkeypatch):
    win = seq_win
    monkeypatch.setattr(MainWindow, "SNAPSHOT_SETTLE", 0.8)
    win._remote_panel._shoot.click()
    assert _attendi(qapp, lambda: _scatti() == 1 and not win._shot_in_flight)
    win.on_sequence_start(1, 300)
    _attendi(qapp, lambda: False, 0.4)
    assert ("list_media", False) not in _RecordingClient.chiamate, "istantanea troppo presto"
    assert _attendi(qapp, lambda: ("list_media", False) in _RecordingClient.chiamate, 2.0)
    assert _attendi(qapp, lambda: len(win._sequence_before or ()) == 4)    # la foto a mano c'è già


def test_stop_while_preparing_cancels_the_start(seq_win, qapp, monkeypatch):
    win = seq_win
    monkeypatch.setattr(MainWindow, "SNAPSHOT_SETTLE", 5.0)
    win._last_shot_end = _time.monotonic()
    win.on_sequence_start(1, 300)
    assert win._remote_panel.sequence.is_running()
    win.on_sequence_stop()
    assert not win._remote_panel.sequence.is_running() and not win._sequence.is_running()
    _attendi(qapp, lambda: False, 0.3)
    assert ("list_media", False) not in _RecordingClient.chiamate and _scatti() == 0
    assert win._remote_active                                # resta nel telecomando


def test_a_failed_snapshot_still_starts_the_sequence(seq_win, qapp):
    win = seq_win
    _SequenceStubClient.istantanea_rotta = True
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert win._sequence_before is None


def test_leaving_during_a_sequence_asks_first(seq_win, qapp, monkeypatch):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert _attendi(qapp, lambda: not win._shot_in_flight)
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: False)
    win._view_grid.click()
    assert win._remote_active and not win._remote_leaving and win._sequence.is_running()
    assert win._view_remote.isChecked() and not win._view_grid.isChecked()
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: True)
    win._view_grid.click()
    assert not win._sequence.is_running()
    assert _attendi(qapp, lambda: not win._remote_active, 5.0)
    assert win._sequence_result is not None and win._sequence_result.taken >= 1


def test_closing_during_a_sequence_asks_first(seq_win, qapp, monkeypatch):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: False)
    assert win.close() is False
    assert win.isVisible() and win._sequence.is_running() and not win._closing
    monkeypatch.setattr(MainWindow, "_confirm", lambda self, titolo, testo: True)
    assert win.close() is True
    assert not win._sequence.is_running()
    assert _FintoBlocco.istanze[-1].eventi[-1] == ("stop",)


def test_the_review_is_shorter_during_a_fast_sequence(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, win._remote_view.is_reviewing)
    assert win._remote_view._review_timer.interval() == 500     # metà dell'intervallo


def test_the_sequence_waits_while_the_remote_is_not_ready(seq_win, qapp):
    win = seq_win
    win.on_sequence_start(1, 300)
    assert _attendi(qapp, lambda: _scatti() >= 1)
    assert _attendi(qapp, lambda: not win._shot_in_flight)
    win._remote_entered = False                               # come durante un rientro nel live
    prima = _scatti()
    _attendi(qapp, lambda: False, 1.5)
    assert _scatti() == prima
    win._remote_entered = True
    assert _attendi(qapp, lambda: _scatti() == prima + 1, 2.0)
