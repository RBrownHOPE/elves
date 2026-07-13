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

Primary trusted full-run (one launch, one persistent session, bounded driver monitoring):

```bash
# Create the one host-owned launch rollback ref before preparing or launching the worker.
python3 scripts/cobbler_agents.py implement rollback-ref --json \
  --run-id <run-id> --session-id <exact-uuid> --batch 0 \
  --head <start-head> --push

python3 scripts/cobbler_agents.py implement full-run-prepare --json \
  --session-id <exact-uuid> --branch <feature-branch> --start-head <sha> \
  --packet <absolute-full-run-packet> --worktree <absolute-worktree> \
  --adapter grok-build --model grok-4.5 --permission-mode auto \
  --effort medium --max-turns 80

python3 scripts/cobbler_agents.py implement full-run-launch --json \
  --session-id <exact-uuid>

python3 scripts/cobbler_agents.py implement full-run-monitor --json \
  --session-id <exact-uuid>

python3 scripts/cobbler_agents.py implement full-run-logs --json \
  --session-id <exact-uuid>

# Cancellation/recovery only; omit on normal successful completion.
python3 scripts/cobbler_agents.py implement full-run-stop --json \
  --session-id <exact-uuid>
```

The host creates no per-batch refs while parked. Worker commit SHAs are the internal rollback
points. Grok never creates, moves, or pushes refs other than its assigned feature branch.

`full-run-stop` explicitly cancels or recovers a live/wedged worker. It is not a normal close step,
does not prove completion, and must not be used after a successful worker exit.

After `full-run-launch` returns healthy, the driver parks. It may poll bounded monitor telemetry and
give light user updates; it does not re-enter per-batch implementation/review loops. Wake only for
blocked/stale/failed state, a safety tripwire, explicit user input, or actual worker exit. Raw
transcripts remain private unless explicitly requested.

Legacy bounded-batch path (use only when the user selected a bounded task or legacy batch resume):

```bash
python3 scripts/cobbler_agents.py implement prepare \
  --branch <b> --worktree <path> --model grok-4.5 --session-id <uuid>

python3 scripts/cobbler_agents.py implement launch \
  --session-id <uuid> --packet .elves/runtime/packets/batch-1.md --cwd <worktree>
# prints exact grok argv (default). Add --exec only when the host should spawn.

python3 scripts/cobbler_agents.py implement gate --batch 1

python3 scripts/cobbler_agents.py implement resume-batch \
  --batch 2 --packet .elves/runtime/packets/batch-2.md

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
   such as `- B1-A1 — Driver remains parked` or `- [ ] M-A1 — One launch completes the run`;
   production preparation rejects missing or duplicate definition rows, and inline mentions/examples
   do not stage criteria
7. **validation commands** — focused + full suite the implementer must run
8. **commit subject prefix** — e.g. `[feat/… · Batch N/M · Implement] …`
9. **stop conditions** — when to stop and what to write back
10. **events/report identity and paths** — the versioned `ELVES_FULL_RUN_EVENTS`,
    `ELVES_FULL_RUN_REPORT`, `ELVES_FULL_RUN_RUN_ID`, and `ELVES_FULL_RUN_ATTEMPT` contract below

Store packets under `.elves/runtime/packets/` (ignored runtime tree). Prefer absolute `--prompt-file`
paths when the process CWD is not the host runtime directory.

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
| `type` | string enum | `run_started`, `heartbeat`, `batch_started`, `commit_pushed`, `gate_result`, `batch_complete`, `blocked`, or `run_complete` |
| `summary` | string | at most 500 characters; no secret-like text such as API keys, bearer tokens, authorization headers, or private-key material |

The exact `session_id` and `branch` must match supervisor state. Emit at most one terminal event:
either `blocked` or `run_complete`. A terminal event is a wake signal, not completion authority.

```json
{"timestamp":"2026-07-13T05:14:00Z","session_id":"20e34572-1a71-44aa-8b90-0123456789ab","branch":"feat/delegated-worker","head":"0123456789abcdef0123456789abcdef01234567","batch":2,"type":"commit_pushed","summary":"Batch 2 implementation and focused tests pushed"}
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
| `batches` | array | internal batch summaries/evidence using stable `B#` ids |
| `acceptance` | array of objects | exact staged `B#-A#` and `M-A#` rows described below |
| `commits` | array | worker commit SHAs or SHA/subject records |

When present, `blockers`, `docs_changed`, and `remaining_risks` must also be arrays. A `complete`
report requires non-empty `final_head` and non-empty `acceptance`. A production prepare fails closed
unless the packet stages at least one stable `B#-A#` or `M-A#` id. Every acceptance item requires
the fields `id` (an exact staged stable id), `criterion` (non-empty string), `met` (boolean), and
`evidence` (string). A row with `met: true` requires non-empty evidence, and the final report id set
must exactly equal the staged id set.

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
    {"id": "B1", "status": "complete", "evidence": "focused and broad gates passed"}
  ],
  "acceptance": [
    {"id": "B1-A1", "criterion": "Driver stays parked during worker batches", "met": true, "evidence": "bounded event log and supervisor status"},
    {"id": "M-A1", "criterion": "One launch completes the delegated run", "met": true, "evidence": "supervisor exit and reconciled commit chain"}
  ],
  "commits": ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
  "blockers": [],
  "docs_changed": ["README.md"],
  "remaining_risks": []
}
```

The report is evidence only. Completion additionally requires the supervisor to validate report
identity and acceptance, feature-branch descendant progress, the process fingerprint/exit record,
and unchanged protected refs.

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
| During run | park; read bounded monitor/events only; give light updates |
| Safety wake | handle blocked/stale/failed state or a safety tripwire |
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
write status=blocked with blockers[] and stop.
```
