# Plan: [Mathematical Research Goal]

> Use this template when the mathematical target is uncertain or when the goal is to produce a
> research note, proof, manuscript, or literature-grounded answer. Math is a Cobbler-managed Elves
> domain workflow: Cobbler routes scouts, critics, auditors, ledgers, and provider roles while the
> human owns mathematical verification. Remove sections that do not apply. Keep the distinction
> between ideas, checks, draft prose, and verified claims.

## Mission

[State the rough mathematical goal in 2-4 sentences. Say what would count as useful progress. If
the theorem is not known yet, say that explicitly.]

Example:
> Investigate whether large horoconvex domains admit a polynomial lower bound for the fundamental
> gap. The first goal is not a full paper; it is a ranked list of plausible theorem targets, known
> obstructions, and quick wins that can be checked rigorously.

## Inputs

- **Known definitions:** [definitions, hypotheses, notation]
- **Known sources:** [papers, books, notes, links]
- **Known examples/counterexamples:** [list]
- **Desired output:** [agenda / proof note / manuscript / source audit / formalization plan]
- **Out of scope:** [topics not to pursue]
- **Verification standard:** [human check expected / formal proof desired / exploratory only]
- **Cobbler route:** [native-subagents/direct-analysis by default; optional configured provider
  routes if explicitly available]

## Batch 1: Discovery Sprint

**Tasks:**
- [ ] Spawn independent subfield scouts across the default lanes:
  geometry/topology, PDE/spectral theory, convexity/optimization, probability/analysis,
  algebraic/combinatorial analogs, numerical experimentation, and formalization prospects.
- [ ] Ask each scout for related solved problems, transferable techniques, natural assumptions,
  early examples/counterexamples, plausible quick wins, proof paths, and source requirements.
- [ ] Run a cross-field synthesis pass that looks for translations and combinations between scout
  reports.
- [ ] Produce a ranked research agenda.

**Acceptance criteria:**
- [ ] Each scout report names sources or search terms to verify, not just intuitions.
- [ ] The synthesis ranks opportunities by tractability, novelty, verification burden, source
  burden, and human value.
- [ ] Every `quick_win` item has a plausible proof path and a clean verification story.

**Docs likely touched:** `docs/math/model-calls.md`, `docs/math/open-questions.md`,
`docs/math/failed-approaches.md`, `docs/math/sources.md`.

**Risk:** Scouts may converge on obvious keyword matches and miss useful adjacent-field transfers.

## Batch 2: Source Grounding

**Tasks:**
- [ ] Collect primary sources for the top-ranked opportunities.
- [ ] Extract exact theorem statements, hypotheses, notation, and constants.
- [ ] Identify what is known, what is folklore, and what remains unverified.
- [ ] Update the source ledger.

**Acceptance criteria:**
- [ ] Every imported result has a primary-source citation or is marked unverified.
- [ ] The top candidate theorem has no hidden source dependency.
- [ ] Any source-access gaps are recorded as blockers or risks.

**Docs likely touched:** `docs/math/sources.md`, bibliography files, manuscript notes.

**Risk:** Secondary summaries may hide hypotheses that break the intended transfer.

## Batch 3: Candidate Theorem And Proof Strategy

**Tasks:**
- [ ] State the strongest plausible candidate theorem and one or two weaker fallback statements.
- [ ] List all dependencies and reductions.
- [ ] Test examples and possible counterexamples.
- [ ] Ask a proof critic to attack the statement before proof writing.

**Acceptance criteria:**
- [ ] The theorem statement is precise enough to be false or true.
- [ ] The proof plan identifies its bottleneck lemma or estimate.
- [ ] Known counterexamples are either ruled out by hypotheses or recorded as blockers.

**Docs likely touched:** `docs/math/claims.md`, `docs/math/open-questions.md`.

**Risk:** The candidate may be true only after adding assumptions that reduce its value.

## Batch 4: Proof Attempt And Derivation Checks

**Tasks:**
- [ ] Write the proof in a durable note.
- [ ] Independently check algebra, limits, constants, inequalities, and edge cases.
- [ ] Ask at least one proof skeptic and one derivation checker to review.
- [ ] Record failed approaches rather than deleting them.

**Acceptance criteria:**
- [ ] Every nontrivial step has either a proof, source, or explicit TODO.
- [ ] Constants and asymptotics are checked independently.
- [ ] Blocking reviewer findings are fixed or the claim is downgraded.

**Docs likely touched:** `docs/math/claims.md`, `docs/math/model-calls.md`,
`docs/math/failed-approaches.md`.

**Risk:** A plausible proof may depend on an unproved regularity or compactness assumption.

## Batch 5: Manuscript Or Research Packet

**Tasks:**
- [ ] Convert verified material into a clean note, manuscript section, or research packet.
- [ ] Audit references and notation.
- [ ] Separate verified results from conjectures, questions, and speculative ideas.
- [ ] Prepare a human-review checklist.

**Acceptance criteria:**
- [ ] The packet can be read without the chat transcript.
- [ ] Every retained claim has proof status, source status, model-review status, and human-review
  status.
- [ ] Remaining risks are named plainly.

**Docs likely touched:** manuscript files, `docs/math/verification.md`, README or handoff notes.

**Risk:** Exposition may sound more confident than the verification ledger supports.

## Non-Negotiables

- Model output is not mathematical evidence.
- Prefer primary sources over secondary summaries.
- Record failed approaches and rejected directions.
- Do not call a result proved until the proof is written in ordinary mathematical form and reviewed
  by a human.

## Tool Configuration

```yaml
math-coordination: cobbler-managed-domain-workflow
math-provider-policy: native-first-with-optional-external-routes
math-required-env: []
math-source-search: exa-optional
math-subfield-scouts: geometry, pde-spectral, convexity-optimization, probability-analysis, algebraic-combinatorial, numerical, formalization
math-ledger-dir: docs/math
math-external-route-examples:
  # proof_critic: openrouter:<model-id>
  # derivation_checker: gemini:<model-id>
  # evolutionary_search: alphaevolve:<task-id>
math-optional-tools:
  # - alphaevolve
math-alphaevolve:
  enabled: false
  auth: gcloud-impersonation
  artifact_dir: alphaevolve_runs
  promote_policy: independent-local-replay-only
review: github-pr-comments
```
