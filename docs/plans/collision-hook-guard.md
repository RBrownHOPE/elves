# Plan: Collision Hook Guard

## Mission

Design an optional deterministic guard that helps enforce the one-run-one-branch-one-checkout rule
after staging. The preflight ownership guard catches duplicate current-branch worktrees before a run
starts, and the auto-worktree helper makes isolated checkout setup easier. This follow-up focuses
on a later failure mode: an agent is already running, context drifts, and a branch tip moves
unexpectedly before a write, commit, push, or merge command.

Status as of the integration preview: the repo includes a prototype helper,
`scripts/workspace_guard.py`, with advisory defaults, strict-mode blocking, and owned-tip recording
commands. Host hook integration remains optional and host-specific. This plan remains as design
context for the guard model and for future hook templates; unchecked historical items below are not
proof that the prototype script is missing.

## Product Shape

### Core Idea

During staging, Elves already records a branch tip:

```bash
START_TIP=$(git rev-parse HEAD)
```

That value is a starting point, not the only allowed tip forever. A collision hook guard would turn
the prose rule into deterministic friction:

1. Read workspace-guard state from `.elves-session.json`, the survival guide, or explicit CLI/env
   values.
2. Before write-ish commands, compare:
   - current `HEAD`;
   - upstream/remote branch tip when available;
   - current branch name.
3. Allow the command only when current `HEAD` matches `allowed_head_tip` and the remote branch
   matches `expected_remote_tip` (or the remote branch does not exist yet and the first push is
   expected).
4. After the agent creates an owned commit, update `allowed_head_tip` to the new local `HEAD` before
   the next write.
5. After the agent pushes, update `expected_remote_tip` to the pushed commit.
6. If either observed tip moved outside that owned-tip sequence, block the command and print a Hard
   Stop message.
7. Allow read-only inspection commands so the agent can diagnose and report the collision.

This guard is not an ownership oracle. It cannot know every process touching the repo, and it
cannot prove another agent is absent. Its job is narrower: block high-risk writes when the recorded
branch-tip invariant is broken. It does not catch uncommitted file writes in the same checkout,
because those can clobber files without moving `HEAD`; dedicated worktrees remain the primary
prevention mechanism.

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
- `git push`, `git stash` including `push`, `pop`, `apply`, and `clear`
- `git checkout`, `git switch`, `git reset`, `git clean`
- `git worktree add`, `git worktree remove`, `git worktree move`
- `git branch -D`, branch creation that changes the active branch
- `gh pr merge`, `gh repo sync`, and `gh pr checkout`
- known bulk file mutations if the hook can see the command text, such as `rm -rf`, `mv`, or
  generated-code commands configured by the project

Allow read-only commands:

- `git status`, `git log`, `git diff`, `git show`, `git fetch`
- `gh pr view`, `gh pr checks`, review-thread reads
- test and lint commands unless the project marks them as mutating

`git fetch` is allowed because the guard may need fresh remote state. The guard should compare
remote state after fetch, not treat fetch itself as collision evidence.

Test and lint commands are diagnostic by default, but some projects generate snapshots, coverage,
caches, or format-on-lint edits. Strict projects should be able to mark specific validation gates as
mutating so they are blocked after a guard-state mismatch.

### Guard State Sources

Prefer explicit, machine-readable sources:

```yaml
workspace-guard:
  enabled: true
  branch: codex/example-run
  start-tip: abc1234
  allowed-head-tip: abc1234
  remote-ref: origin/codex/example-run
  expected-remote-tip: null
  last-pushed-tip: null
  mode: advisory
```

Future implementation can store the same data in `.elves-session.json`:

```json
{
  "workspace_guard": {
    "enabled": true,
    "branch": "codex/example-run",
    "start_tip": "abc1234",
    "allowed_head_tip": "abc1234",
    "remote_ref": "origin/codex/example-run",
    "expected_remote_tip": null,
    "last_pushed_tip": null,
    "mode": "advisory"
  }
}
```

If both are present, `.elves-session.json` should win because it is machine-readable and updated
during the run. If neither is present, the hook should warn and allow the command unless the user
explicitly configured strict mode.

### Owned-Tip State

The first implementation must track four values:

- `start_tip`: the branch tip recorded at staging, for audit and collision diagnosis.
- `allowed_head_tip`: the local `HEAD` the current run is allowed to build on before its next
  write-ish command.
- `expected_remote_tip`: the remote branch tip the current run expects before the next push or
  remote-mutating command. This can be `null` before the branch is first pushed.
- `last_pushed_tip`: the last commit this run successfully pushed, useful for status reporting and
  recovery.

Update rules:

1. At staging, set `start_tip` and `allowed_head_tip` to `git rev-parse HEAD`.
2. If the remote branch exists, set `expected_remote_tip` to the remote branch tip; otherwise set it
   to `null`.
3. Before a local write-ish command, require current `HEAD == allowed_head_tip`.
4. After this run creates an owned commit, update `allowed_head_tip` to the new `HEAD`.
5. Before `git push` or remote-mutating commands such as `gh pr merge`, require the observed remote
   tip to equal `expected_remote_tip`, except when `expected_remote_tip` is `null` and this is the
   first push.
6. After a successful push, set both `expected_remote_tip` and `last_pushed_tip` to the pushed
   commit.
7. Never update these fields to accept an unexpected external tip automatically. That is a Hard Stop
   until the human or a fresh staged run deliberately adopts the new base.

## Scope

### In Scope

- Define hook/wrapper behavior and host-specific documentation.
- Define what commands are blocked versus allowed.
- Define owned-tip source precedence and strict/advisory modes.
- Document script, test, README, and template work needed for the guard surface.
- Keep this dependent on the preflight ownership guard stack so setup enforcement lands first.

### Out of Scope

- Implementing host hooks or installing the helper into a live agent environment in this branch.
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
- [ ] The docs say the guard blocks write-ish commands only after a guard-state mismatch.
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
- [x] Add a small repo/runtime helper, likely `scripts/workspace_guard.py`.
- [x] Support `--check-command "<command>"`, `--branch`, `--start-tip`, `--allowed-head-tip`,
      `--remote-ref`, `--expected-remote-tip`, `--last-pushed-tip`, and `--mode advisory|strict`.
- [x] Read `.elves-session.json` and survival-guide fields when explicit CLI flags are absent.
- [x] Return exit `0` in advisory mode for allowed commands, mismatches, missing guard data, and
      configuration/git-inspection warnings unless `--fail-on-error` is explicit.
- [x] In strict mode, return exit `0` for allowed commands, `1` for blocked write-ish commands after
      a mismatch, and `2` for configuration or git-inspection errors.

**Acceptance criteria:**
- [ ] The helper never mutates git state except optional `git fetch` only when explicitly requested.
- [ ] Missing guard data is advisory by default and strict-failing only when configured.
- [ ] Advisory mode exits `0` for mismatch, missing-data, and configuration/git-inspection warnings
      unless an explicit fail-on-error option is set.
- [ ] Current `HEAD` mismatch and remote-ref mismatch are reported separately.
- [ ] The first self-commit path is safe: commit allowed when `HEAD == allowed_head_tip`, then
      `allowed_head_tip` updates to the new owned commit.
- [ ] The first push path is safe: push allowed when the remote is absent or still equals
      `expected_remote_tip`, then `expected_remote_tip` updates to the pushed commit.
- [ ] The block message tells the agent to Hard Stop, not to auto-merge, rebase, or repair.

**Files likely touched:**
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
- [ ] Add tests for command classification, guard-state source precedence, advisory/strict modes,
      current-tip mismatch, remote-tip mismatch, and missing git metadata.
- [ ] Add tests for the self-commit path: initial commit allowed, `allowed_head_tip` updated, push
      allowed while remote is at `expected_remote_tip`, and remote state updated after push.
- [ ] Add consistency checks only if the feature becomes prominent in public docs.

**Acceptance criteria:**
- [ ] Hook examples are opt-in and clearly labeled advanced.
- [ ] Mutating command examples are blocked on mismatch; read-only diagnostic commands are allowed.
- [ ] Tests cover both current `HEAD` mismatch and remote branch mismatch.
- [ ] The sync helper either installs the runtime guard intentionally or keeps it repo-only
      intentionally; no accidental install drift.

**Files likely touched:**
- `README.md`
- `references/tool-config-examples.md`
- `tests/test_workspace_guard.py`
- `scripts/check_repo_consistency.py` if guard phrasing becomes pinned

**Risk:** Hook command snippets can be brittle across shells and host versions. Keep examples simple,
use the helper for logic, and avoid large inline shell programs.

## Non-Negotiables

- The guard must never auto-repair collisions.
- The guard must allow read-only diagnostics after blocking a write.
- Missing guard data is advisory by default, not a surprise hard stop.
- Strict mode must be explicit.
- Advisory mode must exit `0` after mismatch, missing-data, and configuration/git-inspection
  warnings unless an explicit fail-on-error option is set, so it cannot surprise-block ordinary
  diagnostics or staging checks.
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
- The helper should print the branch, `start_tip`, `allowed_head_tip`, current `HEAD`,
  `expected_remote_tip`, observed remote tip, `last_pushed_tip`, and source of the guard data when
  it blocks.
- Do not parse arbitrary shell syntax deeply in the first version. Match the common command prefixes
  and keep a project override list for additional mutating commands.
- Updating owned-tip state is part of the core invariant, not a later enhancement. A helper that
  checks before writes but cannot update `allowed_head_tip`/`expected_remote_tip` after owned
  commits and pushes should stay advisory-only.
- If the remote branch does not exist yet, remote-tip mismatch should be advisory until the first
  push creates it.

## Notes

- This plan originally depended on the preflight duplicate-worktree guard, which defines the first
  enforcement layer.
- The auto-worktree helper remains the user-experience layer for creating isolated checkouts. This
  hook guard is the last-resort "do not write if the invariant broke" layer.
- The repo-only helper prototype has landed in the integration preview. Future work should focus on
  host-specific hook examples and adoption guidance without implying Codex has the same universal
  hook surface as Claude Code.
