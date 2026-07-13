# CouncilElves Launch Prompt

Cobbler is the coordinator. "CouncilElves" is the compatibility name for the same loop: plan with
independent lenses, implement, review independently, integrate on the host, and repeat.

## Default first

**Vanilla path:** Claude Code or Codex out of the box. The host implements batches itself under
Cobbler. Native subagents (or direct analysis) handle extra lenses. No Grok, OpenRouter, Sakana,
or external implement CLI required. Missing optional tools never block the run.

**Optional upgrades** (capability scan — same idea as the math module):

| Option | When | Default overnight? |
|--------|------|--------------------|
| Host-native implement | Always available on Claude Code / Codex | **Yes** |
| External batch implementer (`implement …`) | User has Grok Build (or similar) and wants it | Opt-in |
| Stricter host-import writer (`worker …`) | Prove a hard writer boundary; repair that runtime | Explicit opt-in |

When using an external implementer or the host-import writer, record:

```text
implementation_lane: fast | untrusted
```

- **External implementer (`fast`):** host stages plan/PR/worktree and one batch packet; e.g. a
  persistent Grok Build session implements the batch; host gates between batches. See
  [`grok-implementer-launch-prompt.md`](grok-implementer-launch-prompt.md).
  Operator CLI: `python3 scripts/cobbler_agents.py implement prepare|launch|gate|resume-batch|status|full-run-*`.
- **Host-import writer (`untrusted`):** exclusive writer lease, detached commits, host audit/import
  only (launch text below). CLI: `python3 scripts/cobbler_agents.py worker …`.

Do **not** use the untrusted lease path as the default overnight path. Do **not** require Grok
Build for ordinary Elves.

## When to use this file

Use after staging is launch-ready (plan, Survival Guide, PR, preflight, Stop Gate ready).

- **Host-native run:** ordinary Elves launch prompt (stage, then start) — no `implementation_lane`
  field required.
- **External Grok implementer:** prefer [`grok-implementer-launch-prompt.md`](grok-implementer-launch-prompt.md).
- **Host-import writer lease:** use the launch text below.

## Launch text (host-import writer — advanced)

```text
Start the staged Elves run now. Read the Survival Guide first, then .elves-session.json, learnings,
the plan, execution log, and .ai-docs manifest/linked docs. Set the Stop Gate and continuation guard
to no. Stay Cobbler-first. The host owns contracts, risk, acceptance, synthesis, git/PR, and
canonical run documents. Give the qualified external implementation worker one whole substantial
batch at a time (target 2–5 meaningful detached commits). Audit the complete chain and shared git
state, import only approved binary patches, validate, and create/push sanitized host commits
recording worker SHAs. Run independent review concurrently excluding the implementer (quorum per
project Survival Guide). Remediate through the same worker. Repeat through all planned batches.
Never merge unless the user explicitly opts in after Final Readiness.
```

## Loop shape (host-import writer)

1. **Planning fan-out (read-only, concurrent):** host + optional configured independent reviewers (or native
   fallbacks). Same redacted context packet; no peer reports before synthesis.
2. **Implementation lease:** one writer, one exact session, one detached worktree, allowed paths only.
3. **Host audit/import:** binary patches, apply-check, validation, sanitized branch commits, push.
4. **Review fan-out (read-only, concurrent):** fresh host + contextual reviewers; exclude implementer.
5. **Remediation:** same worker lease path; re-audit; re-review until clean.
6. **Close batch:** acceptance evidence rows, Close commit, re-read Survival Guide, continue.

## Safety invariants

- Native-only remains complete without external tools/keys.
- `required = true` only via explicit project Survival Guide.
- No credentials in config/packets/logs/git.
- Host-import worker never creates/moves refs, pushes, owns PRs, or edits run memory.
- External implementer still never merges/tags or opens a second PR; host owns final readiness.
- Git history is operator UI: meaningful Contract/Implement/Validate/Review/Close subjects.

## Related surfaces

- Runtime CLI: `scripts/cobbler_agents.py` (`implement` optional external implementer; `worker` host-import)
- Optional Grok implementer: `references/grok-implementer-launch-prompt.md`
- Design: `docs/plans/smart-plan-grok-implement.md`
- Setup: `/setup-cobbler`, `/setup-council`, `$elves setup-cobbler` / natural Codex wording
- Recipes: `references/cobbler-setup-recipes.md`
- Workflow: `references/council-workflow.md`
