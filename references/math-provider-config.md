# Math Provider Configuration

Math provider routing is a Cobbler-managed domain-workflow setting. Configure roles, not model
names. The default fallback is host-native subagents or direct analysis. External providers are
optional role routes that add breadth or a stronger specialized lens when the user configures them.

Normal Cobbler, Quick Cobbler, and ordinary Elves runs do not require OpenRouter or any external
provider key. Math runs also should not require provider keys by default. A project survival guide
may explicitly make a provider required for a specific math run, but do not infer that requirement
from this template, from `openrouter:<model-id>` examples, or from the presence of math work.

## Baseline Policy

Use this default policy unless the user overrides it in the survival guide:

- start with host-native subagents or direct analysis;
- treat OpenRouter as an optional math role preset for broad model diversity;
- use native Gemini, Claude, xAI, OpenAI, Exa, or local tools when the user has configured keys,
  spend, or a model-specific reason;
- record material provider choices and fallbacks in the model-call ledger;
- never silently switch providers for an expensive role.

OpenRouter remains useful for math because it gives broad model access through one key. That is a
domain-specific route option, not a normal Cobbler dependency.

Native providers are optional upgrades:

- `GEMINI_API_KEY` for native Gemini calls;
- `ANTHROPIC_API_KEY` for native Claude calls;
- `XAI_API_KEY` for native xAI/Grok calls;
- `OPENAI_API_KEY` for native OpenAI calls;
- `EXA_API_KEY` for source discovery and literature search.

If a configured provider fails or quota is exhausted, fall back to host-native subagents or direct
analysis unless the survival guide names a different fallback. Record the fallback and confidence
impact before using the result.

## Role Slots

These role names are stable. The model assignments are user-editable.

| Role | Job | Default route |
|---|---|---|
| `subfield_scout` | Explore one mathematical lane and identify known work, transferable techniques, and quick wins. | native-subagent or direct-analysis |
| `cross_field_synthesizer` | Combine scout reports and look for translations between fields. | native-coordinator |
| `proof_critic` | Attack candidate theorem statements and proof sketches. | strongest configured reasoning route, else native-subagent |
| `derivation_checker` | Check algebra, estimates, asymptotics, constants, and edge cases. | strongest configured reasoning route, else native-subagent |
| `source_auditor` | Verify references, hypotheses, citation use, and primary-source grounding. | native-subagent plus search tools |
| `exposition_editor` | Improve mathematical prose without changing claim status. | cost-effective configured route, else native-subagent |
| `formalization_scout` | Assess theorem-statement hygiene and possible Lean/Coq/Isabelle entry points. | native-subagent or local expert tool |

Optional OpenRouter examples:

```yaml
math-role-models:
  subfield_scout: openrouter:<model-id>
  proof_critic: openrouter:<model-id>
  derivation_checker: openrouter:<model-id>
```

Use concrete model IDs in the project survival guide, not in Elves defaults. Prices and model names
change; the role slots should remain stable.

## Optional Environment

No provider key is required by this template.

```bash
# Optional broad model routing for math roles
export OPENROUTER_API_KEY=...

# Optional native/provider-specific routes
export GEMINI_API_KEY=...
export ANTHROPIC_API_KEY=...
export XAI_API_KEY=...
export OPENAI_API_KEY=...
export EXA_API_KEY=...
```

Never write these keys into the survival guide, plan, prompt files, execution log, ledgers, or
source tree.

## Survival Guide Block

Copy this into `## Tool Configuration` or a nearby `## Math Configuration` section:

```yaml
math-coordination: cobbler-managed-domain-workflow
math-provider-policy: native-first-with-optional-external-routes
math-required-env: []
math-optional-env:
  - OPENROUTER_API_KEY
  - GEMINI_API_KEY
  - ANTHROPIC_API_KEY
  - XAI_API_KEY
  - OPENAI_API_KEY
  - EXA_API_KEY
math-role-models:
  subfield_scout: native-subagent
  cross_field_synthesizer: native-coordinator
  proof_critic: native-subagent
  derivation_checker: native-subagent
  source_auditor: native-subagent
  exposition_editor: native-subagent
  formalization_scout: native-subagent
math-native-overrides:
  # proof_critic: gemini:<model-id>
  # derivation_checker: anthropic:<model-id>
  # source_search: exa
math-external-route-examples:
  # subfield_scout: openrouter:<model-id>
  # proof_critic: openrouter:<model-id>
math-fallback-policy: record-before-switching-provider
math-ledger-dir: docs/math
```

To require a provider for a particular math run, the user must write that requirement explicitly in
the project survival guide. Missing optional provider access never blocks ordinary Cobbler use or a
math Discovery Sprint.

## Provider Discipline

- Record every material model call in `docs/math/model-calls.md` or the configured ledger path.
- Record provider, model or route, role, prompt path, source context, result summary, and action
  taken.
- Use source search for literature discovery, not as proof.
- Ask at least two independent roles to check any central proof step.
- Prefer different model families for idea generation and adversarial review when configured and
  available.
- Keep expensive native or external calls for high-leverage proof criticism, derivation checks, and
  synthesis.
