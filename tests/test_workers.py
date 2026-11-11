"""I worker si testano senza avviare thread: si invoca direttamente il metodo
di lavoro e si raccolgono i signal emessi. Serve un QApplication perché i
QObject con signal ne richiedono uno."""
from datetime import datetime
from pathlib import Path

import pytest

from olympus_connect.core.downloader import Job, JobState
from olympus_connect.core.errors import (
    CameraAsleep, CameraNotReachable, NetworkUnavailable, ProtocolError,
)
from olympus_connect.core.model import MediaFile, MediaKind
from olympus_connect.gui.workers import (
    DownloadWorker, ListWorker, ThumbWorker, describe_error,
)


def media(name="A.JPG", size=10):
    return MediaFile(f"/DCIM/100OLYMP/{name}", size,
                     datetime(2026, 9, 24, 12, 0, 0), MediaKind.JPEG)


class FakeClient:
    def __init__(self, files=None, thumb=b"\xff\xd8", fail=None):
        self._files = files or []
        self._thumb = thumb
        self._fail = fail
        self.kept_awake = False

    def connect(self):
        if self._fail:
            raise self._fail
        return "E-M10MarkIV"

    def keep_awake(self, seconds=3600):
        self.kept_awake = True
        return False        # comportamento reale di questo firmware

    def ensure_play_mode(self):
        pass

    def list_media(self, directory="/DCIM"):
        if self._fail:
            raise self._fail
        return self._files

    def thumbnail(self, camera_path):
        if self._fail:
            raise self._fail
        return self._thumb


# --- traduzione degli errori ----------------------------------------------

@pytest.mark.parametrize("exc,fragment", [
    (CameraNotReachable("x"), "non risponde"),
    (CameraAsleep("x"), "disconness"),
    (NetworkUnavailable("x"), "rete"),
    (ProtocolError("x"), "risposta"),
    (OSError(28, "No space left on device"), "spazio"),
])
def test_describe_error_produces_italian_actionable_text(exc, fragment):
    message = describe_error(exc)
    assert fragment.lower() in message.lower()
    assert message[0].isupper()


def test_describe_error_never_promises_resume():
    # Questo firmware non supporta il Range: il messaggio non deve dire
    # che il trasferimento riprende dal punto raggiunto.
    message = describe_error(CameraAsleep("x")).lower()
    assert "riprova" in message
    assert "punto raggiunto" not in message


def test_describe_error_handles_unknown_exception():
    assert describe_error(ValueError("strano")) .startswith("Errore")


# --- ListWorker ------------------------------------------------------------

def test_list_worker_emits_jobs(qapp, tmp_path):
    worker = ListWorker(FakeClient(files=[media()]), tmp_path)
    received = []
    worker.listed.connect(received.append)
    worker.run()

    assert len(received) == 1
    assert received[0][0].state is JobState.NEW


def test_list_worker_calls_keep_awake(qapp, tmp_path):
    client = FakeClient(files=[media()])
    ListWorker(client, tmp_path).run()
    assert client.kept_awake


def test_list_worker_emits_failed_with_italian_message(qapp, tmp_path):
    worker = ListWorker(FakeClient(fail=CameraNotReachable("boom")), tmp_path)
    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors and "non risponde" in errors[0]


def test_list_worker_emits_model(qapp, tmp_path):
    worker = ListWorker(FakeClient(files=[]), tmp_path)
    models = []
    worker.connected.connect(models.append)
    worker.run()
    assert models == ["E-M10MarkIV"]


# --- ThumbWorker -----------------------------------------------------------

def test_thumb_worker_emits_ready_with_path_and_bytes(qapp):
    worker = ThumbWorker(FakeClient(thumb=b"\xff\xd8jpeg"))
    got = []
    worker.ready.connect(lambda p, b: got.append((p, b)))
    worker.request("/DCIM/100OLYMP/A.JPG")

    assert got == [("/DCIM/100OLYMP/A.JPG", b"\xff\xd8jpeg")]


def test_thumb_worker_skips_requests_while_suspended(qapp):
    # Il server HTTP della fotocamera è un microcontrollore: mentre un
    # download è in corso non deve ricevere anche richieste di miniature.
    client = FakeClient(thumb=b"\xff\xd8jpeg")
    worker = ThumbWorker(client)
    got = []
    worker.ready.connect(lambda p, b: got.append(p))

    worker.set_suspended(True)
    worker.request("/DCIM/100OLYMP/A.JPG")
    assert got == []

    worker.set_suspended(False)
    worker.request("/DCIM/100OLYMP/A.JPG")
    assert got == ["/DCIM/100OLYMP/A.JPG"]


def test_thumb_worker_reports_skipped_path_when_suspended(qapp):
    # Il chiamante deve sapere che la richiesta non è stata servita, per
    # poterla ripetere dopo: altrimenti quella miniatura resta vuota.
    worker = ThumbWorker(FakeClient())
    skipped = []
    worker.skipped.connect(skipped.append)

    worker.set_suspended(True)
    worker.request("/DCIM/100OLYMP/A.JPG")
    assert skipped == ["/DCIM/100OLYMP/A.JPG"]


def test_thumb_worker_failure_does_not_raise(qapp):
    worker = ThumbWorker(FakeClient(fail=ProtocolError("nope")))
    failures = []
    worker.failed.connect(lambda p, m: failures.append(p))
    worker.request("/DCIM/100OLYMP/A.JPG")   # non deve propagare

    assert failures == ["/DCIM/100OLYMP/A.JPG"]


# --- DownloadWorker --------------------------------------------------------

def fake_download_ok(client, job, on_progress=None, should_cancel=None, **kw):
    from olympus_connect.core.downloader import Outcome
    if on_progress:
        on_progress(job.media.size, job.media.size)
    return Outcome.DONE


def test_download_worker_reports_total_progress_and_finishes(qapp, tmp_path, monkeypatch):
    jobs = [
        Job(media("A.JPG", 100), tmp_path / "A.JPG", JobState.NEW),
        Job(media("B.JPG", 300), tmp_path / "B.JPG", JobState.NEW),
    ]
    monkeypatch.setattr("olympus_connect.gui.workers.download", fake_download_ok)

    worker = DownloadWorker(FakeClient(), jobs)
    totals, done, finished = [], [], []
    worker.total_progress.connect(lambda w, t: totals.append((w, t)))
    worker.file_done.connect(done.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.run()

    assert done == ["A.JPG", "B.JPG"]
    assert totals[-1] == (400, 400)   # somma delle dimensioni dei job
    assert finished == [True]


def test_download_worker_processes_smallest_file_first(qapp, tmp_path, monkeypatch):
    # L'ordine viene da pending(): senza Range conviene rischiare poco.
    jobs = [
        Job(media("BIG.MOV", 900), tmp_path / "BIG.MOV", JobState.NEW),
        Job(media("SMALL.ORF", 20), tmp_path / "SMALL.ORF", JobState.NEW),
    ]
    monkeypatch.setattr("olympus_connect.gui.workers.download", fake_download_ok)

    worker = DownloadWorker(FakeClient(), jobs)
    done = []
    worker.file_done.connect(done.append)
    worker.run()

    assert done == ["SMALL.ORF", "BIG.MOV"]


def test_download_worker_skips_already_present_jobs(qapp, tmp_path, monkeypatch):
    jobs = [
        Job(media("A.JPG", 100), tmp_path / "A.JPG", JobState.PRESENT),
        Job(media("B.JPG", 100), tmp_path / "B.JPG", JobState.NEW),
    ]
    monkeypatch.setattr("olympus_connect.gui.workers.download", fake_download_ok)

    worker = DownloadWorker(FakeClient(), jobs)
    done = []
    worker.file_done.connect(done.append)
    worker.run()

    assert done == ["B.JPG"]


def test_download_worker_continues_after_single_file_failure(qapp, tmp_path, monkeypatch):
    def flaky(client, job, on_progress=None, should_cancel=None, **kw):
        from olympus_connect.core.downloader import Outcome
        if job.media.name == "A.JPG":
            raise ProtocolError("file illeggibile")
        if on_progress:
            on_progress(100, 100)
        return Outcome.DONE

    jobs = [
        Job(media("A.JPG", 100), tmp_path / "A.JPG", JobState.NEW),
        Job(media("B.JPG", 100), tmp_path / "B.JPG", JobState.NEW),
    ]
    monkeypatch.setattr("olympus_connect.gui.workers.download", flaky)

    worker = DownloadWorker(FakeClient(), jobs)
    failed, done, finished = [], [], []
    worker.file_failed.connect(lambda n, m: failed.append(n))
    worker.file_done.connect(done.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.run()

    assert failed == ["A.JPG"]
    assert done == ["B.JPG"]   # la coda non si ferma per un file
    assert finished == [True]


def test_download_worker_pauses_on_camera_asleep(qapp, tmp_path, monkeypatch):
    def asleep(client, job, on_progress=None, should_cancel=None, **kw):
        raise CameraAsleep("AP caduto")

    jobs = [Job(media("A.JPG", 100), tmp_path / "A.JPG", JobState.NEW)]
    monkeypatch.setattr("olympus_connect.gui.workers.download", asleep)

    worker = DownloadWorker(FakeClient(), jobs)
    paused, finished = [], []
    worker.paused.connect(paused.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.run()

    # Una fotocamera addormentata mette in pausa la coda, non la conclude:
    # i job restanti devono poter riprendere.
    assert paused and "disconness" in paused[0].lower()
    assert finished == []
    assert worker.remaining() == 1


def test_download_worker_resume_retries_the_paused_job(qapp, tmp_path, monkeypatch):
    attempts = []

    def asleep_then_ok(client, job, on_progress=None, should_cancel=None, **kw):
        from olympus_connect.core.downloader import Outcome
        attempts.append(job.media.name)
        if len(attempts) == 1:
            raise CameraAsleep("AP caduto")
        if on_progress:
            on_progress(100, 100)
        return Outcome.DONE

    jobs = [Job(media("A.JPG", 100), tmp_path / "A.JPG", JobState.NEW)]
    monkeypatch.setattr("olympus_connect.gui.workers.download", asleep_then_ok)

    worker = DownloadWorker(FakeClient(), jobs)
    done = []
    worker.file_done.connect(done.append)
    worker.run()
    assert done == []
    worker.resume()
    assert done == ["A.JPG"]


def test_download_worker_cancel_stops_queue(qapp, tmp_path, monkeypatch):
    def cancellable(client, job, on_progress=None, should_cancel=None, **kw):
        from olympus_connect.core.downloader import Outcome
        return Outcome.CANCELLED if should_cancel() else Outcome.DONE

    jobs = [Job(media(f"{c}.JPG", 100), tmp_path / f"{c}.JPG", JobState.NEW)
            for c in "AB"]
    monkeypatch.setattr("olympus_connect.gui.workers.download", cancellable)

    worker = DownloadWorker(FakeClient(), jobs)
    worker.cancel()
    done = []
    worker.file_done.connect(done.append)
    worker.run()

    assert done == []
    assert worker.remaining() == 2


# --- statistiche di avanzamento --------------------------------------------

def test_download_worker_emits_speed_and_eta(qapp, tmp_path, monkeypatch):
    """Su 27,9 GB la domanda vera è «quanto manca», non «a che punto sono»."""
    clock = {"t": 0.0}

    def fake_download(client, job, on_progress=None, should_cancel=None, **kw):
        from olympus_connect.core.downloader import Outcome
        clock["t"] += 1.0
        on_progress(job.media.size, job.media.size)
        return Outcome.DONE

    monkeypatch.setattr("olympus_connect.gui.workers.download", fake_download)

    jobs = [Job(media("A.JPG", 2_000_000), tmp_path / "A.JPG", JobState.NEW),
            Job(media("B.JPG", 2_000_000), tmp_path / "B.JPG", JobState.NEW)]
    worker = DownloadWorker(FakeClient(), jobs, clock=lambda: clock["t"])

    letture = []
    worker.stats_changed.connect(
        lambda written, total, speed, eta: letture.append((written, total, speed, eta))
    )
    worker.run()

    assert letture, "nessuna statistica emessa"
    written, total, speed, eta = letture[-1]
    assert total == 4_000_000
    assert speed is not None and speed > 0
    assert eta == 0          # a fine coda non manca nulla


def test_download_worker_reports_unknown_eta_instead_of_a_made_up_number(
        qapp, tmp_path, monkeypatch):
    def stalled(client, job, on_progress=None, should_cancel=None, **kw):
        from olympus_connect.core.downloader import Outcome
        on_progress(0, job.media.size)      # nessun byte trasferito
        return Outcome.CANCELLED

    monkeypatch.setattr("olympus_connect.gui.workers.download", stalled)
    jobs = [Job(media("A.JPG", 1_000_000), tmp_path / "A.JPG", JobState.NEW)]
    worker = DownloadWorker(FakeClient(), jobs, clock=lambda: 5.0)

    letture = []
    worker.stats_changed.connect(lambda w, t, s, e: letture.append(e))
    worker.run()
    assert letture and letture[-1] is None


def test_download_worker_announces_each_finished_file_by_path(
        qapp, tmp_path, monkeypatch):
    # L'elenco deve aggiornarsi sotto gli occhi: serve il path, non solo il
    # nome, perché è la chiave con cui il modello trova la riga.
    monkeypatch.setattr("olympus_connect.gui.workers.download", fake_download_ok)
    jobs = [Job(media("A.JPG", 10), tmp_path / "A.JPG", JobState.NEW)]
    worker = DownloadWorker(FakeClient(), jobs)
    fatti = []
    worker.file_completed.connect(fatti.append)
    worker.run()
    assert fatti == ["/DCIM/100OLYMP/A.JPG"]


# --- anteprima --------------------------------------------------------------

def test_preview_worker_emits_screennail(qapp):
    from olympus_connect.gui.workers import PreviewWorker

    class ClientConAnteprima(FakeClient):
        def has_screennail(self):
            return True

        def screennail(self, camera_path):
            return b"\xff\xd8grande"

    worker = PreviewWorker(ClientConAnteprima())
    ricevuti = []
    worker.ready.connect(lambda p, d: ricevuti.append((p, d)))
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert ricevuti == [("/DCIM/100OLYMP/A.ORF", b"\xff\xd8grande")]


def test_preview_worker_is_silent_when_camera_lacks_screennail(qapp):
    from olympus_connect.gui.workers import PreviewWorker

    class SenzaAnteprima(FakeClient):
        def has_screennail(self):
            return False

        def screennail(self, camera_path):
            raise AssertionError("non deve essere chiamato")

    worker = PreviewWorker(SenzaAnteprima())
    non_disponibile = []
    worker.unavailable.connect(non_disponibile.append)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert non_disponibile == ["/DCIM/100OLYMP/A.ORF"]


def test_preview_worker_skips_while_suspended(qapp):
    from olympus_connect.gui.workers import PreviewWorker

    class ClientConAnteprima(FakeClient):
        def has_screennail(self):
            return True

        def screennail(self, camera_path):
            return b"\xff\xd8x"

    worker = PreviewWorker(ClientConAnteprima())
    ricevuti = []
    worker.ready.connect(lambda p, d: ricevuti.append(p))
    worker.set_suspended(True)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert ricevuti == []


def test_preview_worker_survives_a_client_without_the_capability_check(qapp):
    """Regressione: l'eccezione non deve sfuggire da uno slot in coda.

    request() è invocato da Qt attraverso una QueuedConnection. Un'eccezione
    che sfugge da lì non risale a un chiamante Python: PyQt fa abortire il
    processo. Con has_screennail fuori dal try, un client che non espone il
    metodo chiudeva l'applicazione invece di degradare.
    """
    from olympus_connect.gui.workers import PreviewWorker

    class ClientIncompleto:
        pass        # nessun has_screennail, nessun screennail

    worker = PreviewWorker(ClientIncompleto())
    errori = []
    worker.failed.connect(lambda p, m: errori.append(m))
    worker.request("/DCIM/100OLYMP/A.ORF")      # non deve sollevare
    assert errori and errori[0]


# --- dati di scatto ------------------------------------------------------------

from fractions import Fraction  # noqa: E402

from olympus_connect.core.exif import ShotInfo  # noqa: E402

# Un EXIF minimo e valido: ISO 200, little-endian, solo IFD0 -> IFD EXIF.
_EXIF_ISO_200 = (
    b"II*\x00\x08\x00\x00\x00"
    b"\x01\x00" b"\x69\x87\x04\x00\x01\x00\x00\x00\x1a\x00\x00\x00" b"\x00\x00\x00\x00"
    b"\x01\x00" b"\x27\x88\x03\x00\x01\x00\x00\x00\xc8\x00\x00\x00" b"\x00\x00\x00\x00"
)


class _ClientConDati(FakeClient):
    def __init__(self, exif=_EXIF_ISO_200, exif_fail=None, screennail_fail=None,
                 has_exif=True):
        super().__init__()
        self.commands = {"get_exif", "get_screennail"}
        self.ordine: list[str] = []
        self._exif = exif
        self._exif_fail = exif_fail
        self._screennail_fail = screennail_fail
        self._has_exif = has_exif

    def has_exif(self):
        return self._has_exif

    def exif(self, camera_path):
        self.ordine.append("exif")
        if self._exif_fail:
            raise self._exif_fail
        return self._exif

    def has_screennail(self):
        return True

    def screennail(self, camera_path):
        self.ordine.append("screennail")
        if self._screennail_fail:
            raise self._screennail_fail
        return b"\xff\xd8grande"


def _registra(worker):
    dati, anteprime, errori = [], [], []
    worker.shot_ready.connect(lambda p, i: dati.append((p, i)))
    worker.ready.connect(lambda p, d: anteprime.append(p))
    worker.failed.connect(lambda p, m: errori.append(p))
    return dati, anteprime, errori


def test_preview_worker_reads_shot_data_before_the_preview(qapp):
    from olympus_connect.gui.workers import PreviewWorker
    client = _ClientConDati()
    worker = PreviewWorker(client)
    dati, anteprime, _ = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert client.ordine == ["exif", "screennail"]
    assert dati == [("/DCIM/100OLYMP/A.ORF", ShotInfo(iso=200))]
    assert anteprime == ["/DCIM/100OLYMP/A.ORF"]


def test_failed_shot_data_still_loads_the_preview(qapp):
    from olympus_connect.core.errors import ProtocolError
    from olympus_connect.gui.workers import PreviewWorker
    worker = PreviewWorker(_ClientConDati(exif_fail=ProtocolError("x")))
    dati, anteprime, errori = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert dati == [("/DCIM/100OLYMP/A.ORF", None)]
    assert anteprime == ["/DCIM/100OLYMP/A.ORF"]
    assert errori == []


def test_unreadable_shot_data_is_reported_as_none(qapp):
    from olympus_connect.gui.workers import PreviewWorker
    worker = PreviewWorker(_ClientConDati(exif=b"non exif"))
    dati, _, _ = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert dati == [("/DCIM/100OLYMP/A.ORF", None)]


def test_failed_preview_still_delivers_shot_data(qapp):
    from olympus_connect.core.errors import ProtocolError
    from olympus_connect.gui.workers import PreviewWorker
    worker = PreviewWorker(_ClientConDati(screennail_fail=ProtocolError("x")))
    dati, _, errori = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert dati == [("/DCIM/100OLYMP/A.ORF", ShotInfo(iso=200))]
    assert errori == ["/DCIM/100OLYMP/A.ORF"]


def test_no_shot_data_request_for_videos(qapp):
    from olympus_connect.gui.workers import PreviewWorker
    client = _ClientConDati()
    worker = PreviewWorker(client)
    dati, _, _ = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.MOV")
    assert "exif" not in client.ordine
    assert dati == []


def test_no_shot_data_when_the_camera_lacks_get_exif(qapp):
    from olympus_connect.gui.workers import PreviewWorker
    client = _ClientConDati(has_exif=False)
    worker = PreviewWorker(client)
    dati, anteprime, _ = _registra(worker)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert client.ordine == ["screennail"]
    assert dati == []
    assert anteprime == ["/DCIM/100OLYMP/A.ORF"]


def test_no_shot_data_while_suspended(qapp):
    from olympus_connect.gui.workers import PreviewWorker
    client = _ClientConDati()
    worker = PreviewWorker(client)
    dati, _, _ = _registra(worker)
    worker.set_suspended(True)
    worker.request("/DCIM/100OLYMP/A.ORF")
    assert client.ordine == []
    assert dati == []


# --- codici d'errore della fotocamera ------------------------------------------

def test_describe_error_explains_code_1004():
    testo = describe_error(ProtocolError("x", code=1004))
    assert "live" in testo and "riprova" in testo.lower()


def test_describe_error_shows_other_codes():
    assert "1005" in describe_error(ProtocolError("x", code=1005))


# --- telecomando -----------------------------------------------------------------

class _RemoteClient:
    def __init__(self, fail=None):
        self.commands = {"exec_takemotion"}
        self.calls = []
        self._fail = fail or {}

    def _call(self, nome, *args):
        self.calls.append((nome, *args))
        if nome in self._fail:
            raise self._fail[nome]

    def enter_rec_mode(self, quality):
        self._call("enter_rec_mode", quality)

    def start_liveview(self, port):
        self._call("start_liveview", port)

    def stop_liveview(self):
        self._call("stop_liveview")

    def take_mode(self):
        self._call("take_mode")
        return "S"

    def touch_area(self):
        self._call("touch_area")
        return (96, 72, 448, 336)

    def assign_af(self, x, y):
        self._call("assign_af", x, y)
        return True

    def take_picture(self, read_timeout=None):
        self._call("take_picture")
        self.read_timeout = read_timeout

    def rec_view(self):
        self._call("rec_view")
        return b"\xff\xd8rev"

    def start_movie(self):
        self._call("start_movie")

    def stop_movie(self):
        self._call("stop_movie")

    def ensure_play_mode(self):
        self._call("ensure_play_mode")


def _remote(client):
    from olympus_connect.gui.workers import RemoteWorker
    worker = RemoteWorker(client, "1024x0768")
    ev = {k: [] for k in ("entered", "focused", "shot", "started", "stopped", "left", "failed")}
    worker.entered.connect(lambda m, a: ev["entered"].append((m, a)))
    worker.focused.connect(ev["focused"].append)
    worker.shot.connect(ev["shot"].append)
    worker.movie_started.connect(lambda: ev["started"].append(True))
    worker.movie_stopped.connect(lambda: ev["stopped"].append(True))
    worker.left.connect(lambda: ev["left"].append(True))
    worker.failed.connect(ev["failed"].append)
    return worker, ev


def test_remote_enter_switches_mode_then_starts_the_live(qapp):
    client = _RemoteClient()
    worker, ev = _remote(client)
    worker.enter("1024x0768", 40123)
    assert client.calls[:2] == [("enter_rec_mode", "1024x0768"), ("start_liveview", 40123)]
    assert ev["entered"] == [("S", (96, 72, 448, 336))]


def test_remote_enter_failure_is_reported_not_raised(qapp):
    client = _RemoteClient(fail={"start_liveview": CameraNotReachable("x")})
    worker, ev = _remote(client)
    worker.enter("1024x0768", 1)
    assert ev["entered"] == [] and ev["failed"]


def test_remote_enter_survives_unreadable_dial_and_area(qapp):
    client = _RemoteClient(fail={"take_mode": ProtocolError("x"), "touch_area": ProtocolError("x")})
    worker, ev = _remote(client)
    worker.enter("1024x0768", 1)
    assert ev["entered"] == [("", None)]


def test_remote_focus_and_shoot(qapp):
    client = _RemoteClient()
    worker, ev = _remote(client)
    worker.focus(320, 240)
    worker.shoot()
    assert ("assign_af", 320, 240) in client.calls
    assert ev["focused"] == [True] and ev["shot"] == [b"\xff\xd8rev"]


def test_remote_shot_without_review_emits_empty(qapp):
    client = _RemoteClient(fail={"rec_view": ProtocolError("x")})
    worker, ev = _remote(client)
    worker.shoot()
    assert ev["shot"] == [b""] and ev["failed"] == []


def test_remote_refused_shot_is_a_failure(qapp):
    client = _RemoteClient(fail={"take_picture": ProtocolError("x", code=1004)})
    worker, ev = _remote(client)
    worker.shoot()
    assert ev["shot"] == [] and "live" in ev["failed"][0]


def test_remote_movie_start_and_stop(qapp):
    client = _RemoteClient()
    worker, ev = _remote(client)
    worker.start_movie()
    assert worker.is_recording() and ev["started"] == [True]
    worker.stop_movie()
    assert not worker.is_recording() and ev["stopped"] == [True]


def test_remote_leave_stops_a_recording_first_and_always_ends_in_play(qapp):
    client = _RemoteClient(fail={"stop_liveview": CameraNotReachable("x")})
    worker, ev = _remote(client)
    worker.start_movie()
    worker.leave()
    nomi = [c[0] for c in client.calls]
    assert nomi[-3:] == ["stop_movie", "stop_liveview", "ensure_play_mode"]
    assert ev["left"] == [True]


def test_remote_leave_stops_a_movie_whose_start_timed_out(qapp):
    # start_movie in timeout: la fotocamera può aver iniziato lo stesso.
    client = _RemoteClient(fail={"start_movie": CameraNotReachable("timeout")})
    worker, ev = _remote(client)
    worker.start_movie()
    assert not worker.is_recording() and ev["failed"]
    worker.leave()
    nomi = [c[0] for c in client.calls]
    assert nomi[-3:] == ["stop_movie", "stop_liveview", "ensure_play_mode"]


def test_remote_leave_does_not_stop_a_movie_after_a_clean_stop(qapp):
    client = _RemoteClient()
    worker, _ = _remote(client)
    worker.start_movie()
    worker.stop_movie()
    worker.leave()
    assert [c[0] for c in client.calls].count("stop_movie") == 1


def test_remote_leave_emits_left_even_if_everything_fails(qapp):
    client = _RemoteClient(fail={n: CameraNotReachable("x") for n in
                                 ("stop_movie", "stop_liveview", "ensure_play_mode")})
    worker, ev = _remote(client)
    worker.leave()
    assert ev["left"] == [True]


def test_remote_client_without_commands_connects_first(qapp):
    client = _RemoteClient()
    client.commands = set()
    client.connect = lambda: client.calls.append(("connect",)) or "E-M10MarkIV"
    worker, _ev = _remote(client)
    worker.enter("1024x0768", 1)
    assert client.calls[0] == ("connect",)


# --- impostazioni ------------------------------------------------------------------

class _PropsClient(_RemoteClient):
    def __init__(self, fail=None, props=None):
        super().__init__(fail=fail)
        self.props = props or {"isospeedvalue": "200"}

    def camera_props(self):
        self._call("camera_props")
        return dict(self.props)

    def set_prop(self, name, value):
        self._call("set_prop", name, value)
        self.props[name] = value


def _props_worker(client):
    worker, ev = _remote(client)
    ev["props"], ev["props_failed"] = [], []
    worker.props_read.connect(ev["props"].append)
    worker.props_failed.connect(ev["props_failed"].append)
    return worker, ev


def test_read_props_emits_the_list(qapp):
    worker, ev = _props_worker(_PropsClient())
    worker.read_props()
    assert ev["props"] == [{"isospeedvalue": "200"}]


def test_set_prop_always_rereads(qapp):
    client = _PropsClient()
    worker, ev = _props_worker(client)
    worker.set_prop("isospeedvalue", "250")
    assert [c[0] for c in client.calls] == ["set_prop", "camera_props"]
    assert ev["props"] == [{"isospeedvalue": "250"}]


def test_refused_set_prop_reports_and_still_rereads(qapp):
    client = _PropsClient(fail={"set_prop": ProtocolError("x", code=1003)})
    worker, ev = _props_worker(client)
    worker.set_prop("isospeedvalue", "250")
    assert ev["failed"] and ev["props"] == [{"isospeedvalue": "200"}]


def test_failed_read_reports_props_failed(qapp):
    worker, ev = _props_worker(_PropsClient(fail={"camera_props": CameraNotReachable("x")}))
    worker.read_props()
    assert ev["props"] == [] and ev["props_failed"]


# --- pose lunghe -------------------------------------------------------------------

_REMOTE_FIXTURES = Path(__file__).parent / "fixtures" / "remote"


def _desclist(file, **valori):
    from olympus_connect.core.camprops import CameraProp, parse_desclist
    props = parse_desclist((_REMOTE_FIXTURES / file).read_bytes())
    for nome, valore in valori.items():
        p = props[nome]
        props[nome] = CameraProp(p.name, valore, p.values, p.settable)
    return props


class _ExposureClient(_PropsClient):
    def __init__(self, props, fail=None):
        super().__init__(fail=fail)
        self.props = props

    def take_picture(self, read_timeout=None):
        self._call("take_picture", read_timeout)
        self.read_timeout = read_timeout


def _exposure_worker(client):
    worker, ev = _props_worker(client)
    ev["exposing"] = []
    worker.exposing.connect(lambda s: ev["exposing"].append((s, list(client.calls))))
    return worker, ev


def test_a_long_exposure_is_announced_before_starttake(qapp):
    client = _ExposureClient(_desclist("desclist_M.xml", shutspeedvalue='60"'))
    worker, ev = _exposure_worker(client)
    worker.read_props()
    worker.shoot()
    [(secondi, chiamate_prima)] = ev["exposing"]
    assert secondi == pytest.approx(120.0)
    assert "take_picture" not in [c[0] for c in chiamate_prima]
    assert ("take_picture", pytest.approx(147.0)) in client.calls
    assert ev["shot"] == [b"\xff\xd8rev"]


def test_a_short_shot_is_not_announced(qapp):
    client = _ExposureClient(_desclist("desclist_M.xml", shutspeedvalue="250"))
    worker, ev = _exposure_worker(client)
    worker.read_props()
    worker.shoot()
    assert ev["exposing"] == []
    assert client.read_timeout == pytest.approx(15.0044)


def test_an_automatic_shutter_is_not_announced_and_waits_the_worst_case(qapp):
    client = _ExposureClient(_desclist("desclist_A.xml"))
    worker, ev = _exposure_worker(client)
    worker.read_props()
    worker.shoot()
    assert ev["exposing"] == []
    assert client.read_timeout == pytest.approx(150.0)


def test_shooting_before_any_read_waits_the_worst_case(qapp):
    client = _ExposureClient({})
    worker, ev = _exposure_worker(client)
    worker.shoot()
    assert ev["exposing"] == [] and client.read_timeout == pytest.approx(150.0)


def test_the_last_read_wins(qapp):
    client = _ExposureClient(_desclist("desclist_M.xml", shutspeedvalue='60"'))
    worker, ev = _exposure_worker(client)
    worker.read_props()
    client.props = _desclist("desclist_M.xml", shutspeedvalue='8"', noisereduction="off")
    worker.read_props()
    worker.shoot()
    assert [s for s, _ in ev["exposing"]] == [pytest.approx(8.0)]
    assert client.read_timeout == pytest.approx(23.8)


# --- sequenze: istantanea dei nomi, pausa del live, montaggio -------------------------

import threading  # noqa: E402

from olympus_connect.core.timelapse import find_ffmpeg  # noqa: E402
from olympus_connect.gui.workers import RemoteWorker, TimelapseWorker  # noqa: E402


class _SequenceClient:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise RuntimeError("guasto finto")

    def list_media(self, directory="/DCIM", switch_to_play=True):
        self._call("list_media", directory, switch_to_play)
        return [media("P1.JPG"), media("P1.ORF")]

    def stop_liveview(self):
        self._call("stop_liveview")

    def start_liveview(self, port):
        self._call("start_liveview", port)


def _sequence_worker(client):
    worker = RemoteWorker(client)
    ev = {"names": [], "resumed": [], "failed": []}
    worker.names_read.connect(ev["names"].append)
    worker.live_resumed.connect(ev["resumed"].append)
    worker.failed.connect(ev["failed"].append)
    return worker, ev


def test_snapshot_reads_the_card_without_leaving_shooting_mode(qapp):
    client = _SequenceClient()
    worker, ev = _sequence_worker(client)
    worker.snapshot_names()
    assert client.calls == [("list_media", "/DCIM", False)]
    assert ev["names"] == [{"/DCIM/100OLYMP/P1.JPG", "/DCIM/100OLYMP/P1.ORF"}]


def test_a_failed_snapshot_reports_none_and_no_error(qapp):
    worker, ev = _sequence_worker(_SequenceClient(fail=["list_media"]))
    worker.snapshot_names()
    assert ev["names"] == [None] and ev["failed"] == []


def test_pause_and_resume_the_live(qapp):
    client = _SequenceClient()
    worker, ev = _sequence_worker(client)
    worker.pause_live()
    worker.resume_live(5555)
    assert client.calls == [("stop_liveview",), ("start_liveview", 5555)]
    assert ev["resumed"] == [True] and ev["failed"] == []


def test_live_pause_and_resume_failures_stay_quiet(qapp):
    worker, ev = _sequence_worker(_SequenceClient(fail=["stop_liveview", "start_liveview"]))
    worker.pause_live()
    worker.resume_live(5555)
    assert ev["resumed"] == [False] and ev["failed"] == []


def _foto(tmp_path, quante=3):
    cartella = tmp_path / "Sequenza"
    cartella.mkdir()
    foto = []
    for i in range(quante):
        percorso = cartella / f"P{i}.JPG"
        percorso.write_bytes(b"jpeg finto")
        foto.append(percorso)
    return cartella, foto


def _finto_ffmpeg(tmp_path, corpo):
    """Uno script al posto di ffmpeg: l'ultimo argomento è il file di uscita."""
    script = tmp_path / "finto-ffmpeg"
    script.write_text('#!/bin/sh\nfor a in "$@"; do out="$a"; done\n' + corpo)
    script.chmod(0o755)
    return str(script)


def _timelapse(foto, uscita, ffmpeg):
    worker = TimelapseWorker(foto, uscita, 25, (1920, 1080), ffmpeg=ffmpeg)
    ev = {"progress": [], "done": [], "failed": [], "cancelled": 0}
    worker.progress.connect(lambda n, t: ev["progress"].append((n, t)))
    worker.done.connect(ev["done"].append)
    worker.failed.connect(ev["failed"].append)
    worker.cancelled.connect(lambda: ev.__setitem__("cancelled", ev["cancelled"] + 1))
    return worker, ev


def test_timelapse_reports_progress_and_renames_the_finished_video(qapp, tmp_path):
    cartella, foto = _foto(tmp_path)
    uscita = cartella / "timelapse.mp4"
    ffmpeg = _finto_ffmpeg(tmp_path, 'echo frame=1\necho fps=25\necho frame=3\necho video > "$out"\n')
    worker, ev = _timelapse(foto, uscita, ffmpeg)
    worker.run()
    assert ev["progress"] == [(1, 3), (3, 3)]
    assert ev["done"] == [str(uscita)] and ev["failed"] == []
    assert uscita.read_text() == "video\n"
    assert not (cartella / "timelapse.mp4.part").exists()


def test_timelapse_failure_removes_the_partial_file_and_keeps_the_photos(qapp, tmp_path):
    cartella, foto = _foto(tmp_path)
    uscita = cartella / "timelapse.mp4"
    ffmpeg = _finto_ffmpeg(tmp_path, 'echo meta > "$out"\necho "codec rotto" >&2\nexit 1\n')
    worker, ev = _timelapse(foto, uscita, ffmpeg)
    worker.run()
    assert ev["done"] == [] and len(ev["failed"]) == 1
    assert "Il montaggio del video non è riuscito" in ev["failed"][0]
    assert "codec rotto" in ev["failed"][0]
    assert not uscita.exists() and not (cartella / "timelapse.mp4.part").exists()
    assert all(p.exists() for p in foto)


def test_timelapse_without_ffmpeg_fails_with_a_clear_message(qapp, tmp_path, monkeypatch):
    cartella, foto = _foto(tmp_path)
    monkeypatch.setattr("olympus_connect.gui.workers.find_ffmpeg", lambda: None)
    worker, ev = _timelapse(foto, cartella / "timelapse.mp4", None)
    worker.run()
    assert ev["failed"] == ["ffmpeg non è installato: il video non si può montare."]


def test_timelapse_can_be_cancelled_from_another_thread(qapp, tmp_path):
    cartella, foto = _foto(tmp_path)
    uscita = cartella / "timelapse.mp4"
    ffmpeg = _finto_ffmpeg(tmp_path, 'echo meta > "$out"\necho frame=1\nexec sleep 30\n')
    worker, ev = _timelapse(foto, uscita, ffmpeg)
    worker.progress.connect(lambda n, t: threading.Thread(target=worker.cancel).start())
    worker.run()                                 # torna quando il processo è stato terminato
    assert ev["cancelled"] == 1 and ev["done"] == [] and ev["failed"] == []
    assert not uscita.exists() and not (cartella / "timelapse.mp4.part").exists()


def test_timelapse_with_the_real_ffmpeg(qapp, tmp_path):
    if find_ffmpeg() is None:
        pytest.skip("ffmpeg non installato")
    from PyQt6.QtGui import QImage
    cartella = tmp_path / "Sequenza"
    cartella.mkdir()
    foto = []
    for i, colore in enumerate((0xFF203040, 0xFF806020, 0xFF20A060)):
        immagine = QImage(640, 480, QImage.Format.Format_RGB32)
        immagine.fill(colore)
        percorso = cartella / f"P{i}.JPG"
        assert immagine.save(str(percorso), "JPEG")
        foto.append(percorso)
    uscita = cartella / "timelapse.mp4"
    worker, ev = _timelapse(foto, uscita, None)
    worker.run()
    assert ev["failed"] == [] and ev["done"] == [str(uscita)]
    assert ev["progress"][-1] == (3, 3) and uscita.stat().st_size > 1000
