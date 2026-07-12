# Cobbler External-Agent Setup Recipes

These recipes map common host tool mixes onto Cobbler roles **without source changes**. Labels:

| Label | Meaning |
| --- | --- |
| **verified** | Behaviorally exercised in this Elves repository's runtime fixtures |
| **experimental** | Documented shape; requires local qualification before write roles |
| **custom** | User wrapper or API-only path; capabilities must be proven per wrapper |

Public default: **native-only** (no setup, no keys, no external executables).

## Operator commands

Claude Code:

- Primary: `/setup-cobbler`
- Compatibility: `/setup-council`

Codex (not top-level slash commands):

- `$elves setup-cobbler`
- `$elves setup-council`
- Natural language: "Set up Cobbler external-agent preferences"

CLI (deterministic / non-interactive):

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

- planning/review: `claude-code`
- implement/validate/synthesize: `host-native` (or Claude when your host is Claude Code)
- fallback: `host-native`

## Recipe: Grok-only (experimental write; verified isolation rules)

- implement: `grok-build` under a **writer lease** with detached registered worktree
- review/planning: `host-native` or other independent lens
- Never use headless `--worktree --resume` as isolation on Grok Build `0.2.93`
- Sandbox: prefer `devbox` for detached commit handoff; `workspace` is not assumed commit-capable

## Recipe: Sakana / codex-fugu only (experimental)

- planning/review: `codex-fugu`
- implement: `host-native` unless a qualified write wrapper exists
- Treat MCP OAuth warnings as optional-tool health, not inference failure

## Recipe: all three subscription CLIs (experimental mix)

- planning: independent mix of host + claude-code + codex-fugu (read-only council)
- implement: grok-build child under one writer lease
- review: fresh host + claude-code + codex-fugu; **exclude** the implementer from independent quorum
- validate/synthesize/document owner: host coordinator

Dated personal presets may name current models in a **project Survival Guide**, never as shipped
public defaults.

## Recipe: Codex wrapper inside Claude (custom)

- `custom-cli` profile pointing at your wrapper executable (for example CLIProxyAPI or a user alias)
- Qualify session/write capabilities before using implement
- Keep `host-native` fallback

## Recipe: OpenRouter breadth (custom / experimental)

- Optional env var **name**: `OPENROUTER_API_KEY` (value stays in the environment)
- Suitable for **read-only** scout/review/synthesis breadth when a wrapper qualifies
- **Not** a default; missing key falls back to native
- Do **not** claim OpenRouter can edit or persist a coding worktree without a qualified wrapper

## Recipe: API-only models such as Muse (custom)

- Map as `custom-cli` or API wrapper route for read-only analysis
- Cannot edit worktrees or hold exact coding sessions unless the wrapper proves those capabilities
- Always document native fallback

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
