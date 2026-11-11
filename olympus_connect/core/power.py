"""Tiene sveglio il PC mentre una sequenza è in corso.

Un processo `systemd-inhibit` blocca la sospensione finché vive. Parte in un
gruppo di processi suo, così alla fine si chiude lui e il `sleep` che tiene
aperto; e il `sleep` ha una durata finita, così se l'app muore male il blocco
non resta per sempre. Verificato il 2026-11-11 su CachyOS: il blocco compare
in `systemd-inhibit --list` e sparisce alla chiusura, senza processi orfani.
"""
from __future__ import annotations

import os
import signal
import subprocess

MARGIN = 600        # secondi oltre la durata chiesta


class SleepInhibitor:
    def __init__(self, program: str = "systemd-inhibit") -> None:
        self._program = program
        self._process: subprocess.Popen | None = None

    def command(self, seconds: float) -> list[str]:
        return [self._program, "--what=sleep:idle", "--who=Olympus Connect",
                "--why=Sequenza di scatti in corso", "sleep", str(int(seconds) + MARGIN)]

    def start(self, seconds: float) -> bool:
        """Falso se il blocco non parte (comando assente): la sequenza va lo stesso."""
        self.stop()
        try:
            self._process = subprocess.Popen(
                self.command(seconds), start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self._process = None
            return False
        return True

    def active(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        processo, self._process = self._process, None
        if processo is None or processo.poll() is not None:
            return
        try:
            os.killpg(processo.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            processo.wait(2)
        except subprocess.TimeoutExpired:
            processo.kill()

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
