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
except OSError:
    # Exotic environments without dup2/fd0 semantics: in-process reads are
    # still redirected when possible; children fall back to runner behavior.
    pass
