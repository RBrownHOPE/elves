# Schema and acceptance identity

## Stable IDs

- `B0` and `B1` are equally valid batch starts.
- Bare `- [ ] B0-A1: text` and bracketed `- [ ] [B0-A1] text` are equivalent.
- Leading-zero aliases (`B00`) are invalid.
- Master acceptance uses `M-A#`.

## Staging contract

Before worker launch, plan / session / packet acceptance id→criterion mappings must match.
Missing, extra, duplicate, or text-mismatched criteria block launch.

`sync-session` derives both batch rows and top-level `master_acceptance` rows from the
authoritative plan. It accepts the bare and bracketed spellings above, keeps the exact parsed
criterion text, and preserves existing `met`, `evidence`, and other runtime fields. It refuses to
rewrite or remove a row that already carries proof.

## Worker packet at staging

For any run that may be delegated (Run Control `Work driver` ≠ host-native), the standalone
coordinator→implementer packet is a **staging deliverable**: write it during staging and record
its path in Run Control (`Worker packet:`) and in the session as `worker_packet_path`. The plan's
per-batch handoff blocks always live in the plan; the consolidated staging packet carries the
run-level handoff for a separate worker session — the two are not substitutes. Packets are
operational artifacts (typically under ignored `.elves/runtime/`), so the validator checks that
the **path is recorded**, not that the file is tracked. `acceptance_contract.py validate` emits an
advisory `worker_packet_missing` warning — never a blocking issue and never an exit-code change —
when a delegable session lacks the recorded path. Host-native runs legitimately skip the packet.

For exact-session native-worker prewalk the same packet remains a staging deliverable and is sent
exactly once, on the guide turn. The later execution turn receives only `Continue.`. The run-level
session may record requested/actual prewalk mode and the safe exact worker-session identifier, but
private version-3 native-worker state under `.elves/runtime/` is authoritative for phase routes,
packet count/digest, TODO/checkpoint identity, continuity, fidelity, attempts, and failure. Do not
copy phase prompts or source contents into canonical run state. A fresh packet handoff is not
prewalk; see [`prewalk.md`](prewalk.md).

## Work-driver spellings

Canonical work drivers are `host-native`, `grok-build`, `devin-cli`, and `untrusted-writer`
(survival-guide Run Control spelling). Hyphen and underscore forms are equivalent everywhere a
work driver is read: `grok_build` == `grok-build`, `devin_cli` == `devin-cli`,
`untrusted_writer` == `untrusted-writer`, `host_native` == `host-native`, and `n_a`/`n-a` mean
not-applicable. Runtime code that documents the underscore forms (for example
`cobbler_runtime/behavior_policy.py`) refers to the same drivers; validators normalize input by
lowercasing and mapping underscores to hyphens before comparing. This section is the canonical
mapping; other docs link here instead of restating it.

The staging session must also record a non-empty `run_id` and an exact 40-character `start_head`.
`start_head` is the canonical machine-readable collision tripwire. If an older session has only an
exact 40-character `collision_tripwire`, explicit `sync-session --write` may copy that value to
`start_head`. If both fields exist, they must match. New sessions should write only `start_head`.
The final landing check additionally proves that `start_head` is a real ancestor commit and that
the recorded branch matches the active branch.

Helpers (installed skill root):

```bash
python3 "$ELVES_SKILL_ROOT/scripts/acceptance_contract.py" sync-session \
  --repo-root . --session .elves-session.json --write
python3 "$ELVES_SKILL_ROOT/scripts/acceptance_contract.py" validate \
  --repo-root . --session .elves-session.json
python3 "$ELVES_SKILL_ROOT/scripts/elves_landing_check.py" \
  --session <session-path> --repo-root .
```

## Canonical final session schema

This is the minimum acceptance-bearing shape used for strict landing. Other run-control and worker
fields may remain alongside it.

```json
{
  "run_id": "project-task-2026-07-16",
  "branch": "codex/project-task",
  "start_head": "0123456789abcdef0123456789abcdef01234567",
  "plan_path": "docs/plans/project-task.md",
  "batches": [
    {
      "id": "B0",
      "status": "complete",
      "acceptance": [
        {
          "id": "B0-A1",
          "criterion": "Exact criterion text from the plan.",
          "met": true,
          "evidence": "Command, artifact, metric, or commit that proves it."
        }
      ]
    }
  ],
  "master_acceptance": [
    {
      "id": "M-A1",
      "criterion": "Exact master criterion text from the plan.",
      "met": true,
      "evidence": "Cumulative review or end-to-end proof."
    }
  ]
}
```

The plan is authoritative. Session criteria match it verbatim by stable ID. `sync-session` should
create the rows before launch so the driver adds evidence later instead of hand-copying criteria.

## Canonical landing and cleanup order

The strict landing check intentionally reads committed evidence. A session that is ignored by the
target repository is not exempt.

1. Complete the plan checkboxes and session evidence, then run `validate`. The rows should already
   come from the staging-time `sync-session --write`; if they don't, synchronize before attaching
   evidence.
2. Commit the plan, `.elves-session.json`, and current run documents together. If the repository
   ignores the session, use `git add -f .elves-session.json`. The committed evidence tip is
   intentional run history.
3. Run `elves_landing_check.py --session .elves-session.json --repo-root .` at that committed tip,
   followed by the one terminal readiness review and its selected proof.
4. Only after acceptance and readiness pass, remove the operational session, survival guide, and
   execution log from the PR in one cleanup commit. Use `git rm` to delete an ordinary tracked
   session, or `git rm --cached .elves-session.json` when retaining an ignored local recovery copy.
5. Run the post-cleanup tip attestation. Reuse the acceptance and product proof from the preceding
   commit unless cleanup touched a relevant product or proof input.

Do not rerun the strict landing check after its required session has been removed. The cleanup
commit follows the proven evidence tip; it does not bypass or replace that proof.

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
