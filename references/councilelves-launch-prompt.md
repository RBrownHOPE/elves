# CouncilElves Launch Prompt

Cobbler is the coordinator. "CouncilElves" is the compatibility name for the same loop: plan with
independent lenses, implement with one qualified external worker, review independently, integrate
on the host, and repeat.

## When to use

Use this prompt after staging is launch-ready (plan, Survival Guide, PR, preflight, Stop Gate ready).

## Launch text

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

## Loop shape

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
- Worker never creates/moves refs, pushes, owns PRs, or edits run memory.
- Git history is operator UI: meaningful Contract/Implement/Validate/Review/Close subjects.

## Related surfaces

- Runtime CLI: `scripts/cobbler_agents.py`
- Setup: `/setup-cobbler`, `/setup-council`, `$elves setup-cobbler` / natural Codex wording
- Recipes: `references/cobbler-setup-recipes.md`
- Workflow: `references/council-workflow.md`
