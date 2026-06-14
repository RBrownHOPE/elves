# Council Provider Configuration

Elves Council is native-subagent-first. Configure providers only when the user wants optional Deep
Council model diversity. Normal `/council`, `/ec`, and `/elves-council` use must work without
OpenRouter or any external provider key.

## Default Quick Council

Quick Council needs no provider configuration:

```yaml
council-enabled: true
council-default-mode: quick
council-default-backend: native-subagents
council-aliases:
  - /council
  - /ec
  - /elves-council
council-default-role-count: 3
council-max-role-count: 5
council-quick-read-only: true
council-quick-stateless: true
council-run-logging: existing-elves-memory
```

In Codex, use Codex subagents when available. In Claude Code, use Claude Code subagents when
available. If subagents are unavailable, perform the same read-only analysis directly.

## Optional Deep Council

Deep Council is opt-in. It may route selected roles to external providers when the user has
configured keys and wants broader model diversity:

```yaml
council-deep-enabled: false
council-deep-provider-policy: optional-external-providers
council-deep-required-env: []
council-deep-optional-env:
  - OPENROUTER_API_KEY
  - GEMINI_API_KEY
  - ANTHROPIC_API_KEY
  - XAI_API_KEY
  - OPENAI_API_KEY
council-deep-role-models:
  architect: native-subagent
  skeptic: native-subagent
  implementation_analyst: native-subagent
  tester: native-subagent
  maintainer: native-subagent
  domain_scout: native-subagent
```

Leave `council-deep-required-env` empty unless a specific project intentionally makes Deep Council
a required workflow. Do not make ordinary `/council` depend on that setting.

## Fallback Policy

If the user requests `--deep` and external providers are not configured:

1. Say Deep Council providers are unavailable.
2. Fall back to native-subagent Quick Council.
3. Preserve the user's requested roles where possible.
4. Do not ask for keys mid-run unless the user explicitly wants provider setup.
5. Do not search local files for secrets.

## Secret Handling

- Never hardcode provider keys in `config.json`, plans, survival guides, or prompt text.
- Refer to environment variable names only.
- Do not paste secrets into role prompts.
- If provider authentication fails during an Elves run, record the failure and continue with native
  subagents unless the provider was explicitly required for the task.

## Run Council Logging

When Council is used inside an active Elves run, record only material outcomes in existing memory
surfaces:

- execution log: decision, dissent, evidence, and next action when it changes the plan or risk
  picture;
- survival guide: only live run-control or next-batch changes;
- `.elves-session.json`: machine-readable status changes or review dispositions when needed;
- learnings: stable, reusable lessons only after they prove durable.

Do not create a separate Council ledger for ordinary software work.
