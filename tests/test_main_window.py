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

    def take_picture(self):
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
