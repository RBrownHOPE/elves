# Kickoff Prompt Template

> Elves works best as a two-call handoff:
>
> 1. **Stage the run**
> 2. **Launch the run**
>
> Most "the elves stopped" failures come from combining a giant plan and the launch instructions
> into one overloaded message. The plan already lives on disk. The launch prompt should stay short.
>
> **E2E convenience (v2.0+):** if you want one kickoff that plans, stages, and runs batches from a
> chat brief, use **Chat-to-work** (landable PR, no merge) or **Chat-to-land** (through merge
> ceremony) at the bottom of this file. Design: [`e2e-chat-to-land.md`](e2e-chat-to-land.md).
>
> Think of staging as winding the spring: clean the docs, line up the branch and PR, run
> preflight, and stop only when the runway is clear. Then use a fresh launch call to start the
> unattended run with momentum.
>
> **The Daily Briefing.** Block time at the end of your workday (even 30 minutes) to brief your
> agents. Friday afternoons deserve more deliberate treatment: the weekend is roughly 60 hours of
> potential agent runtime. A two-hour planning session on Friday can produce a week's worth of
> output before Monday morning.

---

## Step 1: Stage Template

> Use this first. The goal is to get everything lined up and then stop. Do not let the agent start
> implementation in the same call that is still cleaning up the plan or initializing the run.

```
Stage this Elves run. Do not start implementing the batches in this call.

**Plan:** [path/to/plan.md]
**Branch:** [feat/branch-name]
**Survival guide:** [path/to/survival-guide.md]  (or: "generate from template")
**Learnings:** [path/to/learnings.md]            (or: "generate from template")
**Execution log:** [path/to/execution-log.md]    (or: "generate from template")

**Your job in this call:**
- Tighten the plan if needed so it can survive compaction without the conversation
- Generate or refresh the survival guide, learnings file, and execution log
- Set `## Run Control` explicitly, including run mode, checkpoint semantics, may-continue-after-checkpoint, actual stop conditions, workspace ownership (owned branch, and dedicated worktree if used), merge policy (default: you never merge; opt-ins: merge-commit-on-green or reviewed-pr-landing-command), and `Active Compute` if relevant
- Set `Coordination mode` to Cobbler-first by default: use independent lenses for non-trivial
  planning, contract, risk, debugging, review, and synthesis decisions, while keeping writes, git,
  PRs, and durable memory in the coordinator unless explicitly delegated
- Add `## Cobbler Session State` to the survival guide and `cobbler.default_for_session: true` to
  `.elves-session.json` so follow-up prompts remain Cobbler-mediated after compaction
- If the plan is mathematical, record math as a Cobbler-managed domain workflow and copy any
  explicit math role/provider preferences into the survival guide without making provider keys
  required by default
- Create or switch to the branch, open or update the PR, and record the PR number
- Claim a dedicated checkout: confirm no other agent is working this branch or working tree. When other agents may touch the repo, create the branch directly in a dedicated git worktree instead of in the main checkout (`./scripts/preflight.sh --create-worktree <branch> --base origin/main`; add `--dry-run` to inspect first), and record the branch tip as a collision tripwire. The helper prints the branch, worktree path, base ref, and collision tripwire and does not reuse, delete, or repair existing worktrees.
- Configure optional public API surface snapshot behavior if this project has public contract
  surfaces. Default to `api-surface-snapshot.enabled: auto`, keep `required: false` unless I
  explicitly opt in, and keep snapshot artifacts under ignored `.elves/api-surface/`.
- Run preflight and log any warnings or blockers
- Record any durable-doc paths the run should use (`.ai-docs/*`) if the repo keeps them
- Prepare a short launch prompt for the next call

**Non-negotiables:**
- [Hard rule 1]
- [Hard rule 2]
- [Hard rule 3]

**Stop condition for this call:**
- Stop only after the run is launch-ready and you have handed me the launch prompt for the next call
```

**Example:**

```
Stage this Elves run. Do not start implementing the batches in this call.

**Plan:** docs/plans/auth-refactor.md
**Branch:** feat/jwt-auth
**Survival guide:** docs/elves/survival-guide.md  (generate from template if missing)
**Learnings:** docs/elves/learnings.md            (generate from template if missing)
**Execution log:** docs/elves/execution-log.md    (generate from template if missing)

**Your job in this call:**
- Tighten the plan if needed so it can survive compaction without the conversation
- Generate or refresh the survival guide, learnings file, and execution log
- Set `## Run Control` explicitly, including run mode, checkpoint semantics, may-continue-after-checkpoint, actual stop conditions, workspace ownership (owned branch, and dedicated worktree if used), merge policy (default: you never merge; opt-ins: merge-commit-on-green or reviewed-pr-landing-command), and `Active Compute` if relevant
- Set `Coordination mode` to Cobbler-first by default: use independent lenses for non-trivial
  planning, contract, risk, debugging, review, and synthesis decisions, while keeping writes, git,
  PRs, and durable memory in the coordinator unless explicitly delegated
- Add `## Cobbler Session State` to the survival guide and `cobbler.default_for_session: true` to
  `.elves-session.json` so follow-up prompts remain Cobbler-mediated after compaction
- If the plan is mathematical, record math as a Cobbler-managed domain workflow and copy any
  explicit math role/provider preferences into the survival guide without making provider keys
  required by default
- Create or switch to the branch, open or update the PR, and record the PR number
- Claim a dedicated checkout: confirm no other agent is working this branch or working tree. When other agents may touch the repo, create the branch directly in a dedicated git worktree instead of in the main checkout (`./scripts/preflight.sh --create-worktree <branch> --base origin/main`; add `--dry-run` to inspect first), and record the branch tip as a collision tripwire. The helper prints the branch, worktree path, base ref, and collision tripwire and does not reuse, delete, or repair existing worktrees.
- Configure optional public API surface snapshot behavior if this project has public contract
  surfaces. Default to `api-surface-snapshot.enabled: auto`, keep `required: false` unless I
  explicitly opt in, and keep snapshot artifacts under ignored `.elves/api-surface/`.
- Run preflight and log any warnings or blockers
- Record any durable-doc paths the run should use (`.ai-docs/*`) if the repo keeps them
- Prepare a short launch prompt for the next call

**Non-negotiables:**
- Never modify public /api/* response shapes
- All commits must pass lint and typecheck before push
- Do not touch the OAuth routes or password reset flow
- You never merge by default. The PR is for me to review, unless I explicitly opt into merge-on-green or ask for the reviewed-PR landing command.

**Stop condition for this call:**
- Stop only after the run is launch-ready and you have handed me the launch prompt for the next call
```

---

## Step 2: Hard Launch Template

> Use this in a fresh call after staging is done. Keep it short. The plan already carries the
> project detail; the launch prompt should reinforce behavior and momentum.

```
The run is staged. Start now.
Read [path/to/survival-guide.md] first, then `.elves-session.json` if it exists, then [path/to/learnings.md] if it exists, then [path/to/plan.md], then the execution log at [path/to/execution-log.md], then `.ai-docs/manifest.md` if it exists.
I am going offline until [WHEN].
By [WHEN], I want [CHECKPOINT DELIVERABLE]. This is a [delivery checkpoint / hard stop].
Operate Cobbler-first: use independent lenses for non-trivial planning, contract, risk, debugging, review, and synthesis decisions; keep writes, git, PRs, and durable memory in the coordinator unless explicitly delegated.
Do not stop unless you hit a genuine blocker with no reasonable workaround.
Do not be lazy. Work as hard as you can for the entire run.
Do not coast after the first success, first green check, or first useful checkpoint. Push each batch to a thoroughly verified state, then continue immediately.
If the remaining work feels like a lot for one turn, that is the point: the volume is the reason this run exists, not a reason to stop.
Every completed batch must end with a commit and push before you start anything else.
Immediately after every commit and push, re-read the survival guide before any other action.
If this is a delivery checkpoint, log it, push it, and continue immediately. Do not stop at the checkpoint.
Do not wait for me to acknowledge checkpoints, summaries, or clean commits. If work remains, keep going.
Do not send a final response unless the survival guide Stop Gate says stopping is allowed or a true blocker forces it.
Use your judgment. Work in small batches and commit frequently.
Make the commit subjects read like progress reports.
Run every relevant validation gate, including E2E or browser checks wherever they make sense.
After every push, read PR comments and checks, fix blockers, and re-check for regressions against earlier verified work.
If the run uses paid compute, remote jobs, or long-lived servers, keep the survival guide's `Active Compute` section current after every push and topology change.
Keep going until the plan is done, I stop you, or you hit a true blocker.
```

**Example:**

```
The run is staged. Start now.
Read docs/elves/survival-guide.md first, then `.elves-session.json` if it exists, then docs/elves/learnings.md if it exists, then docs/plans/auth-refactor.md, then the execution log at docs/elves/execution-log.md, then `.ai-docs/manifest.md` if it exists.
I am going offline until 7:30am ET.
By 7:30am ET, I want a review-ready checkpoint with green local validation. This is a delivery checkpoint, not a stop boundary.
Do not stop unless you hit a genuine blocker with no reasonable workaround.
Do not be lazy. Work as hard as you can for the entire run.
Do not coast after the first success, first green check, or first useful checkpoint. Push each batch to a thoroughly verified state, then continue immediately.
If the remaining work feels like a lot for one turn, that is the point: the volume is the reason this run exists, not a reason to stop.
Every completed batch must end with a commit and push before you start anything else.
Immediately after every commit and push, re-read the survival guide before any other action.
This checkpoint is for delivery only. Log it, push it, and continue immediately. Do not stop at 7:30am ET.
Do not wait for me to acknowledge checkpoints, summaries, or clean commits. If work remains, keep going.
Do not send a final response unless the survival guide Stop Gate says stopping is allowed or a true blocker forces it.
Use your judgment. Work in small batches and commit frequently.
Make the commit subjects read like progress reports.
Run every relevant validation gate, including E2E or browser checks wherever they make sense.
After every push, read PR comments and checks, fix blockers, and re-check for regressions against earlier verified work.
If the run uses paid compute, remote jobs, or long-lived servers, keep the survival guide's `Active Compute` section current after every push and topology change.
Keep going until the plan is done, I stop you, or you hit a true blocker.
```

---

## Tips

**Stage and launch in separate calls**
The split is the point. Staging should absorb plan cleanup and setup churn. Launch should begin
with a short, behavior-heavy prompt.

**If you only send one message, the agent should stage first**
If you paste a large plan and also say "run now," the agent should treat that message as a staging
request, not a launch request.

**The agent should push back explicitly**
When the prompt is overloaded, the agent should say some version of: "Hang on, we need to get
this right. I'm going to stage the run and wait for your final launch command."

**Don't repeat the whole plan in the launch prompt**
Point to the plan by path. If the launch prompt starts looking like a second plan file, it is too
long.

**Use Codex Goals as a continuation backend when available**
If launching from Codex with Goals enabled, wrap the same launch prompt in `/goal`. Goals keeps
Codex moving; Elves still defines completion through the survival guide Stop Gate and Readiness
Gate. If a goal budget is exhausted before readiness is clean, the agent should write a
reactivation handoff, commit, push, and avoid claiming completion.

**Point to durable memory too**
If the run uses a learnings file or `.ai-docs`, include those paths in the launch prompt so the
agent rehydrates from durable knowledge instead of rediscovering it.

**State checkpoint semantics explicitly**
Don't make the agent guess whether "8am" is a delivery checkpoint or a hard stop. Say which it is.

**Call out paid compute**
If pods, remote jobs, or long-lived servers are involved, tell the agent and require `Active
Compute` updates in the survival guide.

**Make the launch prompt behavior-heavy**
The launch prompt should remind the agent how to behave: don't stop, use judgment, work in small
batches, commit frequently, validate aggressively, review PR feedback, and watch for regressions.

**Keep memory fast**
For long runs, tell the agent to perform memory and resource hygiene during entropy checks: keep
the survival guide concise, archive old execution-log entries in place, promote durable lessons,
stop idle resources, and write a fresh-thread handoff if the active chat or app becomes sluggish.

**Require a final readiness review**
The final step of every run is a final readiness review, and it is not optional. Before the final
handoff, the agent should run a fresh cumulative review of `git diff <default-branch>...HEAD`, read
every PR review comment, run every test that makes sense, and confirm checks, docs, and memory
hygiene are clean. Use a review subagent when the platform supports one; otherwise do the review
directly. Fix blockers and repeat until you are confident the branch is green. Then hand the user
the HTML Elves Report and tell them to review it. Stop for the user to merge unless they explicitly
set a merge-on-green preference or asked for the reviewed-PR landing command; in either opt-in path,
perform a regular merge commit (never a squash).

If the user asks for the reviewed-PR landing command, or types `\land-pr` or `/land-pr`, treat that
as a one-off merge opt-in for the current PR: get a fresh subagent review of
`git diff <default-branch>...HEAD`, read every PR comment and check, fix blockers, run sensible
tests, wait for asynchronous review/CI updates, re-read the feedback queue, and then use
`gh pr merge --merge` only when everything is green. Never squash.

**Check in with `ra:`**
You don't have to disappear completely. If you want to give context or change priorities during
the run, prefix your message with `ra:`. `ride-along:` and `[ride-along]` also work. The agent
will respond briefly and keep going without stopping.

**Friday staging is leverage**
Use Friday afternoon to build a clear plan, stage the run, and make sure preflight is green. Then
launch in a clean second call and let the agent work through the weekend.

---

## Chat-to-work (E2E, no merge)

> One kickoff: clarify intent (optionally multi-planner), materialize plan + stage, run all batches
> to a **landable PR**. **Do not merge.** Design: [`e2e-chat-to-land.md`](e2e-chat-to-land.md).
>
> Internally still stage-then-execute (docs/PR before coding). User may send one message.

```
Elves E2E: chat-to-work (no merge).

**Intent / brief:**
[What I want built or researched. Constraints, non-negotiables, deadline if any.]

**Repo / branch preference:** [auto or feat/…]
**Work driver:** [host-native | grok-build | opencode-cli | …]
**Multi-planner:** [optional host Cobbler only | also use available plan/review lenses]

**Your job (one continuous run after planning is solid):**
1. Chat only as needed to sharpen intent. Optionally route independent planners (Cobbler / available
   lenses). Synthesize one plan on disk.
2. Stage: survival guide, learnings, execution log, branch, PR, preflight, Cobbler session state.
   Set Run Control: `e2e mode: chat-to-work`, `merge policy: never-merge`, labor re-drive budget 3.
3. Execute every planned batch: contract → implement → validate → review PR feedback → document →
   commit/push. Re-read survival guide after every push.
4. If a work driver returns incomplete work (common with Grok Build): gap-packet + re-drive up to
   budget; if still incomplete, host finishes the gap or hard-stop with remaining contract. Never
   mark complete on partial labor.
5. Run Final Readiness / Readiness Gate. Leave a landable PR for me. Generate Elves Report if the
   run is substantial.

**Continuation:** Prefer `/goal` (Codex) or host long-run continuation with the same rules once
staging is launch-ready. Goal/memory authority is the survival guide Stop Gate + Readiness Gate.

**Hard rules:**
- You never merge.
- Supported main driver is this host (Claude Code or Codex). Optional tools never required.
- Do not stop unless Stop Gate allows it, I stop you, or a true blocker.

**Stop when:** plan batches are done (or true blocker), PR is landable, you did not merge.
```

---

## Chat-to-land (E2E through merge)

> Same as chat-to-work, then **reviewed-PR landing** through a regular merge commit.
> This is an explicit merge opt-in. Design: [`e2e-chat-to-land.md`](e2e-chat-to-land.md).

```
Elves E2E: chat-to-land (merge when green).

**Intent / brief:**
[What I want built or researched. Constraints, non-negotiables, deadline if any.]

**Repo / branch preference:** [auto or feat/…]
**Work driver:** [host-native | grok-build | opencode-cli | …]
**Multi-planner:** [optional host Cobbler only | also use available plan/review lenses]

**Your job (one continuous run after planning is solid):**
1. Chat only as needed to sharpen intent. Optionally route independent planners. Synthesize one
   plan on disk.
2. Stage: survival guide, learnings, execution log, branch, PR, preflight, Cobbler session state.
   Set Run Control: `e2e mode: chat-to-land`,
   `merge policy: reviewed-pr-landing-command`, labor re-drive budget 3.
3. Execute every planned batch end-to-end (same quality bar as a normal Elves run).
4. Labor completeness: after every work-driver return, verify contract + evidence; re-drive gaps
   (budget 3) or host-complete / hard-stop. Never accept partial Grok/OpenCode labor as done.
5. Final Readiness Gate on the tip. Elves Report for substantial runs.
6. **Reviewed PR landing (explicit merge opt-in):** fresh cumulative review of
   `git diff <default-branch>...HEAD`, every PR comment/check, fix blockers, re-poll async review/CI,
   then `gh pr merge --merge` only when not draft, checks green, no blocking review, clean worktree.
   Never squash or rebase.

**Continuation:** Prefer `/goal` (Codex) or host long-run continuation once staging is ready.
Authority remains Stop Gate until Readiness; then landing rules above.

**Hard rules:**
- Merge only via regular merge commit after landing criteria; never squash.
- Main driver is this host; optional tools are optional.
- Do not stop mid-run because a work driver "finished a turn" — check completeness.

**Stop when:** PR is merged with a merge commit, or a true blocker prevents safe merge (report exactly what remains).
```

**Codex tip:** after stage inside the same E2E run (or in a second call), you can wrap the
execution tail with `/goal` using the short launch body from [`codex-goals.md`](codex-goals.md),
plus the e2e mode and merge policy from the survival guide.
