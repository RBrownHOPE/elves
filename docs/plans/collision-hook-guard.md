# Plan: Collision Hook Guard

## Mission

Design an optional deterministic guard that helps enforce the one-run-one-branch-one-checkout rule
after staging. The preflight ownership guard catches duplicate current-branch worktrees before a
run starts, and the auto-worktree helper design makes isolated checkout setup easier. This follow-up
focuses on a later failure mode: an agent is already running, context drifts, and a branch tip moves
unexpectedly before a write, commit, push, or merge command.

Done means a future implementation can provide a small hook or wrapper pattern that blocks risky
operations when the current or remote branch tip no longer matches the recorded collision tripwire,
while remaining host-honest for Claude Code, Codex, and plain shell environments.

## Product Shape

### Core Idea

During staging, Elves already records a branch tip:

```bash
START_TIP=$(git rev-parse HEAD)
```

That value is useful only if the agent checks it before dangerous moments. A collision hook guard
would turn the prose rule into deterministic friction:

1. Read the recorded tripwire from the survival guide, `.elves-session.json`, or an explicit env var.
2. Before write-ish commands, compare:
   - current `HEAD`;
   - upstream/remote branch tip when available;
   - current branch name.
3. If either tip moved to a commit the run did not create, block the command and print a Hard Stop
   message.
4. Allow read-only inspection commands so the agent can diagnose and report the collision.

This guard is not an ownership oracle. It cannot know every process touching the repo, and it
cannot prove another agent is absent. Its job is narrower: block high-risk writes when the recorded
branch-tip invariant is broken.

### Host Semantics

- **Claude Code:** document a PreToolUse hook pattern for Bash commands. This is the most natural
  host for command interception.
- **Codex:** do not claim Codex has a universal PreToolUse hook. Use the prose rule, preflight
  guard, and optional wrapper script when the user's Codex environment exposes a suitable hook.
- **Plain shell/other agents:** provide a standalone helper that can be called manually or wired
  into local shell aliases, CI checks, or tool-specific hooks.

### Protected Commands

Start narrow. Block only commands that mutate the checkout, branch, or remote state:

- `git commit`, `git merge`, `git pull`, `git rebase`, `git cherry-pick`, `git revert`
- `git push`
- `git checkout`, `git switch`, `git reset`, `git clean`
- `git worktree remove`, `git worktree move`, `git branch -D`
- known bulk file mutations if the hook can see the command text, such as `rm -rf`, `mv`, or
  generated-code commands configured by the project

Allow read-only commands:

- `git status`, `git log`, `git diff`, `git show`, `git fetch`
- `gh pr view`, `gh pr checks`, review-thread reads
- test and lint commands unless the project marks them as mutating

`git fetch` is allowed because the guard may need fresh remote state. The guard should compare
remote state after fetch, not treat fetch itself as collision evidence.

### Tripwire Sources

Prefer explicit, machine-readable sources:

```yaml
workspace-guard:
  enabled: true
  branch: codex/example-run
  start-tip: abc1234
  remote-ref: origin/codex/example-run
  mode: block-write-on-tip-mismatch
```

Future implementation can store the same data in `.elves-session.json`:

```json
{
  "workspace_guard": {
    "enabled": true,
    "branch": "codex/example-run",
    "start_tip": "abc1234",
    "remote_ref": "origin/codex/example-run",
    "mode": "block-write-on-tip-mismatch"
  }
}
```

If both are present, `.elves-session.json` should win because it is machine-readable and updated
during the run. If neither is present, the hook should warn and allow the command unless the user
explicitly configured strict mode.

## Scope

### In Scope

- Define hook/wrapper behavior and host-specific documentation.
- Define what commands are blocked versus allowed.
- Define tripwire source precedence and strict/advisory modes.
- Plan future script, tests, README, and template updates.
- Keep this dependent on the preflight ownership guard stack so setup enforcement lands first.

### Out of Scope

- Implementing a hook or script in this branch.
- Claiming hooks are available in hosts that do not expose them.
- Blocking read-only diagnostics after a collision.
- Trying to detect every possible process writing to the repo.
- Automatically repairing, rebasing, merging, deleting, or moving worktrees after a collision.
- Replacing dedicated worktrees or preflight duplicate-branch detection.

## Batches

### Batch 1: Guard Contract and Host-Honest Docs

**Tasks:**
- [ ] Add a short collision-hook section to README's advanced hook guidance.
- [ ] Add behavior guidance to `SKILL.md` and `AGENTS.md` without making hooks mandatory.
- [ ] Extend `references/survival-guide-template.md` with optional `workspace-guard` fields.
- [ ] Document that this guard complements, but does not replace, preflight duplicate-worktree
      detection and dedicated worktrees.

**Acceptance criteria:**
- [ ] Claude Code users see a concrete PreToolUse hook path.
- [ ] Codex users are not promised unsupported hook behavior.
- [ ] The docs say the guard blocks write-ish commands only after a tripwire mismatch.
- [ ] Read-only diagnostics remain allowed after a suspected collision.

**Docs likely touched:**
- `README.md`
- `SKILL.md`
- `AGENTS.md`
- `references/survival-guide-template.md`

**Risk:** Overclaiming hook support could make users think Elves can guarantee isolation in every
host. Keep claims tied to the host surface.

### Batch 2: Helper Script Design

**Tasks:**
- [ ] Add a small repo/runtime helper, likely `scripts/workspace_guard.py`.
- [ ] Support `--check-command "<command>"`, `--branch`, `--start-tip`, `--remote-ref`, and
      `--mode advisory|strict`.
- [ ] Read `.elves-session.json` and survival-guide fields when explicit CLI flags are absent.
- [ ] Return exit `0` for allowed commands, `1` for blocked write-ish commands after mismatch, and
      `2` for configuration or git-inspection errors.

**Acceptance criteria:**
- [ ] The helper never mutates git state except optional `git fetch` only when explicitly requested.
- [ ] Missing tripwire data is advisory by default and strict-failing only when configured.
- [ ] Current `HEAD` mismatch and remote-ref mismatch are reported separately.
- [ ] The block message tells the agent to Hard Stop, not to auto-merge, rebase, or repair.

**Docs likely touched:**
- `scripts/workspace_guard.py`
- `tests/test_workspace_guard.py`
- `scripts/sync_installed_skills.py` if the helper is included in the runtime bundle
- `README.md`

**Risk:** If the helper fetches or inspects the wrong remote ref, it could create false positives
or false confidence. Prefer explicit refs and clear diagnostics over clever inference.

### Batch 3: Hook Templates and Validation

**Tasks:**
- [ ] Add Claude Code PreToolUse examples that call the helper before mutating Bash commands.
- [ ] Add plain-shell wrapper examples for environments without host hooks.
- [ ] Add tests for command classification, tripwire source precedence, advisory/strict modes,
      current-tip mismatch, remote-tip mismatch, and missing git metadata.
- [ ] Add consistency checks only if the feature becomes prominent in public docs.

**Acceptance criteria:**
- [ ] Hook examples are opt-in and clearly labeled advanced.
- [ ] Mutating command examples are blocked on mismatch; read-only diagnostic commands are allowed.
- [ ] Tests cover both current `HEAD` mismatch and remote branch mismatch.
- [ ] The sync helper either installs the runtime guard intentionally or keeps it repo-only
      intentionally; no accidental install drift.

**Docs likely touched:**
- `README.md`
- `references/tool-config-examples.md`
- `tests/test_workspace_guard.py`
- `scripts/check_repo_consistency.py` if guard phrasing becomes pinned

**Risk:** Hook command snippets can be brittle across shells and host versions. Keep examples simple,
use the helper for logic, and avoid large inline shell programs.

## Non-Negotiables

- The guard must never auto-repair collisions.
- The guard must allow read-only diagnostics after blocking a write.
- Missing tripwire data is advisory by default, not a surprise hard stop.
- Strict mode must be explicit.
- Host-specific docs must be honest: Claude Code hook examples do not imply Codex has the same
  hook surface.
- Dedicated worktrees and preflight ownership checks remain the primary prevention path.

## Test Strategy

- **Primary consistency gate:** `python3 scripts/check_repo_consistency.py`
- **Unit tests:** `python3 -m unittest discover -s tests -p 'test_*.py'`
- **Script compile gate:** `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py`
- **Bash syntax gate:** `bash -n scripts/preflight.sh scripts/notify.sh`
- **JSON validation:** `python3 -m json.tool config.json.example >/dev/null`
- **Whitespace gate:** `git diff --check`
- **Review loop:** after every push, read PR comments, review threads, and checks; fix blockers
  before continuing.

## Future Implementation Notes

- Command classification should be conservative and transparent. If a command is unknown, advisory
  mode can warn, while strict mode can block only when the command appears write-capable.
- The helper should print the branch, expected start tip, current tip, remote tip, and source of the
  tripwire data when it blocks.
- Do not parse arbitrary shell syntax deeply in the first version. Match the common command prefixes
  and keep a project override list for additional mutating commands.
- If the run creates commits itself, future versions may need to update the accepted tip after each
  successful commit/push. The first implementation can instead compare against a recorded allowed
  tip in `.elves-session.json` that the coordinator updates after its own commits.
- If the remote branch does not exist yet, remote-tip mismatch should be advisory until the first
  push creates it.

## Notes

- This plan should stay draft until the preflight duplicate-worktree guard lands, because that
  branch defines the first enforcement layer.
- The auto-worktree helper remains the user-experience layer for creating isolated checkouts. This
  hook guard is the last-resort "do not write if the invariant broke" layer.
- This is intentionally a design-only scout note. It should be reviewed before any canonical skill,
  hook, or script implementation lands.
