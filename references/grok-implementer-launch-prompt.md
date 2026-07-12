# Grok Implementer Launch Prompt (Lane A — Fast Path)

Default path when the user says **“have Grok run it.”** Smart host plans and gates; one persistent
Grok Build session implements whole batches. Design authority:
[`docs/plans/smart-plan-grok-implement.md`](../docs/plans/smart-plan-grok-implement.md).

For the untrusted detached-writer lease path (Lane B), use the advanced worker lease flow in
[`councilelves-launch-prompt.md`](councilelves-launch-prompt.md) and
`python3 scripts/cobbler_agents.py worker …`. Do **not** use Lane B as the default overnight path.

## Survival Guide field

```text
implementation_lane: fast | untrusted
```

- **`fast` (default)** — Lane A: Grok owns feature-branch progress in a host-created worktree;
  host launches once, gates between batches, final readiness stays smart-host-owned.
- **`untrusted`** — Lane B: exclusive writer lease, detached commits, host audit/import only.

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

## Launch invariants (Lane A)

| Setting | Value | Why |
|---------|-------|-----|
| Session | exact UUID create once; `--resume` for every later batch | preserve context |
| Permission | `auto` (or `acceptEdits`) | never default headless to `dontAsk` |
| Subagents | enabled | never pass `--no-subagents` |
| Unit of work | whole batch per packet | avoid mid-breath host tax |
| Prompt | `--prompt-file` packet on disk | stable, resumable |
| Model default | `grok-4.5` | product default for this path |
| Git default | `branch_progress` (Mode A1) | Grok commits/pushes progress slices on the feature branch |

Example create (host or human):

```bash
grok --session-id <uuid> \
  --cwd <worktree> \
  --model grok-4.5 \
  --permission-mode auto \
  --prompt-file .elves/runtime/packets/batch-1.md
```

Example resume:

```bash
grok --resume <uuid> \
  --cwd <worktree> \
  --model grok-4.5 \
  --permission-mode auto \
  --prompt-file .elves/runtime/packets/batch-N.md
```

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

## What Grok may do (Lane A / Mode A1)

- Edit owned product surfaces only  
- Run focused and full tests  
- Commit and push progress slices on the feature branch  
- Write the done report for the batch  

## What Grok must not do

- Merge to main, tag, or open a second PR  
- Change Survival Guide stop/merge policy unless the packet assigns it  
- Touch credentials or secrets  
- Review its own work as if it were independent host review  
- Use Lane B lease commands unless the packet explicitly selects `untrusted`  

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
