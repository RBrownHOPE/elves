#!/usr/bin/env python3
"""Fresh installed-bundle smokes for Claude Code and Codex skill copies.

Copies a minimal installed-like skill bundle into a temporary directory and
executes operator CLI entry points from an unrelated CWD with a scrubbed
``PYTHONPATH`` so imports resolve only from the installed artifact — never from
the source checkout.

No live model calls. Network is not required.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Commands run against an installed cobbler_agents.py (cwd is outside the bundle).
SMOKE_COMMANDS: list[tuple[str, list[str]]] = [
    ("help", ["--help"]),
    ("validate-config", ["validate-config", "--json"]),
    ("doctor", ["doctor", "--json"]),
    ("setup-dry-run", ["setup", "--dry-run", "--json"]),
    ("onboard-plan", ["onboard", "plan", "--json"]),
    ("implement-status", ["implement", "status", "--json"]),
]

OPENROUTER_HELP = ["--help"]


def _copy_managed_bundle(repo_root: Path, dest_root: Path, *, host: str) -> None:
    """Populate dest_root like sync_installed_skills would for host."""
    # Import sync helpers without mutating process PYTHONPATH permanently.
    scripts_dir = str(repo_root / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import sync_installed_skills as sync  # noqa: PLC0415

    # Temporarily point REPO_ROOT so managed paths resolve from the real checkout.
    original_root = sync.REPO_ROOT
    original_targets = sync.TARGETS
    try:
        sync.REPO_ROOT = repo_root
        sync.TARGETS = sync.build_targets(repo_root)
        # Redirect install root into dest_root.
        cfg = dict(sync.TARGETS[host])
        cfg["root"] = dest_root
        # Do not install real-home aliases during smoke.
        cfg.pop("alias_root", None)
        cfg["managed_aliases"] = []
        sync.TARGETS = {host: cfg}
        problems = sync.apply_target(host)
        if problems:
            raise RuntimeError(f"failed to stage {host} bundle: {problems}")
        # Codex must also receive AGENTS.md; Claude SKILL only is fine.
        if host == "claude" and not (dest_root / "SKILL.md").is_file():
            raise RuntimeError("Claude bundle missing SKILL.md")
        if host == "codex":
            if not (dest_root / "AGENTS.md").is_file():
                raise RuntimeError("Codex bundle missing AGENTS.md")
            if (dest_root / "aliases").exists():
                raise RuntimeError("Codex bundle must not contain Claude aliases")
    finally:
        sync.REPO_ROOT = original_root
        sync.TARGETS = original_targets


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def smoke_host(
    host: str,
    *,
    repo_root: Path | None = None,
    keep: bool = False,
) -> dict[str, object]:
    """Stage and smoke one host. Returns a result dict with ok/failures."""
    root = Path(repo_root or REPO_ROOT).resolve()
    failures: list[str] = []
    notes: list[str] = []
    tmp_parent = Path(tempfile.mkdtemp(prefix=f"elves-install-smoke-{host}-"))
    bundle_root = tmp_parent / "installed" / host / "elves"
    outside_cwd = tmp_parent / "outside-cwd"
    outside_cwd.mkdir(parents=True)
    try:
        bundle_root.mkdir(parents=True)
        _copy_managed_bundle(root, bundle_root, host=host)

        agents = bundle_root / "scripts" / "cobbler_agents.py"
        openrouter = bundle_root / "scripts" / "openrouter_lens.py"
        package = bundle_root / "scripts" / "cobbler_runtime"
        if not agents.is_file():
            failures.append("missing cobbler_agents.py in installed bundle")
        if not openrouter.is_file():
            failures.append("missing openrouter_lens.py in installed bundle")
        if not package.is_dir():
            failures.append("missing cobbler_runtime package in installed bundle")
        # Removing any shipped runtime module must be detectable: assert at least
        # one .py file exists under the package (recursive shipment proof).
        py_files = list(package.rglob("*.py")) if package.is_dir() else []
        if not py_files:
            failures.append("cobbler_runtime package has no .py modules")

        # Scrub env so imports cannot fall back to the source checkout.
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_parent / "home"),
            "TMPDIR": str(tmp_parent / "tmp"),
            "TMP": str(tmp_parent / "tmp"),
            "TEMP": str(tmp_parent / "tmp"),
            "PYTHONPATH": str(bundle_root / "scripts"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
        }
        (tmp_parent / "home").mkdir(exist_ok=True)
        (tmp_parent / "tmp").mkdir(exist_ok=True)

        if not failures:
            for label, args in SMOKE_COMMANDS:
                proc = _run(
                    [sys.executable, str(agents), *args],
                    cwd=outside_cwd,
                    env=env,
                )
                if proc.returncode != 0:
                    failures.append(
                        f"{label} exit={proc.returncode} "
                        f"stderr={_clip(proc.stderr)} stdout={_clip(proc.stdout)}"
                    )
                else:
                    notes.append(f"{label}=ok")

            # OpenRouter help must work without API keys or model calls.
            proc = _run(
                [sys.executable, str(openrouter), *OPENROUTER_HELP],
                cwd=outside_cwd,
                env=env,
            )
            if proc.returncode != 0:
                failures.append(
                    f"openrouter-help exit={proc.returncode} "
                    f"stderr={_clip(proc.stderr)}"
                )
            else:
                notes.append("openrouter-help=ok")

            # Negative proof: deleting a required runtime module makes smoke fail.
            victim = package / "schema.py"
            if victim.is_file():
                backup = victim.read_bytes()
                victim.unlink()
                proc = _run(
                    [sys.executable, str(agents), "implement", "status", "--json"],
                    cwd=outside_cwd,
                    env=env,
                )
                victim.write_bytes(backup)
                if proc.returncode == 0:
                    failures.append(
                        "removed cobbler_runtime/schema.py still allowed implement status"
                    )
                else:
                    notes.append("missing-module-fails=ok")

        return {
            "ok": not failures,
            "host": host,
            "bundle_root": str(bundle_root),
            "outside_cwd": str(outside_cwd),
            "failures": failures,
            "notes": notes,
            "py_module_count": len(py_files),
        }
    finally:
        if not keep:
            shutil.rmtree(tmp_parent, ignore_errors=True)


def _clip(text: str, limit: int = 240) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        choices=("all", "claude", "codex"),
        default="all",
        help="Which installed-bundle shape to smoke",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Source repository root used to stage the bundle",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep temporary directories (debug)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON results",
    )
    args = parser.parse_args(argv)
    hosts = ["claude", "codex"] if args.host == "all" else [args.host]
    results = [smoke_host(h, repo_root=args.repo_root, keep=args.keep) for h in hosts]
    ok = all(bool(r["ok"]) for r in results)
    if args.json:
        import json

        print(json.dumps({"ok": ok, "results": results}, indent=2, sort_keys=True))
    else:
        for r in results:
            status = "OK" if r["ok"] else "FAIL"
            print(f"[{r['host']}] {status} modules={r['py_module_count']}")
            for note in r.get("notes") or []:
                print(f"  - {note}")
            for failure in r.get("failures") or []:
                print(f"  ! {failure}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
