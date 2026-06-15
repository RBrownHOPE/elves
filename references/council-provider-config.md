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
```

In Codex, use Codex subagents when available. In Claude Code, use Claude Code subagents when
available. If subagents are unavailable, perform the same read-only analysis directly.

## Optional Provider-Backed Council

Provider-backed council is opt-in. It may route selected roles to external providers when the user
has configured keys and wants broader model diversity:

```yaml
cobbler-provider-backed-enabled: false
cobbler-provider-backed-policy: optional-external-providers
cobbler-provider-backed-required-env: []
cobbler-provider-backed-optional-env:
  - OPENROUTER_API_KEY
  - GEMINI_API_KEY
  - ANTHROPIC_API_KEY
  - XAI_API_KEY
  - OPENAI_API_KEY
cobbler-provider-backed-role-models:
  architect: native-subagent
  skeptic: native-subagent
  implementation_analyst: native-subagent
  tester: native-subagent
  maintainer: native-subagent
  domain_scout: native-subagent
```

Leave `cobbler-provider-backed-required-env` empty unless a specific project intentionally makes
provider-backed council a required workflow. Do not make ordinary Cobbler or Council-compatible use
depend on that setting.

## Full-Run Phase Routes

Provider-backed council slots may satisfy read-only full-run model-routing phases such as review,
scout, or synthesize when the user has enabled providers and the context is safe to share.
Do not make implementation provider-backed by default. Implementation and validation mutate or
inspect the local checkout, so they stay host-native unless the survival guide explicitly opts into a
write-capable external workflow with branch/worktree isolation, no secret exposure, patch/report
handoff, and mandatory native validation plus review.

When a requested full-run route cannot run, fall back to host-native work and record the requested
route, actual route, and fallback reason only if it changes risk or confidence. A route mismatch is
blocking only when the survival guide explicitly marks that phase `required: true`.

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
