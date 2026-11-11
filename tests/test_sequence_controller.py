"""Il regista della sequenza: timer, scatti, sveglia del live, fine."""
import time
from datetime import datetime

from olympus_connect.core.sequence import SequencePlan, SequenceResult
from olympus_connect.gui.sequence import SequenceController

QUANDO = datetime(2026, 11, 11, 14, 30, 0)


class _Orologio:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def avanti(self, secondi):
        self.t += secondi


class _Blocco:
    def __init__(self, parte=True):
        self.parte = parte
        self.eventi = []

    def start(self, seconds):
        self.eventi.append(("start", seconds))
        return self.parte

    def stop(self):
        self.eventi.append(("stop",))


def _regista(pronto=lambda: True, blocco=None):
    """Il timer vero non scatta mai: i test non fanno girare gli eventi e chiamano tick()."""
    orologio = _Orologio()
    blocco = blocco or _Blocco()
    regista = SequenceController(pronto, clock=orologio, inhibitor=blocco, now=lambda: QUANDO)
    ev = {"scatti": 0, "sveglie": 0, "righe": [], "fine": []}
    regista.shoot_requested.connect(lambda: ev.__setitem__("scatti", ev["scatti"] + 1))
    regista.wake_requested.connect(lambda: ev.__setitem__("sveglie", ev["sveglie"] + 1))
    regista.progress.connect(lambda a, b: ev["righe"].append((a, b)))
    regista.ended.connect(ev["fine"].append)
    return regista, orologio, blocco, ev


def test_start_shoots_at_once_and_blocks_the_pc_sleep(qapp):
    regista, _orologio, blocco, ev = _regista()
    assert not regista.is_running()
    assert regista.start(SequencePlan(5, 1800)) is True
    assert regista.is_running() and ev["scatti"] == 1
    assert blocco.eventi == [("start", 1800)]
    assert ev["righe"][-1] == ("Scatto 1 di 360 · in corso", "Mancano 30 min")


def test_shots_follow_the_interval(qapp):
    regista, orologio, _blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    orologio.avanti(1)
    regista.shot_done(True)
    assert ev["righe"][-1][0] == "Scatto 1 di 360 · prossimo fra 4 s"
    orologio.avanti(3.5)
    regista.tick()
    assert ev["scatti"] == 1
    orologio.avanti(0.5)
    regista.tick()
    assert ev["scatti"] == 2


def test_a_due_shot_waits_until_the_remote_is_ready(qapp):
    pronto = {"si": False}
    regista, orologio, _blocco, ev = _regista(lambda: pronto["si"])
    regista.start(SequencePlan(5, 1800))
    orologio.avanti(2)
    regista.tick()
    assert ev["scatti"] == 0 and ev["righe"][-1][0] == "In partenza…"
    pronto["si"] = True
    regista.tick()
    assert ev["scatti"] == 1


def test_stop_ends_after_the_shot_in_flight(qapp):
    regista, orologio, blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    regista.stop()
    assert regista.is_running() and ev["fine"] == []
    orologio.avanti(1)
    regista.shot_done(True)
    assert ev["fine"] == [SequenceResult(taken=1, skipped=0, aborted=False, started_at=QUANDO)]
    assert not regista.is_running() and blocco.eventi[-1] == ("stop",)
    regista.tick()                              # dopo la fine: niente
    assert ev["scatti"] == 1 and len(ev["fine"]) == 1


def test_stop_between_two_shots_ends_at_once(qapp):
    regista, orologio, _blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    orologio.avanti(1)
    regista.shot_done(True)
    regista.stop()
    assert len(ev["fine"]) == 1 and not regista.is_running()


def test_abort_returns_the_result_without_the_signal(qapp):
    regista, orologio, blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    orologio.avanti(1)
    regista.shot_done(True)
    assert regista.abort() == SequenceResult(1, 0, False, QUANDO)
    assert ev["fine"] == [] and not regista.is_running() and blocco.eventi[-1] == ("stop",)
    assert regista.abort() is None              # non c'è più nulla da fermare


def test_three_failed_shots_in_a_row_end_the_sequence_as_aborted(qapp):
    regista, orologio, blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    for _ in range(3):
        orologio.avanti(1)
        regista.shot_done(False)
        orologio.avanti(4)
        regista.tick()
    assert ev["fine"] == [SequenceResult(0, 3, True, QUANDO)]
    assert ev["scatti"] == 3
    assert blocco.eventi[-1] == ("stop",) and not regista.is_running()


def test_the_sequence_ends_with_its_last_shot(qapp):
    regista, orologio, blocco, ev = _regista()
    regista.start(SequencePlan(5, 10))          # due scatti: a 0 e a 5 s
    orologio.avanti(1)
    regista.shot_done(True)
    orologio.avanti(4)
    regista.tick()
    orologio.avanti(1)
    regista.shot_done(True)
    assert ev["fine"] == [SequenceResult(2, 0, False, QUANDO)]
    assert blocco.eventi[-1] == ("stop",)


def test_the_live_is_woken_once_five_seconds_before_each_shot(qapp):
    regista, orologio, _blocco, ev = _regista()
    regista.start(SequencePlan(120, 900))
    orologio.avanti(1)
    regista.shot_done(True)
    assert regista.may_pause_live() and ev["sveglie"] == 0
    orologio.avanti(113)
    regista.tick()
    assert ev["sveglie"] == 0                   # mancano 6 s
    orologio.avanti(1)
    regista.tick()
    regista.tick()
    assert ev["sveglie"] == 1 and ev["scatti"] == 1
    orologio.avanti(5)
    regista.tick()
    assert ev["scatti"] == 2
    orologio.avanti(1)
    regista.shot_done(True)
    orologio.avanti(114)
    regista.tick()
    assert ev["sveglie"] == 2                   # una per scatto


def test_a_handler_that_aborts_on_the_wake_up_gets_no_shot_after_it(qapp):
    regista, orologio, _blocco, ev = _regista()
    regista.wake_requested.connect(regista.abort)
    regista.start(SequencePlan(120, 900))
    orologio.avanti(1)
    regista.shot_done(True)
    orologio.avanti(119)                        # scatto dovuto e sveglia nello stesso giro
    regista.tick()
    assert ev["sveglie"] == 1 and ev["scatti"] == 1 and ev["fine"] == []
    assert not regista.is_running()


def test_a_shot_refused_by_the_receiver_does_not_confuse_the_controller(qapp):
    regista, orologio, _blocco, ev = _regista()
    regista.shoot_requested.connect(lambda: regista.shot_done(False))    # «non posso scattare»
    regista.start(SequencePlan(5, 1800))
    for _ in range(2):
        orologio.avanti(5)
        regista.tick()
    assert ev["fine"] == [SequenceResult(0, 3, True, QUANDO)] and ev["scatti"] == 3
    assert not regista.is_running()


def test_starting_again_closes_the_sequence_in_progress(qapp):
    regista, _orologio, blocco, ev = _regista()
    regista.start(SequencePlan(5, 1800))
    regista.start(SequencePlan(10, 600))
    assert blocco.eventi == [("start", 1800), ("stop",), ("start", 600)]
    assert ev["fine"] == [] and ev["scatti"] == 2 and regista.is_running()


def test_short_intervals_never_pause_the_live(qapp):
    regista, orologio, _blocco, _ev = _regista()
    assert not regista.may_pause_live()         # ferma
    regista.start(SequencePlan(5, 900))
    orologio.avanti(1)
    regista.shot_done(True)
    assert not regista.may_pause_live()


def test_a_missing_sleep_block_is_reported_and_the_sequence_runs(qapp):
    regista, _orologio, _blocco, ev = _regista(blocco=_Blocco(parte=False))
    assert regista.start(SequencePlan(5, 1800)) is False
    assert regista.is_running() and ev["scatti"] == 1


def test_calls_on_an_idle_controller_do_nothing(qapp):
    regista, _orologio, blocco, ev = _regista()
    regista.tick()
    regista.stop()
    regista.shot_done(True)
    assert ev == {"scatti": 0, "sveglie": 0, "righe": [], "fine": []} and blocco.eventi == []


def test_the_real_timer_drives_the_sequence(qapp):
    regista = SequenceController(lambda: True, inhibitor=_Blocco())
    scatti = []
    regista.shoot_requested.connect(lambda: scatti.append(time.monotonic()))
    regista.start(SequencePlan(1, 300))
    regista.shot_done(True)
    fine = time.monotonic() + 6
    while len(scatti) < 2 and time.monotonic() < fine:
        qapp.processEvents()
        time.sleep(0.01)
    regista.abort()
    assert len(scatti) == 2 and 0.9 <= scatti[1] - scatti[0] <= 2.5
