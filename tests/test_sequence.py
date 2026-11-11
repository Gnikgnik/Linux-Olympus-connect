"""Il piano di una sequenza, l'automa che decide gli scatti, i testi e i file."""
from datetime import datetime

import pytest

from olympus_connect.core.model import MediaFile, MediaKind
from olympus_connect.core.sequence import (
    DEFAULT_DURATION, DEFAULT_INTERVAL, DURATIONS, INTERVALS, SequencePlan, SequenceState,
    folder_name, format_span, nearest, progress_text, sequence_files,
)


def test_the_offered_values_are_sorted_and_hold_the_defaults():
    assert list(INTERVALS) == sorted(INTERVALS) and INTERVALS[0] == 2
    assert list(DURATIONS) == sorted(DURATIONS) and DURATIONS[0] == 300
    assert DEFAULT_INTERVAL in INTERVALS and DEFAULT_DURATION in DURATIONS


@pytest.mark.parametrize("secondi, testo", [
    (0, "0 s"), (5, "5 s"), (60, "1 min"), (72, "1 min 12 s"), (2700, "45 min"),
    (3600, "1 h"), (5400, "1 h 30"), (43200, "12 h"), (14.4, "14 s"),
])
def test_format_span(secondi, testo):
    assert format_span(secondi) == testo


def test_nearest_snaps_a_saved_value_to_the_list():
    assert nearest(INTERVALS, 5) == 5
    assert nearest(INTERVALS, 7) == 8
    assert nearest(INTERVALS, 4) == 3          # a pari distanza, il più piccolo
    assert nearest(INTERVALS, 99999) == 1800


# -- piano ------------------------------------------------------------------------

def test_expected_shots():
    assert SequencePlan(5, 1800).expected_shots() == 360
    assert SequencePlan(120, 900).expected_shots() == 8          # a 0, 120, … 840 s
    assert SequencePlan(5, 1800).expected_shots(busy=0.004) == 360
    assert SequencePlan(5, 1800).expected_shots(busy=120.0) == 15   # 60" più fotogramma scuro
    assert SequencePlan(2, 300).expected_shots(busy=None) == 150


def test_summary_lines():
    assert SequencePlan(5, 1800).summary() == "360 scatti · video di 14 s a 25 fps"
    assert SequencePlan(5, 1800).summary(fps=30) == "360 scatti · video di 12 s a 30 fps"
    assert SequencePlan(1800, 300).summary() == "1 scatti · video di meno di 1 s a 25 fps"
    assert SequencePlan(2, 43200).summary(fps=24) == "21600 scatti · video di 15 min a 24 fps"
    assert SequencePlan(5, 1800).summary(busy=120.0) == (
        "la posa dura più dell'intervallo: scatti uno dopo l'altro, circa 15 in 30 min")
    assert SequencePlan(5, 1800).summary(busy=3.0) == "360 scatti · video di 14 s a 25 fps"


def test_only_long_intervals_pause_the_live():
    assert not SequencePlan(45, 600).pauses_live()
    assert SequencePlan(60, 600).pauses_live()


# -- automa -----------------------------------------------------------------------

def _gira(stato, fino_a, durata_scatto=0.5, passo=0.25, pronto=lambda t: True, ok=lambda n: True):
    """Fa girare l'automa con un orologio finto; ritorna gli istanti di partenza."""
    partenze, fine_scatto, t = [], None, 0.0
    stato.start(0.0)
    while t <= fino_a and not stato.finished:
        if fine_scatto is not None and t >= fine_scatto:
            stato.shot_done(ok(len(partenze) - 1), t)       # indice dello scatto, da 0
            fine_scatto = None
        if stato.tick(t, pronto(t)):
            partenze.append(t)
            fine_scatto = t + durata_scatto
        t = round(t + passo, 6)
    return partenze


def test_regular_cadence_and_end_at_the_duration():
    stato = SequenceState(SequencePlan(5, 30))
    partenze = _gira(stato, 60)
    assert partenze == [0.0, 5.0, 10.0, 15.0, 20.0, 25.0]
    assert stato.finished and not stato.aborted
    assert (stato.taken, stato.skipped, stato.index, stato.total) == (6, 0, 6, 6)


def test_the_sequence_ends_right_after_the_last_shot_not_at_the_duration():
    stato = SequenceState(SequencePlan(120, 900))
    stato.start(0.0)
    for t in range(0, 841, 120):
        assert stato.tick(float(t), True)
        stato.shot_done(True, t + 1.0)
    assert stato.finished and stato.taken == 8        # finita a 841 s, non a 900


def test_a_shot_longer_than_the_interval_goes_back_to_back_without_catching_up():
    stato = SequenceState(SequencePlan(5, 60), busy=12.0)
    partenze = _gira(stato, 80, durata_scatto=12.0)
    assert partenze == [0.0, 12.0, 24.0, 36.0, 48.0]   # uno alla volta, mai una raffica
    assert stato.skipped == 0 and stato.taken == 5
    assert stato.total == 5                            # ceil(60 / 13)


def test_a_tick_exactly_on_the_grid_never_fires_the_same_slot_twice():
    # Dalla revisione: con t0 = 0,1 e giri ogni 0,2 s la divisione arrotondava
    # per difetto e lo scatto appena fatto tornava «dovuto»: 302 scatti invece di 300.
    stato = SequenceState(SequencePlan(2, 600))
    stato.start(0.1)
    partenze, fine_scatto, n = [], None, 0
    while not stato.finished and n < 4000:
        adesso = 0.1 + n * 0.2
        if fine_scatto is not None and adesso >= fine_scatto:
            stato.shot_done(True, adesso)
            fine_scatto = None
        if stato.tick(adesso, True):
            partenze.append(adesso)
            fine_scatto = adesso + 1.0
        n += 1
    assert stato.finished and len(partenze) == 300 == stato.total
    distanze = [b - a for a, b in zip(partenze, partenze[1:])]
    assert min(distanze) > 1.9 and max(distanze) < 2.1


def test_stop_during_a_shot_that_then_fails_still_ends():
    stato = SequenceState(SequencePlan(5, 600))
    stato.start(0.0)
    assert stato.tick(0.0, True)
    stato.stop()
    stato.shot_done(False, 1.0)
    assert stato.finished and not stato.aborted and (stato.taken, stato.skipped) == (0, 1)


def test_nothing_happens_before_start():
    stato = SequenceState(SequencePlan(5, 600))
    assert not stato.running and not stato.tick(0.0, True)
    stato.shot_done(True, 1.0)
    assert stato.index == 0 and not stato.finished
    assert not stato.may_pause_live(0.0) and not stato.wake_due(0.0)
    assert stato.remaining(0.0) == 0.0


def test_no_shot_starts_after_the_duration_but_the_one_in_flight_ends():
    stato = SequenceState(SequencePlan(5, 10))
    stato.start(0.0)
    assert stato.tick(0.0, True)
    stato.shot_done(True, 1.0)
    assert stato.tick(5.0, True)
    assert not stato.tick(11.0, True) and not stato.finished     # scatto in corso
    stato.shot_done(True, 14.0)
    assert stato.finished and stato.taken == 2


def test_stop_waits_for_the_shot_in_flight():
    stato = SequenceState(SequencePlan(5, 600))
    stato.start(0.0)
    assert stato.tick(0.0, True)
    stato.stop()
    assert not stato.finished and stato.running
    stato.shot_done(True, 0.8)
    assert stato.finished and not stato.aborted and stato.taken == 1
    fermo = SequenceState(SequencePlan(5, 600))
    fermo.start(0.0)
    fermo.stop()
    assert fermo.finished


def test_a_failed_shot_is_skipped_and_the_sequence_goes_on():
    stato = SequenceState(SequencePlan(5, 30))
    _gira(stato, 60, ok=lambda n: n != 2)
    assert (stato.taken, stato.skipped) == (5, 1) and not stato.aborted


def test_three_failures_in_a_row_abort():
    stato = SequenceState(SequencePlan(5, 600))
    partenze = _gira(stato, 600, ok=lambda n: n < 2)
    assert stato.aborted and stato.finished
    assert (stato.taken, stato.skipped) == (2, 3) and len(partenze) == 5


def test_a_success_resets_the_row_of_failures():
    stato = SequenceState(SequencePlan(5, 60))
    _gira(stato, 100, ok=lambda n: n % 3 == 0)         # due falliti, uno riuscito, …
    assert not stato.aborted and stato.skipped == 8


def test_remote_not_ready_for_long_counts_as_skipped_and_then_aborts():
    stato = SequenceState(SequencePlan(5, 600))
    stato.start(0.0)
    assert stato.tick(0.0, True)
    stato.shot_done(True, 1.0)
    for t in range(5, 35):                              # dovuto a 5 s, mai pronto
        assert not stato.tick(float(t), False)
    assert stato.skipped == 0                           # 30 s non ancora passati
    stato.tick(35.0, False)
    assert stato.skipped == 1
    stato.tick(65.0, False)
    stato.tick(95.0, False)
    assert stato.aborted and stato.skipped == 3


def test_waiting_for_a_long_exposure_never_counts_as_skipped():
    stato = SequenceState(SequencePlan(5, 600), busy=120.0)
    stato.start(0.0)
    assert stato.tick(0.0, True)
    for t in range(1, 130):
        assert not stato.tick(float(t), False)          # scatto in corso
    stato.shot_done(True, 130.0)
    assert stato.tick(130.0, True)
    assert stato.skipped == 0


def test_the_blocked_period_starts_when_the_camera_is_free():
    stato = SequenceState(SequencePlan(5, 600))
    stato.start(0.0)
    assert stato.tick(0.0, True)
    stato.shot_done(True, 20.0)                         # dovuto a 5 s, libera a 20 s
    stato.tick(49.0, False)
    assert stato.skipped == 0
    stato.tick(50.0, False)
    assert stato.skipped == 1


def test_live_pause_and_wake_windows():
    stato = SequenceState(SequencePlan(120, 900))
    stato.start(0.0)
    assert stato.tick(0.0, True)
    assert not stato.may_pause_live(1.0)                # scatto in corso
    stato.shot_done(True, 1.0)
    assert stato.may_pause_live(3.0) and not stato.wake_due(3.0)
    assert not stato.may_pause_live(111.0)              # mancano 9 s: non vale la pena
    assert not stato.wake_due(114.0) and stato.wake_due(115.0)
    corto = SequenceState(SequencePlan(5, 900))
    corto.start(0.0)
    corto.tick(0.0, True)
    corto.shot_done(True, 1.0)
    assert not corto.may_pause_live(1.5)                # intervalli brevi: live sempre acceso
    assert not corto.wake_due(1.5) and not corto.wake_due(4.9)   # e nessuna sveglia da mandare
    stato.stop()
    assert not stato.may_pause_live(3.0) and not stato.wake_due(116.0)


def test_progress_lines():
    stato = SequenceState(SequencePlan(5, 1800))
    stato.start(0.0)
    assert progress_text(stato, 0.0) == ("In partenza…", "Mancano 30 min")
    stato.tick(0.0, True)
    assert progress_text(stato, 0.5) == ("Scatto 1 di 360 · in corso", "Mancano 30 min")
    stato.shot_done(True, 1.0)
    assert progress_text(stato, 1.8) == ("Scatto 1 di 360 · prossimo fra 4 s", "Mancano 30 min")
    assert progress_text(stato, 5.0)[0] == "Scatto 1 di 360 · in attesa della fotocamera"
    assert progress_text(stato, 1740.0)[1] == "Mancano 1 min"
    assert progress_text(stato, 1760.0)[1] == "Mancano 40 s"
    stato.tick(5.0, True)
    stato.shot_done(False, 6.0)
    assert progress_text(stato, 6.0)[1] == "Mancano 30 min · 1 saltato"
    stato.index = 400                                   # più scatti del previsto
    assert progress_text(stato, 7.0)[0].startswith("Scatto 400 · ")


# -- file e cartella --------------------------------------------------------------

def _media(nome, kind):
    return MediaFile(f"/DCIM/100OLYMP/{nome}", 10, datetime(2026, 11, 11, 12, 0, 0), kind)


def test_sequence_files_are_the_new_jpegs_in_card_order():
    prima = {"/DCIM/100OLYMP/PB110001.JPG", "/DCIM/100OLYMP/PB110001.ORF"}
    dopo = [_media("PB110001.JPG", MediaKind.JPEG), _media("PB110001.ORF", MediaKind.RAW),
            _media("PB110002.JPG", MediaKind.JPEG), _media("PB110002.ORF", MediaKind.RAW),
            _media("PB110003.MOV", MediaKind.MOVIE), _media("PB110004.JPG", MediaKind.JPEG)]
    assert [m.name for m in sequence_files(prima, dopo)] == ["PB110002.JPG", "PB110004.JPG"]
    assert [m.name for m in sequence_files(prima, reversed(dopo))] == ["PB110002.JPG", "PB110004.JPG"]
    assert sequence_files(prima, dopo[:2]) == []
    solo_raw = [_media("PB110009.ORF", MediaKind.RAW)]
    assert sequence_files(prima, solo_raw) == []


def test_sequence_files_follow_the_shots_across_folders_and_midnight():
    def in_cartella(cartella, nome):
        return MediaFile(f"/DCIM/{cartella}/{nome}", 10, datetime(2026, 12, 31, 23, 59, 0),
                         MediaKind.JPEG)
    # l'elenco arriva dall'ultima cartella alla prima; a mezzanotte il nome cambia
    # prefisso (PC31… → P101…) ma il contatore prosegue; a 9999 cambia la cartella
    elenco = [in_cartella("101OLYMP", "P1010001.JPG"), in_cartella("101OLYMP", "P1010002.JPG"),
              in_cartella("100OLYMP", "PC319997.JPG"), in_cartella("100OLYMP", "P1019999.JPG"),
              in_cartella("100OLYMP", "PC319998.JPG")]
    assert [m.camera_path.split("/", 2)[2] for m in sequence_files(set(), elenco)] == [
        "100OLYMP/PC319997.JPG", "100OLYMP/PC319998.JPG", "100OLYMP/P1019999.JPG",
        "101OLYMP/P1010001.JPG", "101OLYMP/P1010002.JPG"]


def test_folder_name_avoids_existing_folders():
    quando = datetime(2026, 11, 11, 14, 30, 59)
    assert folder_name(quando, lambda n: False) == "Sequenza 2026-11-11 14.30"
    esistenti = {"Sequenza 2026-11-11 14.30", "Sequenza 2026-11-11 14.30 (2)"}
    assert folder_name(quando, esistenti.__contains__) == "Sequenza 2026-11-11 14.30 (3)"
