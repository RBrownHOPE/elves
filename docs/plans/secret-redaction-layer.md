# Secret Redaction Layer Design

## Purpose

Design a future Elves safety layer that reduces the chance of secrets being sent to model prompts,
subagent briefs, PR comments, logs, or durable run docs.

This is a design note only. It intentionally does not change `SKILL.md`, `AGENTS.md`, README,
TODO, changelog, runtime scripts, config examples, or release surfaces. A future implementation PR
should update those surfaces together after the mechanism is real.

## Problem

Elves already tells agents not to commit `.env` files and not to use broad staging commands, but the
more subtle risk is prompt context. During long runs, agents read files, summarize logs, paste tool
output into subagent prompts, and quote PR comments. If one of those surfaces contains an API key,
session cookie, private token, customer credential, or signing secret, the current process relies on
the agent noticing it manually.

That is not enough. Where the chosen implementation surface can see the context, the layer should
make the safe path automatic before context leaves the local machine or before it is persisted into
durable run artifacts.

## Design Boundary

A Markdown skill cannot guarantee pre-prompt interception by itself. Real protection requires one
of these implementation surfaces:

- a local wrapper script that prepares prompt bundles before calling a provider;
- an MCP/tool proxy that filters file snippets and command output before returning them to the
  model;
- a Claude Code or Codex hook if the host exposes a suitable pre-tool or pre-prompt extension point;
- a repo-side helper that agents run before pasting large context into subagent prompts or PR
  comments.

The first release should be honest about that boundary. It can add detection helpers and workflow
checks, but it must not claim that all prompts are protected unless the host actually routes prompt
context through the redaction layer.

## Threat Model

Aim to protect against accidental disclosure of:

- provider API keys and bearer tokens;
- GitHub, Slack, Stripe, OpenRouter, Anthropic, OpenAI, Gemini, xAI, database, cloud, and deploy
  tokens;
- private SSH keys, PEM blocks, signing keys, webhook secrets, session cookies, JWTs, and OAuth
  credentials;
- `.env`, `.npmrc`, `.pypirc`, cloud credential files, and copied terminal output containing
  credentials;
- secrets embedded in generated logs, screenshots OCR text, PR comments, or issue bodies.

Do not try to solve:

- malicious users intentionally asking the agent to reveal a secret;
- secrets that a provider has already received before the layer runs;
- perfect classification of every high-entropy string;
- organization-wide secret rotation or incident response.

## UX Goal

The layer should be boring and local:

1. Scan context before it is pasted, logged, or sent outward.
2. Replace high-confidence secrets with stable placeholders.
3. Report what was redacted without printing the secret.
4. Let the run continue when redaction succeeds.
5. Block only when a required destination cannot safely receive the redacted context.

Example redaction:

```text
OPENROUTER_API_KEY=[REDACTED:openrouter-api-key:sha256:8f14e45f]
```

The fingerprint is computed locally from the secret and truncated. It lets the agent recognize the
same secret appearing twice without exposing the value.

## Policy Levels

Use explicit policy levels instead of one vague "redact secrets" switch:

- `off`: no scanning. Only for debugging the scanner itself.
- `warn`: report detections, do not modify output.
- `redact`: replace detected secrets and continue. This should be the default.
- `block`: fail when high-confidence secrets are detected in a destination that should never contain
  credentials, such as PR comments or committed run docs.

Policies can vary by destination:

```yaml
prompt_context: redact
subagent_prompt: redact
execution_log: redact
pr_comment: block
committed_docs: block
local_terminal_summary: warn
```

## Detection Strategy

Use layered detection. No single detector is enough.

### Structured Patterns

High-confidence regular expressions for known credential shapes:

- `sk-...`, provider-prefixed API keys, and bearer tokens;
- `ghp_`, `github_pat_`, deploy tokens, and webhook secrets;
- PEM/private key blocks;
- `*_SECRET=`, `*_TOKEN=`, `*_API_KEY=`, and common cloud credential keys;
- connection strings with passwords.

### File And Path Hints

Treat these as high-risk sources even when contents are ambiguous:

- `.env*`, `.npmrc`, `.pypirc`, `.netrc`;
- cloud credential directories;
- generated logs under temp or audit directories;
- pasted CI logs with masked or unmasked environment dumps.

### Entropy Heuristics

Flag high-entropy strings only as warnings unless paired with a key name, known prefix, credential
file, or assignment syntax. Pure entropy detection creates false positives in hashes, snapshots, and
test fixtures.

### Allowlist

Support a repo-local allowlist for synthetic fixtures and documented examples. Allowlist entries
must never contain real secrets. Prefer matching file paths plus fixture labels rather than raw
values.

## Redaction Semantics

Redaction should preserve enough shape for debugging without exposing the value:

- keep the variable or header name;
- replace the value with `[REDACTED:<kind>:sha256:<fingerprint>]`;
- preserve line counts so logs still map to source output;
- never write raw secret values to scanner logs, JSON reports, PR comments, or exceptions;
- store only counts, kinds, paths, line numbers, and fingerprints.

When the same secret appears in multiple places, use the same fingerprint. When two different
secrets have the same kind, fingerprints keep them distinguishable without revealing content.

## Proposed Interfaces

### Local CLI

```bash
python3 scripts/redact_context.py --policy redact --input /tmp/context.txt --output /tmp/context.redacted.txt
```

Useful modes:

- `--stdin` / `--stdout` for pipe workflows;
- `--policy warn|redact|block`;
- `--destination prompt_context|subagent_prompt|pr_comment|committed_docs`;
- `--json-report <path>` for machine-readable non-secret findings;
- `--allowlist <path>` for repo-local fixture exceptions.

### Python Library

Expose a small function so scripts can share behavior:

```python
redact_text(text, *, destination, policy, allowlist) -> RedactionResult
```

`RedactionResult` should include:

- redacted text;
- finding count;
- findings by kind and severity;
- whether output is safe for the destination;
- non-secret diagnostics.

### Agent Workflow Hook

Future Elves docs can instruct agents:

- before sending a large subagent prompt, run the context bundle through the redactor;
- before posting generated logs or summaries to a PR, scan in `block` mode;
- before committing generated docs, scan changed files in `committed_docs` mode.

This is weaker than true host-level interception, but it is still useful and honest.

## Test Plan

Use only synthetic credentials in tests.

Required cases:

- known provider-like keys are redacted;
- private key blocks are redacted across multiple lines;
- `.env`-style assignments preserve variable names and redact values;
- URLs with embedded passwords are redacted without corrupting the rest of the URL;
- repeated secrets get the same fingerprint;
- different secrets get different fingerprints;
- entropy-only strings produce warnings, not high-confidence blocks, unless paired with credential
  context;
- allowlisted test fixtures pass without printing raw values;
- JSON reports contain no raw secrets;
- `block` mode exits non-zero for PR/comment/committed-doc destinations with high-confidence
  secrets;
- scanner exceptions do not include raw input text.

## Rollout

1. Keep this as a design-only note until the current PR stack lands.
2. Build the redaction core with tests using synthetic secrets only.
3. Add a local CLI and JSON report format.
4. Wire the helper into preflight as an advisory availability check.
5. Update Elves docs so subagent prompts, PR comments, and committed run docs use the helper.
6. Add repo consistency checks that prevent docs from claiming host-level interception unless a
   real hook exists.

## Out Of Scope For The First Implementation

- Remote secret-scanning services.
- Automatic secret rotation.
- Reading the user's global credential stores.
- Searching the machine for secrets unrelated to the context the agent is about to send.
- Blocking all high-entropy strings.
- Host-level guarantees when the host does not expose a hook.

## Acceptance Criteria For A Future Implementation

- A local command can redact synthetic secrets from text without leaking raw values in output,
  reports, or exceptions.
- Agents have a documented, non-interactive path for scanning subagent prompts and PR comments.
- Committed docs and PR comments can run in `block` mode.
- The implementation is local-first and does not send suspected secrets to external services.
- The docs clearly distinguish best-effort workflow scanning from true host-level interception.
