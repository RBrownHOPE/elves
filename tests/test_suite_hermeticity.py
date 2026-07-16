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
    def test_in_process_stdin_is_eof(self) -> None:
        # fd 0 reads EOF instantly instead of blocking.
        self.assertEqual(os.read(0, 1), b"")
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
