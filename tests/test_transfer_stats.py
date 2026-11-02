"""Velocità e tempo stimato di un trasferimento.

Logica pura, con orologio iniettabile: i test non devono aspettare secondi
reali per verificare una media mobile.
"""
import pytest

from olympus_connect.core.stats import TransferStats


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_no_speed_before_two_samples():
    stats = TransferStats(total=1000, clock=FakeClock())
    stats.update(0)
    assert stats.speed() is None
    assert stats.eta() is None


def test_speed_is_bytes_per_second():
    clock = FakeClock()
    stats = TransferStats(total=10_000, clock=clock)
    stats.update(0)
    clock.advance(2.0)
    stats.update(4_000)
    assert stats.speed() == pytest.approx(2_000, rel=0.01)


def test_eta_from_remaining_bytes():
    clock = FakeClock()
    stats = TransferStats(total=10_000, clock=clock)
    stats.update(0)
    clock.advance(1.0)
    stats.update(2_000)          # 2000 B/s, restano 8000 byte
    assert stats.eta() == pytest.approx(4.0, rel=0.01)


def test_speed_uses_a_moving_window_not_the_whole_transfer():
    # Una pausa iniziale non deve deprimere la stima per sempre: la media su
    # tutto il trasferimento direbbe 500 B/s, la finestra mobile 2000.
    clock = FakeClock()
    stats = TransferStats(total=100_000, clock=clock, window=3.0)
    stats.update(0)
    clock.advance(10.0)
    stats.update(0)              # dieci secondi fermi
    for _ in range(3):
        clock.advance(1.0)
        stats.update(stats.written + 2_000)
    assert stats.speed() == pytest.approx(2_000, rel=0.1)


def test_eta_is_none_when_stalled():
    clock = FakeClock()
    stats = TransferStats(total=10_000, clock=clock, window=2.0)
    stats.update(0)
    clock.advance(5.0)
    stats.update(0)
    assert stats.speed() == 0
    assert stats.eta() is None   # non "infinito", non "0": sconosciuto


def test_eta_is_zero_when_complete():
    clock = FakeClock()
    stats = TransferStats(total=1_000, clock=clock)
    stats.update(0)
    clock.advance(1.0)
    stats.update(1_000)
    assert stats.eta() == 0


def test_update_never_goes_backwards():
    # Un riavvio da zero dopo un'interruzione non deve produrre un ETA assurdo.
    clock = FakeClock()
    stats = TransferStats(total=10_000, clock=clock)
    stats.update(5_000)
    clock.advance(1.0)
    stats.update(0)
    assert stats.written == 0
    assert stats.speed() is None or stats.speed() >= 0


@pytest.mark.parametrize("seconds,atteso", [
    (0, "0 s"),
    (45, "45 s"),
    (90, "1 min 30 s"),
    (3600, "1 h 0 min"),
    (5400, "1 h 30 min"),
    (86_400, "oltre un giorno"),
])
def test_format_duration(seconds, atteso):
    from olympus_connect.core.stats import format_duration
    assert format_duration(seconds) == atteso


@pytest.mark.parametrize("rate,atteso", [
    (0, "0 B/s"),
    (900, "900 B/s"),
    (2_500_000, "2.5 MB/s"),
    (1_500, "1.5 kB/s"),
])
def test_format_rate(rate, atteso):
    from olympus_connect.core.stats import format_rate
    assert format_rate(rate) == atteso
