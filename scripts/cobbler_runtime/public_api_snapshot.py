"""Stable public-API surface snapshot and compatibility diff gate.

Implements the helper described by docs/plans/public-api-surface-snapshot.md:
- prefer structured local sources (OpenAPI, package exports, CLI help, schemas)
- normalize + redact; never store secret-shaped values
- diff baseline vs current; block unapproved public breaks when required
- internal-only changes do not fail the gate
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .context import redact_structure, redact_text
from .storage import atomic_write_json, ensure_private_dir


DEFAULT_BASELINE = Path(".elves") / "api-surface" / "baseline.json"
DEFAULT_CURRENT = Path(".elves") / "api-surface" / "current.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SurfaceEntry:
    kind: str  # rest | export | cli | schema | config
    name: str
    signature: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApiSnapshot:
    status: str  # captured | unavailable
    captured_at: str
    source: str
    entries: list[SurfaceEntry] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "captured_at": self.captured_at,
            "source": self.source,
            "reason": self.reason,
            "entries": [e.to_dict() for e in self.entries],
            "digest": self.digest(),
        }

    def digest(self) -> str:
        material = json.dumps(
            sorted(
                (
                    e.kind,
                    e.name,
                    e.signature,
                )
                for e in self.entries
            ),
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _snapshot_openapi(repo_root: Path) -> list[SurfaceEntry]:
    entries: list[SurfaceEntry] = []
    candidates = [
        "openapi.json",
        "openapi.yaml",
        "swagger.json",
        "docs/openapi.json",
        "references/implement-done-report.schema.json",
    ]
    for rel in candidates:
        path = repo_root / rel
        if not path.is_file():
            continue
        if path.suffix == ".json":
            data = _load_json(path)
            if isinstance(data, dict) and "paths" in data:
                for route, methods in sorted((data.get("paths") or {}).items()):
                    if not isinstance(methods, Mapping):
                        continue
                    for method in sorted(methods.keys()):
                        if method.startswith("x-"):
                            continue
                        entries.append(
                            SurfaceEntry(
                                kind="rest",
                                name=f"{method.upper()} {route}",
                                signature=f"{method.upper()} {route}",
                                meta={"source": rel},
                            )
                        )
            elif isinstance(data, dict) and data.get("$schema"):
                # JSON Schema public contract (properties only, not examples).
                props = sorted((data.get("properties") or {}).keys())
                required = sorted(data.get("required") or [])
                entries.append(
                    SurfaceEntry(
                        kind="schema",
                        name=rel,
                        signature=f"props={','.join(props)};required={','.join(required)}",
                        meta={"source": rel},
                    )
                )
    return entries


def _snapshot_python_exports(repo_root: Path) -> list[SurfaceEntry]:
    entries: list[SurfaceEntry] = []
    init_path = repo_root / "scripts" / "cobbler_runtime" / "__init__.py"
    if not init_path.is_file():
        return entries
    text = init_path.read_text(encoding="utf-8")
    match = re.search(r"__all__\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if not match:
        return entries
    names = re.findall(r'"([^"]+)"', match.group(1))
    for name in sorted(names):
        entries.append(
            SurfaceEntry(
                kind="export",
                name=f"cobbler_runtime.{name}",
                signature=name,
                meta={"source": "scripts/cobbler_runtime/__init__.py"},
            )
        )
    return entries


def _snapshot_cli_help(repo_root: Path) -> list[SurfaceEntry]:
    """Capture stable CLI subcommand names from cobbler_agents.py argparse setup."""
    path = repo_root / "scripts" / "cobbler_agents.py"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    # Subparsers dest names and add_parser first args.
    commands = sorted(set(re.findall(r'add_parser\(\s*"([^"]+)"', text)))
    entries = [
        SurfaceEntry(
            kind="cli",
            name=f"cobbler_agents {cmd}",
            signature=cmd,
            meta={"source": "scripts/cobbler_agents.py"},
        )
        for cmd in commands
    ]
    return entries


def capture_snapshot(repo_root: Path) -> ApiSnapshot:
    root = Path(repo_root).resolve()
    entries: list[SurfaceEntry] = []
    sources: list[str] = []
    for collector, label in (
        (_snapshot_openapi, "openapi/schema"),
        (_snapshot_python_exports, "python_exports"),
        (_snapshot_cli_help, "cli"),
    ):
        found = collector(root)
        if found:
            entries.extend(found)
            sources.append(label)
    if not entries:
        return ApiSnapshot(
            status="unavailable",
            captured_at=_utc_now(),
            source="none",
            reason="no structured public surface sources found",
        )
    # Redact any accidental secret-shaped values in signatures/meta.
    cleaned: list[SurfaceEntry] = []
    for entry in entries:
        meta = redact_structure(entry.meta)
        sig = redact_text(entry.signature).text
        cleaned.append(
            SurfaceEntry(kind=entry.kind, name=entry.name, signature=sig, meta=dict(meta))
        )
    return ApiSnapshot(
        status="captured",
        captured_at=_utc_now(),
        source="+".join(sources),
        entries=cleaned,
    )


def write_snapshot(path: Path, snapshot: ApiSnapshot) -> Path:
    ensure_private_dir(path.parent)
    atomic_write_json(path, snapshot.to_dict())
    return path


def load_snapshot(path: Path) -> ApiSnapshot | None:
    data = _load_json(path)
    if not isinstance(data, dict):
        return None
    entries = [
        SurfaceEntry(
            kind=str(e.get("kind")),
            name=str(e.get("name")),
            signature=str(e.get("signature")),
            meta=dict(e.get("meta") or {}),
        )
        for e in (data.get("entries") or [])
        if isinstance(e, dict)
    ]
    return ApiSnapshot(
        status=str(data.get("status") or "unavailable"),
        captured_at=str(data.get("captured_at") or ""),
        source=str(data.get("source") or ""),
        entries=entries,
        reason=data.get("reason"),
    )


def diff_snapshots(
    baseline: ApiSnapshot,
    current: ApiSnapshot,
) -> dict[str, Any]:
    base_map = {(e.kind, e.name): e.signature for e in baseline.entries}
    cur_map = {(e.kind, e.name): e.signature for e in current.entries}
    added = sorted(k for k in cur_map if k not in base_map)
    removed = sorted(k for k in base_map if k not in cur_map)
    changed = sorted(
        k for k in base_map if k in cur_map and base_map[k] != cur_map[k]
    )
    internal_only = baseline.digest() == current.digest()
    return {
        "added": [f"{k[0]}:{k[1]}" for k in added],
        "removed": [f"{k[0]}:{k[1]}" for k in removed],
        "changed": [f"{k[0]}:{k[1]}" for k in changed],
        "breaking": [f"{k[0]}:{k[1]}" for k in removed + changed],
        "compatible_additions": [f"{k[0]}:{k[1]}" for k in added],
        "identical": internal_only and not added and not removed and not changed,
    }


def compatibility_gate(
    repo_root: Path,
    *,
    baseline_path: Path | None = None,
    current_path: Path | None = None,
    required: bool = False,
    approved_breaks: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Diff public surface. Internal-only changes pass; unapproved breaks fail when required."""
    root = Path(repo_root).resolve()
    base_p = root / (baseline_path or DEFAULT_BASELINE)
    cur_p = root / (current_path or DEFAULT_CURRENT)
    current = capture_snapshot(root)
    write_snapshot(cur_p, current)
    baseline = load_snapshot(base_p)
    if baseline is None:
        # First capture becomes baseline when missing.
        write_snapshot(base_p, current)
        return {
            "ok": True,
            "status": current.status,
            "action": "baseline_created",
            "required": required,
            "breaking": [],
            "diff": {"identical": True, "added": [], "removed": [], "changed": []},
        }
    if baseline.status == "unavailable" and current.status == "unavailable":
        return {
            "ok": not required,
            "status": "unavailable",
            "action": "unavailable",
            "required": required,
            "reason": current.reason or baseline.reason,
            "breaking": [],
        }
    diff = diff_snapshots(baseline, current)
    approved = set(approved_breaks or [])
    unapproved = [item for item in diff["breaking"] if item not in approved]
    ok = not unapproved
    if not required and current.status == "unavailable":
        ok = True
    return {
        "ok": ok,
        "status": current.status,
        "action": "diffed",
        "required": required,
        "breaking": unapproved,
        "diff": diff,
        "baseline_digest": baseline.digest(),
        "current_digest": current.digest(),
    }
