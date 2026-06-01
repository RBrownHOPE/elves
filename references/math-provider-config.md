# Math Provider Configuration

The math workflow is provider-configurable. V1 assumes no custom service and no fixed model list.
Configure roles, not model names. OpenRouter is the minimum useful setup; native providers and
search tools are optional upgrades.

## Baseline Policy

Use OpenRouter first when no richer local setup exists:

- it gives users broad model access through one key;
- it avoids hardcoding one vendor into Elves;
- it lets a user swap models as prices, quotas, and capabilities change.

Native providers are optional upgrades. Use them when the user has keys, spend, or a model-specific
reason:

- `GEMINI_API_KEY` for native Gemini calls;
- `ANTHROPIC_API_KEY` for native Claude calls;
- `XAI_API_KEY` for native xAI/Grok calls;
- `OPENAI_API_KEY` for native OpenAI calls;
- `EXA_API_KEY` for source discovery and literature search.

If a native provider fails or quota is exhausted, fall back to OpenRouter only when the user has
enabled that fallback in the run configuration. Do not silently switch providers for an expensive
role without recording it in the model-call ledger.

## Role Slots

These role names are stable. The model assignments are user-editable.

| Role | Job | Default provider policy |
|---|---|---|
| `subfield_scout` | Explore one mathematical lane and identify known work, transferable techniques, and quick wins. | OpenRouter |
| `cross_field_synthesizer` | Combine scout reports and look for translations between fields. | OpenRouter or strongest native model |
| `proof_critic` | Attack candidate theorem statements and proof sketches. | Strongest available reasoning model |
| `derivation_checker` | Check algebra, estimates, asymptotics, constants, and edge cases. | Strongest available reasoning model |
| `source_auditor` | Verify references, hypotheses, citation use, and primary-source grounding. | OpenRouter plus search tools |
| `exposition_editor` | Improve mathematical prose without changing claim status. | Cost-effective writing model |
| `formalization_scout` | Assess theorem-statement hygiene and possible Lean/Coq/Isabelle entry points. | OpenRouter or local expert model |

## Minimal Environment

```bash
export OPENROUTER_API_KEY=...
```

This is enough for the baseline workflow. A user may add optional native keys:

```bash
export GEMINI_API_KEY=...
export ANTHROPIC_API_KEY=...
export XAI_API_KEY=...
export OPENAI_API_KEY=...
export EXA_API_KEY=...
```

Never write these keys into the survival guide, plan, prompt files, execution log, or source tree.

## Survival Guide Block

Copy this into `## Tool Configuration` or a nearby `## Math Configuration` section:

```yaml
math-provider-policy: openrouter-first
math-required-env:
  - OPENROUTER_API_KEY
math-optional-env:
  - GEMINI_API_KEY
  - ANTHROPIC_API_KEY
  - XAI_API_KEY
  - OPENAI_API_KEY
  - EXA_API_KEY
math-role-models:
  subfield_scout: openrouter:<model-id>
  cross_field_synthesizer: openrouter:<model-id>
  proof_critic: openrouter:<model-id>
  derivation_checker: openrouter:<model-id>
  source_auditor: openrouter:<model-id>
  exposition_editor: openrouter:<model-id>
  formalization_scout: openrouter:<model-id>
math-native-overrides:
  # proof_critic: gemini:<model-id>
  # derivation_checker: anthropic:<model-id>
  # source_search: exa
math-fallback-policy: record-before-switching-provider
math-ledger-dir: docs/math
```

Use concrete model IDs in the project survival guide, not in Elves defaults. Prices and model
names change; the role slots should remain stable.

## Provider Discipline

- Record every material model call in `docs/math/model-calls.md` or the configured ledger path.
- Record provider, model, role, prompt path, source context, result summary, and action taken.
- Use source search for literature discovery, not as proof.
- Ask at least two independent roles to check any central proof step.
- Prefer different model families for idea generation and adversarial review when available.
- Keep expensive native calls for high-leverage proof criticism, derivation checks, and synthesis.
