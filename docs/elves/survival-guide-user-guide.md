# Survival guide: Elves user guide

## Mission

Build, review, publish, and release the task-first Elves guide described in
`docs/plans/v2.5.0-user-guide.md`.

## Run control

- **Run mode:** finite
- **E2E mode:** chat-to-land
- **Work driver:** host-native
- **Delegation scope:** none
- **Driver monitor mode:** n_a
- **Driver review policy:** one terminal review after Gemini clarity feedback is applied
- **Risk posture:** standard
- **Trust mode:** trusted
- **Landing outcome:** complete_and_merge
- **Driver merge authorized:** yes, from the user's explicit instruction in this task
- **Worker merge authority:** false
- **Staging acceptance validation:** pending until the session rows are generated

## Stop gate

- **Planned batches remaining:** 1
- **Stop allowed right now:** no
- **Why:** the guide, review, landing, release, live Pages check, and local install refresh remain.
- **Next required action:** create the guide and Pages workflow, then align the public docs.

## Active batch

- **Batch:** B0, one of one
- **Plan:** `docs/plans/v2.5.0-user-guide.md`
- **Execution log:** `docs/elves/execution-log-user-guide.md`
- **Learnings:** `docs/elves/learnings.md`
- **Branch:** `codex/html-guide-pages`
- **Base:** `origin/main`

## Safety

- Never use destructive git commands, rebase a shared branch, or force push.
- The host owns canonical memory, PR operations, protected refs, release operations, and merge.
- Re-read this file after each host-owned commit and push.
- Do not stop at a green check while the stop gate says no.

