# Optional open-source Grok Build worker

Grok Build is an optional autonomous worker. Native Codex or Claude Code remains the default, and
Grok receives no protected-ref, PR, merge, or final-acceptance authority.

## Install and authenticate

Install Grok Build from the [official `xai-org/grok-build` source](https://github.com/xai-org/grok-build)
and authenticate once in an ordinary terminal:

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
grok
```

The first `grok` launch opens the browser login flow; finish it, then exit the interactive CLI.
Elves trusts the installed executable's observed behavior for launches; upstream source is a
semantic reference, not a substitute for probing the installed build.

Use exactly one noninteractive credential route:

- trusted local OAuth: `--grant-grok-auth` keeps a private per-run `HOME` and `GROK_HOME` and
  exposes only the validated owner-private canonical auth file through `GROK_AUTH_PATH`;
- API key: `--grant-env XAI_API_KEY`, preferred for CI and non-trusted lanes.

Never grant host `HOME`, `GROK_HOME`, SSH state, Git configuration, or both auth strategies.

## Capability-check and choose a route

From the target repository, invoke the helper under the active installed Elves skill root (the
`scripts/...` form below is source-checkout shorthand):

```bash
python3 scripts/cobbler_agents.py route-worker --json \
  --host codex --execution-reasoning medium --review-risk high \
  --provider grok --allow-grok --probe-grok
```

The safe snapshot contains no credentials or raw OAuth/provider output. It records installed
version/build, supported permission and read-only controls, create/resume session grammar,
streaming JSON, JSON schema, ACP, live model catalog/default, and concrete unavailable reasons.
`--new-session` is unsupported; new launches use a caller-generated UUID with `--session-id`, and
recovery uses exact `--resume`.

Provider qualification does not depend on goal support. The isolated `/goal status` probe uses the
narrow auth projection, but is independent of catalog lookup and model inference. It proves only
command resolution; it never enables goal launch by itself. Headless `/goal <objective>` requires
separately recorded behavioral evidence that the exact authenticated prompt-file canary reached a
terminal state and returned the requested session identity. The installed 0.2.101 canary emitted
work but did not reach terminal state within 120 seconds, so Elves currently records and uses the
one-packet fallback. A core capability, auth, or live-catalog failure selects native fallback with
a concrete reason before spawn.

Model selection uses only the authenticated live catalog. Omitting `--model` (or using the CLI's
`auto` preparation value) resolves to the parsed live default at launch. An explicit model is
accepted only if the catalog returns that exact identifier.

## Launch, follow, and recover

Create the host-owned rollback ref, prepare one exact session, launch with one auth strategy, and
park on the sanitized stream:

```bash
python3 scripts/cobbler_agents.py implement rollback-ref --json \
  --run-id <run-id> --session-id <uuid> --batch B0 --head <start-head> --push

python3 scripts/cobbler_agents.py implement full-run-prepare --json \
  --session-id <uuid> --branch <feature-branch> --start-head <start-head> \
  --worktree <path> --packet <packet.json> --session .elves-session.json \
  --adapter grok-build --model auto

python3 scripts/cobbler_agents.py implement full-run-launch --json \
  --session-id <uuid> --grant-grok-auth --grant-github-push

python3 scripts/cobbler_agents.py implement full-run-await --json \
  --session-id <uuid>
```

For API-key auth, replace `--grant-grok-auth` with `--grant-env XAI_API_KEY`. The default follower
shows sanitized progress, bounded usage, terminal state, and typed errors; unknown event types are
reported safely. Shared OAuth never exposes raw transcript text.

After a crash, reconcile the registered session and actual feature-branch state, then recover the
same exact identity:

```bash
python3 scripts/cobbler_agents.py implement full-run-prepare --json \
  --session-id <uuid> --branch <feature-branch> --start-head <start-head> \
  --worktree <path> --packet <packet.json> --session .elves-session.json \
  --adapter grok-build --model auto --resume

python3 scripts/cobbler_agents.py implement full-run-launch --json \
  --session-id <uuid> --resume --grant-grok-auth --grant-github-push

python3 scripts/cobbler_agents.py implement full-run-await --json \
  --session-id <uuid>
```

Goal-enhanced recovery uses `/goal resume`; it never resends `/goal <packet>`. Use `full-run-logs`
only for bounded diagnosis and `full-run-stop` only for explicit cancellation or recovery of a
live/wedged process. The host still owns cumulative review, acceptance proof, protected refs, PR
actions, and any user-authorized merge.
