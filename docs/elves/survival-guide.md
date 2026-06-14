# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

> This is the live operator brief for the v1.14.0 Elves Council run. After any compaction or
> restart, read this file first, then `.elves-session.json`, learnings, the plan, and the execution
> log. Trust this file over memory.

---

## Mission

Add the v1.14.0 Elves Council workflow to the repo. The release should document a natural
`/council` / `/ec` / `/elves-council` chat command that uses native read-only subagents first,
returns one decisive synthesis with visible dissent, and keeps OpenRouter/external providers
optional for future Deep Council mode.

The run must update both canonical skill surfaces: `SKILL.md` for Claude-compatible agents and
`AGENTS.md` for Codex.

---

## Run Control

- **Run mode:** finite
- **Stop policy:** launch is active; stop only when all planned batches are complete, final
  readiness review is clean, PR #27 has been landed under the explicit user opt-in below, GitHub
  release/version state is updated, or when genuinely blocked.
- **User intent:** "plan this update out as an elves run and apply the principles as you work.
  get help from other agents. update all docs, bump version number, and do this for both codex and
  claude"
- **Checkpoint due by:** none
- **Checkpoint semantics:** staging boundary was satisfied in the prior call; this call is launch
  mode.
- **May continue after checkpoint:** yes
- **Actual stop conditions:** all four batches complete, validated, reviewed, documented, pushed,
  PR #27 landed with a regular merge commit after final readiness, GitHub version/release state
  updated, X statement prepared, or a genuine blocker.
- **Workspace ownership:** owned branch + main checkout; read-only explorer subagents may inspect
  but must not write.
- **Branch tip at start (collision tripwire):** `26bb766ea8512d252dc93997a83b2d498d0f219c`
- **Merge policy:** explicit one-off merge opt-in for PR #27 from latest user instruction; land
  only after final readiness is clean, with `gh pr merge --merge`, never squash or rebase.
- **Final-response policy:** allowed only when the finite run, landing, GitHub version update, and
  X statement are complete, or when genuinely blocked.
- **Batch completion rule:** Every completed batch ends with `update execution log -> update
  survival guide -> commit -> push`.
- **Re-read rule:** Immediately after every commit and push, re-read this survival guide before
  doing anything else.
- **Checkpoint rule:** Staging is the only checkpoint that is a hard stop; after launch, commits,
  pushes, and useful summaries are progress markers, not stop conditions.
- **Continuation rule:** After launch, if work remains and the actual stop conditions are not met,
  continue without waiting for user acknowledgment.

---

## Session Budget

- **Started:** 2026-06-14 08:52 EDT
- **User returns:** assumed 2026-06-14 16:52 EDT if launched without a different return time
- **Checkpoint expectation:** launch-ready branch, PR #27, plan, survival guide, execution log, and
  `.elves-session.json`
- **Time budget:** ~8 hours after launch unless user overrides
- **Average batch time so far:** N/A
- **Batches remaining:** 2 of 4

---

## Stop Gate

- **Planned batches remaining:** 2
- **Stop allowed right now:** no
- **Why:** Batch 2 is pushed, and Batches 3-4 plus final landing, GitHub version update, and X
  statement remain.
- **Next required action:** start Batch 3 with Verify Green, rollback tag, contract, and
  pre-implementation survey.

---

## Effort Standard

- Work as hard as you can for the full run. Do not be lazy.
- Maintain the same level of effort on the last batch as on the first.
- Do not settle for the minimum acceptable change, first green check, or shallow docs pass when
  deeper verification or the next highest-value action remains.
- Apply the code-quality principles to documentation too: extend existing patterns, avoid parallel
  systems, and keep the repo easier for future agents.

---

## Forbidden Stop Reasons

- A commit or push succeeded.
- The PR exists.
- CI or local checks are green.
- A checkpoint or clean batch boundary feels like a natural pause.
- A useful summary has been written.
- The remaining work feels large for one turn.

---

## Current Phase

- **Status:** Batch 3 ready to start
- **Active batch:** Batch 3: Config, Run Logging, And Tool Examples
- **What was just finished:** Council workflow and prompt references were added, README links were
  updated, and the plan was corrected to use Run Council logging through existing memory instead of
  a separate Council ledger.
- **Single next action:** start Batch 3 with Verify Green, rollback tag, contract, and
  pre-implementation survey.

---

## Next Exact Batch

- **Batch:** Batch 3: Config, Run Logging, And Tool Examples
- **Scope:** add optional Deep Council provider configuration, extend `config.json.example`,
  `references/tool-config-examples.md`, and the survival-guide template, and document Run Council
  logging through existing Elves memory surfaces.
- **Acceptance criteria:** config defaults require no external provider key; external provider
  configuration is clearly optional for Deep Council; Run Council logging reuses the execution log
  and `.elves-session.json`; Quick Council remains stateless unless the user asks for `--run` or is
  already inside an Elves run.
- **Risk:** medium, because config docs can accidentally make external providers or parallel
  ledgers look required.

Before editing config/reference docs, write the Batch 3 contract in the execution log. Build on
`references/council-workflow.md`, `config.json.example` math config style, and the no-parallel-ledger
review finding from Batch 2.

---

## Batch Plan

1. **Release Skeleton And Council Concept** — bump version metadata and add the council concept to
   core skill surfaces, README, and changelog.
2. **Council Workflow And Role Prompts** — add workflow and prompt references for Quick Council,
   Run Council, optional Deep Council, role reports, and synthesis.
3. **Config, Run Logging, And Tool Examples** — add optional council config, Run Council logging
   guidance that reuses existing Elves memory, survival-guide template block, and tool-config
   examples.
4. **Consistency Checks And Release Hardening** — extend checker/tests and run final validation.

---

## Non-Negotiables

- Quick Council is read-only and stateless by default.
- Native subagents are the default backend: Codex subagents in Codex, Claude Code subagents in
  Claude Code.
- External providers are optional Deep Council only; normal `/council` must not require OpenRouter.
- The synthesizer returns one recommendation with visible dissent, not a dump of role reports.
- Do not copy Fable identity, policy, or safety text.
- Update both Claude and Codex surfaces.
- Do not merge before final readiness; the latest user instruction is a one-off landing opt-in for
  this PR only.

---

## Tool Configuration

```yaml
review: github-pr-comments
notification: pr-comment

council-enabled: true
council-default-mode: quick
council-default-backend: native-subagents
council-aliases:
  - /council
  - /ec
  - /elves-council
council-default-role-count: 3
council-max-role-count: 5
council-quick-read-only: true
council-deep-provider-policy: optional
```

Validation commands:

```bash
python3 scripts/check_repo_consistency.py
python3 -m json.tool config.json.example
python3 -m json.tool .elves-session.json
python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py
python3 -m unittest discover -s tests -p 'test_*.py'
git diff --check
python3 scripts/install_doctor.py --doctor
python3 scripts/sync_installed_skills.py --check
```

---

## Active Compute

No paid compute, model jobs, local servers, or remote jobs are active for implementation. Two
read-only explorer subagents were spawned during staging to inspect docs/release patterns.

---

## Post-Checkpoint Control Loop

- Every completed batch must end with a commit and push before starting the next action.
- Immediately after every commit and push, re-read this survival guide before doing anything else.
- Ask: does the Stop Gate still say `Stop allowed right now: no` after launch? If yes, continue.
- Poll PR feedback after every push once the PR exists.
- Reconcile active compute and idle resources before beginning the next batch.

---

## After Any Compaction

1. Read this survival guide first.
2. Read the Run Control section and Stop Gate before deciding whether to stop.
3. Read `.elves-session.json`, especially `continuation_guard`.
4. Read `docs/elves/learnings.md`, then `docs/plans/v1.14.0-elves-council.md`, then
   `docs/elves/execution-log.md`.
5. Resume the single next action named here or in `continuation_guard`.

---

## Launch Readiness

- [x] Plan path recorded.
- [x] Survival guide, execution log, learnings path, and session JSON recorded.
- [x] Branch created and collision tripwire recorded.
- [x] PR created: #27.
- [x] Preflight run; no critical failures remain. Warnings: docs-only repo has no package manifest,
  and recommended non-interactive env vars are not exported.
- [x] Stop Gate initialized with `Stop allowed right now: no` for launch mode; staging handoff is
  the only current exception.
- [x] Launch prompt prepared.

---

## Launch Prompt

```text
Start the staged Elves run for v1.14.0 Elves Council.

Read docs/elves/survival-guide.md first, then .elves-session.json, docs/elves/learnings.md,
docs/plans/v1.14.0-elves-council.md, and docs/elves/execution-log.md. Work batch by batch.

Use native subagents for independent review/help where useful. Update both Claude and Codex
surfaces, all supporting docs, config examples, consistency checks, tests, and changelog. Keep
Quick Council read-only and native-subagent-first. Do not copy Fable identity, policy, or safety
text. Commit and push after each completed batch, poll PR feedback after every push, and stop only
when the finite run is complete or genuinely blocked.
```
