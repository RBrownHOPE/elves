# Optional open-source Grok Build worker

Grok Build is an optional autonomous worker. Native Codex or Claude Code remains the default, and
Grok receives no protected-ref, PR, merge, or final-acceptance authority.

## Install and authenticate

Install Grok Build from the official `xai-org/grok-build` project and authenticate with the CLI.
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
  --provider grok --explicit-grok-consent --probe-grok
```

The safe snapshot contains no credentials or raw OAuth/provider output. It records installed
version/build, supported permission and read-only controls, create/resume session grammar,
streaming JSON, JSON schema, ACP, live model catalog/default, and concrete unavailable reasons.
`--new-session` is unsupported; new launches use a caller-generated UUID with `--session-id`, and
recovery uses exact `--resume`.

Provider qualification does not depend on goal support. An isolated model-free `/goal status`
probe must verify command resolution before Elves uses headless `/goal`. If it is unavailable, a
qualified Grok provider receives the same immutable objective as one packet-backed prompt and the
fallback is recorded. A core capability, auth, or live-catalog failure selects native fallback
with a concrete reason before spawn.

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

After a crash, reconcile the registered session and actual feature-branch state, then resume the
same identity. Use `full-run-logs` only for bounded diagnosis and `full-run-stop` only for explicit
cancellation or recovery of a live/wedged process. The host still owns cumulative review,
acceptance proof, protected refs, PR actions, and any user-authorized merge.
