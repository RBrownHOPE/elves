# Plan: Productize Lane A (smart plan → Grok implement)

## Mission

Finish PR #64 so the **fast implementer lane** is a first-class, documented, test-backed Elves
surface: host stages packets and gates; one persistent Grok Build session implements whole batches
with frequent branch commits. Land with a regular merge commit when green. Do **not** re-open the
untrusted writer lease work unless a regression appears.

## Why

Dogfood proved:

- Nested Codex→Grok headless ceremony is too slow.
- Whole-batch Grok with `--prompt-file --yolo --effort medium` completed real multi-file work in ~3 minutes.
- Grok will commit multiple times inside a batch when the packet requires Elves subject schema.

PR #64 already has CLI + references + tests. Remaining work is skill/human-doc alignment, durable
design docs on the product branch, CHANGELOG, and readiness/land.

## Implementation lane (this run)

```yaml
implementation_lane: fast
git_mode: branch_progress
implementer: persistent-grok-build
model: grok-4.5
launch_recipe: --prompt-file <packet> --yolo --effort medium --max-turns 80 --output-format json
session: one UUID for all implement batches; resume between batches
host_owns: Survival Guide, execution log, .elves-session.json, PR, merge, acceptance Close
grok_owns: product code/docs/tests assigned in packets; frequent feature-branch commits+push
```

## In scope

1. Canonical skill surfaces (`SKILL.md`, `AGENTS.md`) describe Lane A / `implement` CLI and point at
   `references/grok-implementer-launch-prompt.md` without inventing Codex slash commands.
2. README / CHANGELOG reflect the feature (no version bump unless release batch is green and host
   decides 1.20.2; default: Unreleased section only until land decision).
3. Design plans present on the product branch:
   - `docs/plans/smart-plan-grok-implement.md` (already)
   - `docs/plans/adaptive-planner-directed-review.md` (from PR #62, docs-only)
4. Consistency pins if new phrases require them.
5. Final readiness + merge of PR #64 (merge-commit-on-green authorized for this run).

## Out of scope

- Implementing adaptive review **runtime** (design doc only this PR).
- Untrusted writer lease redesign.
- Adaptive-planner feature code.
- Changing public default away from native-only zero-config.

## Batches

### Batch 0 — Staging (host)

- Survival Guide, execution log, session JSON, plan, PR #64 already open.
- Record dogfood Batch 1 as complete with acceptance evidence.
- Collision tripwire, test baseline.

### Batch 1 — Implement CLI + references (DONE in dogfood)

Already on branch at tip `f7c6c60`:

- `scripts/cobbler_runtime/implement.py`, CLI wiring, tests
- `references/grok-implementer-launch-prompt.md`, done schema
- Measured launch argv (`--yolo --effort medium`)

**Acceptance:** already met; host records evidence from dogfood + gate JSON.

### Batch 2 — Skill and human-doc alignment (Grok)

- Minimal surgical edits to `SKILL.md` and `AGENTS.md` Cobbler/external-agent sections:
  - `implementation_lane: fast | untrusted`
  - `python3 scripts/cobbler_agents.py implement …`
  - link to Grok implementer launch prompt
  - host-honest Codex vs Claude wording
- `CHANGELOG.md` Unreleased notes for Lane A
- Optional one README pointer under Cobbler/setup if a natural home exists (do not rewrite release blurb)
- Run consistency checker; update phrase pins if required
- Full suite green; commit with Elves subjects; push

### Batch 3 — Design-doc portfolio on product branch (Grok)

- Add `docs/plans/adaptive-planner-directed-review.md` (content from PR #62)
- Cross-link from smart-plan / learnings if missing
- No adaptive-review runtime code
- Full suite green; push

### Batch 4 — Final readiness and land (host)

- Host gate: full suite, consistency, landing check, PR comments/checks
- Optional native cumulative review of `main...HEAD`
- Undraft if needed, `gh pr merge --merge`
- Close related doc-only PRs #62/#63 if fully subsumed (or leave with comment)
- No version tag unless host explicitly adds 1.20.2 release batch (not default)

## Non-negotiables

- Never merge with squash.
- Never default `dontAsk` or `--no-subagents` for implementer launches.
- Native-only remains complete without setup.
- Grok does not edit Survival Guide stop policy / merge auth / session JSON.
- Secrets never enter git.

## Success

PR #64 merged to main with Lane A CLI + docs; operator can:

```bash
python3 scripts/cobbler_agents.py implement prepare …
python3 scripts/cobbler_agents.py implement launch …   # prints measured grok argv
# Grok runs whole batch, commits often
python3 scripts/cobbler_agents.py implement gate --batch N
```
