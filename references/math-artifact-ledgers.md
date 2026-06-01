# Math Artifact Ledgers

Math runs need durable ledgers because chat transcripts are not reliable memory. Keep the ledgers
in the project, usually under `docs/math/` unless the survival guide configures another directory.

The ledgers distinguish:

- **ideas:** possible directions, not claims;
- **checks:** model or tool reviews, not authority;
- **draft prose:** manuscript language, not proof status;
- **verified results:** claims reviewed by a human in ordinary mathematical form.

## Directory Template

```text
docs/math/
  claims.md
  sources.md
  model-calls.md
  open-questions.md
  failed-approaches.md
  verification.md
```

## Claim Ledger

Use `docs/math/claims.md`.

```markdown
# Claim Ledger

| ID | Statement | Type | Status | Proof location | Dependencies | Source status | Model-review status | Human-review status |
|---|---|---|---|---|---|---|---|---|
| C001 | [precise statement] | theorem/lemma/proposition/remark/conjecture | idea/candidate/draft/proved/rejected | [file/section] | [CIDs/SIDs] | verified/unverified/mismatch | pending/clear/patch/reject | pending/verified/rejected |
```

Rules:

- Use precise mathematical statements, not vague summaries.
- If a proof depends on an external theorem, link the source ID.
- If a model rejects or patches the claim, update the model-review status.
- Do not mark `proved` unless the proof is written and human review is complete or explicitly in
  progress with the status separated.

## Source Ledger

Use `docs/math/sources.md`.

```markdown
# Source Ledger

| ID | Citation | Primary source? | Result used | Exact location | Hypotheses | Draft use | Status | Notes |
|---|---|---|---|---|---|---|---|---|
| S001 | [bibliographic entry] | yes/no | [theorem/equation] | [page/theorem/section] | [hypotheses] | [where used] | verified/needs_primary_source/hypothesis_mismatch/overclaim/unresolved | [notes] |
```

Rules:

- Prefer primary sources.
- Record theorem numbers, equations, pages, or sections.
- Mark secondary summaries as `needs_primary_source` unless the run explicitly accepts them.
- Record hypothesis mismatches instead of smoothing them over in prose.

## Model-Call Ledger

Use `docs/math/model-calls.md`.

```markdown
# Model-Call Ledger

| ID | Date | Role | Provider/model | Prompt/source path | Input scope | Verdict | Action taken |
|---|---|---|---|---|---|---|---|
| M001 | YYYY-MM-DD | proof_critic | openrouter:<model-id> | references/math-review-prompts.md#proof-critic | C001 proof draft | PATCH_REQUIRED | patched Lemma 2 statement |
```

Rules:

- Record material calls, not every tiny formatting request.
- If a provider fallback occurs, record the reason.
- Do not use model agreement as proof status.
- Record when reviewers disagree and how the disagreement was adjudicated.

## Open-Question Ledger

Use `docs/math/open-questions.md`.

```markdown
# Open Questions

| ID | Question | Origin | Needed to prove | Current best path | Owner/status |
|---|---|---|---|---|---|
| Q001 | [question] | scout/proof/source/human | [claim IDs] | [next test] | open/deferred/closed |
```

Rules:

- Keep speculative branches here instead of hiding them in prose.
- Close questions with a source, proof, counterexample, or explicit deferral.

## Failed-Approach Ledger

Use `docs/math/failed-approaches.md`.

```markdown
# Failed Approaches

| ID | Approach | Intended claim | Failure mode | Evidence | Reusable lesson |
|---|---|---|---|---|---|
| F001 | [approach] | C001 | missing hypothesis / false example / bad constant | [source or review] | [lesson] |
```

Rules:

- Failed approaches are assets. They prevent the run from looping.
- Record the exact reason for failure and whether a weakened version remains possible.

## Human-Verification Ledger

Use `docs/math/verification.md`.

```markdown
# Human Verification

| ID | Item reviewed | Reviewer | Date | Scope | Verdict | Notes |
|---|---|---|---|---|---|---|
| H001 | C001 proof | [name] | YYYY-MM-DD | theorem statement and proof | verified/patch/reject | [notes] |
```

Rules:

- Human verification is the gate that promotes a retained result.
- State what was reviewed: theorem statement, proof, constants, sources, exposition, or all of the
  above.
- If a human review is pending, say pending. Do not imply it has happened.

## Readiness Checklist

Before sharing a paper or research packet:

- [ ] Every theorem, lemma, proposition, and conjecture appears in the claim ledger.
- [ ] Every external theorem used in a proof appears in the source ledger.
- [ ] Every material model review appears in the model-call ledger.
- [ ] Open questions and failed approaches are not hidden in the manuscript text.
- [ ] Human verification status is clear for every retained mathematical result.
- [ ] The final document distinguishes proved results, conjectures, and research directions.
