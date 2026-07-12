# CouncilElves Launch Prompt

Cobbler is the coordinator. "CouncilElves" is the compatibility name for the same loop: plan with
independent lenses, implement, review independently, integrate on the host, and repeat.

## Two implementation lanes

Record `implementation_lane: fast | untrusted` in the Survival Guide (and optionally
`.elves-session.json`).

| Lane | When | Default? |
|------|------|----------|
| **`fast`** | User says “have Grok run it”; overnight volume implementation | **Yes** |
| **`untrusted`** | Prove the writer boundary; repair lease/runtime itself | Explicit opt-in |

- **Fast path (Lane A):** smart host stages plan/PR/worktree and writes one batch packet; a
  persistent Grok Build session implements the whole batch with normal coding permissions (`auto`,
  subagents on). Host gates with tests between batches. See
  [`grok-implementer-launch-prompt.md`](grok-implementer-launch-prompt.md) and
  [`docs/plans/smart-plan-grok-implement.md`](../docs/plans/smart-plan-grok-implement.md).
  Operator CLI: `python3 scripts/cobbler_agents.py implement prepare|launch|gate|resume-batch|status`.
- **Untrusted path (Lane B):** exclusive writer lease, detached commits, host audit/import only
  (this document’s legacy launch text below). CLI: `python3 scripts/cobbler_agents.py worker …`.

Do **not** use the untrusted lease path as the default overnight “turn it over to Grok” path.

## When to use

Use this prompt after staging is launch-ready (plan, Survival Guide, PR, preflight, Stop Gate ready).
For Lane A, prefer the Grok implementer launch prompt linked above.

## Launch text (Lane B — untrusted writer)

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

## Loop shape (Lane B)

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
- Lane B worker never creates/moves refs, pushes, owns PRs, or edits run memory.
- Lane A still never merges/tags or opens a second PR; host owns final readiness.
- Git history is operator UI: meaningful Contract/Implement/Validate/Review/Close subjects.

## Related surfaces

- Runtime CLI: `scripts/cobbler_agents.py` (`implement` for Lane A, `worker` for Lane B)
- Fast implementer: `references/grok-implementer-launch-prompt.md`
- Design: `docs/plans/smart-plan-grok-implement.md`
- Setup: `/setup-cobbler`, `/setup-council`, `$elves setup-cobbler` / natural Codex wording
- Recipes: `references/cobbler-setup-recipes.md`
- Workflow: `references/council-workflow.md`
