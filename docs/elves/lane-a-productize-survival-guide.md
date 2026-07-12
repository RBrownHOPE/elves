# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

This Survival Guide is the live brief for productizing Lane A on PR #64.

## Mission

Ship the fast smart-plan → Grok-implement path as a first-class Elves surface on PR #64: skill/doc
alignment, design plans on the product branch, readiness, and regular merge commit when green.

## Run Control

- **Run mode:** finite
- **Stop policy:** plan-complete-or-true-blocker
- **User intent:** “Plan and stage an Elves run, then do it with the Lane A approach (smart plan → Grok implement) to complete the next PR.”
- **Checkpoint semantics:** delivery target only (not a hard stop)
- **May continue after checkpoint:** yes
- **Actual stop conditions:** Batches 2–3 implemented and gated; Batch 4 final readiness clean; PR #64 merged with `gh pr merge --merge` (or true blocker)
- **Workspace ownership:** branch `feat/lane-a-implement-cli` in worktree `/Users/john/aigora/dev/elves-lane-a-grok` only
- **Branch tip at start (collision tripwire):** `f7c6c606005364597bf6d465ee8076cad18f1bbb`
- **Merge policy:** merge-commit-on-green after Final Readiness; never squash
- **Final-response policy:** disallowed until Stop Gate allows
- **Coordination mode:** Cobbler-first
- **Implementation lane:** `fast` (Grok Build persistent session, branch_progress)
- **Launch recipe:** `grok --prompt-file <packet> --cwd <worktree> --model grok-4.5 --yolo --effort medium --max-turns 80 --output-format json` (+ `--resume <id>` after first session)
- **Host owns:** Survival Guide, execution log, `.elves-session.json`, PR, gates, merge
- **Grok owns:** batch product edits + frequent feature-branch commits/push per packet
- **Batch completion rule:** update log → update this guide → commit/push host Close when needed
- **Re-read rule:** after every host push, re-read this file
- **Between-batch review:** fast native host gate only (tests + consistency); no multi-model council

## Cobbler Session State

- **Cobbler default:** on
- **Activated by:** Elves staging for Lane A productize
- **Scope:** current Elves run
- **Exit phrases:** “Cobbler Mode: off”, “leave Cobbler Mode”, “stop using Cobbler by default”

## Session Budget

- **Staging started:** 2026-07-12 ~16:00 EDT
- **Batches remaining after staging:** 3 (2 implement via Grok, 1 host land)
- **User returns:** unspecified; continue until stop conditions

## Stop Gate

- **Planned batches remaining:** 0
- **Stop allowed right now:** yes
- **Why:** implement batches complete; land in progress
- **Next required action:** Final readiness and merge PR #64

## Current Phase

**Status:** staged / launch-ready for Lane A execution.

**Single next action:** run `implement launch` (or equivalent grok argv) with Batch 2 packet; do not
micro-drive Grok; when Grok finishes, host `implement gate` and continue.

## Active Compute

- Grok session: create on first Batch 2 launch; record sessionId from JSON output
- No other paid jobs expected

## Next Exact Batch

**Batch 2 — Skill and human-doc alignment (Grok)**

See plan `docs/plans/lane-a-productize.md` and packet `.elves/runtime/packets/batch-2.md`.

## Memory Surfaces

- **Plan:** `docs/plans/lane-a-productize.md`
- **Survival Guide:** this file
- **Learnings:** `docs/elves/learnings.md`
- **Execution log:** `docs/elves/lane-a-productize-execution-log.md`
- **Session:** `.elves-session.json`
- **PR:** https://github.com/aigorahub/elves/pull/64

## Non-Negotiables

- No squash merge; no force-push; no shared worktree
- No secrets in git
- No inventing Codex slash commands
- No adaptive-review runtime in this PR
- Native-only remains complete without setup
- Grok must not edit this Survival Guide, execution log, or `.elves-session.json`

## Validation Gates

```bash
python3 -m unittest discover -s tests
python3 scripts/check_repo_consistency.py
python3 -m compileall -q scripts tests
git diff --check
python3 scripts/cobbler_agents.py implement --help
```

## Reactivation / Launch Prompt

> Resume PR #64 from this Survival Guide. Lane A: host writes packets and gates; Grok implements
> whole batches with frequent commits using `--prompt-file --yolo --effort medium`. Stop Gate
> closed until merge. Next: Batch 2 packet → Grok → gate → Batch 3 → gate → Batch 4 land.
