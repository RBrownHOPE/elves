# How Cobbler works

![How Cobbler works](../assets/cobbler-infographic.png)

Cobbler is the coordinator inside Elves. It is not a separate model, daemon, or runtime. It is how
Elves decides what to do before it acts.

For simple requests, Cobbler answers directly. For uncertain work, it asks a few independent
reviewers or workers for bounded input, weighs the evidence, keeps the strongest objection visible,
and gives the user one recommendation. During full Elves runs, this happens inside the normal batch
loop.

## The short version

You ask once. Cobbler classifies the request, checks what help is available, sends the right elves
to inspect or work, weighs the evidence, and returns one answer.

For a normal chat question, that answer is the result. For an active Elves run, Cobbler may also
record the decision in the existing run memory.

## The coordination hierarchy

Cobbler sits inside Elves, not beside it:

- **Elves** handles execution: plans, branches, PRs, validation, review, memory, and landing.
- **Cobbler** handles coordination: intent, routing, context, evidence, dissent, medium, and fitted
  answer.
- **Domain workflows** handle specialized work under Cobbler.
- **Providers** are optional role routes.

Math is the first domain workflow. When a task is mathematical, Cobbler routes into the math
workflow: Discovery Sprint, scout lanes, proof critics, derivation checkers, source auditors,
artifact ledgers, and human verification. The math ledgers are domain evidence ledgers, not a
separate Cobbler or Council memory system.

## The handling paths

Cobbler has three handling paths and one sticky setting.

1. Direct answer

   Cobbler answers directly when extra agents would add noise.

2. One-off Cobbler

   One-off Cobbler is read-only and stateless. It can inspect files, docs, tests, PR comments, or
   other evidence, but it does not edit files or update run state. Internally, this is the same
   behavior older docs call Quick Cobbler.

3. Cobbler inside an Elves run

   This is the default coordination pattern for staged and active Elves runs. The coordinator owns
   git, PRs, durable memory, and final synthesis. Worker agents may edit the repo when the active
   batch or user request gives them scoped implementation work.
   After Elves is invoked for a real run, record `## Cobbler Session State` in the survival guide
   and `cobbler.default_for_session` in `.elves-session.json` so compaction preserves the
   Cobbler-first posture.

4. Cobbler Mode

   Cobbler Mode is a current-thread setting. It lets the user keep chatting with Cobbler without
   typing the invocation each time. It does not create a branch, PR, survival guide, execution log,
   Codex Goal, provider route, or config entry.

## The harness loop

The Cobbler harness loop is the part borrowed from Fable-style harness engineering, adapted to
Elves.

1. Intent

   Read the user's request and decide what kind of work it is: direct answer, one-off advice,
   implementation, review, release, research, or an active Elves run decision.

2. Capability scan

   This capability scan checks what can actually help before answering: repo docs, run memory,
   available skills, host subagents, tools, tests, PR checks, source material, and optional
   configured provider routes.

3. Route and medium selection

   Choose the handling path and the output medium. The medium may be an inline answer, a file edit,
   a PR comment, an execution-log entry, a `.elves-session.json` update, an Elves Report, or another
   user-visible artifact.

4. Context packet

   Give every role the task, mode, scope, constraints, relevant files, run-state pointers, source
   freshness needs, available tools or skills, and forbidden actions. Do not include secrets,
   credentials, cookies, or tokens.

5. Execute agents/tools/skills

   Use direct analysis, host-native subagents, scoped worker agents, skills, tools, tests, source
   checks, or optional configured provider routes. Read-only lenses stay read-only. Workers edit
   only inside their assigned scope.

6. Collect evidence

   Assemble facts, file references, command output, tests, PR comments, source links, changed files,
   risks, and dissent. Separate retrieved evidence from inference.

7. Fit answer

   Return one recommendation, not a pile of role reports. The default shape is Recommendation, Why
   this fits, Strongest dissent, Risks, Next move, and Confidence.

8. Present/record

   Present the answer to the user. If the result changes an active Elves run, record only the
   material decision in the existing run memory.

9. Reclassify

   If the evidence changes the task, route again. A one-off answer can become Run Cobbler. A review
   can become implementation. A release can become a blocker. Cobbler should not force the first
   route after new facts arrive.

## What the elves do

Elves are agents or analysis roles chosen for the task. Some are read-only lenses. Some are workers.

Read-only lenses are useful for architecture, risk, review, testing strategy, and source checks.
They do not edit files.

Workers are useful during implementation. They can edit files, but only within the scope given by
the coordinator. The coordinator owns the final answer and the repo-level actions.

## How to use it

In Claude Code:

```text
/cobbler should we refactor this or patch it?
/cobbler-mode
/setup-cobbler
/setup-council
```

In Codex:

```text
$elves cobbler: should we refactor this or patch it?
$elves cobbler-mode
$elves setup-cobbler
$elves setup-council
```

Natural language also works:

```text
Ask the Cobbler to audit this plan.
Cobbler Mode: on
Cobbler Mode: off
Set up Cobbler external-agent preferences.
```

Codex does not get the Claude Code slash aliases. Use `$elves cobbler: ...`,
`$elves setup-cobbler`, or ask naturally. Do not document a top-level Codex `/setup-cobbler`
slash command.

Legacy Council aliases still work and now route to Cobbler. Claude Code supports `/council`, `/ec`,
and `/elves-council`. Codex supports `$elves council: <task>` and natural Council references.

## External-agent setup

Setup is optional. Native-only Elves needs no external tools or keys.

```bash
python3 scripts/cobbler_agents.py setup --json --dry-run
python3 scripts/cobbler_agents.py setup --json --implement grok-build --review claude-code
```

Setup inventories tools without printing credentials, writes only ignored local
`.elves/models.toml` (Never stage it), and never pastes API keys into TOML/chat/Survival Guide.
Host coordinators should snapshot effective routes into the Survival Guide during staging.
Recipes for Claude-only, Grok-only, Sakana-only, custom wrappers, OpenRouter breadth, and API-only
models live in `references/cobbler-setup-recipes.md`. Commit/push/PR remain host operations, not
model roles. `remaining_quota` stays unknown unless a harness explicitly exposes it.

## Provider routing

Cobbler does not need OpenRouter or any external provider key. The default route is the host's own
agent system: Codex subagents in Codex, Claude Code subagents in Claude Code, or direct analysis
when subagents are not available.

External providers are optional. They can be used for selected read-only roles when configured, but
they are another source of evidence, not authority.

## What Cobbler is not

Cobbler is not a daemon. Cobbler Mode lasts only for the current thread.

Cobbler is not a top-level Codex slash command unless a user's Codex install explicitly provides
one.

Cobbler is not a separate ledger. Run decisions go into the normal Elves memory files.

Cobbler is not a license for agents to edit everything. One-off Cobbler stays read-only. Cobbler
inside an Elves run allows worker edits only when the work is scoped.

## Inspiration and credit

Cobbler was inspired by the harness engineering ideas in
[Claude Fable 5](https://github.com/elder-plinius/CL4R1T4S/blob/main/ANTHROPIC/CLAUDE-FABLE-5.md),
a system prompt extracted by Pliny the Prompter in the CL4R1T4S archive.

The part Cobbler borrows is the coordination pattern: route a request through available
capabilities, preserve dissent, assemble evidence, choose the right medium, and fit one answer back
to the user. Cobbler does not copy Fable's model identity, persona, policy text, or safety
guardrails.

## CouncilElves launch

See [`references/councilelves-launch-prompt.md`](../references/councilelves-launch-prompt.md) for the
plan→implement→review loop overview (two lanes: `implementation_lane: fast | untrusted`). For the
default “have Grok run it” path, use
[`references/grok-implementer-launch-prompt.md`](../references/grok-implementer-launch-prompt.md) and
`python3 scripts/cobbler_agents.py implement …`. Design:
[`docs/plans/smart-plan-grok-implement.md`](plans/smart-plan-grok-implement.md).
