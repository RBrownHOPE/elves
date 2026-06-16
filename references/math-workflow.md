# Math Research Workflow

This reference configures a Cobbler-managed Elves domain workflow for mathematical research. It is
a beta workflow kit, not a proof oracle. Use it when the work may involve preliminary research,
proof search, source audit, manuscript drafting, or post-draft review.

Cobbler is the coordinator. It classifies the mathematical intent, builds the math context packet,
routes scouts, proof critics, source auditors, derivation checkers, and optional provider-backed
roles, collects evidence into the math ledgers, preserves the strongest dissent, and fits one
research agenda or proof-review verdict back into the normal Elves run.

The core rule is simple: models can generate ideas, search literature, criticize derivations, and
explain drafts. They do not certify mathematics. A claim becomes verified only when a human records
the proof and source checks in ordinary mathematical form.

## Cobbler Harness Mapping

Map the general Cobbler loop onto math work like this:

- **Intent:** classify the request as discovery, source grounding, theorem drafting, proof attack,
  derivation check, manuscript work, or final packet review.
- **Capability scan:** inspect available sources, math ledgers, host subagents, configured role
  routes, search tools, formalization tools, and user verification requirements.
- **Route and medium selection:** choose scout lanes, critic roles, source-audit passes, direct
  derivation checks, or manuscript editing, then decide whether the output belongs in chat, a proof
  note, a ledger, a PR comment, or a handoff packet.
- **Context packet:** give each role the same goal, definitions, hypotheses, known sources,
  current claim/source/model-call ledger state, scope, and forbidden actions. Do not include
  secrets or provider keys.
- **Execute agents/tools/skills:** use host-native subagents or direct analysis by default, and
  use configured external provider routes only when the survival guide or config enables them.
- **Collect evidence:** record role reports, source locations, counterexamples, proof gaps,
  derivation checks, disagreements, and confidence changes in the math ledgers.
- **Fit answer:** return one agenda, theorem route, proof-review verdict, or manuscript action
  plan with the strongest dissent visible.
- **Present/record:** record material domain evidence in `docs/math/*` ledgers and material run
  decisions in normal Elves memory.
- **Reclassify:** if a proof attempt reveals missing sources, route back to source grounding; if a
  theorem statement fails, route to weakened statements or failed-approach logging.

## When To Use This Workflow

Use the math workflow when the user's request looks like any of these:

- "Can AI work on this conjecture?"
- "Find a theorem we might be able to prove around this idea."
- "Work out this asymptotic."
- "Check whether this result follows from known papers."
- "Turn these notes into a paper."
- "Get several strong models to review this proof."
- "Audit every reference and make sure the citations are used correctly."

Do not skip directly to theorem drafting when the target is vague. If the user gives a rough
mathematical goal, start with a Discovery Sprint.

## The Discovery Sprint

A Discovery Sprint is the preliminary-research loop. Its goal is not to prove the theorem. Its goal
is to discover what looks possible.

### Inputs

Record these before spawning scouts:

- the rough goal or question;
- known definitions and hypotheses;
- known papers, if any;
- what would count as useful progress;
- what is out of scope;
- available host subagents, direct-analysis capacity, configured provider routes, search tools, and
  source access;
- whether the user wants breadth, a quick win, or a publication-grade result.

### Scout Lanes

Spawn independent scouts across the relevant and adjacent mathematical terrain. The default lanes
are:

- **Geometry/topology:** curvature, comparison geometry, convexity, topology, geometric flows.
- **PDE/spectral theory:** eigenvalues, heat kernels, boundary estimates, maximum principles.
- **Convexity/optimization:** convex bodies, log-concavity, localization, variational methods.
- **Probability/analysis:** concentration, stochastic representations, functional inequalities.
- **Algebraic/combinatorial analogs:** discrete models, graph versions, representation-theoretic
  shadows, extremal constructions.
- **Numerical experimentation:** asymptotics, model examples, counterexample search, symbolic
  simplification.
- **Formalization prospects:** theorem statement hygiene, reusable lemmas, possible Lean/Coq/Isabelle
  entry points.

Add or remove lanes to match the problem. The point is independence and diversity: scouts should
not only search for the same keywords.

### Scout Questions

Each scout should answer:

1. What closely related problems have been solved?
2. What techniques from this subfield could transfer?
3. What assumptions would make the rough goal more tractable?
4. What examples or counterexamples should be checked early?
5. What is the most plausible quick win?
6. What proof path would make that quick win verifiable?
7. What sources must be read before trusting this direction?
8. What would falsify or substantially weaken this direction?

### Cross-Pollination

After the scouts report, run a synthesis pass that looks for translations between fields. Do not
only merge similar findings. Look for results that can be carried across language:

- a PDE maximum principle that acts like a convexity statement;
- a spectral estimate that resembles a one-dimensional optimization problem;
- a comparison-geometry lemma that supplies the missing hypothesis in an analysis argument;
- a discrete or numerical model that exposes the right normalization;
- a formalization constraint that forces a cleaner theorem statement.

Some mathematical progress comes from noticing that two subfields already solved nearby problems
under different names. The synthesis pass should explicitly ask what can be borrowed, translated,
or recombined.

### Research Agenda

The output of a Discovery Sprint is a ranked agenda, not a manuscript. Rank each opportunity by:

- **Tractability:** how plausible the proof path is.
- **Novelty:** whether the result looks new or meaningfully sharper.
- **Verification burden:** how hard it will be to check rigorously.
- **Source burden:** how much literature must be audited first.
- **Human value:** whether the result would matter to the mathematician or project.

Use this status language:

- `quick_win`: plausible proof path, clean verification story, likely useful.
- `promising`: substantial but plausible with more work.
- `speculative`: interesting but uncertain or high verification burden.
- `blocked`: requires missing source access, unavailable theorem, or unclear hypotheses.
- `reject`: contradicted by known examples, source audit, or proof obstruction.

## Claim Lifecycle

Every serious mathematical statement moves through this lifecycle:

1. **Idea:** generated by a scout, model, numerical check, or human.
2. **Candidate claim:** stated precisely enough to attack.
3. **Proof sketch:** proposed path with dependencies listed.
4. **Derivation check:** algebra, estimates, limits, and constants independently checked.
5. **Source audit:** every external result traced to a primary source and correct hypothesis.
6. **Adversarial review:** independent critic searches for counterexamples, missing assumptions, and
   invalid inference.
7. **Human verification:** a human reviews the claim in ordinary mathematical form.
8. **Draft integration:** only then should it be treated as a retained theorem, lemma, proposition,
   or remark in a manuscript.

Model agreement can move a claim from one work queue to another. It does not move a claim to
verified.

## Batch Pattern

For an Elves math run, a useful default batch order is:

1. **Discovery Sprint:** scouts, synthesis, ranked agenda.
2. **Source Grounding:** primary sources, definitions, known theorem inventory.
3. **Candidate Theorem:** precise statement, examples, dependencies, expected proof route.
4. **Proof Attempt:** implement the proof in notes with derivation checks.
5. **Adversarial Review:** proof critics, source auditors, edge-case hunters.
6. **Manuscript Draft:** clean exposition, notation ledger, references.
7. **Final Packet:** human-review checklist, remaining risks, shareable draft.

Small tasks can collapse batches, but do not collapse the distinction between discovery, proof,
source audit, and human verification.

## Done Criteria

A math run is review-ready only when:

- the claim ledger names every retained claim and its status;
- the source ledger links each citation to the result actually used;
- the model-call ledger records which roles were asked what;
- failed approaches and rejected ideas are recorded enough to avoid rework;
- unresolved risks are explicit;
- all retained mathematical claims have human-verification status, or are clearly marked as draft.

Use [`math-artifact-ledgers.md`](math-artifact-ledgers.md) for ledger formats and
[`math-review-prompts.md`](math-review-prompts.md) for reusable scout, critic, audit, and
manuscript-review prompts.
