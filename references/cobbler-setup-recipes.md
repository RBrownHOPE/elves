# Cobbler External-Agent Setup Recipes

These recipes map common host tool mixes onto Cobbler roles **without source changes**. Labels:

| Label | Meaning |
| --- | --- |
| **verified** | Behaviorally exercised in this Elves repository's runtime fixtures |
| **experimental** | Documented shape; requires local qualification before write roles |
| **custom** | User wrapper or API-only path; capabilities must be proven per wrapper |

Public default: **native-only** (no setup, no keys, no external executables).

Recipes beyond host-native Claude Code / Codex are **best-effort**: useful when they match your
install, but **exotic interfaces are not heavily tested** (e.g. Antigravity CLI — not dogfooded
here without a subscription). If a recipe fails, **prefer a PR** with a fix or a corrected recipe,
or open an issue with host/OS/command (no secrets):
https://github.com/aigorahub/elves/issues

## Operator commands

Claude Code:

- Primary: `/setup-cobbler` (also: “onboard models”, “update model routes”)
- Compatibility: `/setup-council`

Codex (not top-level slash commands):

- `$elves setup-cobbler`
- `$elves setup-council`
- Natural language: "Set up Cobbler external-agent preferences" / "onboard my models"

### Model onboarding (interview → apply → probe)

Same protocol on both hosts — see [`model-onboarding.md`](model-onboarding.md):

```bash
python3 scripts/cobbler_agents.py onboard plan --json
# host agent interviews user using the packet questions
python3 scripts/cobbler_agents.py onboard apply --json \
  --planning host-native \
  --implement host-native \
  --review claude-code \
  --force
python3 scripts/cobbler_agents.py onboard show --json
python3 scripts/cobbler_agents.py onboard probe --json
# optional paid check: onboard probe --json --smoke (host supplies real smoke)
```

### Apply-only / inventory (non-interactive)

```bash
python3 scripts/cobbler_agents.py setup --json --dry-run
python3 scripts/cobbler_agents.py setup --json \
  --implement grok-build \
  --review claude-code \
  --planning claude-code \
  --lightweight-review host-native
```

Local preferences land in ignored `.elves/models.toml`. Schema example:
`references/models.toml.example`. **Never stage** the local file. During Elves staging, the host
snapshots effective routes into the Survival Guide for reviewable provenance.

## Recipe: native-only (verified)

- All roles: `host-native`
- Setup optional
- No provider keys

## Recipe: Claude-only (verified shape)

- planning/review: `claude-code` or tier `claude-code-planning`
- implement: `host-native`, same Claude install, or labor tier `claude-code-labor`
- validate/synthesize: `host-native` (or Claude when your host is Claude Code)
- fallback: `host-native`
- Pin high vs labor model ids with `requested_model` on each profile in ignored `models.toml`

## Recipe: Claude high-plan / labor-implement (recommended split)

```bash
python3 scripts/cobbler_agents.py onboard apply --json \
  --planning claude-code-planning \
  --review claude-code-planning \
  --implement claude-code-labor \
  --force
```

Then set `requested_model` on each profile to the high-quality vs volume model your Claude install
exposes. Same idea works when the **host** is Claude Code (`host-native` implement) and only
review uses an external high model.

## Recipe: Grok-only (experimental write; verified isolation rules)

- implement: `grok-build` under a **writer lease** with detached registered worktree
- review/planning: `host-native` or other independent lens
- Never use headless `--worktree --resume` as isolation on Grok Build `0.2.93`
- Sandbox: prefer `devbox` for detached commit handoff; `workspace` is not assumed commit-capable

## Recipe: Sakana / codex-fugu only (experimental)

- planning/review: `codex-fugu` or `codex-fugu-planning`
- implement: `host-native` or labor tier `codex-fugu-labor`
- Treat MCP OAuth warnings as optional-tool health, not inference failure

## Recipe: Codex high-plan / labor-implement

```bash
python3 scripts/cobbler_agents.py onboard apply --json \
  --planning codex-fugu-planning \
  --review codex-fugu-planning \
  --implement codex-fugu-labor \
  --force
```

Pin `requested_model` per tier in ignored `models.toml`. Prefer host-native validate/synthesize.

## Recipe: Google Gemini CLI / Antigravity CLI (plan/review; optional labor)

- **Not a supported Elves host.** Claude Code or Codex must still be the main driver (`host-native`
  for the loop). Optional-lens paths are **lightly tested / community-validated**. Prefer PRs when
  flags or behavior drift.
- Inventory: `agy` (Antigravity CLI; fallback `antigravity`) and/or `gemini` on PATH
- **Auth:**
  - `agy`: Google OAuth or GCP project (e.g. `aigora-explorations`); run `agy models` to verify
  - `gemini`: `GEMINI_API_KEY` (or Vertex/GCA env); headless needs `--skip-trust` or a trusted folder
- **Use for:** planning, independent review, scout (called *by* Claude Code / Codex)
- **Models — pin current Gemini generations.** Do not hardcode prestige names as public defaults, but
  **do** pin a *current* model in ignored `models.toml` when using Google routes. As of dogfood
  (2026-07): prefer **Gemini 3.1 Pro (High)** (or newer Pro) for plan/review; **Gemini 3.5 Flash**
  (High/Medium) for optional volume. Older 1.5/2.0 pins are stale — re-check `agy models` /
  Gemini CLI `-m` catalog after upgrades.
- **Profiles:**
  - `antigravity-cli` — preferred Google plan/review lens (`agy`)
  - `gemini-cli` — API-key plan/review lens
  - `antigravity-labor` — **experimental** implement labor via `agy` + Flash-class model
- **Implement with Antigravity (experimental, not Lane A):**
  - Lane A (`cobbler_agents implement prepare|launch|…`) remains **Grok Build–oriented**
  - You *may* set `implement = antigravity-labor` in local `models.toml` for role routing / one-shot
    packets, or host-launch:  
    `agy --model "Gemini 3.5 Flash (High)" --dangerously-skip-permissions -p "…"`  
  - Not host-import write-lease qualified; qualify tools/cost yourself; keep `host-native` validate
- Fallback: `host-native`
- Gemini CLI is transitioning into the Antigravity family; Elves adapters use headless
  `-p`/`--print` (not bare stdin)
- **Plan→review continuity (preferred → fallback):**
  1. **Preferred (most robust):** exact session/conversation id so the planner chat resumes for
     review — Gemini: `--session-id` / `--resume <uuid>`; Antigravity: `--conversation <uuid>`
     only (never `latest` / bare `--continue`). Store ids in the session registry / Survival Guide.
  2. **Fallback if no id:** repo documents the agent can read (plan, contract, execution log,
     Survival Guide, constitution, PR, `.ai-docs`). Do not invent ambiguous resume tokens.
  3. Either way, re-read plan + constitution; session memory does not replace documents.
- **Review bar for Google (and all) lenses:** completeness vs plan+contract, **constitution**
  deal-breakers, and **regressions** (indirect breakage), not only local correctness of the diff

```bash
# Plan/review with Antigravity + current Pro model pin (edit models.toml after apply)
python3 scripts/cobbler_agents.py onboard apply --json \
  --planning antigravity-cli \
  --review gemini-cli \
  --implement host-native \
  --force

# Optional experimental labor (not default overnight):
# python3 scripts/cobbler_agents.py onboard apply --json \
#   --implement antigravity-labor --force
```

In ignored `.elves/models.toml`, pin models explicitly, for example:

```toml
[profiles.antigravity-cli]
adapter = "antigravity-cli"
executable = "agy"
# requested_model = "Gemini 3.1 Pro (High)"   # plan/review — re-check agy models

[profiles.antigravity-labor]
adapter = "antigravity-cli"
executable = "agy"
# requested_model = "Gemini 3.5 Flash (High)" # experimental labor

[profiles.gemini-cli]
adapter = "gemini-cli"
executable = "gemini"
# requested_model = "gemini-2.5-pro"          # or current Gemini CLI model id
```

## Recipe: all three subscription CLIs (experimental mix)

- planning: independent mix of host + claude-code + codex-fugu (read-only council)
- implement: grok-build child under one writer lease **or** labor-tier Claude/Codex
- review: fresh host + claude-code + codex-fugu (+ optional Gemini/Antigravity); **exclude** the
  implementer from independent quorum
- validate/synthesize/document owner: host coordinator

Dated personal presets may name current models in a **project Survival Guide**, never as shipped
public defaults.

## Recipe: Codex wrapper inside Claude (custom)

- `custom-cli` profile pointing at your wrapper executable (for example CLIProxyAPI or a user alias)
- Qualify session/write capabilities before using implement
- Keep `host-native` fallback

## Recipe: OpenRouter models as planner / reviewer (experimental)

**Reference pattern:** production math runs (e.g. Aigora geometry-exploration) use a thin CLI
wrapper plus named **presets**, not a hardcoded “best model” table in Elves core.

When the user has `OPENROUTER_API_KEY` (env or ignored `.env.local`):

1. Call OpenRouter through a project wrapper (geometry shape:
   `node tools/openrouter_tools.mjs --model <openrouter-model-id> --prompt-file …`).
2. Register **named presets** for the models they care about (`or-gpt55`, `or-deepseek-v4-pro`,
   …) that map to that wrapper + argv.
3. Run them as **read-only** independent plan/review/scout lanes via a panel command
   (`review_panel` / research panel) or Cobbler role routes — never as mathematical or merge
   authority.
4. Missing key → skip those presets; host-native continues.

Optional Cobbler / Survival Guide wiring (still native-first):

```yaml
cobbler-provider-backed-enabled: true
cobbler-provider-backed-optional-env:
  - OPENROUTER_API_KEY
# Role hints only — actual launch is the project wrapper + preset:
# openrouter:<provider/model-id>  e.g. openrouter:deepseek/deepseek-v4-pro
```

Rules:

- Env var **name** only in docs/config: `OPENROUTER_API_KEY`
- Do **not** stage keys; do not print them
- OpenRouter is for **breadth** (many models, one key), not the default overnight implementer
- Do not claim OpenRouter can edit a coding worktree without a separate qualified write path

## Recipe: Meta Muse Spark 1.1 as planner / reviewer (experimental)

**Reference pattern:** geometry-exploration `tools/meta_tools.mjs` + preset `meta-muse-spark11`
(+ research lane `meta-muse-spark11-research`), orchestrated through the same multi-model review
panel as Gemini/Grok/OpenRouter lanes.

When the user has a Meta Model API key:

| Item | Value used in the reference project |
| --- | --- |
| Env | `META_API_KEY` (fallback name accepted: `MODEL_API_KEY`) |
| Base URL | `https://api.meta.ai` (override: `META_API_BASE_URL`) |
| Model id | **`muse-spark-1.1`** (pin the catalog id; do not assume `muse-spark-latest`) |
| Endpoint | Responses API (`/v1/responses`) with structured `input_text` |
| Typical preset | high/xhigh reasoning, large max output tokens, long timeout for hard prompts |
| Role | independent brainstorm / plan critique / review lane — **not** sole authority |

Operator shape:

```bash
# Direct (after project provides a meta_tools-style wrapper)
node tools/meta_tools.mjs \
  --model muse-spark-1.1 \
  --reasoning-effort xhigh \
  --prompt-file path/to/packet.md \
  --max-output-tokens 32768 \
  --timeout-ms 1200000

# Via named preset on a multi-model panel
node tools/review_panel.mjs \
  --prompt-file path/to/review.md \
  --models meta-muse-spark11 \
  --min-success 1
```

Cobbler integration:

- Capability scan: if `META_API_KEY` (or `MODEL_API_KEY`) is present **and** a Meta wrapper exists,
  offer Muse as an optional planning/review lens.
- Config route hint: `meta:muse-spark-1.1` or a `custom-cli` profile whose executable is the Meta
  wrapper.
- Missing key / empty key / wrapper missing → native fallback; never block ordinary Elves.
- In mixed panels, one provider’s failure must not decide the whole run (use `min-success` /
  quorum, same idea as geometry’s proof swarm).

Local `.elves/models.toml` sketch (ignored; never stage):

```toml
[profiles.muse_spark]
adapter = "custom-cli"
executable = "node"   # or a shell wrapper around tools/meta_tools.mjs
# extra_args would point at tools/meta_tools.mjs + --model muse-spark-1.1 …
notes = "META_API_KEY; muse-spark-1.1; read-only plan/review; native fallback"

[roles.planning]
profile = "muse_spark"
required = false
fallback_chain = [
  { profile = "host-native", reason = "no META_API_KEY or Meta wrapper" },
]

[roles.review]
profile = "muse_spark"
required = false
fallback_chain = [
  { profile = "host-native", reason = "keep review available without Meta" },
]
```

## Recipe: Google Cloud AlphaEvolve (math evolutionary search; experimental)

Optional **math-module** tool for generating high-quality numerical examples and counterexample
signals. Not a chat lane and not a proof engine. Full operating rules:
[`math-alphaevolve.md`](math-alphaevolve.md).

- Requires project-owned runner + deterministic local evaluator (geometry-exploration shape:
  `tools/alphaevolve_<task>.py` + independent replay)
- Auth: short-lived **gcloud service-account impersonation**; no service-account keys in the repo
- Role slot: `evolutionary_search` / route `alphaevolve:<task-id>`
- Promote only after independent local replay; ledger as numerical signal
- Missing GCP / runner → skip; never block native math Discovery Sprint

## Recipe: future Google / Antigravity tools (custom)

- Add a `custom-cli` profile with your executable
- Run doctor/setup inventory; mark capabilities unknown until probed
- Do not hardcode future model names into Elves source

## Usage and model refresh

- Observed tokens/cost may be recorded when harnesses expose them
- `remaining_quota` is **unknown** unless explicitly provided — never invent limits from token counts
- Optional warning thresholds belong in local preferences when the user sets them
- Newly discovered models require **manual** acceptance; setup/doctor may recommend with a discovery
  date, not a permanent "best model" table

## Safety

- Never print credentials
- Never stage `.elves/models.toml` or secret values
- Never make setup mandatory for ordinary native Elves
- Commit/push/PR remain host operations under Elves rules
