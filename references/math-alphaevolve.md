# Math Module: Google AlphaEvolve (optional)

Google Cloud **AlphaEvolve** is an optional math-domain tool for **evolutionary program search**
with a **deterministic local evaluator**. Use it to generate high-quality numerical examples,
extremal families, and counterexample *signals* while exploring hypotheses.

It is **not** a chat reviewer, not a proof engine, and not required for ordinary Cobbler or math
Discovery Sprints.

**Reference pattern:** production math runs (e.g. Aigora geometry-exploration) implement thin
Python runners that call the managed AlphaEvolve API, isolate candidate programs, score them
locally, and independently replay winners.

## When to use it

Good fit when all of these hold:

1. The scout question can be reduced to a **small evolvable program** (seed with an `EVOLVE-BLOCK`
   or equivalent).
2. Legality of a candidate is **checkable deterministically** (AST allowlist, fixed-size inputs,
   finite grid, exact algebra).
3. Progress has a **numerical score** that rewards the event you care about (e.g. most negative
   Hessian floor, largest gap, sharpest constant).
4. A human or host will **replay** top programs outside the managed ranking loop before promoting
   any conclusion.

Typical jobs:

- search for counterexample families to a proposed inequality;
- rediscover or stress-test extremal profiles / constants;
- generate diverse high-quality examples for hypothesis exploration;
- find that an auxiliary bound is too strong (numerical obstruction signal).

Do **not** launch AlphaEvolve just to “do process.” If there is no finite evaluator, record that
and wait for a bounded subproblem.

## Capability scan

During Cobbler’s math capability scan, treat AlphaEvolve as available only when:

- the project has (or will write) a task runner + local evaluator;
- Google Cloud auth works via **short-lived** credentials (preferred: `gcloud` service-account
  **impersonation**, no long-lived service-account keys in the repo);
- the user wants evolutionary search for this batch.

Missing AlphaEvolve access never blocks native math work. Record “AlphaEvolve unavailable” in the
model-call ledger only when the run had planned to use it.

## Role slot

Stable math role name:

| Role | Job | Default |
|---|---|---|
| `evolutionary_search` | Bounded program evolution + deterministic scoring for examples / counterexample signals | off / project wrapper |

Optional Survival Guide / config:

```yaml
math-optional-tools:
  - alphaevolve   # Google Cloud AlphaEvolve when configured
math-role-models:
  evolutionary_search: alphaevolve   # or custom-cli wrapper path
```

Route form for ledgers: `alphaevolve:<experiment-or-task-id>` (project-local label, not a chat
model id).

## Operator cycle (project-owned)

Projects implement their own runners. The geometry-exploration shape is:

```bash
# 1) Validate evaluator + seed without calling Google
python tools/alphaevolve_<task>.py --dry-run

# 2) Optional: audit the evaluator itself
python tools/<task>_alphaevolve_evaluator_audit.py

# 3) Managed evolutionary search (bounded)
python tools/alphaevolve_<task>.py --max-programs 64

# 4) Independent local replay of leaders (required before promotion)
python tools/<task>_alphaevolve_replay.py
```

Artifact layout (suggested):

```text
alphaevolve_runs/<batch-or-task-id>/
  README.md                 # what is being optimized; metric; legality rules
  result.json               # managed experiment metadata + ranked programs
  result_*.json             # region-specific or corrected runs
  seed / evaluator notes
```

## Safety and mathematical hygiene

These rules come from live math use; treat them as defaults:

1. **Untrusted candidates.** Managed programs are untrusted code. Sandbox evaluation (subprocess,
   CPU/memory limits, AST allowlist, no network inside the candidate).
2. **Local evaluator owns the score.** The cloud service proposes mutations; the **repo** decides
   legality and metric.
3. **Reward the event you seek.** If you are hunting a negative Hessian, do not discard candidates
   because the Hessian went negative.
4. **No convenience bonuses** near a sharp mathematical threshold (e.g. profile-count bonuses that
   swamp true metric differences).
5. **Preserve the geometry.** Keep phases, symmetries, and exact algebraic structure the evaluator
   needs; do not project away structure that hides counterexamples.
6. **Transition band then full replay.** If a universal tail flattens the objective, evolve on a
   transition band and revalidate winners on the full domain / denser grid.
7. **Promote only after independent replay.** Save raw programs and experiment names; promote only
   a locally replayed formula, an exact identity rediscovered and proved, or a clearly labeled
   **numerical obstruction** (not a theorem).
8. **Auth.** Prefer keyless gcloud impersonation and short-lived tokens. Never commit service-account
   keys, project secrets, or tokens. Do not print credentials.
9. **Authority.** AlphaEvolve output is **check evidence**, same class as numerical scouts. It never
   alone moves a claim to `proved`.

## Ledgers

Record material AlphaEvolve work in the math ledgers:

- **model-calls.md:** role `evolutionary_search`, route `alphaevolve:<task>`, metric, program count,
  fallback/idle reasons.
- **claims.md / open-questions.md:** only after independent replay; mark numerical signals as such.
- **failed-approaches.md:** buggy objectives, flattened metrics, false sharp floors discovered by
  search.

## Survival Guide block (optional)

```yaml
math-alphaevolve:
  enabled: false   # set true only when GCP + runner exist for this project
  auth: gcloud-impersonation   # no long-lived keys in repo
  artifact_dir: alphaevolve_runs
  promote_policy: independent-local-replay-only
  role: evolutionary_search
```

## Related

- Workflow: [`math-workflow.md`](math-workflow.md)
- Provider/roles: [`math-provider-config.md`](math-provider-config.md)
- Ledgers: [`math-artifact-ledgers.md`](math-artifact-ledgers.md)
- Cobbler optional-tool recipes: [`cobbler-setup-recipes.md`](cobbler-setup-recipes.md)
