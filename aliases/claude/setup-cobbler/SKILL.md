---
name: setup-cobbler
description: Configure Cobbler external-agent preferences for this checkout. Use when the user types /setup-cobbler or asks to set up Cobbler model routes in Claude Code.
disable-model-invocation: true
---

<!-- elves-managed-alias: claude-skill-alias v1 -->

# Setup Cobbler

This is an Elves-managed Claude Code alias for `/setup-cobbler`.

This alias invokes the Elves Cobbler **setup** contract from the main `elves` skill and the
`python3 scripts/cobbler_agents.py setup` operator CLI. Setup must not require OpenRouter or any
external provider key. Native-only Elves remains fully usable without running setup.

Default behavior:

1. Inventory available host tools (Claude Code, Grok Build, Codex/Fugu, custom CLI) without printing
   credentials or launching paid model turns unless the user explicitly opts into a smoke.
2. Collect role preferences and fallback chains for planning, implementation, lightweight review,
   validation, independent review, synthesis, and scout. Treat commit/push/PR as host **operations**,
   not model roles.
3. Write or update the intentionally ignored local file `.elves/models.toml` for this checkout only.
   Never stage or commit it. Never paste API keys into TOML, chat, or the Survival Guide.
4. Recommend routes by qualified capability and discovery date — not by hardcoded prestige model
   names. Keep host-native fallbacks for optional roles.
5. Remind the host coordinator to snapshot effective routes into the Survival Guide during staging
   so reviews can see provenance without reading machine-local preferences.

Codex equivalent (not a top-level slash command): `$elves setup-cobbler` or natural language such as
"Set up Cobbler external-agent preferences."

Compatibility: `/setup-council` is the same setup contract with a Council-era name.
