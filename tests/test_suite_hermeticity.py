"""Prove the suite's stdin hermeticity guard (plan B9).

The guard in ``tests/__init__.py`` redirects file descriptor 0 to ``os.devnull``
at discovery. Two properties matter and both are pinned here:

1. In-process reads of stdin return EOF immediately.
2. A child process spawned WITHOUT an explicit ``stdin=`` argument inherits the
   closed descriptor and also reads EOF immediately — this is the property that
   prevents a forgotten ``stdin=subprocess.DEVNULL`` in any of the suite's
   subprocess call sites from ever hanging a gate again.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest


class SuiteHermeticityTests(unittest.TestCase):
    def test_guard_took_effect_on_fd0(self) -> None:
        # The guard must have replaced fd 0 with devnull. This asserts the
        # redirect itself, so a runner where the guard never loaded fails
        # loudly here instead of blocking on a read below.
        info = os.fstat(0)
        dev = os.stat(os.devnull)
        self.assertEqual(
            (info.st_dev, info.st_ino),
            (dev.st_dev, dev.st_ino),
            "fd 0 is not devnull — the tests/__init__ hermeticity guard did not "
            "run. Invoke the suite as `python3 -m unittest discover -s tests -t .` "
            "so discovery imports the tests package.",
        )

    def test_in_process_stdin_is_eof(self) -> None:
        # Hang-proof probe: never block even if the guard failed — a blocking
        # fd 0 surfaces as BlockingIOError/empty instead of a frozen suite.
        os.set_blocking(0, False)
        try:
            try:
                data = os.read(0, 1)
            except BlockingIOError:
                self.fail("fd 0 would block — hermeticity guard ineffective")
        finally:
            os.set_blocking(0, True)
        self.assertEqual(data, b"")
        self.assertEqual(sys.stdin.read(), "")

    def test_child_without_stdin_kwarg_inherits_eof(self) -> None:
        proc = subprocess.run(  # deliberately no stdin= — inheritance is the point
            [
                sys.executable,
                "-c",
                "import sys; print(len(sys.stdin.read()))",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "0")


if __name__ == "__main__":
    unittest.main()
