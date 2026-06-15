# Context Index

Use this index as the first map when surveying the Elves repo. It points to the durable surfaces,
the runtime surfaces, and the checks that usually matter before editing.

## Primary Surfaces

- `SKILL.md`: canonical skill instructions and metadata for Claude-style skill hosts.
- `AGENTS.md`: Codex-facing mirror of the skill instructions.
- `README.md`: human-facing overview, installation, usage, troubleshooting, and operator guidance.
- `CHANGELOG.md`: release history and version-specific behavior changes.
- `TODO.md`: deferred follow-ups and scout ideas.
- `config.json.example`: persistent preference schema, Cobbler-first defaults, and optional
  provider configuration.
- `docs/cobbler.md`: human-facing Cobbler walkthrough, paired with `assets/cobbler-infographic.png`.

## Durable Agent Docs

- `.ai-docs/manifest.md`: read-order and file inventory for this directory.
- `.ai-docs/architecture.md`: repo shape and documentation-memory layers.
- `.ai-docs/conventions.md`: rules for versioning, cross-file sync, run control, and merge policy.
- `.ai-docs/gotchas.md`: recurring traps in this documentation-heavy repo.
- `.ai-docs/context-index.md`: this quick map for future pre-implementation surveys.

## Reference Docs

- `references/kickoff-prompt-template.md`: stage-then-launch prompt structure.
- `references/survival-guide-template.md`: run-control, Stop Gate, active compute, and launch
  readiness template.
- `references/execution-log-template.md`: batch logging and regression-attestation template.
- `references/review-subagent.md`: review loop, final readiness review, and reviewed-PR landing
  guidance.
- `references/open-ended-guide.md`: continuation rules for non-stop runs.
- `references/autonomy-guide.md`: non-interactive operation, resource hygiene, and local maintenance.
- `references/council-workflow.md`: Cobbler workflow and Council compatibility path.
- `references/council-prompts.md`: Cobbler role and fitted-answer prompt templates.
- `references/council-provider-config.md`: optional provider-backed Cobbler configuration.
- `references/codex-goals.md`: how Goals keeps Codex moving while Cobbler coordinates the Elves
  loop.
- `references/math-*.md`: math research workflow templates and provider-role guidance.

## Scripts

- `scripts/check_repo_consistency.py`: narrow cross-file drift checker for high-value guardrails.
- `scripts/sync_installed_skills.py`: syncs the installable runtime surface into Claude/Codex skill
  directories.
- `scripts/install_doctor.py`: advisory install/update diagnostics for startup and doctor mode.
- `scripts/release_checklist.py`: read-only release sweep for version alignment, changelog
  promotion, current-version examples, and changed human-facing docs.
- `scripts/pr_portfolio_report.py`: read-only PR stack health summary for merge state, checks, and
  unresolved review threads.
- `scripts/preflight.sh`: staging preflight for git, gh, environment, validation gates,
  notifications, and worktree ownership.
- `scripts/preflight_worktree.py`: explicit dedicated-worktree helper used by
  `./scripts/preflight.sh --create-worktree`.
- `scripts/validate_survival_guide.py`: advisory validator for required survival-guide sections.
- `scripts/workspace_guard.py`: optional repo-only owned-tip guard prototype for candidate write
  commands.
- `scripts/notify.sh`: Slack/custom-command/GitHub fallback notification helper.

## Tests

- `tests/test_check_repo_consistency.py`: unit tests for cross-file phrase and guardrail checks.
- `tests/test_install_doctor.py`: tests for release-cache and install-diagnostic helpers.
- `tests/test_notify_sh.py`: tests for notification helper channels and environment handling.
- `tests/test_preflight_sh.py`: smoke tests for preflight launch-readiness behavior.
- `tests/test_preflight_worktree.py`: tests for dedicated-worktree dry-run, create mode, branch
  collisions, base-ref handling, and generated paths.
- `tests/test_release_checklist.py`: tests for release-sweep parsing and failure modes.
- `tests/test_pr_portfolio_report.py`: tests for PR selection, check classification, and report
  formatting.
- `tests/test_sync_installed_skills.py`: tests for managed install sync, alias safety, config
  template install, and repo-only cleanup.
- `tests/test_validate_survival_guide.py`: tests for advisory survival-guide validation.
- `tests/test_workspace_guard.py`: tests for command classification, advisory/strict modes, and
  owned-tip recording.

## Common Survey Paths

- Behavior or run-loop change: read `SKILL.md`, `AGENTS.md`, `README.md`, related `references/*`,
  `scripts/check_repo_consistency.py`, and `tests/test_check_repo_consistency.py`.
- Cobbler change: read `SKILL.md`/`AGENTS.md` Cobbler sections, README Cobbler sections,
  `docs/cobbler.md`, `references/council-workflow.md`, `references/council-prompts.md`,
  `references/council-provider-config.md`, survival/kickoff/execution-log templates, image assets
  such as `assets/cobbler-infographic.png`, and the Cobbler phrase maps in
  `scripts/check_repo_consistency.py`.
- Preflight or install change: read `scripts/preflight.sh`, `scripts/preflight_worktree.py`,
  `scripts/install_doctor.py`, `scripts/sync_installed_skills.py`, matching tests, README install
  sections, and `.ai-docs/gotchas.md`.
- Release/version change: read `SKILL.md` metadata, `AGENTS.md` front matter, `CHANGELOG.md`,
  README release/install sections, `config.json.example`, and release helper/checker scripts.

## Validation Baseline

For this repo, the usual local proof set is:

```bash
python3 scripts/check_repo_consistency.py
python3 -m unittest discover -v
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/preflight_worktree.py scripts/pr_portfolio_report.py scripts/release_checklist.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py scripts/workspace_guard.py
bash -n scripts/preflight.sh scripts/notify.sh
python3 -m json.tool config.json.example >/dev/null
git diff --check
```

Feature branches can add more scripts or tests. Prefer running focused new tests plus the baseline
before opening or updating a PR.
