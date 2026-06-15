# Plan: Public API Surface Snapshot

## Mission

Design an optional Elves safeguard that captures a project's public API surface at staging and
diffs it after each batch. The goal is to catch accidental changes to routes, schemas, response
shapes, exported library interfaces, CLI commands, or event contracts that can pass ordinary tests
but still break downstream consumers.

Done means a future implementation can add this as a focused extension of the existing regression
attestation and high-risk review flow. It should be useful for REST, GraphQL, libraries, CLIs, and
other contract-bearing projects, while staying off by default for repos where there is no public
API surface to snapshot.

## Product Shape

### Core Idea

Elves already records a test baseline and asks every batch to attest that shared surfaces and
consumers were checked. Public API surface snapshots make one part of that proof more concrete:

1. During staging, identify whether the project has public surfaces.
2. Capture a baseline snapshot using the most boring existing source of truth.
3. After each batch that touches API-adjacent code, regenerate the snapshot.
4. Diff baseline versus current.
5. Treat unexpected contract changes as review findings.

This is not a replacement for tests, review, or the constitution. It is evidence that helps the
regression reviewer distinguish intended API evolution from accidental breakage.

### Public Surface Types

Start with the surfaces users and downstream systems depend on:

- REST routes: methods, paths, status codes, request parameters, response fields, auth mode.
- GraphQL: schema SDL, queries, mutations, subscriptions, input types, output types, directives.
- Exported libraries: public functions, classes, types, interfaces, constants, package entrypoints.
- CLI tools: commands, flags, defaults, output modes, exit codes.
- Webhooks/events: event names, payload fields, version headers, delivery semantics.
- Configuration contracts: documented env vars, config keys, file formats, migration flags.

Do not snapshot private helper functions, internal module layout, local test fixtures, or generated
implementation details that do not form a consumer contract.

### Snapshot Sources

Prefer existing structured sources before inventing scanners:

- OpenAPI or Swagger specs when present.
- GraphQL SDL or introspection output when the project already exposes it locally.
- TypeScript declaration output, package exports, or existing API extractor output for libraries.
- Framework route manifests when the framework provides one.
- CLI `--help` output for command surfaces.
- Existing docs only as a fallback when they are known to be authoritative.

If no reliable source exists, record `api_surface_snapshot.status: unavailable` with the reason
instead of fabricating an unreliable snapshot.

### Draft Survival Guide Shape

```yaml
api-surface-snapshot:
  enabled: auto
  required: false
  baseline-path: docs/elves/api-surface-baseline.json
  current-path: docs/elves/api-surface-current.json
  diff-path: docs/elves/api-surface-diff.md
  surfaces:
    rest: auto
    graphql: auto
    exports: auto
    cli: auto
    events: auto
    config: auto
  policy:
    unexpected-breaking-change: blocking
    additive-change: info
    intentional-breaking-change: requires-plan-note
    unavailable-source: warning
```

The paths above are run artifacts by default, not product docs. Final Completion should remove or
archive them with the rest of Elves session infrastructure unless the user explicitly wants a
durable API report.

## Scope

### In Scope

- Define when public API snapshots should run and when they should be skipped.
- Define baseline/current/diff artifact behavior.
- Define severity rules for additive, modified, breaking, and unavailable snapshot results.
- Plan future updates to staging, survival guide, execution log, review prompts, and consistency
  checks.
- Keep the design stack-agnostic while naming practical defaults for common project types.

### Out of Scope

- Implementing API parsers or scanners in this branch.
- Adding new dependencies by default.
- Requiring every Elves run to produce an API snapshot.
- Treating generated snapshots as a substitute for human-owned API promises.
- Blocking projects with no public API surface.
- Committing temporary snapshot artifacts to final product PRs by default.

## Batches

### Batch 1: Snapshot Contract and Staging UX

**Tasks:**
- [ ] Add a concise API snapshot section to `SKILL.md` and `AGENTS.md`.
- [ ] Extend `references/survival-guide-template.md` with optional `api-surface-snapshot`
      run-control fields.
- [ ] Extend `references/kickoff-prompt-template.md` so staged runs can ask for API snapshot
      behavior without bloating the launch prompt.
- [ ] Document auto-detection: enabled when a credible public-surface source exists, skipped with
      a reason when it does not.

**Acceptance criteria:**
- [ ] Users can see where to opt in, opt out, or require the API snapshot.
- [ ] `enabled: auto` does not block repos with no detectable public API.
- [ ] `required: true` is treated as an explicit per-project opt-in from the survival guide.
- [ ] Snapshot artifacts are described as run artifacts, not default product docs.

**Docs likely touched:**
- `SKILL.md`
- `AGENTS.md`
- `references/survival-guide-template.md`
- `references/kickoff-prompt-template.md`

**Risk:** If the default sounds mandatory, agents will waste time building fragile scanners for
projects that do not need them. Keep auto-detection advisory unless the user requires it.

### Batch 2: Snapshot Sources and Artifact Format

**Tasks:**
- [ ] Define a minimal JSON schema for baseline/current snapshots.
- [ ] Define a human-readable Markdown diff format for execution logs and reviewers.
- [ ] Add stack-specific source examples to `references/tool-config-examples.md`.
- [ ] Prefer existing structured sources and commands before new scripts.

**Acceptance criteria:**
- [ ] The snapshot format distinguishes route/schema/export presence from behavior verification.
- [ ] The diff labels changes as additive, modified, removed, breaking, or unknown.
- [ ] The format records source command, timestamp, project root, and tool version when available.
- [ ] The schema avoids secrets, sample payload values, customer data, and environment-specific
      private URLs.

**Docs likely touched:**
- `references/tool-config-examples.md`
- `references/validation-guide.md`
- `references/execution-log-template.md`

**Risk:** Snapshots can leak sensitive examples if they capture real payloads or environment URLs.
The schema should record shapes and field names, not production data.

### Batch 3: Regression Review Integration

**Tasks:**
- [ ] Extend `references/review-subagent.md` so high-risk regression review reads API snapshot
      diffs when present.
- [ ] Extend the batch completion/regression attestation template to include API surface delta.
- [ ] Define review severity:
      additive changes are usually INFO, documented intentional breaking changes are WARNING or
      planned work, and unexpected breaking changes are BLOCKING.
- [ ] Clarify that the constitution remains the human-owned promise layer for app behavior.

**Acceptance criteria:**
- [ ] Reviewers know exactly where to find baseline/current/diff artifacts.
- [ ] A missing snapshot is not blocking unless `required: true`.
- [ ] API diffs become evidence in the existing regression attestation rather than a separate
      review ceremony.
- [ ] Intentional public contract changes must be named in the plan or execution log.

**Docs likely touched:**
- `references/review-subagent.md`
- `references/execution-log-template.md`
- `SKILL.md`
- `AGENTS.md`

**Risk:** A snapshot diff can look authoritative while missing semantic behavior. The docs must say
it proves surface shape only, not correctness.

### Batch 4: Helper Script and Tests

**Tasks:**
- [ ] Add a boring helper only after the contract is documented, likely
      `scripts/api_surface_snapshot.py`.
- [ ] Implement source adapters incrementally: start with user-supplied commands and existing
      OpenAPI/GraphQL/schema files before framework-specific discovery.
- [ ] Add tests for artifact shape, diff classification, secret redaction boundaries, unavailable
      source behavior, and required-mode failures.
- [ ] Add consistency checks for the new guidance if the public docs mention it.

**Acceptance criteria:**
- [ ] The helper can run in no-op/advisory mode without failing repos that lack API surfaces.
- [ ] Required mode fails clearly when no credible source can be captured.
- [ ] No snapshot artifact includes raw `.env` values, bearer tokens, cookies, customer payloads, or
      private sample data.
- [ ] `python3 scripts/check_repo_consistency.py` passes.
- [ ] `python3 -m unittest discover -s tests -p 'test_*.py'` passes.
- [ ] `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py scripts/api_surface_snapshot.py` passes.
- [ ] `git diff --check` passes.

**Docs likely touched:**
- `scripts/api_surface_snapshot.py`
- `tests/test_api_surface_snapshot.py`
- `scripts/check_repo_consistency.py`
- `tests/test_check_repo_consistency.py`

**Risk:** Framework-specific detection can balloon quickly. Start with explicit commands and
structured spec files; defer clever discovery until there is a concrete project need.

## Non-Negotiables

- Public API snapshots are optional by default and required only by explicit project/run config.
- A missing snapshot source is a warning in auto mode, not a blocker.
- Snapshot artifacts must not leak secrets, credentials, cookies, bearer tokens, customer data, or
  production sample payloads.
- The snapshot is evidence for regression review, not a substitute for tests, E2E checks, or the
  human-owned constitution.
- Temporary snapshot artifacts should not remain in final product PR diffs unless the user
  explicitly asks for a durable API report.
- `SKILL.md` and `AGENTS.md` must move together for behavior changes.

## Test Strategy

- **Primary consistency gate:** `python3 scripts/check_repo_consistency.py`
- **Unit tests:** `python3 -m unittest discover -s tests -p 'test_*.py'`
- **Script compile gate:** `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py`
- **JSON validation:** `python3 -m json.tool config.json.example >/dev/null`
- **Whitespace gate:** `git diff --check`
- **Review loop:** after every push, read PR comments, review threads, and checks; fix blockers
  before continuing.

## Future Implementation Notes

- Store run snapshots under the active Elves session artifact area by default, not in durable
  product docs.
- Record `api_surface_snapshot` status in `.elves-session.json`: `not_applicable`, `captured`,
  `changed`, `unavailable`, or `required_failed`.
- In auto mode, only run after batches whose blast radius mentions API-adjacent files or when the
  diff touches known route/schema/export/config surfaces.
- Use explicit user commands when available, for example `npm run openapi:json`, `pnpm graphql:sdl`,
  `python manage.py show_urls`, or `cargo doc --no-deps` only if the project already uses them.
- Prefer normalized shapes over raw output. For example, record field names and optional/required
  status, not example values.
- Treat removed routes, removed GraphQL fields, changed requiredness, changed status codes, changed
  exported signatures, removed CLI flags, and removed config keys as likely breaking until the plan
  says otherwise.
- Treat new optional fields, new routes, new commands, and new exports as additive unless they
  alter defaults or precedence.

## Notes

- This design follows the existing regression-attestation direction in the Elves loop. It should
  extend that proof surface rather than creating a separate API-governance subsystem.
- The constitution remains the place for durable human-owned promises. API snapshots answer "did
  the public surface change?" The constitution answers "does the product still keep its promises?"
- This is intentionally a design-only scout note. It should be reviewed before any canonical skill,
  script, or config changes land.
