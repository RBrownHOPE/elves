# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

## Mission

Integrate the open-source Grok Build CLI as an optional autonomous Elves worker. The run must use
installed-binary capability evidence, preserve native-first routing and credential isolation, use
headless goal mode plus sanitized streaming when proven, and retain the provider-neutral one-packet
fallback.

## Run control

- **Run mode:** finite single-kickoff run
- **User intent:** plan and stage the open-source Grok Build integration, then continue automatically
  through the internally managed Prewalk, worker execution, and final reviewed PR
- **Workspace:** branch `codex/grok-build-open-source` in
  `/Users/john/aigora/dev/elves-grok-build-open-source`
- **Base and collision tripwire:** `origin/main` at
  `4bbb7b3b6c4f5d57bfa0cc4bc8b0014c39559080`
- **Canonical plan:** `docs/plans/grok-build-open-source-realignment.md`
- **Canonical session:** `.elves-session.json`
- **Worker packet:** `.elves/runtime/packets/grok-build-open-source.md`
- **E2E mode:** chat-to-work
- **Worker route:** one exact `gpt-5.6-sol` Codex session; High Prewalk through the first meaningful
  edit, then exact-session resume at Medium for execution
- **Delegation:** full run, trusted, branch-progress commits
- **Driver mode:** parked monitor during implementation; one cumulative terminal review
- **Progress visibility:** exact private native-worker follow view plus concrete worker commits; no
  timed driver narration
- **High-risk wake conditions:** protected-ref or authority violation, repeated crash/stall,
  worktree collision, auth-isolation regression, or material scope departure
- **Landing outcome:** reviewed PR
- **Draft PR:** `https://github.com/aigorahub/elves/pull/77`
- **Merge/release authority:** none; do not merge, tag, or publish
- **Re-drive budget:** one targeted worker re-drive before driver-owned repair
- **Staging acceptance command:** `python3 scripts/acceptance_contract.py validate --repo-root . --session .elves-session.json`
- **Terminal landing command:** `python3 scripts/elves_landing_check.py --session .elves-session.json --repo-root .`
- **Re-read rule:** immediately after every host-owned commit and push, re-read this file before the
  next action; re-read again after worker terminal wake before review

## Stop Gate

- **Planned batches remaining:** terminal review and readiness
- **Stop allowed right now:** no
- **Why:** the user resumed the full goal through reviewed-PR readiness
- **Next required action:** perform the cumulative review of `git diff origin/main...HEAD` and all
  PR feedback, then address only genuine blockers

## Effort standard

Work hard for the full authorized run, but do not substitute repeated process checks for progress.
The careful plan and complete packet let the worker execute. Batch gates verify claimed work and
focused tests. The driver reviews the cumulative diff once at the end and revisits only changed risk.

## Current phase

- **Status:** terminal review
- **Active compute:** none
- **Active batch:** final readiness
- **What was just finished:** the three stale Grok fixture session IDs now use canonical UUIDs;
  their targeted tests pass and the complete supervisor module passes 142 tests with 1 skip
- **Single next action:** cumulative independent review of the full branch diff and PR feedback

## Worker authority

The worker may edit owned feature-branch surfaces, run focused tests, commit concrete slices, and
push this branch. It may not edit canonical session/run-memory files, operate on protected refs,
open or merge PRs, move tags, publish releases, or weaken auth/worktree/permission boundaries.

The worker should execute all four batches without per-batch review. It should stop only for a real
authority or safety blocker, repeated unrecoverable failure, or material plan conflict. A missing
Elves report is not a blocker; the driver can reconstruct it from commits and evidence.

## Review policy

Final review checks plan completeness and the full `git diff origin/main...HEAD`, with extra
attention to auth isolation, goal fallback, streaming redaction and terminal detection, model
catalog honesty, protected-ref authority, and unchanged native Codex/Claude behavior. Revisions get
targeted checks for affected surfaces, not a fresh full review loop unless the risk actually changed.

## Recovery order

After compaction or restart, read this Stop Gate and Run Control first, then `.elves-session.json`,
`docs/elves/learnings.md`, the plan, the execution log, `.ai-docs/manifest.md` if present, and the
constitution. Honor `continuation_guard.stop_allowed` and take the single next required action.

## Launch readiness

- [x] Dedicated branch and checkout created from the recorded start head.
- [x] Ground-truth CLI/source research corrected the inherited planning note.
- [x] Plan, session, and packet acceptance mappings validated.
- [x] Staging commit pushed and draft PR recorded.
- [x] Exact worker session launched and registered.
