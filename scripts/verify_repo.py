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
8. landing plan/session acceptance in final-readiness mode
9. cumulative/index/worktree git checks

Exit non-zero on the first hard failure unless --continue-on-error is set.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import py_compile
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit
from pathlib import Path
from typing import Callable, Mapping


_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from cobbler_runtime.context import (  # noqa: E402
    is_secret_env_name,
    redact_text,
    scrub_environment,
)


REPO_ROOT = Path(__file__).resolve().parent.parent

SHELL_SCRIPTS = [
    "scripts/preflight.sh",
    "scripts/notify.sh",
]

JSON_PATHS = [
    "config.json.example",
    "references/implement-done-report.schema.json",
]

UNIT_TEST_FAILURE_MAX_LINES = 60
UNIT_TEST_FAILURE_MAX_CHARS = 8_000
DEFAULT_BASE_REFS = ("origin/main", "origin/master", "main", "master")
SECRET_SCAN_DIRS = ("scripts", "references", "docs", ".ai-docs", ".github", "aliases")
SECRET_SCAN_FILES = (
    "README.md",
    "SKILL.md",
    "AGENTS.md",
    "CHANGELOG.md",
    "TODO.md",
    "config.json.example",
)


def _secret_env_values(
    parent_env: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Exact secret-looking values that must never appear in diagnostics."""
    source = parent_env if parent_env is not None else os.environ
    return frozenset(
        value
        for name, value in source.items()
        if is_secret_env_name(name) and isinstance(value, str) and len(value) >= 8
    )


def _verification_environment(
    parent_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Minimal environment for repository verification child processes."""
    scrubbed = scrub_environment(parent_env)
    env = dict(scrubbed.env)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _redact_message(value: object) -> str:
    return redact_text(
        str(value),
        exact_values=_secret_env_values(),
    ).text


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
        env=_verification_environment(),
    )


def _bounded_failure_tail(output: str) -> str:
    lines = output.strip().splitlines()
    if not lines:
        return ""
    tail = lines[-UNIT_TEST_FAILURE_MAX_LINES:]
    traceback_indexes = [
        index
        for index, line in enumerate(lines)
        if line.startswith("Traceback (most recent call last):")
    ]
    if traceback_indexes and traceback_indexes[-1] < len(lines) - len(tail):
        traceback_start = traceback_indexes[-1]
        traceback_excerpt = lines[
            traceback_start : traceback_start + UNIT_TEST_FAILURE_MAX_LINES // 2
        ]
        remaining = UNIT_TEST_FAILURE_MAX_LINES - len(traceback_excerpt) - 1
        suffix = tail[-remaining:] if remaining > 0 else []
        tail = traceback_excerpt + ["[...tail...]"] + suffix
    rendered = "\n".join(tail)
    if len(rendered) > UNIT_TEST_FAILURE_MAX_CHARS:
        rendered = rendered[-UNIT_TEST_FAILURE_MAX_CHARS:]
        rendered = "[...bounded...]\n" + rendered.lstrip("\n")
    if len(lines) > len(tail) and not rendered.startswith("[...bounded...]"):
        rendered = "[...bounded...]\n" + rendered
    return _redact_message(rendered)


def _resolve_default_branch_ref(repo_root: Path) -> str | None:
    candidates: list[str] = []
    symbolic = _run(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo_root,
    )
    if symbolic.returncode == 0 and symbolic.stdout.strip():
        candidates.append(symbolic.stdout.strip())
    candidates.extend(DEFAULT_BASE_REFS)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        exists = _run(
            ["git", "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
            cwd=repo_root,
        )
        if exists.returncode == 0:
            return candidate
    return None


def _merge_base(repo_root: Path, base_ref: str) -> str | None:
    proc = _run(["git", "merge-base", base_ref, "HEAD"], cwd=repo_root)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


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
        return False, _redact_message(
            f"compileall failed ({len(failures)}/{count}): {failures[0]}"
        )
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
            return False, _redact_message(
                f"shell syntax failed for {rel}: {proc.stderr.strip()}"
            )
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
            return False, _redact_message(f"invalid json {rel}: {exc}")
        checked += 1
    return True, f"json ok ({checked} files)"


def check_consistency(repo_root: Path) -> tuple[bool, str]:
    proc = _run([sys.executable, "scripts/check_repo_consistency.py"], cwd=repo_root)
    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-5:]
        return False, _redact_message("consistency failed: " + " | ".join(tail))
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
        return False, _redact_message(
            "release checklist failed: " + " | ".join(tail)
        )
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
        tail = _bounded_failure_tail(output)
        detail = f"\n--- bounded unittest failure tail ---\n{tail}" if tail else ""
        return False, f"unit tests failed ({summary or 'see output'}){detail}"
    return True, f"unit tests ok ({summary or 'OK'})"


def check_installed_smokes(repo_root: Path) -> tuple[bool, str]:
    proc = _run(
        [sys.executable, "scripts/installed_bundle_smoke.py", "--host", "all"],
        cwd=repo_root,
    )
    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-8:]
        return False, _redact_message(
            "installed bundle smoke failed: " + " | ".join(tail)
        )
    return True, "installed bundle smoke ok"


def check_landing(
    repo_root: Path,
    *,
    session_path: Path,
    plan_path: Path | None = None,
) -> tuple[bool, str]:
    """Run the plan/session acceptance gate required for final readiness."""
    session_ok, resolved_session, detail = _verified_repo_file(
        repo_root,
        session_path,
        base=repo_root,
        label="session",
    )
    if not session_ok or resolved_session is None:
        return False, detail
    try:
        session = json.loads(resolved_session.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, _redact_message(f"landing session is unreadable: {exc}")
    if not isinstance(session, dict):
        return False, "landing session must be a JSON object"

    recorded_raw = session.get("plan_path")
    if not isinstance(recorded_raw, str) or not recorded_raw.strip():
        return False, "landing session must record a non-empty plan_path"
    recorded_ok, recorded_plan, recorded_detail = _verified_repo_file(
        repo_root,
        Path(recorded_raw),
        base=resolved_session.parent,
        label="plan",
    )
    if not recorded_ok or recorded_plan is None:
        return False, recorded_detail

    resolved_plan = recorded_plan
    if plan_path is not None:
        plan_ok, explicit_plan, plan_detail = _verified_repo_file(
            repo_root,
            plan_path,
            base=repo_root,
            label="plan",
        )
        if not plan_ok or explicit_plan is None:
            return False, plan_detail
        if explicit_plan != recorded_plan:
            return False, (
                f"landing --plan {explicit_plan} does not exactly match session "
                f"plan_path {recorded_plan}"
            )
        resolved_plan = explicit_plan

    identity_ok, identity_detail = _verify_session_identity(repo_root, session)
    if not identity_ok:
        return False, identity_detail
    cmd = [
        sys.executable,
        "scripts/elves_landing_check.py",
        "--session",
        str(resolved_session),
        "--plan",
        str(resolved_plan),
        "--repo-root",
        str(repo_root),
        "--json",
    ]
    proc = _run(cmd, cwd=repo_root)
    if proc.returncode != 0:
        tail = _bounded_failure_tail((proc.stderr or "") + "\n" + (proc.stdout or ""))
        return False, "landing acceptance failed" + (f": {tail}" if tail else "")
    return True, "landing acceptance ok"


def _verified_repo_file(
    repo_root: Path,
    raw_path: Path,
    *,
    base: Path,
    label: str,
) -> tuple[bool, Path | None, str]:
    """Require an ordinary non-symlink tracked file from the current HEAD tree."""
    root = repo_root.resolve()
    candidate = raw_path.expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    lexical = Path(os.path.abspath(candidate))
    try:
        resolved = lexical.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        return False, None, f"landing {label} must stay inside repository: {lexical}"

    cursor = lexical
    while cursor != cursor.parent:
        if cursor.is_symlink():
            return False, None, f"landing {label} must not use a symlink: {cursor}"
        try:
            at_repo_root = cursor.resolve(strict=False) == root
        except OSError:
            at_repo_root = False
        if at_repo_root:
            break
        cursor = cursor.parent
    if not lexical.is_file():
        return False, None, f"landing {label} must be a regular file: {lexical}"

    rel_text = relative.as_posix()
    tracked = _run(
        ["git", "ls-files", "--error-unmatch", "--", rel_text], cwd=root
    )
    if tracked.returncode != 0:
        return False, None, f"landing {label} must be tracked by Git: {rel_text}"
    committed = _run(["git", "cat-file", "-e", f"HEAD:{rel_text}"], cwd=root)
    if committed.returncode != 0:
        return False, None, f"landing {label} must exist in current HEAD: {rel_text}"
    return True, resolved, f"landing {label} provenance ok"


def _verify_session_identity(
    repo_root: Path, session: dict[str, object]
) -> tuple[bool, str]:
    run_id = session.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return False, "landing session must record a non-empty run_id"
    branch = session.get("branch")
    current = _run(["git", "branch", "--show-current"], cwd=repo_root)
    active = current.stdout.strip() if current.returncode == 0 else ""
    if not isinstance(branch, str) or not branch.strip() or branch != active:
        return False, f"landing session branch {branch!r} does not match active branch {active!r}"
    start_head = session.get("start_head")
    if not isinstance(start_head, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", start_head):
        return False, "landing session must record an exact 40-character start_head"
    commit = _run(
        ["git", "rev-parse", "--verify", f"{start_head}^{{commit}}"], cwd=repo_root
    )
    if commit.returncode != 0 or commit.stdout.strip().lower() != start_head.lower():
        return False, f"landing session start_head is not an exact repository commit: {start_head}"
    ancestor = _run(
        ["git", "merge-base", "--is-ancestor", start_head, "HEAD"], cwd=repo_root
    )
    if ancestor.returncode != 0:
        return False, f"landing session start_head is not an ancestor of HEAD: {start_head}"
    return True, f"landing session identity ok run_id={run_id} branch={branch}"


def check_git_diff(
    repo_root: Path,
    *,
    final_readiness: bool = False,
    cumulative_required: bool | None = None,
    base_ref: str | None = None,
) -> tuple[bool, str]:
    if cumulative_required is None:
        cumulative_required = final_readiness
    if not (repo_root / ".git").exists() and not (repo_root / ".git").is_file():
        # Worktree or non-git: skip softly.
        proc = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_root)
        if proc.returncode != 0 or proc.stdout.strip() != "true":
            if final_readiness or cumulative_required:
                return False, "git readiness failed: repository is not a git worktree"
            return True, "git diff --check skipped (not a git worktree)"

    cumulative = ""
    if cumulative_required:
        resolved_base = base_ref or _resolve_default_branch_ref(repo_root)
        if resolved_base is None:
            return False, "git diff --check failed: could not resolve default branch ref"
        merge_base = _merge_base(repo_root, resolved_base)
        if merge_base is None:
            return False, f"git diff --check failed: no merge-base for {resolved_base}...HEAD"
        range_spec = f"{merge_base}..HEAD"
        proc = _run(["git", "diff", "--check", range_spec], cwd=repo_root)
        if proc.returncode != 0:
            return False, _redact_message(
                f"git diff --check failed for cumulative {resolved_base}...HEAD "
                f"(merge-base {merge_base}): {(proc.stdout or proc.stderr).strip()}"
            )
        cumulative = f"cumulative {resolved_base}...HEAD (merge-base {merge_base}) + "

    # Check both index and working tree. Plain ``git diff`` excludes staged
    # content, while the cumulative branch range excludes all uncommitted work.
    for label, cmd in (
        ("index", ["git", "diff", "--check", "--cached"]),
        ("working tree", ["git", "diff", "--check"]),
    ):
        proc = _run(cmd, cwd=repo_root)
        if proc.returncode != 0:
            return False, _redact_message(
                f"git diff --check failed for {label}: "
                f"{(proc.stdout or proc.stderr).strip()}"
            )

    if final_readiness:
        status = _run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo_root,
        )
        if status.returncode != 0:
            return False, _redact_message(
                f"git status failed: {(status.stderr or status.stdout).strip()}"
            )
        dirty = [line for line in status.stdout.splitlines() if line.strip()]
        if dirty:
            preview = " | ".join(dirty[:8])
            suffix = f" | +{len(dirty) - 8} more" if len(dirty) > 8 else ""
            return False, _redact_message(
                f"git readiness failed: worktree is not clean: {preview}{suffix}"
            )
    return True, f"git diff --check ok ({cumulative}index + working tree)"


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
        return False, _redact_message(f"preflight cache failed: {exc}")


def check_public_api(
    repo_root: Path,
    *,
    required: bool = False,
    base_ref: str | None = None,
) -> tuple[bool, str]:
    try:
        scripts = str(repo_root / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from cobbler_runtime.public_api_snapshot import compatibility_gate  # noqa: PLC0415

        result = compatibility_gate(repo_root, required=required, base_ref=base_ref)
        if result.get("ok"):
            return True, f"public-api gate ok action={result.get('action')} required={required}"
        return False, _redact_message(
            f"public-api gate failed: {result.get('breaking') or result}"
        )
    except Exception as exc:  # noqa: BLE001
        return False, _redact_message(f"public-api gate error: {exc}")


def _markdown_destination(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")].strip()
    # Markdown permits an optional quoted title after a whitespace separator.
    match = re.match(r"^(\S+?)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?$", value)
    return match.group(1) if match else value


def _github_heading_slug(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    value = re.sub(r"!?(?:\[([^]]+)\])\([^)]+\)", r"\1", value)
    value = value.replace("`", "").strip().casefold()
    value = re.sub(r"[^\w\-\s]", "", value, flags=re.UNICODE)
    return re.sub(r"\s+", "-", value)


def _blank_markdown(value: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in value)


def _active_markdown_text(value: str) -> str:
    value = re.sub(
        r"<!--.*?(?:-->|\Z)",
        lambda match: _blank_markdown(match.group(0)),
        value,
        flags=re.DOTALL,
    )
    rendered: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    for line in value.splitlines(keepends=True):
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence_char is None:
            if match is None:
                rendered.append(line)
                continue
            marker = match.group(1)
            fence_char = marker[0]
            fence_length = len(marker)
            rendered.append(_blank_markdown(line))
            continue
        rendered.append(_blank_markdown(line))
        if match is not None:
            marker = match.group(1)
            if marker[0] == fence_char and len(marker) >= fence_length:
                fence_char = None
                fence_length = 0
    return "".join(rendered)


def _markdown_anchors(path: Path) -> set[str]:
    text = _active_markdown_text(path.read_text(encoding="utf-8"))
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for match in re.finditer(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", text):
        base = _github_heading_slug(match.group(1))
        if not base:
            continue
        duplicate = counts.get(base, 0)
        counts[base] = duplicate + 1
        anchors.add(base if duplicate == 0 else f"{base}-{duplicate}")
    for match in re.finditer(
        r"(?i)<(?:a|[a-z][a-z0-9:-]*)\b[^>]*\b(?:id|name)=[\"']([^\"']+)[\"']",
        text,
    ):
        anchors.add(html.unescape(match.group(1)).strip())
    return anchors | {anchor.casefold() for anchor in anchors}


def _tracked_link_target(repo_root: Path, destination: Path) -> bool:
    relative = destination.relative_to(repo_root).as_posix()
    if destination.is_dir():
        proc = _run(["git", "ls-files", "--", f"{relative.rstrip('/')}/"], cwd=repo_root)
        return proc.returncode == 0 and bool(proc.stdout.strip())
    proc = _run(
        ["git", "ls-files", "--error-unmatch", "--", relative], cwd=repo_root
    )
    return proc.returncode == 0


def check_markdown_links(repo_root: Path) -> tuple[bool, str]:
    """Resolve relative Markdown links and require real tracked targets/anchors."""
    roots = [
        repo_root / "README.md",
        repo_root / "SKILL.md",
        repo_root / "AGENTS.md",
        repo_root / "docs",
        repo_root / "references",
        repo_root / ".ai-docs",
    ]
    link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    definition_re = re.compile(
        r"(?m)^\s{0,3}\[([^]]+)\]:\s*(<[^>]+>|\S+)(?:\s+.*)?$"
    )
    reference_re = re.compile(r"\[([^]]+)\]\[([^]]*)\]")
    failures: list[str] = []
    checked = 0
    anchor_cache: dict[Path, set[str]] = {}
    resolved_root = repo_root.resolve()
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
                text = _active_markdown_text(path.read_text(encoding="utf-8"))
            except OSError as exc:
                failures.append(f"{path}: {exc}")
                continue
            definitions = {
                re.sub(r"\s+", " ", match.group(1)).strip().casefold(): _markdown_destination(
                    match.group(2)
                )
                for match in definition_re.finditer(text)
            }
            targets = [
                _markdown_destination(match.group(2)) for match in link_re.finditer(text)
            ]
            for match in reference_re.finditer(text):
                label = match.group(2) or match.group(1)
                key = re.sub(r"\s+", " ", label).strip().casefold()
                target = definitions.get(key)
                if target is None:
                    failures.append(
                        f"{path.relative_to(repo_root)}: missing reference definition [{label}]"
                    )
                    continue
                targets.append(target)
            for target in targets:
                if not target:
                    continue
                parsed = urlsplit(target)
                if parsed.scheme in {"http", "https", "mailto", "tel", "data"} or parsed.netloc:
                    continue
                if parsed.scheme or "://" in target:
                    continue
                raw_rel = unquote(parsed.path)
                fragment = unquote(parsed.fragment).strip()
                if raw_rel:
                    base = resolved_root if raw_rel.startswith("/") else path.parent
                    dest = (base / raw_rel.lstrip("/")).resolve()
                else:
                    dest = path.resolve()
                checked += 1
                try:
                    dest.relative_to(resolved_root)
                except ValueError:
                    failures.append(f"{path.relative_to(repo_root)}: escapes repo: {target}")
                    continue
                if not dest.exists():
                    failures.append(f"{path.relative_to(repo_root)}: missing {target}")
                    continue
                if not _tracked_link_target(resolved_root, dest):
                    failures.append(
                        f"{path.relative_to(repo_root)}: target is not shipped/tracked: {target}"
                    )
                    continue
                if fragment and dest.is_file() and dest.suffix.lower() in {".md", ".markdown"}:
                    try:
                        anchors = anchor_cache.get(dest)
                        if anchors is None:
                            anchors = _markdown_anchors(dest)
                            anchor_cache[dest] = anchors
                    except (OSError, UnicodeError) as exc:
                        failures.append(
                            f"{path.relative_to(repo_root)}: cannot inspect anchor {target}: {exc}"
                        )
                        continue
                    if fragment not in anchors and fragment.casefold() not in anchors:
                        failures.append(
                            f"{path.relative_to(repo_root)}: missing anchor #{fragment} in "
                            f"{dest.relative_to(resolved_root)}"
                        )
            if len(failures) > 40:
                break
        if len(failures) > 40:
            break
    if failures:
        return False, _redact_message(
            f"markdown links failed ({len(failures)}): {failures[0]}"
        )
    return True, f"markdown links ok (checked≈{checked})"


_SECRET_ASSIGNMENT = re.compile(
    r"""
    (?<![A-Z0-9_-])
    (?P<name_quote>[\"']?)
    (?P<name>[A-Z0-9_-]*(?:API[-_]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[-_]?KEY|ACCESS[-_]?KEY|AUTHORIZATION)[A-Z0-9_-]*)
    (?P=name_quote)
    \s*(?P<separator>[:=])\s*
    (?P<value>(?:[rubf]{0,2})?\"[^\"\n]*\"|(?:[rubf]{0,2})?'[^'\n]*'|[^\s#,;,)]+)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _secret_placeholder(value: str) -> bool:
    raw = value.strip()
    string_prefix = re.match(r"(?i)^[rubf]{1,2}(?=[\"'])", raw)
    if string_prefix:
        raw = raw[string_prefix.end() :]
    quoted = len(raw) >= 2 and raw[0] in "`\"'" and raw[-1] == raw[0]
    token = raw.strip("`\"'").strip()
    upper = token.upper()
    if not token or token in {"...", "=", "(", "[", "{"} or token.startswith(
        ("(", "[", "{")
    ) or upper in {
        "NONE",
        "NULL",
        "FALSE",
        "TRUE",
    }:
        return True
    if upper.startswith(
        (
            "[REDACTED",
            "<REDACTED",
            "${",
            "$",
            "<YOUR_",
            "<YOUR-",
            "YOUR_",
            "YOUR-",
            "PLACEHOLDER",
            "REDACTED",
            "EXAMPLE_",
            "EXAMPLE-",
            "TEST_",
            "TEST-",
            "FAKE_",
            "FAKE-",
            "DUMMY_",
            "DUMMY-",
            "SAMPLE_",
            "SAMPLE-",
        )
    ):
        return True
    lowered = token.casefold()
    if upper.startswith("BEARER "):
        return _secret_placeholder(token.split(None, 1)[1])
    if lowered.startswith(
        (
            "os.environ",
            "os.getenv",
            "getenv(",
            "re.compile(",
            "process.env",
            "env.",
            "settings.",
            "config.",
            "args.",
        )
    ):
        return True
    if not quoted and re.match(r"^[A-Za-z_][A-Za-z0-9_.]*\(", token):
        return True
    if not quoted and re.match(r"^[A-Za-z_][A-Za-z0-9_.]*\[", token):
        return True
    if len(token) >= 8 and len(set(token.casefold())) == 1:
        return True
    if ("{" in token and "}" in token) or re.search(r"\\[sSdDwWbB]|\[A-Z", token):
        return True
    return False


def _secret_value_placeholder(pattern_name: str, value: str) -> bool:
    """Recognize documented/example sentinels inside provider token wrappers."""
    candidate = value
    if pattern_name == "bearer_token":
        parts = value.split(None, 1)
        candidate = parts[1] if len(parts) == 2 else value
    elif pattern_name == "sk_token":
        candidate = re.sub(r"(?i)^sk-(?:proj-|svcacct-)?", "", value)
    elif pattern_name == "xai_token":
        candidate = re.sub(r"(?i)^xai-", "", value)
    elif pattern_name in {"github_pat", "github_oauth", "github_token"}:
        candidate = value.split("_", 1)[1] if "_" in value else value
    elif pattern_name == "github_fine_grained_pat":
        candidate = re.sub(r"(?i)^github_pat_", "", value)
    elif pattern_name == "aws_access_key":
        return value.upper() == "AKIAIOSFODNN7EXAMPLE"
    elif pattern_name == "pem_block":
        candidate = re.sub(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
            r"-----END [A-Z ]*PRIVATE KEY-----",
            "",
            value,
        ).strip()
    return _secret_placeholder(candidate)


def _secret_assignment_name(name: str) -> bool:
    normalized = name.upper().replace("-", "_")
    return bool(
        re.search(
            r"(?:^|_)(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE_?KEY|"
            r"ACCESS_?KEY|AUTHORIZATION)(?:_|$)",
            normalized,
        )
    )


def _assignment_is_pattern_literal(text: str, match: re.Match[str]) -> bool:
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    local_start = match.start() - line_start
    local_separator = match.start("separator") - line_start
    for delimiter in ('`', '"', "'"):
        if line[:local_start].count(delimiter) % 2 != 1:
            continue
        closing = line.find(delimiter, local_start)
        if closing < local_separator:
            continue
        suffix = line[local_separator + 1 : closing].strip()
        if not suffix or suffix.startswith(("[", "\\", "(")):
            return True
    return False


def check_secret_patterns(repo_root: Path) -> tuple[bool, str]:
    """Scan shipped text for named assignments and shared secret value shapes."""
    # Use the verifier's shipped runtime definitions even when scanning a
    # synthetic fixture repository in tests.
    scripts_path = str(Path(__file__).resolve().parent)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    try:
        from cobbler_runtime.context import SECRET_VALUE_PATTERNS  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return False, _redact_message(
            f"secret-pattern scan could not load shared patterns: {exc}"
        )
    # Scan every shipped/runtime and human-facing surface while keeping deliberate
    # test sentinels out of the release gate. Root docs are explicit because they
    # are the most likely place for a credential to be pasted accidentally.
    scan_roots = [repo_root / relative for relative in SECRET_SCAN_DIRS]
    scan_files = [repo_root / relative for relative in SECRET_SCAN_FILES]
    tracked = _run(["git", "ls-files"], cwd=repo_root)
    if tracked.returncode == 0:
        scan_files.extend(
            repo_root / relative
            for relative in tracked.stdout.splitlines()
            if Path(relative).name == ".env" or Path(relative).name.startswith(".env.")
        )
    hits: list[str] = []
    for root in [*scan_roots, *scan_files]:
        if root.is_file():
            candidates = (root,)
        elif root.is_dir():
            candidates = root.rglob("*")
        else:
            continue
        for path in candidates:
            if not path.is_file():
                continue
            env_config = path.name == ".env" or path.name.startswith(".env.")
            if (
                path.suffix
                not in {".py", ".md", ".sh", ".yml", ".yaml", ".toml", ".json"}
                and not env_config
            ):
                continue
            if "test" in path.name.lower() and "fixture" in path.name.lower():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            relative = path.relative_to(repo_root)
            for match in _SECRET_ASSIGNMENT.finditer(text):
                if not _secret_assignment_name(match.group("name")):
                    continue
                if _assignment_is_pattern_literal(text, match):
                    continue
                if match.group("separator") == ":" and not match.group("name_quote"):
                    line_start = text.rfind("\n", 0, match.start()) + 1
                    prefix = text[line_start : match.start()].strip()
                    if path.suffix == ".py" or (prefix and prefix != "-"):
                        continue
                value = match.group("value")
                if (
                    match.group("name").upper().replace("-", "_") == "AUTHORIZATION"
                    and value.strip("`\"'").casefold() == "bearer"
                ):
                    line_end = text.find("\n", match.start("value"))
                    if line_end < 0:
                        line_end = len(text)
                    bearer = re.match(
                        r"(?i)Bearer\s+[^\s#,;)]+",
                        text[match.start("value") : line_end].strip(),
                    )
                    if bearer:
                        value = bearer.group(0)
                if path.suffix == ".py" and re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_.]*", value.strip()
                ):
                    continue
                if _secret_placeholder(value):
                    continue
                line_number = text.count("\n", 0, match.start()) + 1
                hits.append(
                    f"{relative}:{line_number}:named-{match.group('name').casefold()}"
                )
            for pattern_name, pattern in SECRET_VALUE_PATTERNS:
                for match in pattern.finditer(text):
                    value = match.group(0)
                    if _secret_value_placeholder(pattern_name, value):
                        continue
                    line_number = text.count("\n", 0, match.start()) + 1
                    hits.append(f"{relative}:{line_number}:{pattern_name}")
            if len(hits) > 20:
                break
        if len(hits) > 20:
            break
    if hits:
        return False, f"secret-pattern scan hits: {hits[0]}"
    return True, "secret-pattern scan ok"


def _cumulative_changed_paths(
    repo_root: Path, *, base_ref: str | None = None
) -> tuple[bool, list[str], str]:
    resolved_base = base_ref or _resolve_default_branch_ref(repo_root)
    if resolved_base is None:
        return False, [], "could not resolve default branch ref"
    merge_base = _merge_base(repo_root, resolved_base)
    if merge_base is None:
        return False, [], f"could not resolve merge-base for {resolved_base}...HEAD"
    range_spec = f"{merge_base}..HEAD"
    paths: set[str] = set()
    commands = (
        ("committed", ["git", "diff", "--name-only", range_spec]),
        ("index", ["git", "diff", "--name-only", "--cached"]),
        ("working tree", ["git", "diff", "--name-only"]),
        (
            "untracked",
            ["git", "ls-files", "--others", "--exclude-standard"],
        ),
    )
    for label, command in commands:
        diff = _run(command, cwd=repo_root)
        if diff.returncode != 0:
            detail = (diff.stderr or diff.stdout or f"git {label} diff failed").strip()
            return False, [], _redact_message(detail)
        paths.update(path for path in diff.stdout.splitlines() if path.strip())
    return (
        True,
        sorted(paths),
        f"{resolved_base}...HEAD (merge-base {merge_base}) + index/worktree/untracked",
    )


def check_evidence_review_plan(
    repo_root: Path,
    *,
    execute_focused: bool = False,
    final_readiness: bool = False,
    release_version: str | None = None,
    base_ref: str | None = None,
    result_cache: dict[str, tuple[bool, str]] | None = None,
) -> tuple[bool, str]:
    try:
        scripts = str(repo_root / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from cobbler_runtime.evidence_review import plan_review  # noqa: PLC0415

        diff_ok, paths, diff_context = _cumulative_changed_paths(
            repo_root, base_ref=base_ref
        )
        if final_readiness and not diff_ok:
            return False, f"evidence-review cumulative diff failed: {diff_context}"
        plan = plan_review(
            changed_paths=paths or ["scripts/verify_repo.py"],
            is_final_readiness=final_readiness,
        )
        executed: list[str] = []
        if execute_focused:
            concrete_checks = {
                "full_unittest": ("unit-tests", lambda: check_unit_tests(repo_root)),
                "consistency": ("consistency", lambda: check_consistency(repo_root)),
                "release": (
                    "release",
                    lambda: check_release(repo_root, release_version),
                ),
                "installed_smokes": (
                    "installed-smokes",
                    lambda: check_installed_smokes(repo_root),
                ),
                "secret_redaction": (
                    "secret-patterns",
                    lambda: check_secret_patterns(repo_root),
                ),
                "isolation_smoke": ("unit-tests", lambda: check_unit_tests(repo_root)),
                "unit:security": ("unit-tests", lambda: check_unit_tests(repo_root)),
                "unit:runtime": ("unit-tests", lambda: check_unit_tests(repo_root)),
                "unit:focused": ("unit-tests", lambda: check_unit_tests(repo_root)),
                "compileall_scripts": ("compileall", lambda: compile_scripts(repo_root)),
                "installed_bundle_smoke": (
                    "installed-smokes",
                    lambda: check_installed_smokes(repo_root),
                ),
                "docs_consistency": (
                    "markdown-links",
                    lambda: check_markdown_links(repo_root),
                ),
                "ci_workflow": ("consistency", lambda: check_consistency(repo_root)),
            }
            completed_gates: set[str] = set()
            for raw_check in list(plan.focused_checks or [])[:8]:
                name = str(raw_check)
                mapped = concrete_checks.get(name)
                if mapped is None:
                    return False, f"evidence-review check has no concrete runner: {name}"
                gate_name, runner = mapped
                if gate_name in completed_gates:
                    executed.append(f"{name}:deduplicated-as-{gate_name}")
                    continue
                ok, message = runner()
                completed_gates.add(gate_name)
                if result_cache is not None:
                    result_cache[gate_name] = (ok, message)
                executed.append(f"{name}->{gate_name}:{'ok' if ok else 'fail'}")
                if not ok:
                    return False, (
                        f"evidence-review concrete check failed: {name}->{gate_name}: {message}"
                    )
        return True, (
            f"evidence-review risk={plan.risk_level} broad={plan.broad_gate_required} "
            f"focused={len(plan.focused_checks)} executed={len(executed)} "
            f"diff={diff_context}"
        )
    except Exception as exc:  # noqa: BLE001
        return False, _redact_message(f"evidence-review plan failed: {exc}")


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
        help="Pin release checklist to this version (required for --ci/final readiness)",
    )
    parser.add_argument(
        "--base-ref",
        default=None,
        help=(
            "Explicit historical Git ref/SHA for strict cumulative and public-API "
            "comparison (CI should pass the PR base or push before SHA)"
        ),
    )
    parser.add_argument(
        "--session",
        type=Path,
        default=None,
        help=(
            "Elves session JSON for landing acceptance; required only with "
            "--final-readiness"
        ),
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=None,
        help=(
            "Optional authoritative plan path for landing acceptance; defaults to "
            "the session plan_path"
        ),
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
        help=(
            "Run live broad, landing, installed, compatibility, secret/link, and clean-Git "
            "gates; requires --session and never accepts preflight cache alone"
        ),
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help=(
            "Run strict non-landing release verification: live broad tests/smokes, "
            "base-range whitespace, API, secret, link, consistency, and release gates"
        ),
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    final = bool(args.final_readiness)
    ci_mode = bool(args.ci)
    if final and ci_mode:
        parser.error("--final-readiness and --ci are mutually exclusive")
    strict = final or ci_mode
    if strict and not args.version:
        parser.error("--ci and --final-readiness require --version")
    if strict and (args.skip_tests or args.skip_smokes):
        skipped = []
        if args.skip_tests:
            skipped.append("--skip-tests")
        if args.skip_smokes:
            skipped.append("--skip-smokes")
        parser.error(
            "strict verification cannot be combined with " + ", ".join(skipped)
        )
    if final and args.session is None:
        parser.error("--final-readiness requires --session")

    evidence_results: dict[str, tuple[bool, str]] = {}

    def cached(name: str, runner: Callable[[], tuple[bool, str]]) -> tuple[bool, str]:
        return evidence_results.get(name) or runner()

    # Strict CI/final readiness never accepts preflight cache alone and runs
    # broad + structural gates live. Only final readiness performs landing.
    gates: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("compileall", lambda: compile_scripts(repo_root)),
        ("shell", lambda: check_shell(repo_root)),
        ("json", lambda: check_json(repo_root)),
        (
            "evidence-review",
            lambda: check_evidence_review_plan(
                repo_root,
                execute_focused=strict,
                final_readiness=strict,
                release_version=args.version,
                base_ref=args.base_ref,
                result_cache=evidence_results,
            ),
        ),
        ("consistency", lambda: cached("consistency", lambda: check_consistency(repo_root))),
        (
            "release",
            lambda: cached("release", lambda: check_release(repo_root, args.version)),
        ),
        (
            "public-api",
            lambda: check_public_api(
                repo_root, required=strict, base_ref=args.base_ref
            ),
        ),
    ]
    if final:
        gates.append(
            (
                "landing-acceptance",
                lambda: check_landing(
                    repo_root,
                    session_path=args.session,
                    plan_path=args.plan,
                ),
            )
        )
    if strict:
        gates.append(("markdown-links", lambda: check_markdown_links(repo_root)))
        gates.append(("secret-patterns", lambda: check_secret_patterns(repo_root)))
    if not args.skip_tests:
        gates.append(
            ("unit-tests", lambda: cached("unit-tests", lambda: check_unit_tests(repo_root)))
        )
    if not args.skip_smokes:
        gates.append(
            (
                "installed-smokes",
                lambda: cached(
                    "installed-smokes", lambda: check_installed_smokes(repo_root)
                ),
            )
        )
    gates.append(
        (
            "git-diff-check",
            lambda: check_git_diff(
                repo_root,
                final_readiness=final,
                cumulative_required=strict,
                base_ref=args.base_ref,
            ),
        )
    )
    # Preflight cache may only be recorded after all included gates pass (append last).
    gates.append(
        (
            "preflight-cache",
            lambda: check_preflight_cache(repo_root, final_readiness=strict),
        )
    )

    results: list[dict[str, object]] = []
    overall_ok = True
    for name, fn in gates:
        # Never turn a failed verification run into reusable passing evidence.
        if name == "preflight-cache" and not overall_ok:
            results.append(
                {
                    "gate": name,
                    "ok": False,
                    "message": "preflight cache skipped: prior verification gates failed",
                }
            )
            if not args.json:
                print(f"[FAIL] {name}: preflight cache skipped: prior gates failed")
            overall_ok = False
            continue
        try:
            ok, message = fn()
        except Exception as exc:  # noqa: BLE001
            ok = False
            message = f"{name} raised {type(exc).__name__}: {exc}"
        # Defense in depth: individual gates redact their own diagnostics, and
        # the coordinator sanitizes every message again before printing/JSON.
        message = _redact_message(message)
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
                    "ci": ci_mode,
                    "strict": strict,
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
