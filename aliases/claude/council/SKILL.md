---
name: council
description: Compatibility alias for Elves Cobbler. Use when the user types /council and wants a read-only fitted answer from independent lenses.
disable-model-invocation: true
---

<!-- elves-managed-alias: claude-skill-alias v1 -->

# Council Compatibility Alias

This is an Elves-managed Claude Code compatibility alias for `/council`.

Route this request to the installed `elves` skill's `## Cobbler` instructions. Council is not a
separate product: it is a temporary read-only gathering Cobbler may convene when several lenses
help.

Default behavior:

1. Stay read-only and stateless.
2. Use Claude Code subagents first when independent lenses help.
3. Fall back to direct read-only lens analysis when subagents are unavailable.
4. Do not edit files, create branches, open PRs, install packages, or mutate run state.
5. Return one fitted answer with `Recommendation`, `Why this fits`, `Strongest dissent`, `Risks`,
   `Next move`, and `Confidence`.

Normal Cobbler and Council-compatible use must not require OpenRouter or any external provider key.
