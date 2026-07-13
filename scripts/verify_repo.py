#!/usr/bin/env python3
"""Canonical repository verification command for Elves.

Runs the local proof gates in a stable order without duplicate noisy discovery:

1. recursive Python compile under scripts/
2. shell syntax checks for shipped shell helpers
3. JSON validation for schema/example files
4. repo consistency checker
5. release checklist (optional version pin)
6. unittest discovery under tests/ (single discovery surface)
7. installed-bundle smokes for Claude and Codex (optional, default on)
8. git diff --check when inside a git worktree

Exit non-zero on the first hard failure unless --continue-on-error is set.
"""

from __future__ import annotations

import argparse
import json
import py_compile
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

SHELL_SCRIPTS = [
    "scripts/preflight.sh",
    "scripts/notify.sh",
]

JSON_PATHS = [
    "config.json.example",
    "references/implement-done-report.schema.json",
]


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


def compile_scripts(repo_root: Path) -> tuple[bool, str]:
    scripts = repo_root / "scripts"
    failures: list[str] = []
    count = 0
    for path in sorted(scripts.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        count += 1
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(str(exc))
    if failures:
        return False, f"compileall failed ({len(failures)}/{count}): {failures[0]}"
    return True, f"compileall ok ({count} files)"


def check_shell(repo_root: Path) -> tuple[bool, str]:
    checked = 0
    for rel in SHELL_SCRIPTS:
        path = repo_root / rel
        if not path.is_file():
            return False, f"missing shell script: {rel}"
        proc = _run(["bash", "-n", str(path)], cwd=repo_root)
        checked += 1
        if proc.returncode != 0:
            return False, f"shell syntax failed for {rel}: {proc.stderr.strip()}"
    return True, f"shell syntax ok ({checked} files)"


def check_json(repo_root: Path) -> tuple[bool, str]:
    checked = 0
    for rel in JSON_PATHS:
        path = repo_root / rel
        if not path.is_file():
            return False, f"missing json: {rel}"
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"invalid json {rel}: {exc}"
        checked += 1
    return True, f"json ok ({checked} files)"


def check_consistency(repo_root: Path) -> tuple[bool, str]:
    proc = _run([sys.executable, "scripts/check_repo_consistency.py"], cwd=repo_root)
    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-5:]
        return False, "consistency failed: " + " | ".join(tail)
    return True, "consistency ok"


def check_release(repo_root: Path, version: str | None) -> tuple[bool, str]:
    cmd = [sys.executable, "scripts/release_checklist.py"]
    if version:
        cmd.extend(["--version", version])
    else:
        cmd.append("--allow-unreleased")
    proc = _run(cmd, cwd=repo_root)
    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-5:]
        return False, "release checklist failed: " + " | ".join(tail)
    return True, "release checklist ok"


def check_unit_tests(repo_root: Path) -> tuple[bool, str]:
    # Single discovery surface under tests/ — avoid double discovery of the same suite.
    proc = _run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        cwd=repo_root,
    )
    output = (proc.stderr or "") + "\n" + (proc.stdout or "")
    summary = ""
    for line in output.splitlines()[::-1]:
        if line.startswith("Ran ") or line.strip() in {"OK", "FAILED"}:
            summary = line.strip()
            break
    if proc.returncode != 0:
        return False, f"unit tests failed ({summary or 'see output'})"
    return True, f"unit tests ok ({summary or 'OK'})"


def check_installed_smokes(repo_root: Path) -> tuple[bool, str]:
    proc = _run(
        [sys.executable, "scripts/installed_bundle_smoke.py", "--host", "all"],
        cwd=repo_root,
    )
    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-8:]
        return False, "installed bundle smoke failed: " + " | ".join(tail)
    return True, "installed bundle smoke ok"


def check_git_diff(repo_root: Path) -> tuple[bool, str]:
    if not (repo_root / ".git").exists() and not (repo_root / ".git").is_file():
        # Worktree or non-git: skip softly.
        proc = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_root)
        if proc.returncode != 0 or proc.stdout.strip() != "true":
            return True, "git diff --check skipped (not a git worktree)"
    proc = _run(["git", "diff", "--check"], cwd=repo_root)
    if proc.returncode != 0:
        return False, f"git diff --check failed: {(proc.stdout or proc.stderr).strip()}"
    return True, "git diff --check ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Pin release checklist to this version (omit for --allow-unreleased)",
    )
    parser.add_argument(
        "--skip-smokes",
        action="store_true",
        help="Skip installed-bundle smokes",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip unittest discovery",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Run all gates even after failures",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable results",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    gates: list[tuple[str, callable]] = [
        ("compileall", lambda: compile_scripts(repo_root)),
        ("shell", lambda: check_shell(repo_root)),
        ("json", lambda: check_json(repo_root)),
        ("consistency", lambda: check_consistency(repo_root)),
        ("release", lambda: check_release(repo_root, args.version)),
    ]
    if not args.skip_tests:
        gates.append(("unit-tests", lambda: check_unit_tests(repo_root)))
    if not args.skip_smokes:
        gates.append(("installed-smokes", lambda: check_installed_smokes(repo_root)))
    gates.append(("git-diff-check", lambda: check_git_diff(repo_root)))

    results: list[dict[str, object]] = []
    overall_ok = True
    for name, fn in gates:
        ok, message = fn()
        results.append({"gate": name, "ok": ok, "message": message})
        status = "OK" if ok else "FAIL"
        if not args.json:
            print(f"[{status}] {name}: {message}")
        if not ok:
            overall_ok = False
            if not args.continue_on_error:
                break

    if args.json:
        print(
            json.dumps(
                {"ok": overall_ok, "results": results, "repo_root": str(repo_root)},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("VERIFY " + ("OK" if overall_ok else "FAILED"))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
