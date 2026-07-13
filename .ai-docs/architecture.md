# Architecture

## Repo shape

This repo is a portable skill package, not an application. Its primary surfaces are documentation,
templates, and small support scripts:

- `SKILL.md`: canonical Claude-style skill surface
- `AGENTS.md`: canonical Codex-style skill surface
- `references/*`: reusable templates and supporting guidance
- `README.md`, `CHANGELOG.md`, `TODO.md`: human-facing project docs
- `scripts/*`: supporting operator utilities, such as preflight
- `docs/plans/*` and `docs/elves/*`: run-specific working memory during an active Elves session

## Coordination hierarchy

Elves has one coordination hierarchy:

1. Elves executes plans through branches, PRs, validation, review, memory, and landing.
2. Cobbler coordinates intent, routing, context, evidence, dissent, medium, and fitted answer.
3. Domain workflows specialize Cobbler for a work type.
4. Providers are optional role routes, not orchestration layers.

Math is the first domain workflow. Its ledgers under `docs/math/*` are domain evidence artifacts
managed inside the Elves run, not a separate Council or Cobbler memory system.

## External-agent runtime (stdlib)

`scripts/cobbler_runtime/` is the standard-library external-agent foundation:

- `schema.py` / `config.py` / `capabilities.py`: provider-neutral contracts and route resolution
- `context.py`: redacted context packets and minimal child environments
- `adapters.py`: read-only argv command builders, structured role-report parsing, exact session
  create/resume builders (no bare `--resume` / `--continue` / `--last`)
- `dispatch.py`: parallel read-only council fan-out, quorum policy, lightweight-review utility
- `sessions.py`: exact session registry, lifecycle transitions, context digests, usage ledger,
  Grok parent→child lineage helpers
- `leases.py`: exclusive one-writer lease, worker preflight, write packets, refresh-after-import
- `audit.py`: pre/post refs/path/process audit, binary format-patch export, host `git apply --check`

The thin CLI is `scripts/cobbler_agents.py` (`validate-config`, `doctor`, `council`,
`lightweight-review`, `session …`, `worker prepare|audit|export|refresh`). Private runtime state
belongs under ignored `.elves/runtime/` (`council/`, `sessions/`, `leases/`). Worker detached
commits are untrusted handoff boundaries; the host alone creates branch commits, pushes, PRs, and
run-memory updates after binary patch audit/import.

The survival guide remains the home for live run control, checkpoint semantics, active compute, next exact batch, and operator constraints; the Cobbler session state extends that live layer.

## Memory layers

Elves now uses distinct layers instead of one giant note pile:

1. `plan`: authoritative scope and batch structure for the current run
2. `survival guide`: live run control, Cobbler session state, checkpoint semantics, active compute,
   next exact batch, and operator constraints
3. `learnings`: durable reusable lessons that should survive this run
4. `execution log`: chronological proof of what happened
5. `.ai-docs/*`: curated durable truths about this repo

The point of the layering is to keep raw chronology, reusable lessons, and stable repo knowledge in
different places so later agents do not need to infer intent from noisy notes.

## Documentation system

This repo now treats documentation as a maintained surface:

- Raw observations belong in the execution log.
- Live operator state belongs in the survival guide and should be rewritten in place.
- Reusable lessons belong in the learnings file.
- Stable architecture, conventions, and traps belong in `.ai-docs/*`.
- Human-facing explanations and release notes belong in `README.md`, `CHANGELOG.md`, and `TODO.md`.

Because this repo *is* a skill, changes almost always cross multiple surfaces. Updating one file in
isolation is usually not sufficient.


## v2.1.0 full-run note

Trusted Grok full-run uses one session, feature-branch progress, parked-monitor driver, and bounded events. Untrusted detached leases remain a distinct authority model.
