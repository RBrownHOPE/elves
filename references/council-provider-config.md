# Cobbler Provider-Backed Council Configuration

Cobbler is native-subagent-first. Configure providers only when the user wants optional
provider-backed council model diversity. Normal Cobbler, `/cobbler`, `$elves cobbler: <task>`,
`/council`, `/ec`, `/elves-council`, and `$elves council: <task>` use must work without
OpenRouter or any external provider key.

This file keeps its `council-provider-config.md` name for `v1.14.0` compatibility. In `v1.15.0+`,
provider-backed council replaces the old product label while preserving legacy `council-*` config
keys where users already rely on them.

## Default Quick Cobbler

Quick Cobbler needs no provider configuration:

```yaml
cobbler-enabled: true
cobbler-default-mode: quick
cobbler-default-backend: native-subagents
cobbler-primary-invocations:
  claude-code: /cobbler
  codex: "$elves cobbler: <task>"
cobbler-compatibility-aliases:
  - /council
  - /ec
  - /elves-council
  - "$elves council: <task>"
cobbler-default-answer-shape:
  - Recommendation
  - Why this fits
  - Strongest dissent
  - Risks
  - Next move
  - Confidence
cobbler-default-role-count: 3
cobbler-max-role-count: 5
cobbler-quick-read-only: true
cobbler-quick-stateless: true
cobbler-run-logging: existing-elves-memory
cobbler-model-routing-policy: native-first
```

In Codex, use Codex subagents when available. In Claude Code, use Claude Code subagents when
available. If subagents are unavailable, perform the same read-only analysis directly.

## Optional Provider-Backed Council

Provider-backed council is opt-in. It may route selected roles to external providers when the user
has configured keys and wants broader model diversity:

```yaml
cobbler-provider-backed-enabled: false
cobbler-provider-backed-policy: optional-external-providers
cobbler-provider-backed-fallback: native-subagent-and-note
cobbler-provider-backed-required-env: []
cobbler-provider-backed-optional-env:
  - OPENROUTER_API_KEY
  - GEMINI_API_KEY
  - ANTHROPIC_API_KEY
  - XAI_API_KEY
  - OPENAI_API_KEY
cobbler-provider-backed-role-models:
  default: native-subagent
  architect: native-subagent
  skeptic: native-subagent
  implementation_analyst: native-subagent
  tester: native-subagent
  synthesis: native-coordinator
cobbler-provider-backed-role-effort:
  architect: high
  skeptic: high
  tester: medium

# Example external routes when provider-backed council is explicitly enabled:
# cobbler-provider-backed-role-models:
#   skeptic: "openrouter:<model-id>"
#   fast_sanity: "openrouter:<fast-model-id>"
```

Leave `cobbler-provider-backed-required-env` empty unless a specific project intentionally makes
provider-backed council a required workflow. Do not make ordinary Cobbler or Council-compatible use
depend on that setting.

Role routing is a hint for the coordinator, not a new mode the user has to invoke. `native-subagent`
means "use the current host's subagent feature"; `native-coordinator` means the main coordinator
synthesizes directly; `provider:model-id` values are optional external routes. If the route cannot
run, fall back to native and mention the fallback in the fitted answer. Resolve disagreements by
evidence and task constraints, not by assuming a configured model is more authoritative.

Role effort is optional. Use `low`, `medium`, `high`, or `xhigh` only when the selected host or
provider supports an effort setting; otherwise omit it and keep the route native-first.

## Legacy Council Compatibility

Existing `council-*` config keys are still recognized as compatibility aliases for the same
Cobbler behavior. Prefer new `cobbler-*` keys for fresh configs, but do not delete old keys from an
existing project just to rename them.

```yaml
council-enabled: true
council-default-mode: quick
council-default-backend: native-subagents
council-aliases:
  - /council
  - /ec
  - /elves-council
council-run-logging: existing-elves-memory
council-provider-backed-enabled: false
council-provider-backed-required-env: []
```

## Fallback Policy

If the user requests provider-backed council and external providers are not configured:

1. Say provider-backed council is unavailable.
2. Fall back to native-subagent Quick Cobbler.
3. Preserve the user's requested roles where possible.
4. Do not ask for keys mid-run unless the user explicitly wants provider setup.
5. Do not search local files for secrets.

## Secret Handling

- Never hardcode provider keys in `config.json`, plans, survival guides, or prompt text.
- Refer to environment variable names only.
- Do not paste secrets into role prompts.
- If provider authentication fails during an Elves run, record the failure and continue with native
  subagents unless the provider was explicitly required for the task.

## Run Cobbler Logging

When Cobbler is used inside an active Elves run, record only material outcomes in existing memory
surfaces:

- execution log: decision, dissent, evidence, and next action when it changes the plan or risk
  picture;
- survival guide: only live run-control or next-batch changes;
- `.elves-session.json`: machine-readable status changes or review dispositions when needed;
- learnings: stable, reusable lessons only after they prove durable.

Do not create a separate Council ledger for ordinary software work.
