# Parallel unittest deferral (Elves 2.2 / B3-A4)

**Decision:** defer parallel unittest-module execution.

**Why:** process-sensitive modules (`test_full_run_supervisor`, isolation, dispatch
external boundaries) are not proven safe under concurrent module workers. Shipping
optimistic concurrency without sequential parity evidence is forbidden by the 2.2 plan.

**Measured sequential baseline:** run

```bash
python3 -m unittest discover -s tests -v
```

on an unchanged product/test tree after implementation. Record pass/fail counts and wall
time in the implementer scratch evidence for the run.

**Future enablement requires:**

1. A deterministic two-worker partition of `tests/test_*.py`.
2. Identical pass/fail counts and case IDs versus sequential discovery.
3. Process-sensitive modules remaining serial or proven isolated.
4. Committed parity evidence (this file updated with the measurement table).
