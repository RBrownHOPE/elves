---
version: "2.4.0"
---

# Elves: Codex repository adapter

This file is a **thin Codex adapter**, not a second workflow fork. The compact **canonical workflow is `SKILL.md`**. Runtime, authority, follow mode, proof, and host-parity details live in
`references/joyful-runs-contract.md`, `landing-authority.md`, `follow-mode.md`,
`proof-and-review.md`, `host-parity.md`, and `schema-and-acceptance.md`.

When Codex loads this repository, follow `SKILL.md` for all Elves behavior. Differences below are
**invocation surface only** — workflow semantics, safety kernel, landing policy, and acceptance
identity are identical to Claude Code.

## Cobbler

This file is a **thin Codex adapter**; the compact **canonical workflow is** in `SKILL.md`
(SKILL.md wins on workflow). Default user path **v2.0+** / **v2.1** / **v2.3**. Use Cobbler via
`$elves cobbler: <task>` or natural language. **Codex Goals** are continuation plumbing only;
**Grok Build goal mode** is a separate worker capability. Default work routing is a separate
subscription-native Codex/Claude worker at plan-matched effort; permitted Grok is optional and
capability-probed. Safe preferences are shared at
`${XDG_CONFIG_HOME:-~/.config}/elves/config.json`. Repository safety vetoes win; convenience order
is explicit run intent, repository defaults, global preferences, then built-ins. Repository allow
is not Grok consent. Regular clear Grok work pins Composer 2.5 Fast; genuinely complex work may
explicitly pin Grok 4.5.
Default landing remains **chat-to-work**
(or **chat-to-land** when authorized); aliases `\land-pr` / `/land-pr`. Honor **Stop Gate** /
`continuation_guard`. Landable is **plan Acceptance with proof**. Installed helpers use the
**active Elves skill root** and **source-checkout shorthand** while keeping the **target repository as the working directory**;
`$ELVES_SKILL_ROOT/scripts/elves_landing_check.py --session <session-path> --repo-root .`.
Coordinator handoff: **Build On**, **owned surfaces**, **forbidden surfaces**, **acceptance evidence**, **blocking coordinator defect**. Progress subjects use
`Contract|Implement|Validate|Review|Close` and **Forbid vague subjects**. Full Cobbler protocol
lives in `SKILL.md` — this adapter does not re-fork it.

## Codex invocation (host-honest)

| Intent | Codex |
|--------|--------|
| Run Elves | natural language or skill load; not an invented top-level `/elves` unless the install provides it |
| Cobbler | `$elves cobbler: <task>` or “Ask the Cobbler…” |
| Cobbler Mode | `$elves cobbler-mode` or natural “Cobbler Mode: on/off” |
| Setup | `$elves setup-cobbler` / `$elves setup-council` |
| Council aliases | `$elves council: <task>` (same Cobbler behavior) |
| Land PR | natural language; `\land-pr` / `/land-pr` when the host maps them |

Do **not** invent top-level Codex slash commands for implement, setup, or cobbler. Claude Code
managed aliases (`/cobbler`, `/setup-cobbler`, `/land-pr`, …) are Claude-specific surfaces.

## Codex Goals vs Grok goal mode

- **Codex Goals** — optional host continuation seatbelt for long Codex sessions. Not durable memory.
  See `references/codex-goals.md`. `/goal` in this sense is a continuation aid, not a product plan.
- **Grok Build goal mode** — optional trusted-worker orchestration when capability-proven; otherwise
  the compatible one-packet fallback is recorded honestly. Distinct from Codex Goals.

## Recovery (same as SKILL)

After compaction: survival guide (Stop Gate + Run Control) → `.elves-session.json` → learnings →
plan → execution log → `.ai-docs/manifest.md` → constitution. Honor
`continuation_guard.stop_allowed`. Resume the single next required action.

## Non-negotiables (pointers into SKILL)

- **Stop Gate** / `continuation_guard` — do not send a final response while stop is disallowed
- After every host-owned commit and push, re-read the survival guide before doing anything else.
  Do not wait for user acknowledgment
- **Effort Standard**: Do not be lazy. Work as hard as you can for the full run
- **Final Readiness Review** of `git diff <default-branch>...HEAD` (review subagent when available)
- Default user path (v2.0+): one kickoff; v2.1 adds trusted Grok full-run; v2.3 joyful parked
  follow path; **chat-to-work** / **chat-to-land**; full-run parked-monitor; legacy two-call handoff
  only when explicit
- Adaptive worker route: separate exact native session by default, optional permitted Grok,
  deterministic inspectable fallback, and no transferable parent/worker prompt-cache promise
- Landable is **plan Acceptance with proof** (`elves_landing_check.py`); one batch per close commit;
  God-file rule
- Installed helpers use the **active Elves skill root** / `$ELVES_SKILL_ROOT` (**source-checkout shorthand**: `python3 scripts/...`). Paths: `~/.claude/skills/elves`, `~/.codex/skills/elves`, `$ELVES_SKILL_ROOT/scripts/acceptance_contract.py`, `$ELVES_SKILL_ROOT/scripts/elves_landing_check.py`. An installed Elves bundle never requires a repo-only helper
- Landing check: `python3 "$ELVES_SKILL_ROOT/scripts/elves_landing_check.py" --session <session-path> --repo-root .`
  — session `plan_path` is authoritative; explicit `--plan` is only an equality assertion
- Thin safety kernel; risk `low|standard|high` with independent trust mode; validate once, verify
  changes, attest final; touched surfaces; risk checkpoints; terminal readiness
- Git history as operator UI: `[<branch> · Batch N/total · Contract|Implement|Validate|Review|Close] <concrete outcome>`;
  Forbid vague subjects; audited detached handoff commits; never own refs, remotes, push, PRs, or
  canonical run memory; Close phase for acceptance-backed completion; Protected refs, PR operations,
  and merge never dispatch model inference
- Anti-patterns: `[feat/auth · Batch 3/12] Updates`, `[feat/auth · Batch 3/12 · Implement] progress`
- Coordinator-to-implementer handoff: Build On targets, owned surfaces, forbidden surfaces,
  acceptance evidence; incomplete handoff is a blocking coordinator defect
- Workspace isolation / one branch one checkout / collision tripwire
- Cobbler-first coordination; Quick Cobbler read-only; provider routes optional
- Reviewed PR Landing Command and user-owned merge; regular merge commit only
- Elves Report under `/tmp` for substantial runs
- Math domain workflow remains Cobbler-managed when invoked
- Public API surface snapshots optional unless `required: true` explicitly set
- Model routing native-first with honest fallback

## Authoritative sources when this file and SKILL disagree

**`SKILL.md` wins** for workflow. This adapter wins only for Codex invocation wording. If you find
divergence, fix this file to re-point at SKILL rather than re-forking protocol text.

## Docs hygiene

Treat stale user-facing docs as **PENDING-DOCS** until updated (see SKILL.md).
