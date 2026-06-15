# Plan: Full-Run Model Routing

## Mission

Add an optional design for phase-aware model routing across full Elves runs. Cobbler already gives
users a natural chat-native coordinator for one-off questions; this follow-up makes longer runs
able to record which kind of model or agent should handle implementation, validation, review,
scouting, and synthesis without making OpenRouter or any external provider required.

Done means a future implementation can add explicit routing preferences to the survival guide,
config examples, and review prompts while preserving host honesty: Codex and Claude Code use their
native subagents first, provider-backed model diversity is optional, and missing provider access
falls back gracefully instead of stalling the run.

## Product Shape

### Core Idea

Cobbler is the coordinator. The elves are the workers. A full Elves run can record phase
preferences in Cobbler language:

- implementation wants the strongest host-native coding model available;
- validation wants a reliable, lower-cost checker that can run commands and inspect results;
- review wants an independent lens with different blind spots from the implementer;
- scouting wants breadth, speed, and source awareness;
- synthesis wants the coordinator to reconcile evidence, dissent, and next actions.

This is routing metadata, not a magic model switch. If the host cannot programmatically choose a
model for a subagent, Elves records the preference and asks the available native agent to follow
the phase contract. If a provider-backed council is configured, Elves may use it for read-only
review, scouting, or synthesis roles. Ordinary Elves runs must keep working without external keys.

### Host Semantics

- **Claude Code:** use Claude Code subagents and whatever model-selection controls the user's
  Claude Code install exposes. Do not promise model choices beyond that host capability.
- **Codex:** use Codex subagents when available and Codex Goals only as optional continuation
  plumbing. Do not promise a universal top-level slash command or arbitrary model selection unless
  the user's Codex install exposes it.
- **Provider-backed roles:** optional, read-only by default, and useful mainly for model diversity
  in review, scouting, and synthesis. Do not route implementation to an external provider unless
  the user explicitly opted into a write-capable workflow with minimum constraints: a dedicated
  branch or worktree, no direct secret access, provider output limited to patches or reports unless
  a trusted local host applies changes, and mandatory native validation plus review before any push.
- **No provider access:** fall back to host-native subagents or direct read-only analysis, and log
  the degraded route only when it changes risk or confidence.

### Draft Survival Guide Shape

Use simple phase preferences first:

```yaml
model-routing:
  enabled: true
  policy: native-first
  fallback: host-native
  phases:
    implement:
      preference: strongest-host-native
      provider-backed-allowed: false
      notes: "Use the main coding agent or a host-native implementation subagent."
    validate:
      preference: reliable-host-native
      provider-backed-allowed: false
      notes: "Run commands locally; prioritize reproducible evidence over model diversity."
    review:
      preference: independent-lens
      provider-backed-allowed: true
      notes: "Prefer a fresh model or subagent when available."
    scout:
      preference: broad-fast-lens
      provider-backed-allowed: true
      notes: "Use optional external providers for breadth only when configured."
    synthesize:
      preference: coordinator
      provider-backed-allowed: true
      notes: "Return one fitted answer with dissent and next action."
```

For users who want terse knobs, support aliases in documentation:

```yaml
implement-model: strongest-host-native
validate-model: reliable-host-native
review-model: independent-lens
scout-model: broad-fast-lens
synthesize-model: coordinator
```

These aliases should expand to the structured block during staging or be interpreted as
compatibility sugar by the agent. They should not be treated as literal provider model IDs unless
the project also declares a provider namespace such as `openrouter:<model-id>`.

Use hyphenated keys in survival-guide YAML examples and underscored keys in JSON examples such as
`config.json.example`.

## Scope

### In Scope

- Define the model-routing contract for full Elves runs.
- Distinguish Quick Cobbler role selection from full-run phase routing.
- Specify host-native defaults for Codex and Claude Code.
- Specify optional provider-backed routing for read-only review, scouting, and synthesis.
- Define fallback and logging behavior when the requested route is unavailable.
- Plan the future docs, config, prompts, and consistency checks needed to implement this safely.

### Out of Scope

- Implementing a provider router in this branch.
- Adding new runtime dependencies.
- Requiring OpenRouter, Anthropic, Gemini, xAI, OpenAI, or any other provider key for ordinary
  Elves or Cobbler use.
- Claiming Codex or Claude Code can select arbitrary models unless the host actually exposes that
  capability.
- Routing write-capable implementation work to external providers by default.
- Copying vendor identity, vendor policy, or prompt-persona framing from any external system.

## Batches

### Batch 1: Routing Contract and User Wording

**Tasks:**
- [ ] Add a concise full-run model-routing section to `SKILL.md` and `AGENTS.md`.
- [ ] Update README with a short user-facing explanation: "the Cobbler can prefer different elves
      for different phases."
- [ ] Keep Quick Cobbler docs separate: one-off Cobbler answers choose roles; full Elves runs
      choose phase preferences.
- [ ] Add survival-guide examples for both structured `model-routing` and terse `*-model` aliases.

**Acceptance criteria:**
- [ ] A user can tell where to write phase preferences before launching a run.
- [ ] Docs state that routing preferences are advisory unless the host/provider exposes actual
      model selection.
- [ ] Normal Elves and Cobbler use still require no external provider key.
- [ ] The wording is Cobbler-first and does not reintroduce Council as a competing product.

**Docs likely touched:**
- `SKILL.md`
- `AGENTS.md`
- `README.md`
- `references/survival-guide-template.md`
- `references/council-workflow.md` only for the Quick Cobbler distinction

**Risk:** The feature can sound more capable than the host surfaces really are. Keep every claim
tied to an available host capability or clearly mark it as a preference.

### Batch 2: Config and Prompt Integration

**Tasks:**
- [ ] Add optional `model_routing` examples to `config.json.example`.
- [ ] Extend `references/tool-config-examples.md` with provider namespace examples such as
      `native-subagent`, `host-default`, and `openrouter:<model-id>`.
- [ ] Extend `references/review-subagent.md` so review prompts include the current phase route,
      fallback route, and evidence requirements.
- [ ] Keep `references/council-prompts.md` Quick-Cobbler-only or add an explicit guardrail that
      phase routing belongs to full Elves runs, not the one-off Cobbler role selector.
- [ ] Extend `references/council-provider-config.md` to explain how provider-backed council slots
      can satisfy read-only full-run routing roles.

**Acceptance criteria:**
- [ ] Config examples keep provider-backed required env lists empty by default.
- [ ] Provider-backed roles are explicitly optional and mostly read-only.
- [ ] Prompts instruct agents to report when they could not satisfy a requested route.
- [ ] No secret, token, or private model key appears in docs or examples.
- [ ] External provider routes receive only minimum necessary context and are disabled for
      private or sensitive projects unless the user explicitly opts in.

**Docs likely touched:**
- `config.json.example`
- `references/tool-config-examples.md`
- `references/review-subagent.md`
- `references/council-prompts.md`
- `references/council-provider-config.md`

**Risk:** Config can become too verbose for the happy path. Keep examples progressive: default
native-first first, provider-backed examples later.

### Batch 3: Staging and Run Memory

**Tasks:**
- [ ] Add staging guidance: copy model-routing preferences from the plan into the survival guide's
      `Run Control` block.
- [ ] Add `.elves-session.json` fields for requested route, actual route, and fallback reason per
      batch or role when routing materially changes the risk picture.
- [ ] Update execution-log contract guidance so each batch can name its phase routing assumption.
- [ ] Define when routing mismatches are warnings versus blockers.

**Acceptance criteria:**
- [ ] A resumed agent can recover the requested and actual route without reading the full chat.
- [ ] Missing optional provider access never blocks a native-first run.
- [ ] A route mismatch becomes blocking only when the user marked that phase/provider as required.
- [ ] `required: true` is accepted only as an explicit per-project survival-guide opt-in. It is
      never a Quick Cobbler default, never inferred from provider config, and downgraded or rejected
      for ordinary native-first runs when the user did not explicitly set it.
- [ ] Logs record material routing degradation without creating noisy ceremony for every subagent.

**Docs likely touched:**
- `references/survival-guide-template.md`
- `references/execution-log-template.md`
- `.elves-session.json` examples in `SKILL.md` and `AGENTS.md`
- `references/open-ended-guide.md` if continuation guidance needs a route field

**Risk:** Over-logging can make strategic forgetting worse. Store only requested route, actual
route, and material fallback reason.

### Batch 4: Consistency Checks and Tests

**Tasks:**
- [ ] Add consistency checks that protect the native-first/no-required-provider invariants.
- [ ] Add tests for model-routing docs, config defaults, and host-honesty phrasing.
- [ ] Ensure the checker distinguishes math workflow provider policy from Cobbler and full-run
      model-routing policy.
- [ ] Run the full repo validation set.

**Acceptance criteria:**
- [ ] `python3 scripts/check_repo_consistency.py` passes.
- [ ] `python3 -m unittest discover -s tests -p 'test_*.py'` passes.
- [ ] `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py` passes.
- [ ] `python3 -m json.tool config.json.example >/dev/null` passes.
- [ ] `git diff --check` passes.
- [ ] The checker rejects docs that make OpenRouter required for ordinary full-run routing.

**Docs likely touched:**
- `scripts/check_repo_consistency.py`
- `tests/test_check_repo_consistency.py`
- Any docs changed in earlier batches

**Risk:** The checker can accidentally flag the math module, where OpenRouter remains the baseline
provider. Tests must pin the distinction.

## Non-Negotiables

- User experience comes first: users should set a few clear preferences, not operate a provider
  router.
- Native host capability is the default for Codex and Claude Code.
- Normal Elves, Quick Cobbler, and compatibility aliases must not require external provider keys.
- Provider-backed routing is optional and read-only by default unless the user explicitly configures
  a write-capable workflow with dedicated branch/worktree isolation, no direct secret access,
  patch/report handoff, and mandatory native validation plus review.
- Routing preferences must degrade gracefully and be logged honestly.
- `SKILL.md` and `AGENTS.md` must move together for behavior changes.

## Test Strategy

- **Primary consistency gate:** `python3 scripts/check_repo_consistency.py`
- **Unit tests:** `python3 -m unittest discover -s tests -p 'test_*.py'`
- **Script compile gate:** `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py`
- **JSON validation:** `python3 -m json.tool config.json.example >/dev/null`
- **Whitespace gate:** `git diff --check`
- **Review loop:** after every push, read PR comments, review threads, and checks; fix blockers
  before continuing.

## Future Implementation Notes

- Treat phase preferences as a staging-time contract, not as a hidden runtime guarantee.
- Prefer role labels like `strongest-host-native`, `reliable-host-native`, and `independent-lens`
  over brittle provider-specific model names in public docs.
- Allow provider namespaces only when explicitly configured: `native-subagent`, `host-default`,
  `codex:<host-option>`, `claude-code:<host-option>`, `openrouter:<model-id>`,
  `gemini:<model-id>`, `anthropic:<model-id>`, `xai:<model-id>`, or `openai:<model-id>`.
- Send external provider-backed roles only the minimum necessary context. Never include secrets,
  tokens, credentials, customer data, or private repository context unless the user explicitly
  opted that project into external-provider sharing.
- When a requested route cannot be satisfied, use the fallback route, record the reason if
  material, and continue unless the user explicitly marked the route `required: true` in the
  survival guide.
- Keep implementation work host-native by default because it mutates the checkout. External
  provider-backed roles should normally inspect, critique, or synthesize rather than edit.

## Notes

- This plan depends conceptually on the Cobbler release because it uses Cobbler as the coordinator
  language. It should land after the Cobbler docs are on `main`.
- This plan is intentionally a design note only. It is useful as a draft PR for review before
  touching the canonical skill, config, and consistency-check surfaces.
- The math workflow remains different: it can be OpenRouter-first because mathematical research
  workflows explicitly configure provider roles. Full-run software-development routing is
  native-first and provider-optional.
