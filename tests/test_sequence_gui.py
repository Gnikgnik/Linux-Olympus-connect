"""I selettori della sequenza, la sezione del pannello e la finestra di fine sequenza."""
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QDialog

from olympus_connect.core.sequence import INTERVALS, SequencePlan
from olympus_connect.gui.sequence import (
    EndChoice, SequenceEndDialog, SequenceSection, SpanStepper,
)


def _rotella(su: bool) -> QWheelEvent:
    return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120 if su else -120),
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


# -- SpanStepper --------------------------------------------------------------------

def test_stepper_shows_the_value_and_steps_through_the_list(qapp):
    s = SpanStepper("Intervallo", INTERVALS, 5)
    visti = []
    s.changed.connect(visti.append)
    assert s.value() == 5 and s.text() == "5 s"
    s.step(1)
    assert s.value() == 8 and s.text() == "8 s"
    s.step(-2)
    assert s.value() == 3
    assert visti == [8, 3]


def test_stepper_stops_at_the_ends_without_signals(qapp):
    s = SpanStepper("Intervallo", INTERVALS, INTERVALS[0])
    visti = []
    s.changed.connect(visti.append)
    s.step(-1)
    assert s.value() == INTERVALS[0] and visti == []
    s.set_value(INTERVALS[-1])
    s.step(1)
    assert s.value() == INTERVALS[-1] and s.text() == "30 min" and visti == []


def test_stepper_snaps_an_unknown_value_and_set_value_is_silent(qapp):
    s = SpanStepper("Intervallo", INTERVALS, 7)
    assert s.value() == 8
    visti = []
    s.changed.connect(visti.append)
    s.set_value(61)
    assert s.value() == 60 and s.text() == "1 min" and visti == []


def test_stepper_wheel_and_list(qapp):
    s = SpanStepper("Intervallo", INTERVALS, 5)
    visti = []
    s.changed.connect(visti.append)
    s.wheelEvent(_rotella(True))
    s.wheelEvent(_rotella(False))
    s.wheelEvent(_rotella(False))
    assert visti == [8, 5, 3]
    assert s.menu_values() == list(INTERVALS)
    s.choose(120)
    s.choose(120)                       # già quello: nessun segnale
    s.choose(7)                         # non in elenco: ignorato
    assert s.value() == 120 and visti == [8, 5, 3, 120]


def test_a_disabled_stepper_does_not_move(qapp):
    s = SpanStepper("Intervallo", INTERVALS, 5)
    visti = []
    s.changed.connect(visti.append)
    s.setEnabled(False)
    s.step(1)
    s.choose(10)
    s.wheelEvent(_rotella(True))
    assert s.value() == 5 and visti == []


# -- SequenceSection ----------------------------------------------------------------

def test_section_starts_with_its_values_and_reports_changes(qapp):
    sezione = SequenceSection(10, 600)
    cambi = []
    sezione.changed.connect(lambda: cambi.append(1))
    assert (sezione.interval(), sezione.duration()) == (10, 600)
    assert sezione.plan() == SequencePlan(10, 600)
    sezione._interval.step(1)
    sezione._duration.step(1)
    assert (sezione.interval(), sezione.duration()) == (15, 900) and len(cambi) == 2
    sezione.set_values(2, 43200)
    assert sezione.plan() == SequencePlan(2, 43200) and len(cambi) == 2     # set_values è muto


def test_section_button_asks_to_start_and_then_to_stop(qapp):
    sezione = SequenceSection(5, 1800)
    eventi = []
    sezione.start_requested.connect(lambda i, d: eventi.append(("avvia", i, d)))
    sezione.stop_requested.connect(lambda: eventi.append(("ferma",)))
    assert sezione._button.text().strip() == "Avvia sequenza"
    sezione._button.click()
    sezione.set_running(True)
    assert sezione.is_running() and sezione._button.text().strip() == "Ferma sequenza"
    sezione._button.click()
    assert eventi == [("avvia", 5, 1800), ("ferma",)]


def test_section_swaps_the_choices_for_the_progress_while_running(qapp):
    sezione = SequenceSection()
    sezione.set_summary("360 scatti · video di 14 s a 25 fps")
    assert sezione.summary_text() == "360 scatti · video di 14 s a 25 fps"
    assert not sezione._interval.isHidden() and not sezione._summary.isHidden()
    assert sezione._line1.isHidden() and sezione._line2.isHidden()
    sezione.set_running(True)
    sezione.set_progress("Scatto 42 di 360 · prossimo fra 3 s", "Mancano 26 min")
    assert sezione.progress_texts() == ("Scatto 42 di 360 · prossimo fra 3 s", "Mancano 26 min")
    assert sezione._interval.isHidden() and sezione._duration.isHidden()
    assert sezione._summary.isHidden()
    assert not sezione._line1.isHidden() and not sezione._line2.isHidden()
    sezione.set_running(False)
    assert not sezione._interval.isHidden() and sezione._line1.isHidden()


def test_section_disabled_blocks_start_but_never_stop(qapp):
    sezione = SequenceSection()
    sezione.set_enabled(False)
    assert not sezione._button.isEnabled() and not sezione._interval.isEnabled()
    sezione.set_running(True)
    assert sezione._button.isEnabled()          # Ferma resta sempre premibile
    sezione.set_running(False)
    assert not sezione._button.isEnabled()
    sezione.set_enabled(True)
    assert sezione._button.isEnabled() and sezione._interval.isEnabled()


# -- finestra di fine sequenza --------------------------------------------------------

def test_dialog_offers_download_and_video(qapp):
    d = SequenceEndDialog(photos=358, skipped=2, resolution="4K", fps=30)
    assert d.message().startswith("Sequenza finita: 358 foto (2 saltate).")
    assert "Scaricarle in una cartella dedicata?" in d.message()
    assert d._video.isEnabled() and d._video.isChecked()
    assert d._resolution.currentText() == "4K" and d._fps.currentData() == 30
    d._download.click()
    assert d.result() == QDialog.DialogCode.Accepted
    assert d.choice() == EndChoice(True, True, "4K", 30)


def test_dialog_not_now_downloads_nothing(qapp):
    d = SequenceEndDialog(photos=10)
    d._later.click()
    assert d.result() == QDialog.DialogCode.Rejected
    assert d.choice().download is False
    chiusa = SequenceEndDialog(photos=10)
    chiusa.reject()                                 # Esc, o la X della finestra
    assert chiusa.choice().download is False
    assert [chiusa._fps.itemText(i) for i in range(chiusa._fps.count())] == ["24", "25", "30"]


def test_dialog_video_choices_follow_the_checkbox(qapp):
    d = SequenceEndDialog(photos=10)
    d._video.setChecked(False)
    assert not d._resolution.isEnabled() and not d._fps.isEnabled()
    d._fps.setCurrentIndex(0)
    d._download.click()
    assert d.choice() == EndChoice(True, False, "Full HD", 24)


def test_dialog_without_ffmpeg_or_with_one_photo_has_no_video(qapp):
    senza = SequenceEndDialog(photos=10, ffmpeg=False)
    assert not senza._video.isEnabled() and not senza._video.isChecked()
    assert "ffmpeg non è installato" in senza._video_note.text()
    una = SequenceEndDialog(photos=1)
    assert not una._video.isEnabled() and "almeno due foto" in una._video_note.text()
    una._download.click()
    assert una.choice() == EndChoice(True, False, "Full HD", 25)


def test_dialog_for_a_raw_only_camera_only_closes(qapp):
    d = SequenceEndDialog(photos=0, only_raw=True)
    assert "La fotocamera ha salvato solo RAW: niente da scaricare qui, niente video." in d.message()
    assert d._download is None and d._later is None
    d._close.click()
    assert d.choice().download is False


def test_dialog_with_no_new_photos_only_closes(qapp):
    d = SequenceEndDialog(photos=0)
    assert "Nessuna foto nuova sulla scheda." in d.message() and d._download is None


def test_dialog_says_when_it_was_aborted_and_when_the_photos_are_since_entry(qapp):
    d = SequenceEndDialog(photos=12, skipped=3, aborted=True, since_remote_entry=True)
    assert d.message().startswith(
        "Sequenza interrotta: la fotocamera non risponde. 12 foto (3 saltate).")
    assert "foto nuove dall'ingresso nel telecomando" in d.message()
