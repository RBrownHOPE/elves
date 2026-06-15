# Preflight Auto-Worktree Helper Design

## Purpose

Design a small, boring helper that makes the safe path easy when an Elves run should use a dedicated
`git worktree`. The helper should reduce operator friction during staging without weakening the
one-run-one-branch-one-checkout rule.

This is a design note only. It intentionally does not change `scripts/preflight.sh`, README,
`SKILL.md`, `AGENTS.md`, TODO, or release docs. Implementation should wait until the current
preflight worktree-ownership guard lands, because that guard is the foundation this helper builds
on.

## Current State

Elves already tells agents to create a dedicated worktree when another agent may touch the same
repo:

```bash
git worktree add -b <branch> ../<repo>-<branch>
```

The staged preflight hardening work adds a deterministic duplicate-current-branch guard. That guard
detects unsafe shared ownership, but it does not create the safer checkout for the operator.

The remaining friction is therefore setup ergonomics: the user or staging agent must remember the
exact worktree command, choose a path, avoid branch collisions, and switch into the new checkout.

## UX Goal

When preflight sees that a dedicated checkout would be safer, it should print one copy-paste command
that creates the worktree in the conventional location and explains what to do next.

The helper should feel like a seatbelt, not a wizard:

- detect likely need for a dedicated worktree;
- recommend a deterministic branch and path;
- print an exact command;
- optionally execute only when called with an explicit non-interactive flag;
- never move or delete existing worktrees automatically.

## Proposed Interface

Keep the default preflight behavior advisory:

```bash
./scripts/preflight.sh
```

If the current checkout appears unsafe for a new run, preflight prints:

```text
Recommended dedicated worktree:
git worktree add -b <branch> ../<repo>-<branch>
cd ../<repo>-<branch>
```

Add an opt-in helper mode later:

```bash
./scripts/preflight.sh --create-worktree <branch>
```

Optional flags:

- `--worktree-dir <path>`: override the generated sibling directory.
- `--base <ref>`: default to the current `HEAD`; commonly `origin/main`.
- `--dry-run`: print the command and checks without creating anything.

The helper should not prompt. If required inputs are missing, it should print a clear error and exit
non-zero.

## Detection Rules

Recommend a dedicated worktree when any of these are true:

- the survival guide or kickoff prompt says another agent may touch the repo;
- the current branch is already checked out in more than one worktree;
- the operator requested a branch name that already exists in the current checkout;
- `git worktree list --porcelain` shows another active checkout for the same branch;
- the staging instruction explicitly asks for isolated or concurrent agent work.

Do not infer too much. If the signal is weak, print an advisory note instead of creating anything.

## Naming Rules

Default branch name:

```text
codex/<short-task-slug>
```

Default worktree path:

```text
../<repo>-<short-task-slug>
```

Rules:

- keep slugs lowercase ASCII;
- replace non-alphanumeric runs with one hyphen;
- trim leading and trailing hyphens;
- cap the slug length to keep paths readable;
- if the path exists, append `-2`, `-3`, and so on;
- if the branch exists, fail with a clear message unless the user supplied an explicit reuse mode in
  a future design.

No implementation should silently reuse an existing worktree. Reuse is where collisions hide.

## Safety Checks

Before creating anything, validate:

- `git rev-parse --show-toplevel` succeeds;
- `git status --short` is clean unless the command is only `--dry-run`;
- `git remote get-url origin` succeeds;
- the base ref resolves;
- the target branch does not already exist locally or remotely;
- the target worktree directory does not already contain a git checkout;
- `git worktree list --porcelain` does not already show the target branch.

After creation:

- print the new path;
- print `git rev-parse HEAD` as the collision tripwire;
- remind the agent to run preflight again from inside the new checkout;
- do not open a PR or edit run docs from the original checkout.

## Implementation Shape

Prefer a small shell helper inside `scripts/preflight.sh` only if it stays readable. If the shell
logic grows beyond simple parsing and checks, move the worktree creation logic into a separate
Python helper and call it from preflight.

The shell path is acceptable for:

- argument parsing for `--create-worktree`, `--worktree-dir`, `--base`, and `--dry-run`;
- git command checks;
- printing the recommended command.

Use Python if implementing:

- slug generation with edge cases;
- path collision suffixing;
- structured test fixtures;
- clearer error reporting across platforms.

## Test Plan

Add tests before enabling automatic creation:

- clean repo prints no create-worktree requirement by default;
- duplicate current-branch checkout produces an advisory and non-zero guard result;
- `--dry-run --create-worktree <branch>` prints the exact command and creates nothing;
- existing branch fails before creating a worktree;
- existing target directory fails before creating a worktree;
- valid create mode calls `git worktree add -b <branch> <dir> <base>`;
- after creation, output includes the branch tip collision tripwire;
- no mode prompts for user input.

If implementation remains in shell, use temporary git repositories in a Python unittest file so the
test can inspect filesystem effects without depending on this repo's live worktrees.

## Rollout

1. Land the duplicate-current-branch guard first.
2. Add advisory-only command printing to preflight.
3. Add `--dry-run --create-worktree` tests.
4. Add explicit `--create-worktree` execution mode.
5. Update README, `SKILL.md`, `AGENTS.md`, kickoff template, survival-guide template, changelog,
   and repo consistency checks in the same implementation PR.

## Out Of Scope

- Moving an active run between worktrees.
- Reusing existing worktrees.
- Deleting, pruning, or repairing stale worktrees.
- Creating branches from uncommitted dirty state.
- Opening PRs or writing survival guides from the original checkout.
- Coordinating two active agents on the same branch.

## Acceptance Criteria For A Future Implementation

- A staging agent can get from "this repo may have another writer" to an isolated checkout with one
  explicit non-interactive command.
- The default `./scripts/preflight.sh` remains advisory and does not mutate the repo.
- The helper never overwrites, reuses, deletes, or repairs an existing worktree automatically.
- The generated output records the branch, worktree path, base ref, and collision tripwire.
- Tests cover both advisory output and successful opt-in creation in temporary repositories.
