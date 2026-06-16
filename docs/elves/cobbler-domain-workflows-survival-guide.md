# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

## Mission

Implement the v1.18.0 Cobbler coherence update for Elves. Make Cobbler the durable default
coordinator for Elves sessions, make math the first Cobbler-managed domain workflow, preserve all
existing base functionality, land the implementation PR, then cut the GitHub version tag after
merge.

## Run Control

- **Run mode:** finite
- **Stop policy:** complete-and-land
- **User intent:** "use cobbler to plan out all these improvements and make them... when fully done, update all docs, land the pr, and bump the version number on github post merge"
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Actual stop conditions:** stop only when implementation PR is merged, release tag is published, and docs/tests are green, or when a genuine blocker has no workaround
- **Workspace ownership:** owned branch + main checkout
- **Branch tip at start (collision tripwire):** `77f08a5770c21ca21a63c0601375d433edfdb3c6`
- **Merge policy:** reviewed-pr-landing-command / explicit user opt-in for this run; use regular merge commits only
- **Final-response policy:** allowed only after implementation and release are complete or a true blocker is reached
- **Coordination mode:** Cobbler-first
- **Batch completion rule:** update execution log, update survival guide, commit, push, then re-read this file
- **Re-read rule:** immediately after every commit and push, re-read this survival guide before doing anything else
- **Continuation rule:** if work remains and actual stop conditions are not met, continue without waiting for user acknowledgment

## Cobbler Session State

- **Cobbler default:** on
- **Activated by:** `$elves` invocation for this run
- **Scope:** current Elves run
- **Behavior:** treat follow-up prompts as Cobbler-mediated by default; answer directly for simple tasks, use bounded independent lenses for non-trivial decisions, and escalate to full Elves run coordination for repo-changing work
- **Persistence:** durable in this survival guide and `.elves-session.json`
- **Exit phrases:** "Cobbler Mode: off", "leave Cobbler Mode", or "stop using Cobbler by default"

## Session Budget

- **Started:** 2026-06-16 16:24 EDT
- **User returns:** active ride-along
- **Checkpoint expectation:** not applicable
- **Time budget:** until release completion
- **Average batch time so far:** not yet measured
- **Batches remaining:** 3 of 3

## Stop Gate

- **Planned batches remaining:** 0
- **Stop allowed right now:** no
- **Why:** implementation is locally validated, but PR landing and release bump remain
- **Next required action:** commit implementation, push, poll PR feedback and checks

## Active Compute

No active paid or long-running compute.

## Current Phase

**Status:** In progress

**Active batch:** PR landing

**What was just finished:** Implementation batches 1-3 were completed together because the docs,
config, and consistency tests were tightly coupled.

**Single next action:** Commit and push the implementation, then run the PR feedback loop.

## Next Exact Batch

PR landing: push the implementation commit, poll GitHub checks and comments, fix blockers, and
merge PR #56 only after final readiness is clean.

Build on:

- Existing Cobbler section in `SKILL.md`, `AGENTS.md`, and `README.md`
- Existing Cobbler workflow docs in `docs/cobbler.md` and `references/council-workflow.md`
- Existing survival guide and execution log templates
- Existing repo consistency phrase-pin style

Acceptance:

- Implementation PR checks are green.
- No blocking review comments remain.
- Operational run artifacts are removed before merge.
- Release bump happens after merge on main.

## Validation Gates

- `./scripts/preflight.sh`
- `python3 scripts/check_repo_consistency.py`
- `python3 scripts/release_checklist.py --allow-unreleased`
- `python3 -m unittest discover`
- `python3 -m py_compile scripts/*.py tests/*.py`
- `bash -n scripts/*.sh`

## PR

- **Branch:** `codex/cobbler-domain-workflows`
- **PR:** #56 https://github.com/aigorahub/elves/pull/56

## Review Lenses

- Architecture lens: Cobbler hierarchy and math-domain framing
- Docs/config lens: surfaces and wording hazards
- Skeptic/release lens: consistency checks, tests, landing risks
