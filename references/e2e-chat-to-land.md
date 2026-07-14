# E2E chat-to-work and chat-to-land (design)

**Status:** **recommended default user path (v2.0+)** — design + kickoff templates. v2.1 adds trusted
Grok full-run delegation with parked-driver semantics. Classic **two-call stage-then-start** remains
optional for huge or unstable plans.

**Product intent:** efficient, intelligent workflows for agentic development and research —
chat to conceptual agreement (optionally multi-planner), then **one prompt** runs plan + stage +
batches, without locking the user into one model ecosystem. Cobbler coordinates; Claude Code or
Codex is the main driver. User chooses **landable PR only** (chat-to-work) or **merge ceremony**
(chat-to-land).

**Why single kickoff:** requiring a separate human “stage” call often failed — incomplete staging
meant the overnight run never really started. The agent now owns staging quality; the human owns
intent and merge policy.

## Two user-facing prompts

| Mode | User outcome | Merge |
| --- | --- | --- |
| **Chat-to-work** | Plan + stage + implement + validate + review → **landable PR** | **Never** merge unless the user later opts in |
| **Chat-to-land** | Same as chat-to-work, then **reviewed-PR landing ceremony** through merge | Explicit opt-in in the kickoff (merge-commit only, never squash) |

Both modes may start from a **conversation** (not only a pre-written plan file). The host agent
still materializes a plan and full run docs on disk before unattended batches.

Internally, keep **stage then execute** as separate *phases* even when the user sends one message:
plan/docs/PR first, then batch loop. One *user* message is the recommended product path; coding
before launch-ready is still forbidden. After launch-ready in E2E mode, **continue into the batch
loop without waiting for a second human call.**

## Flow

```text
User chat (intent, constraints, non-negotiables)
    │
    ├─ optional multi-planner panel (Cobbler / OpenRouter / Gemini / …)
    │     re-chat if intent still fuzzy
    ▼
Host materializes plan + survival guide + learnings + execution log + session scaffold
    │
    ▼
Stage: branch, PR, Cobbler session state, Stop Gate, merge policy
    │
    ▼
Acceptance staging: derive/validate session rows from the plan; bind the exact worker packet
    │
    ▼
Preflight and launch-ready checks
    │
    ├─ optional: /goal (Codex) or host continuation harness (Claude Code)
    ▼
Execution route
    │
    ├─ host-native / legacy bounded driver:
    │    batch loop → validate → review → document → host push
    │    after each bounded return: labor completeness check
    │
    └─ trusted Grok full-run:
         one complete packet → one persistent launch → parked bounded telemetry
         worker loops batches, validates, commits, and pushes without host re-prompts
         host wakes only on safety/blocked/terminal events, then audits cumulatively
    ▼
Readiness Gate (landable PR)
    │
    ├─ chat-to-work: STOP here (PR open, green, reviewed; user merges)
    └─ chat-to-land: reviewed-PR landing (tests, PR comments, cumulative review, merge commit)
```

## Run Control fields

Record in the survival guide `## Run Control` (and mirror in `.elves-session.json` when useful):

```markdown
## Run Control
- run mode: finite | open-ended
- e2e mode: chat-to-work | chat-to-land | off
- merge policy: never-merge | merge-commit-on-green | reviewed-pr-landing-command
- work driver: host-native | grok-build | opencode-cli | …
- delegation scope: none | batch | full_run
- driver monitor mode: interactive | parked_monitor | n_a
- driver update policy: material transitions + host-coalesced heartbeat at most every 15m;
  unchanged healthy polls silent | interactive
- driver poll policy: host wait primitive | half stale window, bounded 60–300s | interactive
- driver review policy: final independent review only | per-batch
- labor re-drive budget: 3
- multi-planner: optional | required-for-plan
- continuation harness: none | codex-goal | host-native
```

Rules:

- **`chat-to-work`** ⇒ `merge policy: never-merge` (default). PR is for the human.
- **`chat-to-land`** ⇒ `merge policy: reviewed-pr-landing-command` (or merge-commit-on-green after
  Final Readiness). Regular **merge commit only**; never squash/rebase for this path.
- Missing optional multi-planner tools never blocks planning; fall back to host-native Cobbler.

## Continuation harness (`/goal` and friends)

Use platform continuation as a **seatbelt**, not as the source of truth:

- **Codex:** after stage (or as part of a single E2E kickoff), wrap the launch with `/goal` so the
  host keeps looping. Goal text must point at the survival guide Stop Gate and Readiness Gate.
  See [`codex-goals.md`](codex-goals.md).
- **Claude Code:** use the host’s long-run / goal-like features if available; otherwise rely on
  Elves open-ended mode + Stop Gate + “do not stop unless…” language in the kickoff.
- Elves memory files remain authoritative. Do not put the whole plan only inside the goal string.

## Labor completeness (work-driver laziness)

Grok Build and similar work drivers often **do some but not all** of a batch. That is a **host
defect** if accepted as “done.”

After every bounded work-driver return, or once at trusted full-run terminal/safety wake (before the
host accepts any reported batch as complete):

1. **Contract** — every acceptance criterion has concrete evidence (not narrative).
2. **Surfaces** — owned files in the packet were touched as required; forbidden paths untouched.
3. **Worker report** — trusted full-run v1 report/events validate at terminal wake; if a legacy
   bounded packet requires `.elves/runtime/implement/done/batch-N.json`, it exists and is coherent
   with the tip.
4. **Gates** — focused + agreed broad tests pass.
5. **Diff honesty** — no “status complete” with empty or off-contract diff.

If incomplete:

1. Write a **gap packet** (remaining criteria, files, commands, exact session id).
2. **Re-drive** the same work driver (prefer exact session resume after interruption) up to
   `labor re-drive budget`. Do not turn a healthy trusted full-run into per-batch prompting.
3. If still incomplete: host finishes the gap **or** hard-stop with remaining contract listed.
4. Log every re-drive under **Decisions made** / execution log.

Never silently absorb a partial work-driver turn into batch `status: complete`.

## Multi-planner involvement

- **Before stage freezes the plan:** good time for independent plan/risk lenses.
- **After a bounded-driver return:** host and independent review lenses may review before the next
  bounded turn.
- **During a trusted parked full-run:** do not launch per-batch host review or planning chatter.
  Run independent cumulative review after terminal/safety wake.
- Planners are evidence, not authority; the host synthesizes one plan and owns canonical memory,
  protected refs, PR actions, final review, and merge. The exact registered trusted full-run worker
  may commit/push only its assigned feature branch.

## Relationship to existing modes

| Existing | Relationship |
| --- | --- |
| Stage then launch (two calls) | **Legacy / advanced** for huge or unstable plans; E2E is the default product path |
| Open-ended run | Compatible; chat-to-work often finite-to-Readiness |
| Reviewed PR landing / `\land-pr` | Used by **chat-to-land** at the end |
| Lane A implement / OpenCode labor | Optional work drivers under labor completeness |
| Math domain / AlphaEvolve | Same E2E shell; domain workflow still Cobbler-managed |

## Non-goals (for this design)

- Auto-merge without explicit kickoff language or Run Control opt-in
- Replacing Elves memory with only `/goal` text
- Treating work-driver session end as batch complete
- Requiring multi-provider setup for E2E (native-only E2E is valid)

## Kickoff templates

Copy-paste prompts: [`kickoff-prompt-template.md`](kickoff-prompt-template.md) sections
**Chat-to-work (E2E, no merge)** and **Chat-to-land (E2E through merge)**.

## Implemented v2.1 contract

- Survival-guide Run Control records E2E mode, delegation/Git/monitor policy, and re-drive budget.
- Before any worker launch, `acceptance_contract.py sync-session`/`validate` reconciles exact
  plan/session criterion text and exact normalized Batch sets. Trusted full-run prepare receives
  the canonical `--session` (or the repo-root `.elves-session.json`) and immutably binds that
  plan/session mapping to the packet;
  use the exact recipe in [`grok-implementer-launch-prompt.md`](grok-implementer-launch-prompt.md).
- Legacy `implement gate` and the trusted full-run supervisor validate acceptance/report evidence.
- Trusted full-run uses one packet/session, `branch_progress`, bounded events/report, a parked host,
  and cumulative terminal review; legacy bounded re-drive remains available after an actual return.
- Chat-to-work and chat-to-land share staging/readiness and differ only in explicit merge authority.

---

*Elves v2.1 contract under Cobbler; classic stage-then-start remains available when the plan is
still unstable.*
