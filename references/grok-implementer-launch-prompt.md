> **Primary path (v2.1+):** trusted Grok full-run uses
> `full-run-prepare|full-run-launch|full-run-monitor|full-run-logs` with parked-monitor.
> `full-run-stop` is cancellation/recovery only. **Batch resume**
> (`prepare|launch|gate|resume-batch`) is the legacy/alternative path.

# Grok Implementer Launch Prompt (optional external implementer)

**This is not the Elves default.** Vanilla Cobbler implements with the host agent (Claude Code or
Codex) only. Use this document when the user already has Grok Build (or a similar CLI) and wants it
to implement a whole trusted run—or an explicitly legacy bounded batch—under host staging and
gates, the same optional-upgrade pattern as the math module’s provider routes.

Smart host plans and gates; one persistent Grok Build session implements the full delegated scope.
This installed reference is self-contained. A source checkout may also contain repo-only worked
plans under `docs/plans/`; installed bundles must not depend on those files.

For the stricter host-import writer lease (detached commits, host audit/import only), use the
advanced worker flow in [`councilelves-launch-prompt.md`](councilelves-launch-prompt.md) and
`python3 scripts/cobbler_agents.py worker …`. Do **not** use that lease path as the default
overnight path.

Future community-plugin ideas (not implemented):
[`community-grok-plugin-ideas.md`](community-grok-plugin-ideas.md).

## Survival Guide field (only when using an external implementer)

```text
implementation_lane: fast | untrusted
```

- **`fast`** — external implementer (e.g. Grok) owns feature-branch progress in a host-created
  worktree; host launches once and parks on bounded telemetry, while final readiness stays
  host-owned.
- **`untrusted`** — exclusive writer lease, detached commits, host audit/import only (advanced).

Omit `implementation_lane` entirely for host-native runs.

## Operator CLI

The `python3 scripts/...` forms below are source-checkout shorthand. From an installed Claude Code
or Codex skill, invoke the helper from the active Elves skill root and keep the target repository as
the working directory; see [`runtime-helper-paths.md`](runtime-helper-paths.md).

Primary trusted full-run (one launch, one persistent session, bounded driver monitoring):

```bash
# Create the one host-owned launch rollback ref before preparing or launching the worker.
python3 scripts/cobbler_agents.py implement rollback-ref --json \
  --run-id <run-id> --session-id <exact-uuid> --batch B0 \
  --head <start-head> --push

python3 scripts/cobbler_agents.py implement full-run-prepare --json \
  --session-id <exact-uuid> --branch <feature-branch> --start-head <sha> \
  --packet <absolute-full-run-packet> --worktree <absolute-worktree> \
  --session <canonical-.elves-session.json> \
  --adapter grok-build --model grok-4.5 --permission-mode auto \
  --effort medium --max-turns 80

python3 scripts/cobbler_agents.py implement full-run-launch --json \
  --session-id <exact-uuid> --grant-grok-auth --grant-github-push

python3 scripts/cobbler_agents.py implement full-run-monitor --json \
  --session-id <exact-uuid>

# After host review of the exact pending checkpoint:
# python3 scripts/cobbler_agents.py implement full-run-monitor --json \
#   --session-id <exact-uuid> --ack-high-risk-checkpoint <checkpoint-id>

python3 scripts/cobbler_agents.py implement full-run-logs --json \
  --session-id <exact-uuid>

# Cancellation/recovery only; omit on normal successful completion.
python3 scripts/cobbler_agents.py implement full-run-stop --json \
  --session-id <exact-uuid>
```

`full-run-prepare` uses the canonical session's recorded `plan_path`; optional `--plan` is only an
equality assertion. It reconciles plan, session, and packet criteria before creating worker state,
and `full-run-launch` revalidates that bound contract before credentials or spawn.

The host creates no per-batch refs while parked. Worker commit SHAs are the internal rollback
points. Grok never creates, moves, or pushes refs other than its assigned feature branch.

`full-run-stop` explicitly cancels or recovers a live/wedged worker. It is not a normal close step,
does not prove completion, and must not be used after a successful worker exit.

Choose exactly one noninteractive auth strategy at launch. `--grant-grok-auth` is the explicit
trusted-Lane-A subscription/OAuth path: Elves keeps private per-run `HOME`/`GROK_HOME` state and
exposes only the validated canonical owner-private host `auth.json` through Grok's native
`GROK_AUTH_PATH`. One canonical file preserves Grok's lock and refresh-token rotation semantics;
raw transcript tails are disabled for this route. For API-key use, replace that flag with
`--grant-env XAI_API_KEY`, which is preferred for CI or untrusted lanes. Never combine the routes,
grant `GROK_HOME`, or inherit the host HOME; a launch without either supported strategy fails before
Grok can enter an unattended device-login wait. Shared OAuth requires Grok Build 0.2.93+ with its
native capability marker; unsupported binaries fail before spawn and must upgrade or use the
API-key route.

Before spawn, Elves probes and binds an exact native Mach-O/ELF Grok executable plus its full safe
ancestor chain in an isolated credential-free environment, then validates the auth file plus its
full owner/mode/link/ACL
ancestor chain. Replacement or permissive ACLs fail closed.

GitHub push authentication is independent of Grok provider authentication. For a canonical
`https://github.com/...` origin, add `--grant-github-push` to project the authenticated host `gh`
token through one launch-scoped credential helper, or grant exactly one of `GH_TOKEN` and
`GITHUB_TOKEN` by name. No host HOME, XDG directory, Git config, or SSH agent is inherited; only
keyed token metadata is persisted, and unsupported network transports fail before spawn. Elves
projects validated explicit `user.name` / `user.email` values into author/committer variables so
Git never guesses identity inside the isolated HOME; missing identity is a pre-spawn error.

The stop capability hardens the runtime artifact channel against malformed/forged leaves inside a
trusted branch-progress route. It is not a same-user security boundary against a malicious worker;
the worker remains constrained by the trusted-lane contract and final host audit.

After `full-run-launch` returns healthy, the driver parks. Prefer a host wait/monitor primitive;
otherwise use the monitor response's `poll_after_seconds` (half the stale window, bounded to 60–300
seconds). `chat_update_recommended: false` with `unchanged_healthy_poll_silent: true` means exactly
that: emit no chat, do not read raw output, and do not re-enter reasoning. The response exposes
`user_heartbeat_seconds` (default 900); the host owns coalescing nonterminal progress into at most one
1–3 sentence update in that window. Wake immediately only for blocked/stale/failed state, a safety
tripwire, an explicitly planned high-risk checkpoint, explicit user input, or actual worker exit.
Stage each checkpoint in the packet as `- High-risk checkpoint: <stable-id>`. The worker emits one
matching `high_risk_checkpoint` event; after review the host passes that exact ID to
`--ack-high-risk-checkpoint`. Missing or unacknowledged planned checkpoints block reconciliation,
including when the provider completed before the next poll. Raw transcripts remain private unless
explicitly requested.

Legacy bounded-batch path (use only when the user selected a bounded task or legacy batch resume):

All batch-taking helpers accept equivalent integer and stable-id spellings (`0` / `B0`, `1` /
`B1`, and so on). Canonical stable ids are `B0` or `B1` and above; negatives and leading-zero
aliases fail before state changes or launch.

```bash
python3 scripts/cobbler_agents.py implement prepare \
  --branch <b> --worktree <path> --model grok-4.5 --session-id <uuid>

python3 scripts/cobbler_agents.py implement launch \
  --session-id <uuid> --packet .elves/runtime/packets/batch-1.md --cwd <worktree>
# Prints exact Grok argv. Current supported OSes have no qualified recursive
# boundary for legacy --exec, so that option fails closed before spawn.

python3 scripts/cobbler_agents.py implement gate --batch B0

python3 scripts/cobbler_agents.py implement resume-batch \
  --batch 1 --packet .elves/runtime/packets/batch-1.md

python3 scripts/cobbler_agents.py implement status
```

Runtime metadata lives under `.elves/runtime/implement/` (mode `0700` dirs). Network is not required
for `prepare` / `status` / argv emission.

## Launch invariants (external Grok implementer)

| Setting | Value | Why |
|---------|-------|-----|
| Session | exact UUID create once; exact `--resume` only after interruption | preserve context without per-batch re-prompts |
| Unattended tools | **`--yolo`** (alias `--always-approve`) | required for headless edits; `--permission-mode auto` alone is not enough |
| Effort | **`medium`** default (`--effort medium`) | `high` roughly doubles tiny-task latency; reserve high for hard batches |
| Subagents | enabled | never pass `--no-subagents` |
| Unit of work | whole delegated run per full-run packet | avoid per-batch host tax |
| Prompt | `--prompt-file` packet **or** `-p` text — never both | CLI rejects combining them |
| Model default | `grok-4.5` | product default for this path |
| Model aliases | `fast` → `grok-composer-2.5-fast`; `deep` → `grok-4.5` + `--effort high` | operator shorthand on `implement prepare/launch --model` (re-check `grok models`) |
| Optional verify | `--check` on `full-run-prepare` (or legacy `launch` / `resume-batch`) | passes Grok CLI `--check` (post-work verification; higher latency — opt-in) |
| Git default | `branch_progress` (Mode A1) | Grok commits/pushes progress slices on the feature branch |
| Failure UX | `error_human` on failed `--exec` | short mapped messages for auth / tool-config / rate-limit dumps |

### Grok CLI 0.2.93 tool-gating note

For **read-only review / media-style** Grok invocations (not Lane A implement), prefer the
**default toolset + `--disallowed-tools` denylist** over a `--tools` allowlist. On Grok Build
~0.2.93, allowlists have failed session create with `RequirementError` / `run_terminal_cmd`
background constraints. Lane A implement still uses the default toolset + **`--yolo`** for
unattended writes.

This denylist guidance is informed by community companion battle-scars in
[stdevMac/grok-in-claude](https://github.com/stdevMac/grok-in-claude) and
[stdevMac/grok-in-codex](https://github.com/stdevMac/grok-in-codex) (Apache-2.0). Elves does not
vendor those plugins; host-owned implement leases and run memory remain Elves-native.

### Legacy bounded-batch headless recipe (Grok Build 0.2.93)

Whole-batch implement (~3 minutes for docs+CLI+tests on this repo):

```bash
grok --prompt-file .elves/runtime/packets/batch-1.md \
  --cwd <worktree> \
  --model grok-4.5 \
  --yolo \
  --effort medium \
  --max-turns 80 \
  --output-format json
```

Resume next batch in the same session (use `sessionId` from prior JSON):

```bash
grok --resume <sessionId> \
  --prompt-file .elves/runtime/packets/batch-N.md \
  --cwd <worktree> \
  --model grok-4.5 \
  --yolo \
  --effort medium \
  --max-turns 80 \
  --output-format json
```

### Interactive TUI (human-fast path)

Positional prompt, **no** `-p` / `--prompt-file` headless flags:

```bash
cd <worktree>
grok --model grok-4.5 \
  "Read and execute .elves/runtime/packets/batch-1.md end-to-end."
```

### Slow anti-patterns (measured)

| Recipe | Result |
|--------|--------|
| Nested host loop (Codex drives every tool call) | Hours of ceremony tax |
| `--reasoning-effort high` on volume implement | ~2× tiny-task latency; long thought streams |
| `--permission-mode auto` without `--yolo` | May not auto-approve writes unattended |
| `--tools` allowlist for read-only/media on 0.2.93 | Session-create `RequirementError` — use denylist instead |
| Host re-audits / full suite between every amend | Recreates nested-driver slowness |

## Full-run packet contract (versioned expectations)

Packet file version: **1** (markdown preferred; JSON optional for machine fields).

A full-run packet must stand alone after compaction and include:

1. **run scope** and why it exists, including the complete ordered batch list
2. **intent / behaviors** — product-level done criteria
3. **Build On** — existing paths, patterns, and utilities to extend
4. **owned surfaces** — exact files/modules the implementer may edit
5. **forbidden surfaces** — run memory, credentials, other worktrees, out-of-scope paths
6. **acceptance** — concrete criteria and what evidence looks like, defined as canonical bullet rows
   such as `- B0-A1 — Driver remains parked`, `- [ ] B0-A1: Driver remains parked`,
   `- [ ] [B0-A1] Driver remains parked`, or `- [ ] M-A1 — One launch completes the run`;
   `B0` and `B1` are equally valid batch starts, with no preferred convention, and bare or
   bracketed stable-id checkbox rows are equivalent;
   production preparation rejects missing or duplicate definition rows, and inline mentions/examples
   do not stage criteria
7. **validation commands** — focused + full suite the implementer must run
8. **commit subject prefix** — e.g. `[feat/… · Batch N/M · Implement] …`
9. **stop conditions** — when to stop and what to write back
10. **events/report identity, paths, and exact schema** — the versioned `ELVES_FULL_RUN_EVENTS`,
    `ELVES_FULL_RUN_REPORT`, `ELVES_FULL_RUN_RUN_ID`, and `ELVES_FULL_RUN_ATTEMPT` contract below.
    Embed the required event fields/types and report row shapes in the self-contained packet (or a
    machine-equivalent schema); shorthand such as “write valid events/report” is not sufficient for
    unattended completion and can force an otherwise healthy parked driver to wake on malformed
    evidence.

Store packets under `.elves/runtime/packets/` (ignored runtime tree). Prefer absolute `--prompt-file`
paths when the process CWD is not the host runtime directory.

Production preparation validates acceptance before launch: it parses the authoritative plan with
targeted line-level syntax diagnostics, then requires the session and packet id-to-criterion maps
to match that plan. Missing, duplicate, or text-mismatched rows stop preparation before a worker is
spawned; do not defer these errors to the terminal report or landing check. The coordinator may
run the installed `acceptance_contract.py validate` helper earlier in staging and use explicit
`sync-session --write` to derive pending session rows; production prepare/launch still revalidate.

## Trusted full-run event and report schema (v1)

This schema is separate from the legacy bounded-batch done report. `full-run-prepare` pre-creates
the private events path and a baseline report. The worker must preserve the baseline `run_id`,
`attempt`, `session_id`, `branch`, and `start_head`, append one JSON object per line to the JSONL
events file, and atomically replace the report file after material state changes. Never truncate or
rewrite the events file.

### Event v1

Every JSONL event object has exactly these required contract fields (additional non-secret fields
may be ignored by the supervisor):

| Field | Type | Contract |
| --- | --- | --- |
| `timestamp` | string | UTC ISO-8601 timestamp |
| `session_id` | string | exact registered session id |
| `branch` | string | exact assigned feature branch |
| `head` | string | observed feature-branch commit SHA |
| `batch` | integer | current internal batch; use `0` for run-level setup |
| `type` | string enum | `run_started`, `heartbeat`, `batch_started`, `commit_pushed`, `gate_result`, `batch_complete`, `high_risk_checkpoint`, `blocked`, or `run_complete` |
| `summary` | string | at most 500 characters; no secret-like text such as API keys, bearer tokens, authorization headers, or private-key material |
| `checkpoint_id` | string, conditional | required only when `type` is `high_risk_checkpoint`; exact packet-staged ID of 1–64 characters, beginning alphanumeric and continuing with alphanumerics, dot, underscore, or hyphen; forbidden on every other event type |

The exact `session_id` and `branch` must match supervisor state. Emit at most one terminal event:
either `blocked` or `run_complete`. A terminal event is a wake signal, not completion authority.

```json
{"timestamp":"2026-07-13T05:14:00Z","session_id":"20e34572-1a71-44aa-8b90-0123456789ab","branch":"feat/delegated-worker","head":"0123456789abcdef0123456789abcdef01234567","batch":2,"type":"commit_pushed","summary":"Batch 2 implementation and focused tests pushed"}
```

```json
{"timestamp":"2026-07-13T05:15:00Z","session_id":"20e34572-1a71-44aa-8b90-0123456789ab","branch":"feat/delegated-worker","head":"0123456789abcdef0123456789abcdef01234567","batch":2,"type":"high_risk_checkpoint","checkpoint_id":"security-boundary","summary":"Host review requested before the security boundary"}
```

### Full-run report v1

The report is one JSON object with these required fields:

| Field | Type | Contract |
| --- | --- | --- |
| `run_id` | string | exact host-created run id from the baseline report |
| `attempt` | integer | exact positive integer from `ELVES_FULL_RUN_ATTEMPT`; starts at `1` and increments only on an authenticated resume |
| `session_id` | string | exact registered session id |
| `branch` | string | exact assigned feature branch |
| `start_head` | string | exact launch head from the baseline report |
| `final_head` | string | observed final feature-branch SHA; non-empty when `status` is `complete` |
| `status` | string enum | `running`, `complete`, `blocked`, `failed`, or `stopped` |
| `batches` | array | internal batch summaries; each complete row has non-empty `id`, `status: "complete"`, and non-empty string `evidence` |
| `acceptance` | array of objects | exact staged `B#-A#` and `M-A#` rows described below |
| `commits` | array | exact 40-character worker SHAs, or `{sha, subject}` records with an exact SHA and non-empty subject |

When present, `blockers`, `docs_changed`, and `remaining_risks` must also be arrays. A `complete`
report requires non-empty `final_head`, `batches`, `acceptance`, and `commits`; every batch must have
a non-empty `id`, `status: "complete"`, and non-empty string `evidence`; and both `blockers` and
`remaining_risks` must be empty. Its commit SHAs must equal the exact ordered
`start_head..final_head` Git chain, not merely a subset or summary, and the worker must leave both
tracked and untracked worktree state clean before publishing completion. A production prepare fails
closed unless the packet stages at least one stable `B#-A#` or `M-A#` id. Every acceptance item
requires the fields `id` (an exact staged stable id), `criterion` (non-empty string), `met: true`,
and non-empty string `evidence`; the final report id set must exactly equal the staged id set.

```json
{
  "run_id": "full-run-run-a1b2c3d4",
  "attempt": 1,
  "session_id": "20e34572-1a71-44aa-8b90-0123456789ab",
  "branch": "feat/delegated-worker",
  "start_head": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "final_head": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "status": "complete",
  "batches": [
    {"id": "B0", "status": "complete", "evidence": "focused and broad gates passed"}
  ],
  "acceptance": [
    {"id": "B0-A1", "criterion": "Driver stays parked during worker batches", "met": true, "evidence": "bounded event log and supervisor status"},
    {"id": "M-A1", "criterion": "One launch completes the delegated run", "met": true, "evidence": "supervisor exit and reconciled commit chain"}
  ],
  "commits": ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
  "blockers": [],
  "docs_changed": ["README.md"],
  "remaining_risks": []
}
```

The report is evidence only. Completion additionally requires the supervisor to validate report
identity and acceptance, the exact ordered commit chain, a clean worktree including untracked files,
feature-branch descendant progress, the process fingerprint/exit record, and unchanged protected
refs.

## Legacy bounded-batch done report schema

Machine-readable batch close report:

- Schema file: [`implement-done-report.schema.json`](implement-done-report.schema.json)
- Default path: `.elves/runtime/implement/done/batch-N.json`

Minimum fields:

```json
{
  "batch": 1,
  "status": "complete",
  "session_id": "<uuid>",
  "head": "<git rev-parse HEAD>",
  "commits": ["sha subject", "..."],
  "tests": {"passed": 0, "failed": 0, "skipped": 0},
  "blockers": [],
  "acceptance": [
    {"criterion": "...", "met": true, "evidence": "..."}
  ]
}
```

On this legacy bounded path only, `implement gate` may read the done report when present and
**warn** if missing (dogfood default: missing report is non-fatal). That behavior is not a full-run
completion fallback: primary full-run readiness requires the validated `status: complete` report
described above. Host still owns protected refs, merge, and final readiness.

## What Grok may do (when selected as implementer / Mode A1)

- Edit owned product surfaces only
- Run focused and full tests
- Commit and push progress slices on the feature branch
- Append trusted full-run v1 events and atomically replace the final full-run report; write the
  legacy done report only on the explicitly selected bounded-batch path

## What Grok must not do

- Merge to main, create/move protected or rollback refs, or open a second PR
- Change Survival Guide stop/merge policy unless the packet assigns it
- Touch credentials or secrets
- Review its own work as if it were independent host review
- Use the host-import lease commands unless the packet explicitly selects `untrusted`

## Host after full-run handoff

| Moment | Host does |
|--------|-----------|
| Staging | plan, PR, worktree, host-created `b0` rollback ref, prepare metadata, write packet |
| During run | park; wait or poll at `poll_after_seconds`; stay silent on unchanged health; host-coalesce nonterminal updates using `user_heartbeat_seconds` |
| Safety wake | handle blocked/stale/failed state or a safety tripwire |
| Planned high-risk checkpoint | wake only for an exact staged ID; acknowledge that event after review, then re-park if healthy or continue final readiness if the worker already exited |
| Worker exit | verify report, feature-branch ancestry, actual exit, and protected refs |
| Final | independent cumulative readiness; merge only if authorized |

## Launch text (paste into Grok)

```text
Read the launch packet at the path given via --prompt-file. Restate the contract (owned/forbidden
surfaces, acceptance, validation) in one short block, then implement the whole run end-to-end.
Do not wait for per-batch host prompts. Commit and push progress with the packet's subject schema.
Run the packet's validation commands before claiming done. Write bounded events and the final run
report to the paths supplied in the environment. Do not
merge, tag, or open a second PR. Do not review your own work as independent review. If blocked,
write status=blocked, populate blockers with concrete bounded and redacted reasons, and stop.
```
