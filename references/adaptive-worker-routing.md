# Adaptive subscription-native workers

Start with the user flow, not route vocabulary:

> “Implement this plan while I’m offline. Keep the PR unmerged.”

The live Claude Code or Codex driver classifies execution reasoning separately from review risk,
shows one worker recommendation, and asks at most one useful preference question. The default is a
separate worker on the subscription already in use. The worker receives the complete packet, the
driver parks while its stream stays visible, and the driver wakes for one cumulative terminal
review. Worker completion never grants PR or merge authority.

## Small policy surface

Both hosts read the same versioned convenience file:

```text
${XDG_CONFIG_HOME:-~/.config}/elves/config.json
```

```json
{
  "version": 1,
  "worker": {
    "provider": "auto",
    "native_effort": "auto"
  }
}
```

Inspect or change it from the active installed skill root (commands shown with source-checkout
shorthand):

```bash
python3 scripts/cobbler_agents.py preferences show
python3 scripts/cobbler_agents.py preferences set worker.provider native
python3 scripts/cobbler_agents.py preferences set worker.provider grok
python3 scripts/cobbler_agents.py preferences reset
```

Writes are private and atomic. Safe unknown fields survive updates. Credentials and
merge/destructive/protected-ref/approval-bypass authority are rejected. Resolution is:

```text
repository safety policy > explicit run intent > global convenience > built-in native default
```

Repository prohibition is always a veto. Availability, preference, recommendation, and permission
are separate facts.

## Deterministic recommendation

`route-worker` performs no model inference and reports provider, transport, model policy, effort,
review risk, provenance, fallback, optional driver-upgrade advice, and qualified goal behavior:

```bash
python3 scripts/cobbler_agents.py route-worker --json \
  --host codex --execution-reasoning medium --review-risk high --probe-grok
```

Native workers inherit the current driver model unless an explicit model is routed. Effort follows
the plan's low/medium/high execution classification; the driver itself is never downgraded. High
review risk may advise a stronger terminal-review driver without changing it in place.

When Grok Build is explicitly permitted and silently qualifies:

- regular clear low/medium execution pins `grok-composer-2.5-fast`;
- genuinely complex high execution pins `grok-4.5`;
- missing install, auth, model, permission, or behaviorally qualified goal mode records an honest
  native fallback. A TUI-only `/goal` mention is not headless goal capability.

## Host transports, same contract

| Concern | Claude Code | Codex |
|---|---|---|
| Fresh identity | caller-assigned UUID | capture `thread.started.thread_id` |
| Exact resume | `--resume <uuid>` | `codex exec resume <thread-id>` |
| Worktree | native isolated worktree or supervisor CWD | `-C` on create; supervisor OS CWD on resume |
| Model | inherit unless explicitly pinned | inherit unless explicitly pinned |
| Effort | `--effort <level>` | `model_reasoning_effort=<level>` |
| Stream | native background/structured stream | JSONL thread stream |

Build an inspectable CLI-fallback launch specification with `native-worker --host claude|codex
--worktree <path> --effort <level> --model <observed-current-model> --json`. Native custom agents
inherit the live model. A supervised CLI child cannot safely infer a parent invocation override, so
the host must supply the observed current model (or an explicit routed model) and the command pins
it. Every path uses a separate session, never uses `--last`, and never claims a cross-session cache
handoff.

## Cache and authority limits

Exact worker resume preserves provider conversation continuity. Provider-side prompt caching may
also report cached tokens. Neither host exposes a supported cache object that Elves can export from
the driver and inject into another process or model; a session ID is not such an object. Cache hits
are opportunistic telemetry, never launch or acceptance gates.

Preferences cannot grant credentials, unattended approval bypass, destructive commands, protected
refs, PR operations, or merge. A trusted assigned worker may advance only its registered feature
branch when the packet grants that narrow authority. The driver retains canonical run memory,
terminal proof/review, PR state, landing policy, and merge.
