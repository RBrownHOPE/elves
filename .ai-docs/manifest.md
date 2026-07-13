# AI Docs Manifest

This directory holds durable agent-facing docs for the Elves repo itself. These files are the
curated layer above the run-specific `docs/elves/*` memory surfaces.

## When to read this directory

- Read `architecture.md` when you need the repo map or want to understand how the docs system fits
  together.
- Read `conventions.md` before making changes that affect skill behavior, versioning, staging, or
  cross-file wording.
- Read `gotchas.md` when a change looks simple but keeps touching multiple docs or workflows.
- Read `context-index.md` when you need a quick map of the repo surfaces, scripts, tests, and
  common survey paths before editing.

## Relationship to other docs

- `<plan_path>` from `.elves-session.json` (`docs/plans/*`): authoritative scope for the current run
- `<survival_guide_path>` from `.elves-session.json` (`docs/elves/*`): active run brief
- `<execution_log_path>` from `.elves-session.json` (`docs/elves/*`): chronological run record
- `<learnings_path>` from `.elves-session.json` (`docs/elves/*`): durable promotion inbox for
  reusable lessons
- `.ai-docs/*`: curated durable truths worth keeping beyond one run

Promotion flow: `execution log -> learnings -> .ai-docs`

## Files

- `architecture.md`: what this repo is made of and how the doc layers fit together
- `conventions.md`: stable rules for changing the repo without creating drift
- `gotchas.md`: recurring traps and misleading simplifications
- `context-index.md`: quick map for pre-implementation surveys and validation baselines
