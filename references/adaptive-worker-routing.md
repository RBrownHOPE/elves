# Adaptive subscription-native workers

Start with the user flow, not route vocabulary:

> “Implement this plan while I’m offline. Keep the PR unmerged.”

The live Claude Code or Codex driver classifies execution reasoning separately from review risk,
shows one worker recommendation, and asks at most one useful preference question. The default is a
separate worker on the subscription already in use. The worker receives the complete packet, the
driver exposes a capability-proven native agent view or exact follow command before parking, and
wakes for one cumulative terminal review. Worker completion never grants PR or merge authority.

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
repository safety veto > explicit run intent > repository defaults > global convenience > built-in default
```

Repository prohibition is always a veto. A current-run or global `provider=grok` is remembered
consent; repository `allow_grok=true` is not consent. Availability, preference, recommendation,
consent, and prohibition are separate facts.

## Deterministic recommendation

`route-worker` performs no model inference and reports provider, transport, model policy, effort,
review risk, provenance, fallback, optional driver-upgrade advice, advertised goal evidence, and
separately recorded behavioral verification:

```bash
python3 scripts/cobbler_agents.py route-worker --json \
  --host codex --execution-reasoning medium --review-risk high --probe-grok
```

An operator may bind previously recorded proof with
`--grok-goal-behavioral-evidence <verification-id-or-path>`; the help probe alone never sets it.

Native workers inherit the current driver model unless an explicit model is routed. Effort follows
the plan's low/medium/high execution classification; the driver itself is never downgraded. High
review risk may advise a stronger terminal-review driver without changing it in place.

When Grok Build is explicitly permitted and silently qualifies:

- choose the parsed default from the authenticated live `grok models` catalog unless the operator
  explicitly requests another catalog member;
- never invent `auto`, `grok-code-fast-1`, `grok-4.5`, or any other unavailable model;
- missing install, auth, live catalog, supported session grammar, consent, or another core launch
  capability records an honest native fallback with a concrete reason;
- goal support is separate: behaviorally verified headless `/goal` enhances the launch, while an
  otherwise qualified provider uses the recorded one-packet prompt fallback when goal is absent.
  Help text alone proves only an advertised entrypoint.

The installed executable is launch authority; upstream source is semantic reference only. See
[`grok-open-source-worker.md`](grok-open-source-worker.md) for the complete optional-worker path.

## Host transports, same contract

| Concern | Claude Code | Codex |
|---|---|---|
| Fresh identity | caller-assigned UUID | capture `thread.started.thread_id` |
| Exact resume | `--resume <uuid>` | `codex exec resume <thread-id>` |
| Worktree | native isolated worktree or supervisor CWD | `-C` on create; supervisor OS CWD on resume |
| Model | inherit unless explicitly pinned | inherit unless explicitly pinned |
| Effort | `--effort <level>` | `model_reasoning_effort=<level>` |
| Visibility | capability-proven native agent view or private follow log | capability-proven native agent view or private follow log |

Build an inspectable CLI-fallback launch specification with `native-worker spec --host claude|codex
--worktree <path> --effort <level> --model <observed-current-model> --json`. Native custom agents
inherit the live model. A supervised CLI child cannot safely infer a parent invocation override, so
the host must supply the observed current model (or an explicit routed model) and the command pins
it. Every path uses a separate session, never uses `--last`, and never claims a cross-session cache
handoff.

`native-worker launch` supervises the child and tees redacted structured stdout/stderr into a
mode-0600 per-run follow log. It returns an exact `native-worker follow --run-id <id>` command
before the driver parks; `native-worker status` reports the bound PID, session, worktree,
visibility state, and exit status. A spec with neither a proven host view nor a follow log reports
`visibility_ready=false` and `visibility_mode=commit_only`.

## Cache and authority limits

Exact worker resume preserves provider conversation continuity. Provider-side prompt caching may
also report cached tokens. Neither host exposes a supported cache object that Elves can export from
the driver and inject into another process or model; a session ID is not such an object. Cache hits
are opportunistic telemetry, never launch or acceptance gates.

Preferences cannot grant credentials, unattended approval bypass, destructive commands, protected
refs, PR operations, or merge. A trusted assigned worker may advance only its registered feature
branch when the packet grants that narrow authority. The driver retains canonical run memory,
terminal proof/review, PR state, landing policy, and merge.
