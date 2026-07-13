# Using Elves With Codex Goals

Codex Goals can make Elves runs more reliable by providing a native continuation backend for
Codex. Goals keeps Codex moving; Cobbler coordinates the Elves loop by selecting the execution
route; Elves memory/readiness surfaces define completion before the branch is review-ready.

Goals should not replace the Elves loop. The plan, survival guide, execution log,
`.elves-session.json`, learnings file, PR feedback, and Readiness Gate remain the source of truth.
Cobbler-first coordination remains the default for non-trivial planning, contract, risk, debugging,
review, and synthesis decisions inside the goal.

Codex Goals are not required for Quick Cobbler. For a one-off Cobbler answer in Codex, use
`$elves cobbler: <task>` or natural language such as "Ask the Cobbler to..." Use
`$elves council: <task>` only as the Council-compatible path.

## When To Use Goals

Use Codex Goals when:

- You are launching from Codex and your installed version supports `/goal`.
- The work is long enough that normal chat continuation may drift, pause, or hit memory pressure.
- You want Codex to keep looping until an objective is complete or its configured budget is
  exhausted.

Use the normal Elves launch prompt when:

- You are running in Claude Code, Claude.ai, or another non-Codex environment.
- `/goal` is unavailable or disabled in your Codex install.
- The task is short enough that the normal launch prompt is simpler.
- You only need a Quick Cobbler answer instead of a full multi-batch Elves run.

## Setup

Follow the current Codex documentation for enabling Goals, feature flags, and any token or runtime
budgets. Elves should not hard-code Codex configuration details because Goals is platform-specific
and may change across Codex releases.

Useful references:

- [Codex release notes](https://github.com/openai/codex/releases)
- [Codex changelog](https://developers.openai.com/codex/changelog)
- [Codex best practices](https://developers.openai.com/codex/learn/best-practices)

## E2E chat-to-work / chat-to-land

For a single kickoff that plans, stages, and runs batches (and optionally lands the PR), see
[`e2e-chat-to-land.md`](e2e-chat-to-land.md) and the **Chat-to-work** / **Chat-to-land** templates
in [`kickoff-prompt-template.md`](kickoff-prompt-template.md). `/goal` is the recommended Codex
continuation seatbelt for those modes after staging is solid; Elves memory and the Stop Gate remain
authority. Re-drive a work driver only after a bounded return or terminal/safety wake establishes a
gap—never after a healthy trusted full-run internal batch.

## Choose The Execution Route First

Goals must not run two orchestration loops at once:

- **Host-native or legacy bounded:** the Goal runs the full host-owned Elves loop for every batch,
  including contracts, validation, review, memory updates, commit/push, and PR polling.
- **Trusted Grok full-run:** the Goal creates one host-owned launch rollback ref before handoff:

  ```bash
  python3 scripts/cobbler_agents.py implement rollback-ref --json \
    --run-id <run-id> --session-id <exact-session-id> --batch 0 \
    --head <start-head> --push
  ```

  Then it prepares and launches one exact full-run packet/session and parks. While the worker is
  healthy, the Goal performs bounded `full-run-monitor`/`full-run-logs` reads and gives only light
  progress updates. It does not run per-batch review, canonical-memory updates, pushes, or worker
  re-prompts. A terminal event, safety condition, explicit user intervention, or actual worker exit
  wakes one cumulative review/recovery and landing-readiness loop.

## Launch Pattern

Stage the Elves run first. The branch, PR, plan, survival guide, learnings file, execution log,
preflight, run mode, Stop Gate, and launch prompt should already exist.

Then start the run with `/goal`:

```text
/goal The run is staged. Start now.
Read docs/elves/survival-guide.md first, then `.elves-session.json` if it exists, then
docs/elves/learnings.md if it exists, then docs/plans/my-plan.md, then the execution log at
docs/elves/execution-log.md, then `.ai-docs/manifest.md` if it exists.

Use the survival guide Stop Gate and Elves Readiness Gate as the definition of completion.
Do not stop unless the Stop Gate allows it, I explicitly stop you, or you hit a genuine blocker.
Follow the execution route recorded in Run Control. For host-native/legacy bounded work, run the
full host-owned Elves loop: Cobbler-coordinate non-trivial decisions, verify green, contract,
implement, validate, review PR feedback, document, update memory, commit, push, reread the
survival guide, and continue. For a trusted full-run, create the host-owned b0 rollback ref before
handoff, launch one exact worker session, and park on bounded monitor/log reads. Do not shadow its
internal batches with host review, memory, push, or re-prompt work. Wake only for terminal/safety
conditions, explicit user input, or actual worker exit, then run one cumulative final review.

If the goal budget is exhausted before the Readiness Gate is clean, do not claim completion. If a
trusted worker is still healthy, preserve its exact session identity and supervisor state in the
reactivation handoff without inventing a per-batch host checkpoint. Otherwise update the execution
log and survival guide, commit, push, and leave the exact prompt needed to resume in a fresh goal or
normal launch.
```

## Completion Rules

Codex may decide a goal is complete when the objective appears satisfied. Elves should use a
stricter definition. The goal is complete only when the Elves Readiness Gate is clean:

- All planned batches are complete or explicitly deferred.
- Local and preview proof are green on the current tip.
- PR comments, review threads, issue comments, and checks are handled.
- The final cumulative review is clean.
- Strategic forgetting is complete and a reactivation handoff exists if any work remains.
- Git status is clean and the branch is pushed.

Progress is not completion. A checkpoint is not completion. A clean goal turn is not completion.

## Budget Exhaustion

If Codex Goals stops because its token, time, or continuation budget is exhausted, treat that as a
checkpoint:

If an exact trusted full-run worker is still healthy, do **not** mutate the shared checkout,
canonical run memory, or feature branch just to manufacture a Goal checkpoint. Leave a bounded
reactivation handoff containing the session id, supervisor paths/state, last observed descendant
tip, and next `full-run-monitor` command. A fresh Goal resumes supervision of that same session.

Otherwise:

1. Update the execution log with the current state and remaining work.
2. Update the survival guide's `Current Phase`, `Stop Gate`, and `Next Exact Batch`.
3. Update `.elves-session.json` so the next session can recover quickly.
4. Write a concise reactivation handoff with branch, PR, validation state, unresolved risks, and
   the next prompt.
5. Commit and push.
6. Do not say the run is complete unless the Readiness Gate is actually clean.

## Why This Split Works

Codex Goals provides persistence and continuation. Elves provides:

- staged plans and launch prompts
- batch contracts and route-appropriate rollback authority (`bN` for host-native/legacy, one `b0`
  plus worker SHAs for trusted full-run)
- validation and review discipline
- PR feedback handling
- documentation and durable memory
- strategic forgetting and resource hygiene
- final merge-readiness checks

Together, Goals keeps the engine running and Elves keeps the work pointed at a review-ready branch.
