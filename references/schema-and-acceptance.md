# Schema and acceptance identity

## Stable IDs

- `B0` and `B1` are equally valid batch starts.
- Bare `- [ ] B0-A1: text` and bracketed `- [ ] [B0-A1] text` are equivalent.
- Leading-zero aliases (`B00`) are invalid.
- Master acceptance uses `M-A#`.

## Staging contract

Before worker launch, plan / session / packet acceptance id→criterion mappings must match.
Missing, extra, duplicate, or text-mismatched criteria block launch.

Helpers (installed skill root):

```bash
python3 "$ELVES_SKILL_ROOT/scripts/acceptance_contract.py" validate \
  --repo-root . --session .elves-session.json
python3 "$ELVES_SKILL_ROOT/scripts/elves_landing_check.py" \
  --session <session-path> --repo-root .
```

## Full-run event/report v1

Events JSONL: append-only. Fields: `timestamp`, `session_id`, `branch`, `head`, `batch`, `type`,
`summary` (≤500, redacted). Types: `run_started`, `heartbeat`, `batch_started`, `commit_pushed`,
`gate_result`, `batch_complete`, `high_risk_checkpoint`, `blocked`, `run_complete`.

Complete report: `status: complete`, full batch and acceptance rows with evidence, ordered commit
chain, empty `blockers` / `remaining_risks`. Worker `merge_authority` is always false.

## Session recovery order

1. Survival guide (Stop Gate first)
2. `.elves-session.json`
3. Learnings
4. Plan
5. Execution log
6. `.ai-docs/manifest.md` if present
7. Constitution if present
