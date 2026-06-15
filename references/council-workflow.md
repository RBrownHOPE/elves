# Cobbler Workflow (Council Compatibility)

Cobbler is the lightweight chat-native coordinator inside Elves. The user asks once, and Cobbler
decides whether to answer directly, bring in a few specialist elves, or convene a temporary
read-only council of independent lenses before returning one fitted answer.

This file keeps its `council-workflow.md` name for `v1.14.0` compatibility. In `v1.15.0+`, Cobbler
is the user-facing coordinator. Council is the compatibility path and gathering mechanism, not a
separate product.

## Invocation Semantics

Host invocation depends on the agent surface:

- Claude Code primary: `/cobbler <task>`.
- Codex primary: `$elves cobbler: <task>` or natural language such as "Ask the Cobbler..."
- Claude Code compatibility: `/council <task>`, `/ec <task>`, and `/elves-council <task>`.
- Codex compatibility: `$elves council: <task>` and natural Council references.

Do not document Codex as having a top-level `/cobbler`, `/council`, `/ec`, or `/elves-council`
command unless the user's Codex install explicitly provides one.

These are documentation semantics for agent behavior. This repo does not ship a hosted parser or
runtime for slash commands.

## Modes

### Quick Cobbler

Quick Cobbler is the default. It is read-only and stateless unless the user explicitly asks to
attach the result to an active Elves run. Use native subagents first:

- Codex subagents in Codex;
- Claude Code subagents in Claude Code;
- the same read-only analysis directly when subagents are unavailable.

Quick Cobbler should not edit files, create branches, open PRs, install packages, or mutate run
state. It inspects, thinks, and recommends.

### Run Cobbler

Run Cobbler is Quick Cobbler inside an existing Elves run. It follows the same read-only default,
but the coordinator may record the synthesized result in the execution log, survival guide, or
`.elves-session.json` when it materially changes the run plan, risk picture, or review queue.

Run Cobbler reuses existing Elves memory surfaces. Do not create a separate council ledger for
ordinary software work.

Record only material outcomes:

- execution log: Cobbler recommendation, strongest dissent, evidence, and next action when it
  changes the plan, risk picture, or review queue;
- survival guide: live run-control, active compute, Stop Gate, or next-batch changes only;
- `.elves-session.json`: machine-readable batch status, continuation guard, or review-comment
  dispositions when needed;
- learnings: stable reusable lessons only after they are no longer just one-off run context.

If the Cobbler answer does not change the run, do not log it just to create ceremony.

### Provider-Backed Council

Provider-backed council is optional. It may use configured external providers for broader model
diversity, but normal Cobbler, `/council`, `/ec`, `/elves-council`, and `$elves council: <task>`
use must not require OpenRouter or any external provider key. If provider-backed council is
requested and providers are unconfigured, degrade gracefully to native-subagent Quick Cobbler and
say what was unavailable.

See [`council-provider-config.md`](council-provider-config.md) for optional provider setup.

Optional model routing is role-scoped. A configured route such as `openrouter:<model-id>` is a hint
for one lens, not a new Cobbler mode and not proof that the routed model is right. If a configured
route cannot run, fall back to native subagents or direct read-only analysis and mention the
fallback in the fitted answer. During synthesis, resolve disagreement by evidence, repo facts,
tests, sources, and user constraints rather than model prestige.

## Coordinator Flow

```text
User question
  -> classify task
  -> decide direct answer vs independent lenses
  -> choose two or three roles when lenses help
  -> spawn independent read-only role agents
  -> collect bounded reports
  -> synthesize one fitted answer
  -> optionally log the result if this is Run Cobbler
```

For small questions, use two roles. For design, migration, release, or ambiguous risk questions,
use three. Use more only when the user asks or the problem genuinely needs domain breadth.

## Role Selection

Default role pool:

- `architect`: boundaries, coupling, architecture, long-term shape.
- `skeptic`: assumptions, regressions, security/product risks, failure modes.
- `implementation_analyst`: concrete files, sequencing, integration details.
- `tester`: validation, regression proof, observability, missing tests.
- `maintainer`: repo conventions, documentation, future-agent readability.
- `domain_scout`: unfamiliar domain, source discovery, adjacent techniques.

Useful defaults:

- Short/simple task: `implementation_analyst`, `skeptic`.
- Design/refactor/planning task: `architect`, `skeptic`, `maintainer`.
- Bug/debug task: `implementation_analyst`, `tester`, `skeptic`.
- Test/validation task: `tester`, `implementation_analyst`, `maintainer`.
- Docs/process task: `maintainer`, `skeptic`, `architect`.
- Research-heavy task: `domain_scout`, `skeptic`, plus the most relevant implementation or
  architecture role.

Explicit `--roles` overrides these heuristics.

## Independence Invariant

Role agents do not see each other's reports before synthesis. Give every role the same user
question, relevant context, and constraints, then collect bounded reports independently. This keeps
the council from turning into an echo chamber.

The coordinator may share the final synthesis back to the user, and may show individual reports
only when the user asks for `--verbose`, `--show-reports`, or `--json`.

## Role Report Shape

Each role returns a bounded report:

```yaml
role:
verdict:
confidence:
key_findings:
evidence:
risks:
recommended_actions:
open_questions:
```

Keep reports brief. Evidence should cite files, commands, PR comments, docs, or source material
when available. If a role lacks enough context, it should say so directly.

## Fitted Answer Shape

The synthesizer returns one user-facing answer:

```text
Recommendation
Why this fits
Strongest dissent
Risks
Next move
Confidence
```

The recommendation comes first and takes a position. The strongest dissent is not a vote tally; it
is the objection, uncertainty, or verification gap that should affect the user's decision. The
`Next move` should be concrete enough for a user to act on without reading raw role reports.

For `--json`, use stable structured keys, but keep the default human-facing headings above.

## Non-Goals

- Do not copy vendor identity, policy, persona, or safety framing.
- Do not make Quick Cobbler require OpenRouter, `OPENROUTER_API_KEY`, or any external provider.
- Do not let Quick Cobbler edit files or mutate run state.
- Do not create a separate PR, branch, survival guide, execution log, or ledger for ordinary
  council calls.
- Do not run multi-turn debate by default. Role agents work independently; the synthesizer
  reconciles them.
- Do not dump raw reports by default. Return one synthesized recommendation unless the user asks
  for verbose output.

## Done Criteria

A Cobbler response is useful when it:

- answers the user's actual question;
- names the aliases or mode only when relevant;
- shows one recommendation before caveats;
- preserves the strongest dissent or verification gap;
- gives a concrete next move;
- stays read-only unless the user explicitly asked for implementation;
- records material Run Cobbler decisions in existing Elves memory surfaces.
