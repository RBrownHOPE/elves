# Execution Log: Issue #86 review fixes (v2.10.2)

## Run metadata

- Branch: `claude/issue-86-review-fixes`
- Base: `origin/main` at `0e58fde` (v2.10.1)
- Starting tip (START_TIP / collision tripwire): `0e58fde77baa9c5a256f081dde07d2ca2c10a72a`
- Worktree: `/Users/john/aigora/dev/elves-issue-86-review-fixes`
- Plan: `docs/plans/v2.10.2-issue-86-review-fixes.md`
- Survival guide: `docs/elves/survival-guide-issue-86-review-fixes.md`
- Run id: `issue-86-review-fixes-2026-07-19`
- Driver: Claude Code (Fable 5), interactive session, parked during worker run
- Worker route: separate subscription-native Claude Code session, exact `claude-fable-5`,
  effort `low` (explicit user route: "hand off to fable low")
- Worker session: recorded at launch (see `.elves-session.json` `worker_session_id`)
- Merge authority: chat-to-land — explicit user kickoff this session ("land the pr");
  merge-commit-on-green after Final Readiness

## Baseline (2026-07-19, staging)

- `python3 -m unittest discover -s tests` green at `0e58fde` (1,213 tests; exit 0 under
  pipefail).
- Worktree clean at START_TIP before staging docs.
- Prewalk capability probe (read-only, no model calls):
  `cobbler_agents.py native-worker prewalk-capabilities --host claude --json` →
  `qualified: false`, `unavailable_reason: prewalk_exact_resume_unqualified`,
  `instruction_fidelity: unsupported`, installed claude `2.1.207`, evidence
  `installed_help_only`. Per `references/prewalk.md`, requested mode `auto` therefore records
  actual mode `off`; the handoff is an honest one-packet cold handoff (not prewalk), and no
  behavioral qualification is fabricated.

## Issue #86 triage (driver verification, 2026-07-19)

Verified directly at cited lines and via an independent read-only verification pass:

- Bug 1 CONFIRMED (`full_run.py:859–863` regex requires "batch"; `:906` `or index` falsy-zero
  trap with 1-based enumerate; `:924` event merge keyed on 1-based report map while events are
  zero-based → B1 events merge under B0's label, B0 orphans, same-batch conflict never fires).
- Bug 2 CONFIRMED (`cobbler_agents.py:3121–3128` literal-token scan + default `allow_abbrev` →
  `--effor low` parses but reports not-explicit; `:1797–1805` then substitutes the grok default).
- Risk 3 CONFIRMED (`full_run.py:7404–7422`: `allow_partial_final=False` + all-or-nothing
  `confidence_event_errors` gate discards every event on one torn line).
- Risk 4 PARTLY: `_validate_confidence_fields` (`:771–811`) never inspects `unsure_about_count`
  (gap confirmed) but every projection copy clamps to `MAX_UNSURE_ABOUT_ITEMS = 16`
  (`provider_auth.py:72`), so the "renders 9999 reservation(s)" repro does not occur. Scope:
  validation-layer defense-in-depth (plan B1-A3).
- Risk 5 CONFIRMED (fixture path skips reconcile at `:6577–6581`; `review_context` sourced at
  `:6703–6707`; await re-emits at `:7192,7202`; no test asserts monitor/await carry it; three
  print sites `cobbler_agents.py:1867,1914,1934`).
- Risk 6 CONFIRMED (`grok-implementer-launch-prompt.md:162` high default vs `:241` anti-pattern
  row).
- Risk 7 CONFIRMED (`worker_routing.py:999–1003` auto→high; `prewalk.py:136–151` route_matches
  equality; `worker_routing.py:1056–1068` mismatch fallback; no live artifact exists → impact
  nil today; operator note is the fix).
- Risk 8 CONFIRMED (raw adapter compare in `cmd_implement` vs normalized
  `full_run.py:3523–3528`).
- Polish 9–14 CONFIRMED as described (overflow fallback `:1092–1100` with
  MAX_REVIEW_CONFIDENCE_PROMPT_CHARS=128KiB at `:213`; bare "..." truncation `:1068–1072`;
  order-sensitive tuple compare `:986–995`; four projection copies `:814–856`, `:4951–4973`,
  `:6186–6211`, `:6245–6269`; no SHA cross-check between `worker_routing.py:37` and
  `consistency_policy.py:2187–2188`; `PUBLIC_WORDING_FORBIDDEN_PHRASES` `:1803–1811` misses
  bare-Fable persona claims; `prewalk.md:166` pins `--execution-effort medium`).
- Backlog 15 CONFIRMED (`leases.py:281–289` `timeout: float | None = None` → `subprocess.run`
  `:300`; 11 call sites on the default; `git_contract.py` routes through it, own `_remote_refs`
  bounded 15s).
- Backlog 16 CONFIRMED (path-based `_read_bounded_regular_json` `prewalk.py:242–275` vs fd-bound
  `worker_routing._read_goal_canary_artifact` `:191–215` and
  `prewalk._read_qualification_artifact_json` `:962–1037`).
- Consistency-pin sweep: `*_PHRASES` maps are required-substring pins (no hashes/line pins); the
  strings our doc edits touch are not pinned except commit strings in
  `references/grok-open-source-worker.md`; `check_repo_consistency.py:176–177` reads
  `grok-implementer-launch-prompt.md` — B4 owns keeping pins green.

### Decisions made

- Scope: accept issue items 1–16 across six batches (B1 confidence family, B2 effort authority,
  B3 salvage + delivery proof, B4 docs/policy guards, B5 supervisor hardening, B6 release
  v2.10.2).
- Defer item 17 (full_run decomposition — dedicated run; B1's helper unification removes the
  worst duplication), items 18–19 (roadmap feature work needing product decisions), item 20
  (operator-owned live Grok canary requiring a real authenticated install). Recorded in plan Out
  of Scope; TODO.md entries land in B6.
- Delegation: trusted separate subscription-native Claude worker, exact `claude-fable-5` /
  `low` (explicit user route), `branch_progress` on this branch only, parked driver, explicit
  handoff v1 (session `handoff` object + leading Markdown packet capsule).
- Release: ship as v2.10.2 following `release_checklist.py` conventions (SKILL.md frontmatter is
  version of record; AGENTS.md matches; CHANGELOG heading promotion).
- Prewalk: honest fallback (see Baseline); requested `auto`, actual `off`.

## Staging (2026-07-19)

- Plan, survival guide, this log, and `.elves-session.json` written; `acceptance_contract.py
  sync-session --write` derived all 28 batch rows + 3 master rows; `validate` PASS (explicit
  handoff v1: session `handoff` object + leading Markdown packet capsule; 31 packet rows).
- Preflight: origin `https://github.com/aigorahub/elves.git`; gh auth `john-aigora`;
  `.elves-session.json` tracked (not ignored); worktree clean at commit time.
- Staging commit `0521668` pushed; PR #87 opened (draft) with issue mapping and deferral
  rationale.
- Worker session UUID minted: `fff5a5a1-1ae6-43c5-b57c-c2508f31b3c4`.
- Packet: `.elves/runtime/worker-packet-issue-86-review-fixes.md`, generated from template +
  session (capsule launch_head bound to the pre-launch tip by `finalize_packet.py`; acceptance
  rows emitted verbatim from the session so plan/session/packet cannot drift).

### Decisions made (staging mechanics)

- PR body uses `Closes #86`: accepted items land here; deferrals are tracked in TODO.md by
  B6-A3, so closing the review issue on merge is correct hygiene. Final issue comment maps all
  20 items at landing.
- Draft PR until Final Readiness; marked ready only after cumulative review + gates.
- Rollback authority: single host-owned `b0` ref at the pre-launch tip (trusted full-run shape).

## Launch (2026-07-19)

- (recorded below as it happens)
