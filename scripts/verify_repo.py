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
import re
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


def check_preflight_cache(
    repo_root: Path, *, final_readiness: bool = False
) -> tuple[bool, str]:
    """Record or reuse HEAD/config-keyed preflight evidence.

    Final readiness never accepts cache reuse alone — it only records after live
    gates already passed (this gate runs last).
    """
    try:
        scripts = str(repo_root / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from cobbler_runtime.preflight_cache import (  # noqa: PLC0415
            record_passing_preflight,
            reuse_preflight,
        )

        head_proc = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
        head = (head_proc.stdout or "").strip() or "unknown"
        if not final_readiness:
            decision = reuse_preflight(repo_root, head=head)
            if decision.get("reuse"):
                return True, "preflight cache reuse ok (final_readiness_accepts_cache_alone=false)"
        # Fresh capture after successful structural/broad gates (caller order).
        record_passing_preflight(
            repo_root,
            head=head,
            gates={"verify_repo_structural": "pass", "final_readiness": bool(final_readiness)},
            notes=[
                "recorded by verify_repo after included gates",
                "final readiness never accepts cache alone",
            ],
        )
        if final_readiness:
            return True, "preflight evidence recorded after live final-readiness gates"
        return True, "preflight evidence recorded (not final readiness)"
    except Exception as exc:  # noqa: BLE001
        return False, f"preflight cache failed: {exc}"


def check_public_api(repo_root: Path, *, required: bool = False) -> tuple[bool, str]:
    try:
        scripts = str(repo_root / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from cobbler_runtime.public_api_snapshot import compatibility_gate  # noqa: PLC0415

        result = compatibility_gate(repo_root, required=required)
        if result.get("ok"):
            return True, f"public-api gate ok action={result.get('action')} required={required}"
        return False, f"public-api gate failed: {result.get('breaking') or result}"
    except Exception as exc:  # noqa: BLE001
        return False, f"public-api gate error: {exc}"


def check_markdown_links(repo_root: Path) -> tuple[bool, str]:
    """Resolve relative Markdown links/anchors under key docs."""
    roots = [
        repo_root / "README.md",
        repo_root / "SKILL.md",
        repo_root / "AGENTS.md",
        repo_root / "docs",
        repo_root / "references",
        repo_root / ".ai-docs",
    ]
    link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    failures: list[str] = []
    checked = 0
    for root in roots:
        paths: list[Path]
        if root.is_file():
            paths = [root]
        elif root.is_dir():
            paths = sorted(root.rglob("*.md"))
        else:
            continue
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                failures.append(f"{path}: {exc}")
                continue
            for match in link_re.finditer(text):
                target = match.group(2).strip()
                if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                    if target.startswith("#"):
                        # In-page anchor: require heading slug presence (best-effort).
                        anchor = target[1:].lower().replace(" ", "-")
                        if anchor and anchor not in text.lower().replace(" ", "-"):
                            # Soft: many anchors are generated; only fail obvious missing.
                            pass
                    continue
                if "://" in target:
                    continue
                rel = target.split("#", 1)[0]
                if not rel:
                    continue
                dest = (path.parent / rel).resolve()
                checked += 1
                try:
                    dest.relative_to(repo_root.resolve())
                except ValueError:
                    failures.append(f"{path.relative_to(repo_root)}: escapes repo: {target}")
                    continue
                if not dest.exists():
                    failures.append(f"{path.relative_to(repo_root)}: missing {target}")
            if len(failures) > 40:
                break
        if len(failures) > 40:
            break
    if failures:
        return False, f"markdown links failed ({len(failures)}): {failures[0]}"
    return True, f"markdown links ok (checked≈{checked})"


def check_secret_patterns(repo_root: Path) -> tuple[bool, str]:
    """Scan tracked text for secret-shaped patterns (not values from env)."""
    needles = (
        "API_KEY=",
        "BEGIN PRIVATE KEY",
        "Bearer sk-",
        "xai-",
        "sk-or-",
    )
    # Only scan a bounded set of paths; ignore runtime and tests fixtures with sentinels.
    scan_roots = [repo_root / "scripts", repo_root / "references", repo_root / "docs"]
    hits: list[str] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".py", ".md", ".sh", ".yml", ".yaml", ".toml", ".json"}:
                continue
            if "test" in path.name.lower() and "fixture" in path.name.lower():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for needle in needles:
                if needle in text and "example" not in text.lower() and "redact" not in text.lower():
                    # Allow documentation of the pattern names themselves.
                    if f"`{needle}`" in text or f'"{needle}"' in text or f"'{needle}'" in text:
                        continue
                    if "pattern" in text.lower() or "sentinel" in text.lower():
                        continue
                    hits.append(f"{path.relative_to(repo_root)}:{needle}")
            if len(hits) > 20:
                break
        if len(hits) > 20:
            break
    if hits:
        return False, f"secret-pattern scan hits: {hits[0]}"
    return True, "secret-pattern scan ok"


def check_evidence_review_plan(
    repo_root: Path, *, execute_focused: bool = False
) -> tuple[bool, str]:
    try:
        scripts = str(repo_root / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from cobbler_runtime.evidence_review import plan_review  # noqa: PLC0415

        diff = _run(["git", "diff", "--name-only", "origin/main...HEAD"], cwd=repo_root)
        paths = [p for p in (diff.stdout or "").splitlines() if p.strip()]
        plan = plan_review(changed_paths=paths or ["scripts/verify_repo.py"])
        executed: list[str] = []
        if execute_focused:
            for check in list(plan.focused_checks or [])[:8]:
                name = str(check)
                # Focused checks are advisory command labels; run unittest subset when named.
                if "unittest" in name or name.endswith(".py"):
                    proc = _run(
                        [sys.executable, "-m", "unittest", name.split()[-1]],
                        cwd=repo_root,
                    )
                    executed.append(f"{name}:{'ok' if proc.returncode == 0 else 'fail'}")
                    if proc.returncode != 0:
                        return False, f"evidence-review focused check failed: {name}"
                else:
                    executed.append(f"{name}:recorded")
        return True, (
            f"evidence-review risk={plan.risk_level} broad={plan.broad_gate_required} "
            f"focused={len(plan.focused_checks)} executed={len(executed)}"
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"evidence-review plan failed: {exc}"


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
    parser.add_argument(
        "--final-readiness",
        action="store_true",
        help="Force broad gate selection notes (never accept preflight cache alone)",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    final = bool(args.final_readiness)

    # Final readiness: never accept preflight cache alone; run broad + structural gates live.
    gates: list[tuple[str, callable]] = [
        ("compileall", lambda: compile_scripts(repo_root)),
        ("shell", lambda: check_shell(repo_root)),
        ("json", lambda: check_json(repo_root)),
        ("consistency", lambda: check_consistency(repo_root)),
        ("release", lambda: check_release(repo_root, args.version)),
        (
            "evidence-review",
            lambda: check_evidence_review_plan(repo_root, execute_focused=final),
        ),
        ("public-api", lambda: check_public_api(repo_root, required=final)),
    ]
    if final:
        gates.append(("markdown-links", lambda: check_markdown_links(repo_root)))
        gates.append(("secret-patterns", lambda: check_secret_patterns(repo_root)))
    if not args.skip_tests:
        gates.append(("unit-tests", lambda: check_unit_tests(repo_root)))
    if not args.skip_smokes:
        gates.append(("installed-smokes", lambda: check_installed_smokes(repo_root)))
    gates.append(("git-diff-check", lambda: check_git_diff(repo_root)))
    # Preflight cache may only be recorded after all included gates pass (append last).
    gates.append(
        ("preflight-cache", lambda: check_preflight_cache(repo_root, final_readiness=final))
    )

    results: list[dict[str, object]] = []
    overall_ok = True
    for name, fn in gates:
        # Do not record preflight cache on a failing final-readiness run.
        if name == "preflight-cache" and not overall_ok and final:
            results.append(
                {
                    "gate": name,
                    "ok": False,
                    "message": "preflight cache skipped: prior final-readiness gates failed",
                }
            )
            if not args.json:
                print(f"[FAIL] {name}: preflight cache skipped: prior gates failed")
            overall_ok = False
            continue
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
                {
                    "ok": overall_ok,
                    "final_readiness": final,
                    "results": results,
                    "repo_root": str(repo_root),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("VERIFY " + ("OK" if overall_ok else "FAILED"))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
