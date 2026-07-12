# E2E chat-to-work and chat-to-land (design)

**Status:** design + kickoff templates (v2.0+). Not the only Elves path; classic
**stage-then-start** remains the default discipline for large plans.

**Product intent:** efficient, intelligent workflows for agentic development and research —
chat about intent, optionally involve multi-model planners, then fire a full Elves run without
locking the user into one model ecosystem. Cobbler coordinates; Claude Code or Codex is the main
driver.

## Two user-facing prompts

| Mode | User outcome | Merge |
| --- | --- | --- |
| **Chat-to-work** | Plan + stage + implement + validate + review → **landable PR** | **Never** merge unless the user later opts in |
| **Chat-to-land** | Same as chat-to-work, then **reviewed-PR landing ceremony** through merge | Explicit opt-in in the kickoff (merge-commit only, never squash) |

Both modes may start from a **conversation** (not only a pre-written plan file). The host agent
still materializes a plan and full run docs on disk before unattended batches.

Internally, keep **stage then execute** as separate *phases* even when the user sends one message:
plan/docs/PR first, then batch loop. Collapsing those into one *user* message is fine; collapsing
them in the agent’s head (coding before launch-ready) is not.

## Flow

```text
User chat (intent, constraints, non-negotiables)
    │
    ├─ optional multi-planner panel (Cobbler / OpenRouter / Gemini / …)
    │     re-chat if intent still fuzzy
    ▼
Host materializes plan + survival guide + learnings + execution log
    │
    ▼
Stage: branch, PR, preflight, Cobbler session state, Stop Gate, merge policy
    │
    ├─ optional: /goal (Codex) or host continuation harness (Claude Code)
    ▼
Batch loop: contract → implement (host or work driver) → validate → review → document → push
    │
    ├─ after every work-driver return: labor completeness check (see below)
    │     incomplete → re-packet gaps + re-drive (budget N) or host fills gap / hard-stop
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

After every work-driver return (and before marking a batch complete):

1. **Contract** — every acceptance criterion has concrete evidence (not narrative).
2. **Surfaces** — owned files in the packet were touched as required; forbidden paths untouched.
3. **Done report** — if the packet requires `.elves/runtime/implement/done/batch-N.json`, it exists
   and is coherent with the tip.
4. **Gates** — focused + agreed broad tests pass.
5. **Diff honesty** — no “status complete” with empty or off-contract diff.

If incomplete:

1. Write a **gap packet** (remaining criteria, files, commands, exact session id).
2. **Re-drive** the same work driver (prefer exact session resume) up to `labor re-drive budget`.
3. If still incomplete: host finishes the gap **or** hard-stop with remaining contract listed.
4. Log every re-drive under **Decisions made** / execution log.

Never silently absorb a partial work-driver turn into batch `status: complete`.

## Multi-planner involvement

- **Before stage freezes the plan:** good time for independent plan/risk lenses.
- **After launch:** prefer host + work driver + independent **review** lenses; avoid re-opening
  the whole plan every batch unless blocked.
- Planners are evidence, not authority; the host synthesizes one plan and owns git/PR/memory.

## Relationship to existing modes

| Existing | Relationship |
| --- | --- |
| Stage then launch (two calls) | Still recommended for huge plans; E2E is a convenience wrapper |
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

## Implementation backlog (later)

1. Survival-guide template fields for `e2e mode` + labor re-drive budget  
2. `implement gate` / host checklist that fails closed on missing acceptance evidence after labor  
3. Optional scripted gap-packet helper after work-driver status  
4. Dogfood: force partial Grok batch, prove re-drive, then chat-to-work and chat-to-land once each  

---

*Designed for Elves 2.0 multi-model workflows under Cobbler; keep classic stage-then-start as the
safe default when the plan is still unstable.*
