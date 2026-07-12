# Model Onboarding (Claude Code + Codex)

Users need a clear way to **choose** which tools/models handle which jobs, **update** those
choices later, and **verify** they work. Elves does this as a **host-mediated** flow: the agent
interviews the user; the CLI inventories, stores preferences, and probes.

Native-only remains fully valid. Onboarding is optional.

## Hosts

| Host | How to start |
| --- | --- |
| **Claude Code** | `/setup-cobbler` or natural language: “set up my model routes” / “onboard models” |
| **Codex** | `$elves setup-cobbler` or natural language — **not** a top-level Codex slash command |

Both hosts follow the same four steps. Do not invent different product rules per host.

## Operator CLI

```bash
# 1) Interview packet (inventory + env *names* present + purpose questions)
python3 scripts/cobbler_agents.py onboard plan --json

# 2) After the user answers, write ignored local preferences
python3 scripts/cobbler_agents.py onboard apply --json \
  --planning host-native \
  --implement host-native \
  --review claude-code \
  --scout host-native \
  --force

# 3) Show current map
python3 scripts/cobbler_agents.py onboard show --json

# 4) Structural probe (no tokens)
python3 scripts/cobbler_agents.py onboard probe --json

# Optional: request live smoke (host must supply a real smoke; never print secrets)
python3 scripts/cobbler_agents.py onboard probe --json --smoke
```

Equivalent flags also exist on `setup` for apply-only use.

**Never stage** `.elves/models.toml`. Never paste API key values into chat, TOML, or the Survival
Guide. Env var **names** only.

## Purpose → route catalog

| Purpose | Default | Typical optional routes |
| --- | --- | --- |
| Planning / design | host-native | `claude-code-planning`, `codex-fugu-planning`, Gemini CLI, Antigravity CLI |
| Implementation (labor) | host-native | `claude-code-labor`, `codex-fugu-labor`, grok-build |
| Independent review | host-native | planning-tier Claude/Codex, Gemini CLI, Antigravity CLI, OpenRouter, Muse |
| Lightweight review | host-native | labor-tier Claude/Codex, Gemini CLI |
| Scout / discovery | host-native | Gemini CLI, Antigravity CLI, OpenRouter, Muse |
| Validation ownership | host-native | host-native only preferred |
| Synthesis | host-native | host-native only preferred |
| Math evolutionary search | off | alphaevolve (when gcloud + project runner exist) |

`host-native` means the **current** agent (Claude Code or Codex) owns that work.

### Within-family model tiers (Claude and Codex)

You can use a **stronger model for planning/review** and a **cheaper/faster model for implement
labor** without switching product families:

| Profile | Adapter | Typical use |
| --- | --- | --- |
| `claude-code-planning` | claude-code | Plan + independent review |
| `claude-code-labor` | claude-code | Batch implement volume |
| `codex-fugu-planning` | codex-fugu | Plan + independent review |
| `codex-fugu-labor` | codex-fugu | Batch implement volume |

After `onboard apply`, edit ignored `.elves/models.toml` and set `requested_model` on each tier
profile to the model ids **your** Claude/Codex install supports. Elves does not ship prestige
model ids as public defaults.

Example shape (machine-local only):

```toml
[profiles.claude-code-planning]
adapter = "claude-code"
# requested_model = "…"   # high-quality plan/review

[profiles.claude-code-labor]
adapter = "claude-code"
# requested_model = "…"   # labor implement

[roles.planning]
profile = "claude-code-planning"

[roles.review]
profile = "claude-code-planning"

[roles.implement]
profile = "claude-code-labor"
```

Same pattern with `codex-fugu-planning` / `codex-fugu-labor`.

### Google subscription CLIs (plan/review, not bulk labor)

| Route | Executable (typical) | Recommended purposes |
| --- | --- | --- |
| `gemini-cli` | `gemini` | planning, review, scout |
| `antigravity-cli` | `antigravity` (fallback `agy`) | planning, review, scout |

Google is consolidating coding-agent surfaces around **Antigravity** (Gemini CLI transitions into
that family). Treat both as optional **subscription CLIs** when installed.

**Cost guidance:** these are usually **not** cost-effective as the main overnight implement engine.
Prefer host-native or labor-tier Claude/Codex (or optional Grok implement) for bulk batch coding;
use Gemini/Antigravity as independent plan/review lenses when you already pay for the subscription.

## Host agent protocol

When the user asks to onboard, reconfigure models, or “which model should do what”:

1. **Plan**  
   Run `onboard plan --json`. Do not invent inventory.

2. **Interview**  
   Walk `questions[]` from the packet. For each purpose, show available options (respect
   `available_hint`: missing CLI or env name → say so). Offer **host-native** first.  
   If they want OpenRouter / Muse, confirm the env **name** is set (key already in environment or
   ignored `.env.local`) — never ask them to paste the secret into chat.

3. **Apply**  
   Run `onboard apply` with the chosen routes. Prefer host-native fallbacks (default).

4. **Probe**  
   Run `onboard probe --json`. Report pass / warn / fail. Fix or re-choose failing routes.

5. **Optional live smoke**  
   Only if the user wants a paid check: run a **tiny** real completion per external route (host
   tool), then re-run probe or record smoke evidence. Never print credentials. Default is
   structural probes only.

6. **Update later**  
   Same flow. `onboard show` first, then re-interview changed purposes, `apply --force`, `probe`.

7. **Staging snapshot**  
   During Elves staging, paste effective routes into the Survival Guide so the PR shows provenance
   without committing machine-local TOML.

## What probe checks

| Kind | What |
| --- | --- |
| **Structural** | host-native always; configured CLI on PATH + `--help`; env **names** for OpenRouter/Meta; gcloud for AlphaEvolve hint |
| **Live smoke** | Opt-in; requires host-provided real model response; empty/fake smoke does not count |

Probe never invents remaining quota and never prints secrets.

## Math / Muse / OpenRouter / AlphaEvolve

These are **optional upgrades** on top of native:

- **OpenRouter** — review/scout breadth when `OPENROUTER_API_KEY` is set + project wrapper  
- **Meta Muse Spark 1.1** — plan/review when `META_API_KEY` or `MODEL_API_KEY` is set  
- **AlphaEvolve** — math `evolutionary_search` when gcloud + project runner exist  

See [`math-alphaevolve.md`](math-alphaevolve.md), [`math-provider-config.md`](math-provider-config.md),
[`cobbler-setup-recipes.md`](cobbler-setup-recipes.md).

## Related

- CLI setup (non-interview): `python3 scripts/cobbler_agents.py setup …`
- Doctor inventory: `python3 scripts/cobbler_agents.py doctor --json`
- Recipes: [`cobbler-setup-recipes.md`](cobbler-setup-recipes.md)
