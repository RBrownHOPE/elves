# Joyful runs contract (Elves 2.3)

Machine source of truth: `scripts/cobbler_runtime/canonical_contract.py`.

## Happy path

```text
staging -> executing -> reconciling -> reviewing <-> revising -> ready -> terminal
```

1. Plan with acceptance, risk, caution, affected surfaces, constitution impacts, focused tests.
2. Stage once (branch, PR, session, preflight, packet).
3. Hand one self-contained packet to a trusted worker (or implement host-native).
4. Park the driver. Follow the sanitized worker stream (no model inference).
5. On terminal wake: reconcile, one cumulative review, consolidate blockers, revise, delta re-review.
6. Attest readiness at an exact HEAD. Land as a landable PR, or merge only with explicit host authorization at that same HEAD.

## Independent axes

| Axis | Values | Notes |
|------|--------|-------|
| `risk` | `low` \| `standard` \| `high` | Selects proof depth |
| `trust_mode` | `trusted` \| `untrusted` | Selects writer authority boundary |
| `landing_outcome` | `landable_pr` \| `complete_and_merge` | Host-owned |
| `ready` | bool | Exact-HEAD attestation only |
| `driver_authorized` | bool | User/host grant only |

Invariants:

- ready=true never grants merge permission.
- `driver_authorized=true` never proves readiness.
- Merge requires both at the same exact HEAD.
- Worker evidence cannot grant merge or change `landing_outcome`.

## Actors

| Actor | May | Must not |
|-------|-----|----------|
| User | Grant merge authorization, merge, stop the run | — |
| Driver (Claude Code / Codex) | Stage, park, follow, reconcile, review, attest readiness, merge when authorized | Infer authorization from worker text |
| Worker | Edit owned product surfaces, commit/push feature branch, emit events | Merge, tags, protected refs, PR ops, run memory, landing policy |
| Reviewer | Independent terminal review | Edit product code as part of review authority |

## Wake conditions (deterministic)

Death, hangs, stale heartbeat, missing/malformed completion, safety tripwires, explicit blockers,
material scope/assumption changes, high-risk checkpoints, user input, worker exit, final readiness,
reconcile. **Not** per-push, per-tool, per-batch prompts, timed chat updates, or model monitor ticks.

## Proof

- Impact path: changed surface → affected consumer → selected test.
- Evidence records inputs + invalidation scope; reuse when digests match.
- Broad proof at high-risk checkpoints and terminal readiness.
- Cleanup-only operational deletes do not invalidate product proof.
- Test integrity: never weaken for green.

## Terminal outcomes

- **landable_pr** (default complete-without-merge)
- **complete_and_merge** (regular merge commit only, never squash), when ready + authorized at exact HEAD

Both share one readiness pipeline (`landing_authority.shared_readiness_pipeline_id`).

## Host parity

Claude Code and Codex share identical workflow semantics. Only the invocation surface differs
(Claude slash skills/aliases vs Codex `$elves` / natural language). Neither requires Grok or
optional providers. See `references/host-parity.md`.
