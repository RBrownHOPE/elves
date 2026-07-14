# Follow mode (default live worker window)

Machine source: `scripts/cobbler_runtime/full_run.py` (`await_full_run`, `format_follow_stream_line`).

## Default

Trusted full-run await follows a sanitized human-readable worker stream by default.

```bash
python3 scripts/cobbler_agents.py implement full-run-await --session-id <id>
# Quiet opt-out:
python3 scripts/cobbler_agents.py implement full-run-await --session-id <id> --quiet
```

(`python3 scripts/...` is source-checkout shorthand; prefer `$ELVES_SKILL_ROOT` when installed.)

## Guarantees

- **No model inference** while following.
- Replaces timed driver chat updates.
- Shared OAuth runs project structural fields only (no free-text summary).
- Raw transcript tail remains restricted; shared OAuth refuses raw transcript.
- Stream lines are operator-readable activity, not hidden reasoning.

## Wakes

Follow mode does not decide completion. The parked sentinel still wakes on death, hangs,
malformed completion, safety tripwires, blockers, material scope/assumption changes, checkpoints,
user input, and worker exit. Previously pushed trusted progress survives recovery from the
verified feature tip.

## Recovery

On wake: verify process fingerprint and protected refs, reconcile report/events, resume from the
verified feature-branch tip when safe. Do not discard pushed commits.
