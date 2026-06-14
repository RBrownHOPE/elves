---
name: cobbler
description: Invoke Elves Cobbler for one fitted answer from independent lenses. Use when the user types /cobbler or asks for Cobbler coordination in Claude Code.
disable-model-invocation: true
---

<!-- elves-managed-alias: claude-skill-alias v1 -->

# Cobbler

This is an Elves-managed Claude Code alias for `/cobbler`.

Use the installed `elves` skill's `## Cobbler` instructions as the source of truth. Treat this as
Quick Cobbler unless the user explicitly attaches the answer to an active Elves run.

Default behavior:

1. Stay read-only and stateless.
2. Use Claude Code subagents first when independent lenses help.
3. Fall back to direct read-only lens analysis when subagents are unavailable.
4. Do not edit files, create branches, open PRs, install packages, or mutate run state.
5. Return one fitted answer with `Recommendation`, `Why this fits`, `Strongest dissent`, `Risks`,
   `Next move`, and `Confidence`.

Normal Cobbler use must not require OpenRouter or any external provider key.
