"""I worker si testano senza avviare thread: si invoca direttamente il metodo
di lavoro e si raccolgono i signal emessi. Serve un QApplication perché i
QObject con signal ne richiedono uno."""
from datetime import datetime

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
