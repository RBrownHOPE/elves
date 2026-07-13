> **Primary path (v2.1+):** trusted Grok full-run uses `full-run-prepare|full-run-launch|full-run-monitor|full-run-logs|full-run-stop` with parked-monitor. **Batch resume** (`prepare|launch|gate|resume-batch`) is the legacy/alternative path.

# Grok Implementer Launch Prompt (optional external implementer)

**This is not the Elves default.** Vanilla Cobbler implements with the host agent (Claude Code or
Codex) only. Use this document when the user already has Grok Build (or a similar CLI) and wants it
to implement a whole batch under host staging and gates — same optional-upgrade pattern as the math
module’s provider routes.

Smart host plans and gates; one persistent Grok Build session implements whole batches. Design
authority:
[`docs/plans/smart-plan-grok-implement.md`](../docs/plans/smart-plan-grok-implement.md).

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
  worktree; host launches once, gates between batches, final readiness stays host-owned.
- **`untrusted`** — exclusive writer lease, detached commits, host audit/import only (advanced).

Omit `implementation_lane` entirely for host-native runs.

## Operator CLI

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
| Session | exact UUID create once; `--resume` for later batches | preserve context |
| Unattended tools | **`--yolo`** (alias `--always-approve`) | required for headless edits; `--permission-mode auto` alone is not enough |
| Effort | **`medium`** default (`--effort medium`) | `high` roughly doubles tiny-task latency; reserve high for hard batches |
| Subagents | enabled | never pass `--no-subagents` |
| Unit of work | whole batch per packet | avoid mid-breath host tax |
| Prompt | `--prompt-file` packet **or** `-p` text — never both | CLI rejects combining them |
| Model default | `grok-4.5` | product default for this path |
| Model aliases | `fast` → `grok-composer-2.5-fast`; `deep` → `grok-4.5` + `--effort high` | operator shorthand on `implement prepare/launch --model` (re-check `grok models`) |
| Optional verify | `--check` on `implement launch` / `resume-batch` | passes Grok CLI `--check` (post-work verification; higher latency — opt-in) |
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

### Headless recipe that worked in dogfood (Grok Build 0.2.93)

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

## Packet contract (versioned expectations)

Packet file version: **1** (markdown preferred; JSON optional for machine fields).

A batch packet must stand alone after compaction and include:

1. **batch** name / number and why it exists
2. **intent / behaviors** — product-level done criteria
3. **Build On** — existing paths, patterns, and utilities to extend
4. **owned surfaces** — exact files/modules the implementer may edit
5. **forbidden surfaces** — run memory, credentials, other worktrees, out-of-scope paths
6. **acceptance** — concrete criteria and what evidence looks like
7. **validation commands** — focused + full suite the implementer must run
8. **commit subject prefix** — e.g. `[feat/… · Batch N/M · Implement] …`
9. **stop conditions** — when to stop and what to write back
10. **done report path** — typically `.elves/runtime/implement/done/batch-N.json`

Store packets under `.elves/runtime/packets/` (ignored runtime tree). Prefer absolute `--prompt-file`
paths when the process CWD is not the host runtime directory.

## Done report schema

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

`implement gate` may read the done report when present and **warn** if missing (dogfood default:
missing report is non-fatal). Host still owns merge/tag and final readiness.

## What Grok may do (when selected as implementer / Mode A1)

- Edit owned product surfaces only
- Run focused and full tests
- Commit and push progress slices on the feature branch
- Write the done report for the batch

## What Grok must not do

- Merge to main, tag, or open a second PR
- Change Survival Guide stop/merge policy unless the packet assigns it
- Touch credentials or secrets
- Review its own work as if it were independent host review
- Use the host-import lease commands unless the packet explicitly selects `untrusted`

## Host after handoff

| Moment | Host does |
|--------|-----------|
| Staging | plan, PR, worktree, prepare metadata, write packet |
| During batch | leave Grok alone (no mid-breath audit by default) |
| Batch end | `implement gate` (tests + tip record); optional native glance |
| Next batch | `implement resume-batch` with next packet, same session |
| Final | cumulative readiness; merge only if authorized |

## Launch text (paste into Grok)

```text
Read the launch packet at the path given via --prompt-file. Restate the contract (owned/forbidden
surfaces, acceptance, validation) in one short block, then implement the whole batch end-to-end.
Do not wait for further host prompts. Commit with the packet's subject schema. Run the packet's
validation commands before claiming done. Write the done report to the path in the packet. Do not
merge, tag, or open a second PR. Do not review your own work as independent review. If blocked,
write status=blocked with blockers[] and stop.
```
