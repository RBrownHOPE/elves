# Execution Log — parallelves run

Chronological record. Newest entries at the bottom.

---

## 2026-07-19 ~12:30 — Batch 0: Staging

**What happened:**
- Design provenance: Parallelves designed in-session after the v2.10.1 review (issue #86 closed
  by upstream v2.10.2 with 16/20 items fixed). Key design decisions locked with the user:
  Cobbler framing (parallel lanes = Cobbler routing writer agents; coordination pattern shared,
  authority model NOT shared), trunk -> lanes -> integration topology, pairwise
  surface-disjointness as a staging invariant, serial-by-default with a four-gate width test and
  honest `parallel_declined:` provenance, integration entropy review with confidence-ordered
  queue, reclassification/demotion, optional competitive lanes, prewalk-lanes unchanged/gated.
- Created worktree `/Users/ruthbrown-ennis/research/dev/elves-parallelves`, branch
  `feat/parallelves` from `upstream/main` v2.10.4 (`639b0cb`, collision tripwire).
- Wrote plan (`docs/plans/parallelves.md`, B1-B4 + M-A1..M-A3), this log, survival guide,
  `.elves-session.json`, worker packet.

**Preflight notes:**
- Same environment constraints as the audit run, re-verified then: Python 3.9.6 local (floor
  3.10; suite green-with-skips — v2.10.4 baseline 1234/0/0/38 verified on reconciled main),
  miniforge 3.13 available off-PATH for spot corroboration, no `claude` CLI binary (supervised
  native-worker transport unavailable; in-session subagent workers, fallback recorded), `gh`
  authenticated as RBrownHOPE, upstream READ-only.
- Worker model/effort per user direction: `claude-fable-5` at `low`. Batches sized smaller and
  more mechanical accordingly; re-drive budget 2/batch.
- Prewalk: requested `auto`, actual `off` (supervised transport unavailable) — recorded.

**Decisions made:**
- v1 scope guard: contracts + deterministic tooling only; NO runtime orchestrator (documented as
  Phase 3 follow-up). Keeps the PR honest and reviewable.
- Deliverable is a submitted upstream PR (user-merges policy; no landing this run).
- Fork PR opened for the review-loop surface (bot + driver reviews) during the run.
- Lanes grammar is line-based fenced yaml-shaped text parsed WITHOUT yaml (stdlib-only rule).

**Next:** commit staging, push, open fork review PR, staging acceptance validation, tag
`elves/parallelves/pre-batch-1`, launch B1 worker (fable, low).

---

## 2026-07-19 ~13:20 — Batch 1: Normative contract and vocabulary — COMPLETE

**Timing:** worker ~14m (fable/low, 24 tool calls) · driver validate ~4m · review ~7m · fix ~4m.
**Contract:** B1-A1..B1-A3 all met (evidence in `.elves-session.json`).
**Validation:** consistency green after every file (worker ran it 4x; driver + reviewer re-ran); worker full suite 1234/0/0/38; linter tests 83 OK.
**Review:** FINE — 0 blocking, cardinal-rule CLEAN (every orchestration/launch term inside airtight negations, verified by vocabulary grep), 30/30 pins verified verbatim, detection proven by scratchpad mutation. W1 (agentless reclassification actor) fixed in-batch — driver-actored wording; W2 (parser must accept inline `depends_on: []` from the template example) and W3 (corpus-shape test) carried into B2's brief. One driver slip recorded honestly: the first W1 patch double-applied across a line wrap and was rewritten cleanly before commit.
**Worker confidence:** high, unsure_about [] (asserted clean) — consistent with review outcome.
**Commit:** see session json. Next: B2 (fable/low) with W2/W3 carry-notes.

---

## 2026-07-19 ~14:40 — Batch 2: Deterministic lane validator and width test — COMPLETE

**Timing:** worker ~20m + re-drive ~14m (fable/low) · driver validate ~12m · adversarial review ~18m · driver blocker re-reproduction ~5m.
**Contract:** B2-A1..B2-A4 all met (evidence in `.elves-session.json`).
**Validation:** full suite **1284/0/0/38** (worker + driver runs); consistency green; public API additions only; driver re-reproduced all three review blockers against the fixed code — clean envelopes, zero tracebacks.
**Review:** adversarial-parser lens — 3 BLOCKING (NaN defeats the worker-dominance gate producing spurious `parallel: true`; huge-int OverflowError raw traceback; RecursionError on ~1200-lane chains) + 10 WARNING (surface-normalization holes incl. whole-repo/absolute/escaping shapes, case-insensitive FS overlap, fence-decoy section matching, dead RISK_POSTURES, duplicate-key last-wins, plan-file-typo swallowed as no-lanes decline, three untested CLI branches). All 13 fixed via worker re-drive (re-drive 1 of 2 consumed) with 15 regression tests; both B1 carry-notes verified present. fable/low pattern confirmed: implementation competent, adversarial robustness arrives via the review loop — exactly the cadence this run budgeted for.
**Worker confidence:** high / [] on both passes; the review found blockers after the first high/[] — noted for the calibration story (worker confidence is triage, not proof; the lens still runs regardless).
**Commit:** f869876. Next: B3 (docs/parity batch), then dedicated parity lens.

---

## 2026-07-19 ~15:40 — Batch 3: Session, parity, and review-surface integration — COMPLETE

**Timing:** worker ~20m (fable/low, 42 tool calls) · driver validate ~5m · dual lenses (standard + parity, parallel) ~10m.
**Contract:** B3-A1..B3-A3 all met (evidence in `.elves-session.json`).
**Validation:** consistency green after every file and at close; suite 1284/0/0/38 (worker run); linter tests 84 OK; reviewer verified CLI claims live including the ELVES_SKILL_ROOT-unset invocation.
**Standard review:** 0 BLOCKING, cardinal-rule PASS; accuracy vs landed code verified (schema field list = template grammar = parser fields; advisory claim checked against every validator — nothing reads the lanes key); 24/24 pins verbatim with per-file scratchpad detection proof; style fit PASS on all six surfaces. One process disclosure: reviewer used one stash/pop cycle to inspect HEAD pins — driver verified restoration (stash list empty; diff numstat 101/0 unchanged; consistency green).
**DEDICATED CODEX/CLAUDE PARITY LENS (B3-A2 record): verdict PARITY: CLEAN** over the entire cumulative branch diff (2,444 lines). Zero Claude-only or Codex-only assumptions in any added product-doc line; the only invocation is host-neutral python3 with the $ELVES_SKILL_ROOT convention both installs resolve; worker.parallel lives in the shared XDG file both hosts read; the Codex driver's pointer chain (AGENTS.md → SKILL.md → parallelves.md → host-parity.md → per-host grammars) traced end-to-end operable with no dead end. Three symmetric polish items carried to B4 (phase-1 wording provider-axis tension; shared-prefs example missing worker.parallel; docstring nit follows existing pattern — dropped).
**Commit:** 392eb9f. Next: B4 (final batch) with carry-notes, then final readiness review and upstream PR submission.
