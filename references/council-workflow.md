# Elves Council Workflow

Elves Council is a lightweight chat workflow for questions that need several independent lenses
before one answer. It is not an Elves run, not a command runtime, and not a parallel memory system.
Use it for planning, design, debugging, review, and risk analysis when a single straight-line answer
would be too brittle.

The natural aliases are `/council`, `/ec`, and `/elves-council`.

## Modes

### Quick Council

Quick Council is the default. It is read-only and stateless unless the user explicitly asks to
attach the result to an active Elves run. Use native subagents first:

- Codex subagents in Codex;
- Claude Code subagents in Claude Code;
- the same read-only analysis directly when subagents are unavailable.

Quick Council should not edit files, create branches, open PRs, install packages, or mutate run
state. It inspects, thinks, and recommends.

### Run Council

Run Council is Quick Council inside an existing Elves run. It follows the same read-only default,
but the coordinator may record the synthesized result in the execution log, survival guide, or
`.elves-session.json` when it materially changes the run plan, risk picture, or review queue.

Run Council reuses existing Elves memory surfaces. Do not create a separate council ledger for
ordinary software work.

### Deep Council

Deep Council is optional. It may use configured external providers for broader model diversity, but
normal `/council` must not require OpenRouter or any external provider key. If Deep Council is
requested and providers are unconfigured, degrade gracefully to native-subagent Quick Council and
say what was unavailable.

## Invocation Semantics

Document these semantics when a host supports slash-command style use:

- `/council <task>`: Quick Council, native/read-only/default.
- `/ec <task>`: short alias for Quick Council.
- `/elves-council <task>`: explicit alias.
- `--brief`: two-role fast pass.
- `--verbose` or `--show-reports`: include individual role reports.
- `--json`: return the structured synthesis.
- `--roles a,b,c`: override role selection.
- `--n N`: cap role count.
- `--run`: attach the result to an active Elves run log.
- `--deep`: optional external-provider mode when configured.

These are documentation semantics for agent behavior. This repo does not ship a hosted parser or
runtime for slash commands.

## Coordinator Flow

```text
User question
  -> classify task
  -> choose two or three roles
  -> spawn independent read-only role agents
  -> collect bounded reports
  -> synthesize one answer
  -> optionally log the result if this is Run Council
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

## Synthesis Shape

The synthesizer returns one answer:

```yaml
recommendation:
why:
dissent:
risks:
next_actions:
confidence:
action_contract:
```

The recommendation comes first and takes a position. Dissent is not a vote tally; it is the
strongest objection, uncertainty, or verification gap that should affect the user's decision.

The `action_contract` is optional. Include it when the user is likely to turn the recommendation
into implementation work. It should state what to do, what not to do, and how to verify it.

## Non-Goals

- Do not copy vendor identity, policy, persona, or safety framing.
- Do not make Quick Council require OpenRouter, `OPENROUTER_API_KEY`, or any external provider.
- Do not let Quick Council edit files or mutate run state.
- Do not create a separate PR, branch, survival guide, execution log, or ledger for ordinary
  council calls.
- Do not run multi-turn debate by default. Role agents work independently; the synthesizer
  reconciles them.
- Do not dump raw reports by default. Return one synthesized recommendation unless the user asks
  for verbose output.

## Done Criteria

A Council response is useful when it:

- answers the user's actual question;
- names the aliases or mode only when relevant;
- shows one recommendation before caveats;
- preserves the strongest dissent or verification gap;
- gives concrete next actions;
- stays read-only unless the user explicitly asked for implementation;
- records material Run Council decisions in existing Elves memory surfaces.
