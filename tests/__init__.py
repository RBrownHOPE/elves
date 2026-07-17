"""Regression tests for the Elves repo helper scripts."""

import os as _os
import sys as _sys

# Hermeticity guard (docs/plans/hygiene-and-hardening.md B9): the suite must
# never block on stdin, and — the part sys.stdin assignment alone cannot give —
# every child process spawned by a test inherits a CLOSED stdin at the file
# descriptor level. One non-hermetic subprocess froze the readiness gate for
# three hours; this kills the class at the root for every runner.
try:
    _devnull = open(_os.devnull, "r")  # noqa: SIM115 — deliberately process-lifetime
    _os.dup2(_devnull.fileno(), 0)
    _sys.stdin = _devnull
except OSError as _exc:  # pragma: no cover — exotic environments only
    print(f"tests hermeticity guard FAILED to redirect fd 0: {_exc}", file=_sys.stderr)
else:
    _info = _os.fstat(0)
    _dev = _os.stat(_os.devnull)
    if (_info.st_dev, _info.st_ino) != (_dev.st_dev, _dev.st_ino):  # pragma: no cover
        print("tests hermeticity guard did not take effect on fd 0", file=_sys.stderr)

# NOTE: this module only runs when the suite is discovered WITH a top-level
# directory that makes `tests` a package import — always invoke as
# `python3 -m unittest discover -s tests -t .` (see test_suite_hermeticity).
