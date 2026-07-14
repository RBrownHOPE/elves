# Elves 2.2: faster trusted runs

## Goal

Keep Elves' quality and safety intent while making trusted Grok Build runs feel like delegation:
one staged objective, visible worker commits, a genuinely parked driver, bounded proof, and one
terminal readiness loop. Preserve Claude Code and Codex as first-class native hosts.

## Safety kernel (must not weaken)

- exact plan/session/packet acceptance identity and criterion text;
- credential, protected-ref, origin, branch, worktree, ancestry, and clean-tip invariants;
- explicit host acknowledgement for declared high-risk checkpoints;
- no worker merge or protected-ref authority;
- test integrity, one live broad current-runtime proof before readiness, one independent terminal
  cumulative review, and required final CI;
- strict detached/import evidence for untrusted writers.

Everything else should be risk-tiered, incremental, or mechanically recoverable.

## Batches

### Batch 0 [B0]: Risk-tiered execution policy

Replace time quotas and repeat-everything language with `validate once, verify changes, attest
final`. Define trivial/docs, standard trusted, high-risk trusted, and untrusted tiers. During a
trusted run, read only new/unresolved PR feedback and wait for asynchronous checks once at terminal.
Category-bug expansion stays within confirmed same-root affected surfaces; unrelated siblings are
advisory follow-up work.

**Acceptance criteria:**

- [ ] [B0-A1] SKILL.md, AGENTS.md, templates, examples, and consistency guards agree on the thin safety kernel and four risk tiers.
- [ ] [B0-A2] Per-batch proof defaults to touched surfaces; broad proof is required at risk checkpoints and terminal readiness, not before every ordinary batch.
- [ ] [B0-A3] Host-native and legacy mid-run pushes use one nonblocking new/unresolved feedback fetch; trusted parked worker pushes defer all host PR polling until terminal readiness.
- [ ] [B0-A4] Bug-category review blocks only confirmed same-root failures in owned or affected shared surfaces and records unrelated siblings as advisory follow-up.

### Batch 1 [B1]: Native Grok goal and truly parked supervision

Add capability-detected native Grok `/goal` use for trusted full-run launch. Do not fake goal mode by
merely adding the word to a normal prompt. Keep a compatible headless fallback when the installed
CLI cannot expose native goal orchestration. Add a blocking `full-run-await` (or equivalent
`--wait`) so the host can make one tool call that returns only on material transition. Make healthy
monitoring incremental: liveness and newly appended event validation on ordinary polls; remote
protected-ref audit on a bounded cadence and at terminal; deep Git/report reconciliation once at
terminal or safety wake. Worker commits and pushes are the live operator readout.

**Acceptance criteria:**

- [ ] [B1-A1] Trusted Grok launch capability-detects and records actual native goal use or an explicit compatible fallback; Claude Code and Codex docs are honest about the invocation surface.
- [ ] [B1-A2] A healthy driver can block in one await/monitor call until material progress, checkpoint, stale/failure, user input, or exit without model-turn polling.
- [ ] [B1-A3] Unchanged healthy polls do not rescan the full event history, repeat deep Git reconciliation, or run an uncached remote all-ref audit.
- [ ] [B1-A4] Terminal/safety reconciliation still proves the complete safety kernel before readiness.
- [ ] [B1-A5] Trusted workers are instructed and tested to commit and push independently reviewable progress slices with concrete subjects.

### Batch 2 [B2]: Recoverable reports and incremental proof

The driver owns the human HTML report. A missing trusted machine report after authenticated clean
exit becomes `driver_wake_reconcile`, never immediate rejection. The host may reconstruct only
independently provable fields after exact ancestry, clean worktree, protected refs/origin,
plan/session/packet binding, checkpoints, host-run acceptance, and tests pass. Record
`provenance: host_reconstructed`; leave worker-only claims unknown. Never reconstruct missing
untrusted security/audit handoffs.

Key test evidence by gate inputs rather than HEAD alone: relevant path/mode/content, command,
dependency/config inputs, runtime/tool identity, and material environment. Pure operational-artifact
cleanup reuses the live broad proof only after mechanically proving the cleanup parent is the proven
tip and the diff deletes exactly recorded run paths without changing the product/test input digest.

**Acceptance criteria:**

- [ ] [B2-A1] Clean trusted exit without a valid report wakes for host reconciliation and can reach readiness only through the independently verified reconstruction path.
- [ ] [B2-A2] Reconstructed reports have explicit provenance, never invent worker claims, and cannot bypass missing checkpoint/security evidence or untrusted-writer handoffs.
- [ ] [B2-A3] Gate evidence survives docs-only or run-metadata-only commits when its complete input digest is unchanged, while runtime/dependency/security changes invalidate the relevant proof.
- [ ] [B2-A4] Cleanup-only current-tip attestation avoids a second broad suite; any non-operational change forces live proof again.

### Batch 3 [B3]: Phase routing, optional media, CI economy, and release

Support phase-aware model and reasoning-effort preferences, including a strong/high-thinking planner
followed by a cheaper/lower-thinking implementer. Capability-detect and record requested/actual
routes and fallbacks for native Claude Code, native Codex, Grok, and optional providers without
making any provider mandatory. Expose Grok image/video generation as optional task capabilities
with bounded artifact ownership and host review, not as a default report format.

Add PR/branch-scoped GitHub Actions concurrency cancellation. Keep the supported OS/Python matrix
as final merge evidence in this PR; do not reduce platform coverage without measured evidence.
Benchmark deterministic two-worker unittest-module execution against sequential execution; ship it
only if counts/results match and process-sensitive modules remain safe, otherwise record the
measured deferral. Update every human/operator surface, bump to 2.2.0, and promote the changelog.

**Acceptance criteria:**

- [ ] [B3-A1] Phase model plus reasoning-effort downshift is configurable, capability-detected, recorded, and equivalent across Claude Code and Codex hosts with native fallback.
- [ ] [B3-A2] Grok image/video capabilities are discoverable and optional, with bounded ownership, review, and graceful unavailable-tier fallback.
- [ ] [B3-A3] Superseded GitHub workflow runs cancel without weakening the final required matrix.
- [ ] [B3-A4] Parallel unittest execution ships only with deterministic parity evidence or is explicitly deferred with benchmark evidence; no optimistic concurrency change.
- [ ] [B3-A5] README, SKILL.md, AGENTS.md, references, examples, templates, tests, consistency policy, version metadata, and CHANGELOG agree on 2.2.0 behavior.

## Master acceptance

- [ ] [M-A1] A trusted Grok full run can be launched as one real goal, observed through worker commits, and supervised without active driver chatter or repeated deep audits.
- [ ] [M-A2] Standard runs do at most one live broad local suite per unchanged product/test input tree, plus required final CI; targeted failures are fixed and rerun precisely.
- [ ] [M-A3] Missing presentation/report ceremony cannot discard otherwise verifiable trusted work, while security, acceptance, and untrusted provenance failures still block.
- [ ] [M-A4] Claude Code and Codex remain fully supported without Grok or any external provider.
- [ ] [M-A5] Focused tests, consistency/release checks, installed-bundle smoke, one broad local suite, and the final GitHub matrix are green.

## Build on

- `scripts/cobbler_runtime/full_run.py` and `tests/test_full_run_supervisor.py`
- `scripts/cobbler_runtime/preflight_cache.py`, `evidence_review.py`, and `scripts/verify_repo.py`
- `scripts/cobbler_runtime/adapters.py`, config/schema/onboarding modules, and adapter tests
- `.github/workflows/repo-consistency.yml`
- existing parked-monitor, exact-acceptance, redaction, storage, and release/consistency helpers

Centralize extensions in those existing surfaces. Do not create a parallel supervisor, cache,
report protocol, or model-routing subsystem.

## Validation budget

During implementation, run exact regressions and affected test modules. Run consistency/release and
installed-bundle checks when their inputs change. Run the broad local unittest suite once on the
final unchanged product/test tree; repeat it only after a material runtime/test/dependency change or
to diagnose/fix a failure. GitHub's full matrix is final platform redundancy.

## Worker contract

The Grok goal may edit product code, tests, product documentation, and the workflow. It must not edit
the survival guide, execution log, `.elves-session.json`, `.elves/runtime`, credentials, refs other
than `codex/faster-goal-runs`, another worktree, or PR/merge state. It should commit and push each
independently reviewable slice and finish with a concise mapping from every acceptance ID to proof.
