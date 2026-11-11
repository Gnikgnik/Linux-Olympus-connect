"""Il blocco della sospensione del PC durante una sequenza."""
import os
import time

from olympus_connect.core.power import MARGIN, SleepInhibitor


def test_command_line():
    comando = SleepInhibitor().command(1800)
    assert comando[0] == "systemd-inhibit" and "--what=sleep:idle" in comando
    assert comando[-2:] == ["sleep", str(1800 + MARGIN)]      # durata finita, mai infinito


def test_a_missing_program_does_not_stop_the_sequence():
    blocco = SleepInhibitor("comando-che-non-esiste-xyz")
    assert blocco.start(60) is False
    assert not blocco.active()
    blocco.stop()                                               # non solleva


def _finto(tmp_path):
    """Un finto systemd-inhibit: scrive il suo pid e resta vivo."""
    script = tmp_path / "finto-inhibit"
    script.write_text(f"#!/bin/sh\necho $$ > '{tmp_path / 'pid'}'\nexec sleep 60\n")
    script.chmod(0o755)
    return str(script)


def _vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_start_and_stop_kill_the_whole_process_group(tmp_path):
    blocco = SleepInhibitor(_finto(tmp_path))
    assert blocco.start(60) is True
    file_pid, fine = tmp_path / "pid", time.monotonic() + 5
    while not (file_pid.exists() and file_pid.read_text().strip()) and time.monotonic() < fine:
        time.sleep(0.02)
    pid = int(file_pid.read_text())
    assert blocco.active() and _vivo(pid)
    assert os.getpgid(pid) == pid != os.getpgid(0)              # gruppo suo, non il nostro
    blocco.stop()
    assert not blocco.active() and not _vivo(pid)
    blocco.stop()                                               # due volte: innocuo


def test_stop_after_the_process_has_already_ended(tmp_path):
    script = tmp_path / "subito"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    blocco = SleepInhibitor(str(script))
    assert blocco.start(60)
    blocco._process.wait(5)
    assert not blocco.active()
    blocco.stop()                                               # non solleva, niente da fermare


def test_a_process_that_ignores_the_request_is_killed_with_its_group(tmp_path, monkeypatch):
    script = tmp_path / "sordo"
    script.write_text(f"#!/bin/sh\ntrap '' TERM\necho $$ > '{tmp_path / 'pid'}'\n"
                      "while true; do sleep 1; done\n")
    script.chmod(0o755)
    blocco = SleepInhibitor(str(script))
    assert blocco.start(60)
    file_pid, fine = tmp_path / "pid", time.monotonic() + 5
    while not (file_pid.exists() and file_pid.read_text().strip()) and time.monotonic() < fine:
        time.sleep(0.02)
    pid = int(file_pid.read_text())
    vera = blocco._process.wait
    monkeypatch.setattr(blocco._process, "wait",
                        lambda timeout=None: vera(0.3 if timeout else None))
    blocco.stop()
    assert not _vivo(pid)


def test_starting_again_replaces_the_previous_block(tmp_path):
    blocco = SleepInhibitor(_finto(tmp_path))
    assert blocco.start(60)
    primo = blocco._process
    assert blocco.start(60)
    assert primo.poll() is not None and blocco.active()
    blocco.stop()
