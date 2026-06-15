# How Cobbler works

![How Cobbler works](../assets/cobbler-infographic.png)

Cobbler is the coordinator inside Elves. It is not a separate model. It is the part of the harness
that decides how a request should be handled, which agents should help, what evidence matters, and
what answer the user gets.

Cobbler has two jobs. First, it keeps the user from managing agents by hand. Second, it keeps the
answer from turning into a pile of role reports.

## The short version

You ask once. Cobbler classifies the request, chooses a route, sends the right elves to inspect or
work, weighs the evidence, keeps the strongest dissent visible, and returns one answer.

For a normal chat question, that answer is the result. For an active Elves run, Cobbler may also
record the decision in the existing run memory.

## The four routes

Most requests fall into one of four routes.

1. Direct answer

   Cobbler answers directly when extra agents would add noise.

2. Quick Cobbler

   Quick Cobbler is for one-off advice. It is read-only and stateless. It can inspect files, docs,
   tests, PR comments, or other evidence, but it does not edit files or update run state.

3. Cobbler Mode

   Cobbler Mode is a current-thread convention. It lets the user keep chatting with Cobbler without
   typing the invocation each time. It does not create a branch, PR, survival guide, execution log,
   Codex Goal, provider route, or config entry.

4. Run Cobbler

   Run Cobbler is the default coordination pattern inside an Elves run. The coordinator still owns
   git, PRs, durable memory, and final synthesis. Worker agents may edit the repo when the active
   batch or user request gives them scoped implementation work.

## The flow

1. Intent

   The user asks a question or gives a run request.

2. Classify

   Cobbler decides whether this is direct, Quick, Mode, or Run work.

3. Route

   Cobbler chooses roles, tools, skills, docs, tests, and sources. It uses host-native subagents
   first when they are available.

4. Send the elves

   Read-only lenses inspect independently. Worker agents edit only when the task is scoped as
   implementation work.

5. Gather evidence

   The useful inputs are facts, tests, source material, changed files, risks, and dissent. Model
   prestige does not settle disagreements.

6. Fit the answer

   Cobbler returns one answer with the recommendation first. It includes why the answer fits, the
   strongest dissent, the risks, the next move, and confidence.

7. Answer or record

   Quick Cobbler answers the user and stops. Run Cobbler records material decisions in existing
   Elves memory, such as the execution log, survival guide, or `.elves-session.json`.

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
```

In Codex:

```text
$elves cobbler: should we refactor this or patch it?
$elves cobbler-mode
```

Natural language also works:

```text
Ask the Cobbler to audit this plan.
Cobbler Mode: on
Cobbler Mode: off
```

Compatibility aliases still work. Claude Code supports `/council`, `/ec`, and `/elves-council`.
Codex supports `$elves council: <task>` and natural Council references. They all route to Cobbler.

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

Cobbler is not a license for agents to edit everything. Quick Cobbler stays read-only. Run Cobbler
allows worker edits only when the work is scoped.
