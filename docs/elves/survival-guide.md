# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

> Survival Guide for the parallelves run. After any compaction, read this before touching code.
> Read order: survival guide -> `.elves-session.json` -> learnings -> plan -> execution log.

---

## Mission

Ship Parallelves v1 (contracts + deterministic width-test tooling, serial-by-default,
recommend-only) per `docs/plans/parallelves.md`, with fable/low workers implementing, per-batch
independent review, codex/claude parity verified, all docs + changelog + guide updated, and a
final upstream PR submitted against `aigorahub/elves` `main`. No runtime orchestrator, no
authority changes, no prewalk activation claims.

---

## Run Control

- **Run mode:** finite
- **Stop policy:** blocker-only
- **User intent:** "plan out parallelves and stage all the work, includeing prewalk, then hand
  off to fable low ot the do work. when done review all, ensure codex/claude parity, update all
  docs and change log and guide, review and submit the pr"
- **Checkpoint due by:** none · **Checkpoint semantics:** none · **May continue after:** yes
- **Actual stop conditions:** all four batches complete, final readiness review clean, upstream
  PR submitted — or a genuine blocker.
- **Workspace ownership:** dedicated worktree `/Users/ruthbrown-ennis/research/dev/elves-parallelves`
  (`git worktree add -b feat/parallelves ... upstream/main`)
- **Branch tip at start (collision tripwire):** `639b0cb0646dde7055c16c15132dec90b565a87c`
- **Merge policy:** user-merges (default). The driver NEVER merges this run; the deliverable is
  a submitted upstream PR on `aigorahub/elves` (user has READ there; maintainers own the merge).
  A fork PR (RBrownHOPE/elves) exists only as the review-loop surface during the run.
- **Final-response policy:** disallowed until stop conditions met
- **Coordination mode:** Cobbler-first
- **Batch completion rule:** host-native; every completed batch ends with
  `update execution log -> update survival guide -> commit -> push`.
- **Progress visibility rule:** subjects use
  `[feat/parallelves · Batch N/4 · Contract|Implement|Validate|Review|Close] <concrete outcome>`;
  Close commits carry the Confidence trailer per the v2.10.x SKILL contract.
- **Coordinator-to-implementer handoff:** consolidated packet at
  `.elves/runtime/packets/parallelves.md` + per-batch scope in each launch brief.
- **Worker packet:** `.elves/runtime/packets/parallelves.md`
- **Handoff validation:** v2.8 advisory path
- **Re-read rule:** after every host commit+push, re-read this guide.

- **E2E mode:** chat-to-work (stops at a submitted upstream PR)
- **Work driver:** host-native (in-session native subagent workers pinned `claude-fable-5`,
  effort `low` per user direction; the standalone `claude` CLI binary remains uninstalled on
  this machine, so the supervised `native-worker` transport is unavailable — honest fallback
  recorded, same as the audit run)
- **Delegation scope:** batch · **Git mode:** host_only (driver owns all git)
- **Driver review policy:** per-batch independent lens + dedicated parity lens (B3) + final
  cumulative honesty/parity review
- **Risk posture:** standard · **Trust mode:** trusted
- **Landing outcome:** landable_pr (upstream submission; no landing this run)
- **Driver merge authorized:** no
- **Worker merge authority:** false
- **Stable plan IDs:** batches `B1`-`B4` (headings `Batch 1..4`); acceptance `B#-A#`; Master `M-A1`-`M-A3`
- **Staging acceptance command:** `python3 scripts/acceptance_contract.py validate --repo-root .
  --session .elves-session.json`
- **High-risk checkpoints:** none staged
- **GitHub push auth route:** host `gh` (RBrownHOPE)
- **Re-drive budget:** 2 substantive worker re-drives per batch (fable/low may need them)
- **Prewalk for this run's workers:** requested `auto`, actual `off`
  (`prewalk_capability_unavailable:supervised_cli_transport_unavailable`) — recorded, honest,
  same constraint as the audit run. The PLAN's content documents prewalk lanes (gated) — that is
  repo content, not this run's transport.
- **Continuation rule:** if work remains and stop conditions are unmet, continue without waiting.

---

## Cobbler Session State

- **Cobbler default:** on · **Activated by:** Elves invocation · **Scope:** current run
- **Persistence:** survival guide and `.elves-session.json`

---

## Session Budget

- **Started:** 2026-07-19 ~12:30 local
- **User returns:** unspecified (assume hours)
- **Batches remaining:** 4 of 4

---

## Stop Gate

- **Planned batches remaining:** 2
- **Stop allowed right now:** no
- **Why:** B1-B2 complete; B3-B4 remain, then final review and upstream submission.
- **Next required action:** launch B3 worker (fable, low), then the dedicated parity lens.

---

## Non-Negotiables

- Serial default / recommend-only `auto` / no runtime-orchestration claims / no authority
  changes / prewalk gating untouched — in every sentence this run writes.
- Never weaken, skip, or delete a test for green. Deterministic model-free tooling only.
- Consistency pins land in the same batch as every edited normative sentence.
- Public API snapshot: additions only.
- **Never merge.** Deliverable is a submitted upstream PR; maintainers merge.
- **Never run destructive git commands.** One run owns one branch and one checkout; unexpected
  tip movement is a collision — stop.

---

## Current Phase

**Status:** In progress

**Active batch:** B3: Session, parity, and review-surface integration

**Single next action:** Launch B3 worker (fable, low).

---

## Active Compute

**No active paid or long-running compute.** Worker subagents run per batch.

---

## Next Exact Batch

**Batch:** B3: Session, parity, and review-surface integration

**Scope:** schema-and-acceptance.md advisory `lanes` session key; host-parity.md Parallelves parity section; review-subagent.md integration entropy review protocol (three cross-lane question classes + confidence-ordered queue); SKILL.md compact subsection; AGENTS.md pointer; README reference-index row; pins same-batch.

**Acceptance criteria:** B3-A1..B3-A3 (see plan). Dedicated codex/claude parity lens runs after the standard review.

**Risk:** pin volume; parity phrasing.

**Rollback authority:** tag `elves/parallelves/pre-batch-3`.

## Tool Configuration

```yaml
lint: python3 -m compileall -q scripts
test: python3 -m unittest discover -s tests -t .
smoke: python3 scripts/check_repo_consistency.py
review: github-pr-comments
notification: pr-comment
```

Model routing (user-directed): implement -> native subagent pinned `claude-fable-5` effort
`low`; validate -> host; review -> independent native subagent lenses (session default effort);
parity -> dedicated lens at B3 and final. Requested transport supervised `native-worker` CLI;
actual in-session subagents (no `claude` binary on PATH); fallback recorded.

---

## Plan and Log Paths

- **Plan:** `docs/plans/parallelves.md`
- **Learnings:** `docs/elves/learnings.md` (durable cross-run file; append only)
- **Execution log:** `docs/elves/execution-log.md`
- **Branch:** `feat/parallelves`
- **PR number:** #2 (RBrownHOPE/elves, review surface)
- **Plan hash at session start:** `36753dfc3b7cd2453246f08119576ef0`

---

# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART
