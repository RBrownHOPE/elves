"""Exact persistent session registry, context digests, and usage parsing.

Canonical disk state outranks chat memory. Session selection is always exact
(ID + CWD/worktree). Ambiguous forms like bare --resume, --continue, or --last
are forbidden. Remaining quota is unknown unless a harness exposes it; never
invent subscription limits from token counts.

Registry files live under ignored `.elves/runtime/sessions/` and must not be
committed as product state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .schema import UsageRecord, ValidationIssue


class SessionLifecycle(str, Enum):
    NEW = "new"
    ACTIVE = "active"
    REHYDRATION_REQUIRED = "rehydration-required"
    DRIFTED = "drifted"
    FAILED = "failed"
    CLOSED = "closed"


class CreationMethod(str, Enum):
    CREATE = "create"
    RESUME = "resume"
    FORK_CHILD = "fork_child"
    DISCOVERED = "discovered"


# Valid directed transitions. Same-state no-ops are allowed for idempotent writes.
_ALLOWED_TRANSITIONS: dict[SessionLifecycle, frozenset[SessionLifecycle]] = {
    SessionLifecycle.NEW: frozenset(
        {
            SessionLifecycle.NEW,
            SessionLifecycle.ACTIVE,
            SessionLifecycle.FAILED,
            SessionLifecycle.CLOSED,
        }
    ),
    SessionLifecycle.ACTIVE: frozenset(
        {
            SessionLifecycle.ACTIVE,
            SessionLifecycle.REHYDRATION_REQUIRED,
            SessionLifecycle.DRIFTED,
            SessionLifecycle.FAILED,
            SessionLifecycle.CLOSED,
        }
    ),
    SessionLifecycle.REHYDRATION_REQUIRED: frozenset(
        {
            SessionLifecycle.REHYDRATION_REQUIRED,
            SessionLifecycle.ACTIVE,
            SessionLifecycle.DRIFTED,
            SessionLifecycle.FAILED,
            SessionLifecycle.CLOSED,
        }
    ),
    SessionLifecycle.DRIFTED: frozenset(
        {
            SessionLifecycle.DRIFTED,
            SessionLifecycle.FAILED,
            SessionLifecycle.CLOSED,
        }
    ),
    SessionLifecycle.FAILED: frozenset(
        {
            SessionLifecycle.FAILED,
            SessionLifecycle.CLOSED,
        }
    ),
    SessionLifecycle.CLOSED: frozenset({SessionLifecycle.CLOSED}),
}


@dataclass
class SessionRecord:
    """One exact persistent session identity."""

    session_id: str
    harness: str
    profile: str
    role: str
    requested_model: str | None = None
    actual_model: str | None = None
    parent_id: str | None = None
    cwd: str | None = None
    worktree: str | None = None
    creation_method: CreationMethod = CreationMethod.CREATE
    resume_method: str | None = None
    source_head: str | None = None
    context_digest: str | None = None
    # Pending values are recorded on expected canonical drift but must not replace
    # active identity fields until an exact resume proves the new packet/digest.
    pending_context_digest: str | None = None
    pending_source_head: str | None = None
    rehydration_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    lifecycle: SessionLifecycle = SessionLifecycle.NEW
    usage: UsageRecord = field(default_factory=UsageRecord)
    last_qualification: str | None = None
    notes: str = ""
    # Write reuse is blocked when True (model/CWD/parent/worktree drift).
    write_reuse_blocked: bool = False
    block_reason: str | None = None
    # Monotonic revision for compare-and-swap style registry updates.
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["creation_method"] = self.creation_method.value
        payload["lifecycle"] = self.lifecycle.value
        payload["usage"] = self.usage.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SessionRecord:
        usage_raw = data.get("usage") or {}
        if isinstance(usage_raw, UsageRecord):
            usage = usage_raw
        else:
            usage = UsageRecord(
                input_tokens=usage_raw.get("input_tokens"),
                output_tokens=usage_raw.get("output_tokens"),
                total_tokens=usage_raw.get("total_tokens"),
                cost_usd=usage_raw.get("cost_usd"),
                remaining_quota=usage_raw.get("remaining_quota", "unknown"),
                quota_known=bool(usage_raw.get("quota_known", False)),
            )
            # Never invent known quota from tokens alone.
            if not usage.quota_known:
                usage = UsageRecord(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    total_tokens=usage.total_tokens,
                    cost_usd=usage.cost_usd,
                    remaining_quota="unknown",
                    quota_known=False,
                )
        return cls(
            session_id=str(data["session_id"]),
            harness=str(data["harness"]),
            profile=str(data["profile"]),
            role=str(data.get("role") or ""),
            requested_model=data.get("requested_model"),
            actual_model=data.get("actual_model"),
            parent_id=data.get("parent_id"),
            cwd=data.get("cwd"),
            worktree=data.get("worktree"),
            creation_method=CreationMethod(
                str(data.get("creation_method") or CreationMethod.CREATE.value)
            ),
            resume_method=data.get("resume_method"),
            source_head=data.get("source_head"),
            context_digest=data.get("context_digest"),
            pending_context_digest=data.get("pending_context_digest"),
            pending_source_head=data.get("pending_source_head"),
            rehydration_reason=data.get("rehydration_reason"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            lifecycle=SessionLifecycle(
                str(data.get("lifecycle") or SessionLifecycle.NEW.value)
            ),
            usage=usage,
            last_qualification=data.get("last_qualification"),
            notes=str(data.get("notes") or ""),
            write_reuse_blocked=bool(data.get("write_reuse_blocked", False)),
            block_reason=data.get("block_reason"),
            revision=int(data.get("revision") or 0),
        )


@dataclass(frozen=True)
class ContextDigest:
    """Stable hash over identity + canonical run-file digests (not transcripts)."""

    digest: str
    components: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {"digest": self.digest, "components": dict(self.components)}


@dataclass(frozen=True)
class RehydrationPacket:
    """Bounded packet when expected HEAD/plan hash changes."""

    session_id: str
    reason: str
    previous_digest: str | None
    current_digest: str
    previous_head: str | None
    current_head: str | None
    plan_path: str | None
    note: str = "Re-read canonical disk state before continuing; chat memory is not authority"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sessions_root(repo_root: Path) -> Path:
    return Path(repo_root) / ".elves" / "runtime" / "sessions"


def ensure_sessions_dir(repo_root: Path) -> Path:
    path = sessions_root(repo_root)
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(stat.S_IRWXU)
    except OSError:
        pass
    return path


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def compute_context_digest(
    *,
    session_id: str,
    harness: str,
    profile: str,
    role: str,
    requested_model: str | None,
    actual_model: str | None,
    parent_id: str | None,
    cwd: str | None,
    worktree: str | None,
    source_head: str | None,
    plan_path: Path | None = None,
    survival_guide_path: Path | None = None,
    execution_log_path: Path | None = None,
    session_json_path: Path | None = None,
    extra_stable: Mapping[str, str] | None = None,
) -> ContextDigest:
    """Hash stable IDs plus canonical file content hashes — not transcripts."""
    components: dict[str, str] = {
        "session_id": session_id,
        "harness": harness,
        "profile": profile,
        "role": role,
        "requested_model": requested_model or "",
        "actual_model": actual_model or "",
        "parent_id": parent_id or "",
        "cwd": cwd or "",
        "worktree": worktree or "",
        "source_head": source_head or "",
    }
    for label, path in (
        ("plan", plan_path),
        ("survival_guide", survival_guide_path),
        ("execution_log", execution_log_path),
        ("session_json", session_json_path),
    ):
        if path is None:
            components[f"{label}_sha256"] = ""
            continue
        components[f"{label}_path"] = str(path)
        components[f"{label}_sha256"] = file_sha256(Path(path)) or ""
    if extra_stable:
        for key, value in sorted(extra_stable.items()):
            components[f"extra.{key}"] = value

    material = "\n".join(f"{k}={components[k]}" for k in sorted(components))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return ContextDigest(digest=digest, components=components)


def transition_lifecycle(
    current: SessionLifecycle,
    target: SessionLifecycle,
) -> SessionLifecycle:
    allowed = _ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise ValidationIssue(
            "invalid_lifecycle_transition",
            f"Cannot transition session lifecycle from `{current.value}` to `{target.value}`",
            path="session.lifecycle",
            hint=f"Allowed from {current.value}: {', '.join(sorted(s.value for s in allowed))}",
        )
    return target


def parse_usage_payload(raw: Mapping[str, Any] | None) -> UsageRecord:
    """Parse observed usage. remaining_quota stays unknown unless explicitly known."""
    if not raw:
        return UsageRecord()

    def _num(key: str) -> int | None:
        value = raw.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _float(key: str) -> float | None:
        value = raw.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    input_tokens = _num("input_tokens")
    if input_tokens is None:
        input_tokens = _num("prompt_tokens")
    output_tokens = _num("output_tokens")
    if output_tokens is None:
        output_tokens = _num("completion_tokens")
    total_tokens = _num("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    cost_usd = _float("cost_usd")
    if cost_usd is None:
        cost_usd = _float("cost")

    quota_known = bool(raw.get("quota_known", False))
    remaining = raw.get("remaining_quota")
    if remaining is None:
        remaining = raw.get("quota_remaining")
    # Never treat missing quota as zero. Only honor remaining when quota_known.
    if not quota_known or remaining is None or remaining == "":
        return UsageRecord(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            remaining_quota="unknown",
            quota_known=False,
        )
    try:
        remaining_val: str | int | None = int(remaining)
    except (TypeError, ValueError):
        remaining_val = str(remaining)
    return UsageRecord(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
        remaining_quota=remaining_val,
        quota_known=True,
    )


@dataclass
class DriftCheckResult:
    ok: bool
    expected_change: bool
    rehydration: RehydrationPacket | None = None
    write_reuse_blocked: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "expected_change": self.expected_change,
            "rehydration": self.rehydration.to_dict() if self.rehydration else None,
            "write_reuse_blocked": self.write_reuse_blocked,
            "reasons": list(self.reasons),
        }


def evaluate_session_continuity(
    record: SessionRecord,
    *,
    observed_model: str | None,
    observed_cwd: str | None,
    observed_worktree: str | None,
    observed_parent_id: str | None,
    observed_head: str | None,
    current_digest: ContextDigest,
    plan_path: str | None = None,
) -> DriftCheckResult:
    """Classify expected rehydration vs unexpected drift that blocks write reuse."""
    reasons: list[str] = []
    unexpected = False

    if record.actual_model and observed_model and record.actual_model != observed_model:
        unexpected = True
        reasons.append(
            f"actual_model drift: recorded={record.actual_model} observed={observed_model}"
        )
    if record.cwd and observed_cwd and Path(record.cwd).resolve() != Path(observed_cwd).resolve():
        unexpected = True
        reasons.append(f"cwd drift: recorded={record.cwd} observed={observed_cwd}")
    if (
        record.worktree
        and observed_worktree
        and Path(record.worktree).resolve() != Path(observed_worktree).resolve()
    ):
        unexpected = True
        reasons.append(
            f"worktree drift: recorded={record.worktree} observed={observed_worktree}"
        )
    if record.parent_id and observed_parent_id and record.parent_id != observed_parent_id:
        unexpected = True
        reasons.append(
            f"parent_id drift: recorded={record.parent_id} observed={observed_parent_id}"
        )

    expected_change = False
    rehydration: RehydrationPacket | None = None
    if record.source_head and observed_head and record.source_head != observed_head:
        expected_change = True
        reasons.append(
            f"source_head advanced: recorded={record.source_head} observed={observed_head}"
        )
    if record.context_digest and record.context_digest != current_digest.digest:
        expected_change = True
        reasons.append("context_digest changed (canonical files or identity)")

    if unexpected:
        return DriftCheckResult(
            ok=False,
            expected_change=expected_change,
            rehydration=None,
            write_reuse_blocked=True,
            reasons=reasons,
        )

    if expected_change:
        rehydration = RehydrationPacket(
            session_id=record.session_id,
            reason="; ".join(reasons) if reasons else "canonical state changed",
            previous_digest=record.context_digest,
            current_digest=current_digest.digest,
            previous_head=record.source_head,
            current_head=observed_head,
            plan_path=plan_path,
        )
        return DriftCheckResult(
            ok=True,
            expected_change=True,
            rehydration=rehydration,
            write_reuse_blocked=False,
            reasons=reasons,
        )

    return DriftCheckResult(
        ok=True,
        expected_change=False,
        rehydration=None,
        write_reuse_blocked=False,
        reasons=reasons,
    )


def _atomic_write_text(path: Path, payload: str, *, mode: int = 0o600) -> None:
    """Write ``payload`` via temp file + os.replace; set permission bits when possible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            path.chmod(mode)
        except OSError:
            pass
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class SessionRegistry:
    """Disk-backed exact session registry under ignored runtime path."""

    def __init__(self, repo_root: Path, *, create: bool = True) -> None:
        self.repo_root = Path(repo_root)
        self._create = create
        if create:
            self.root = ensure_sessions_dir(self.repo_root)
        else:
            self.root = sessions_root(self.repo_root)
        self._index_path = self.root / "index.json"
        self.malformed_records: list[dict[str, str]] = []

    @classmethod
    def open_readonly(cls, repo_root: Path) -> "SessionRegistry":
        """List/doctor path that never creates runtime directories."""
        return cls(Path(repo_root), create=False)

    def _ensure_writable(self) -> None:
        if not self._create:
            raise ValidationIssue(
                "registry_read_only",
                "Session registry was opened read-only; refusing to create or mutate state",
                path=str(self.root),
            )
        if not self.root.is_dir():
            self.root = ensure_sessions_dir(self.repo_root)

    def _record_path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", session_id)
        return self.root / f"{safe}.json"

    def list_sessions(self) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        self.malformed_records = []
        if not self.root.is_dir():
            return records
        for path in sorted(self.root.glob("*.json")):
            if path.name == "index.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                records.append(SessionRecord.from_dict(data))
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                # Fail closed for callers that inspect malformed_records; do not silently drop.
                self.malformed_records.append(
                    {
                        "path": str(path),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return records

    def list_sessions_strict(self) -> list[SessionRecord]:
        """Like list_sessions, but raise when any record file is malformed."""
        records = self.list_sessions()
        if self.malformed_records:
            detail = "; ".join(
                f"{item['path']}: {item['error']}" for item in self.malformed_records
            )
            raise ValidationIssue(
                "session_record_malformed",
                f"Malformed session registry records: {detail}",
                path=str(self.root),
            )
        return records

    def get(self, session_id: str) -> SessionRecord:
        path = self._record_path(session_id)
        if not path.is_file():
            raise ValidationIssue(
                "session_not_found",
                f"No session record for exact id `{session_id}`",
                path=str(path),
                hint="Exact session IDs are required; never use last/continue selection",
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationIssue(
                "session_read_error",
                f"Unable to read session `{session_id}`: {exc}",
                path=str(path),
            ) from exc
        try:
            return SessionRecord.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationIssue(
                "session_record_malformed",
                f"Session `{session_id}` record is malformed: {exc}",
                path=str(path),
            ) from exc

    def save(self, record: SessionRecord) -> SessionRecord:
        self._ensure_writable()
        record.revision = int(record.revision or 0) + 1
        record.updated_at = _utc_now()
        if not record.created_at:
            record.created_at = record.updated_at
        path = self._record_path(record.session_id)
        payload = json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n"
        _atomic_write_text(path, payload, mode=stat.S_IRUSR | stat.S_IWUSR)
        self._rewrite_index()
        return record

    def _rewrite_index(self) -> None:
        index = [
            {
                "session_id": rec.session_id,
                "harness": rec.harness,
                "profile": rec.profile,
                "role": rec.role,
                "lifecycle": rec.lifecycle.value,
                "parent_id": rec.parent_id,
                "actual_model": rec.actual_model,
                "revision": rec.revision,
            }
            for rec in self.list_sessions()
        ]
        _atomic_write_text(
            self._index_path,
            json.dumps({"sessions": index}, indent=2, sort_keys=True) + "\n",
            mode=stat.S_IRUSR | stat.S_IWUSR,
        )

    def create(
        self,
        *,
        session_id: str,
        harness: str,
        profile: str,
        role: str,
        requested_model: str | None = None,
        actual_model: str | None = None,
        parent_id: str | None = None,
        cwd: str | None = None,
        worktree: str | None = None,
        source_head: str | None = None,
        creation_method: CreationMethod = CreationMethod.CREATE,
        plan_path: Path | None = None,
        survival_guide_path: Path | None = None,
        execution_log_path: Path | None = None,
        session_json_path: Path | None = None,
        notes: str = "",
    ) -> SessionRecord:
        if not session_id or not str(session_id).strip():
            raise ValidationIssue(
                "missing_session_id",
                "Exact session_id is required to create a registry record",
            )
        # Reject overwrite of closed/existing without explicit path.
        path = self._record_path(session_id)
        if path.is_file():
            raise ValidationIssue(
                "session_exists",
                f"Session `{session_id}` already exists; use resume/update paths",
                path=str(path),
            )
        digest = compute_context_digest(
            session_id=session_id,
            harness=harness,
            profile=profile,
            role=role,
            requested_model=requested_model,
            actual_model=actual_model,
            parent_id=parent_id,
            cwd=cwd,
            worktree=worktree,
            source_head=source_head,
            plan_path=plan_path,
            survival_guide_path=survival_guide_path,
            execution_log_path=execution_log_path,
            session_json_path=session_json_path,
        )
        record = SessionRecord(
            session_id=session_id.strip(),
            harness=harness,
            profile=profile,
            role=role,
            requested_model=requested_model,
            actual_model=actual_model,
            parent_id=parent_id,
            cwd=cwd,
            worktree=worktree,
            creation_method=creation_method,
            source_head=source_head,
            context_digest=digest.digest,
            lifecycle=SessionLifecycle.NEW,
            notes=notes,
        )
        return self.save(record)

    def activate(self, session_id: str) -> SessionRecord:
        record = self.get(session_id)
        if record.lifecycle == SessionLifecycle.CLOSED:
            raise ValidationIssue(
                "session_closed",
                f"Session `{session_id}` is closed and cannot be activated",
            )
        record.lifecycle = transition_lifecycle(record.lifecycle, SessionLifecycle.ACTIVE)
        return self.save(record)

    def mark_rehydration_required(
        self,
        session_id: str,
        reason: str,
        *,
        pending_context_digest: str | None = None,
        pending_source_head: str | None = None,
    ) -> SessionRecord:
        record = self.get(session_id)
        record.lifecycle = transition_lifecycle(
            record.lifecycle, SessionLifecycle.REHYDRATION_REQUIRED
        )
        record.rehydration_reason = reason
        if pending_context_digest is not None:
            record.pending_context_digest = pending_context_digest
        if pending_source_head is not None:
            record.pending_source_head = pending_source_head
        record.notes = (record.notes + f"\nrehydration: {reason}").strip()
        return self.save(record)

    def mark_drifted(self, session_id: str, reason: str) -> SessionRecord:
        record = self.get(session_id)
        record.lifecycle = transition_lifecycle(record.lifecycle, SessionLifecycle.DRIFTED)
        record.write_reuse_blocked = True
        record.block_reason = reason
        return self.save(record)

    def mark_failed(self, session_id: str, reason: str) -> SessionRecord:
        record = self.get(session_id)
        record.lifecycle = transition_lifecycle(record.lifecycle, SessionLifecycle.FAILED)
        record.notes = (record.notes + f"\nfailed: {reason}").strip()
        return self.save(record)

    def close(self, session_id: str) -> SessionRecord:
        record = self.get(session_id)
        record.lifecycle = transition_lifecycle(record.lifecycle, SessionLifecycle.CLOSED)
        return self.save(record)

    def resume_exact(
        self,
        session_id: str,
        *,
        observed_model: str | None,
        observed_cwd: str | None,
        observed_worktree: str | None = None,
        observed_parent_id: str | None = None,
        observed_head: str | None = None,
        plan_path: Path | None = None,
        survival_guide_path: Path | None = None,
        execution_log_path: Path | None = None,
        session_json_path: Path | None = None,
        usage_payload: Mapping[str, Any] | None = None,
    ) -> tuple[SessionRecord, DriftCheckResult]:
        """Resume an exact session ID after continuity checks against disk state."""
        record = self.get(session_id)
        if record.lifecycle == SessionLifecycle.CLOSED:
            raise ValidationIssue(
                "session_closed",
                f"Session `{session_id}` is closed; create a new exact session instead",
            )
        digest = compute_context_digest(
            session_id=record.session_id,
            harness=record.harness,
            profile=record.profile,
            role=record.role,
            requested_model=record.requested_model,
            actual_model=observed_model or record.actual_model,
            parent_id=observed_parent_id or record.parent_id,
            cwd=observed_cwd or record.cwd,
            worktree=observed_worktree or record.worktree,
            source_head=observed_head or record.source_head,
            plan_path=plan_path,
            survival_guide_path=survival_guide_path,
            execution_log_path=execution_log_path,
            session_json_path=session_json_path,
        )
        drift = evaluate_session_continuity(
            record,
            observed_model=observed_model,
            observed_cwd=observed_cwd,
            observed_worktree=observed_worktree,
            observed_parent_id=observed_parent_id,
            observed_head=observed_head,
            current_digest=digest,
            plan_path=str(plan_path) if plan_path else None,
        )
        if drift.write_reuse_blocked:
            record = self.mark_drifted(session_id, "; ".join(drift.reasons))
            return record, drift

        # Expected canonical drift: record pending digest/head only. Active identity
        # fields stay frozen until a later exact resume proves the pending packet.
        if drift.expected_change and drift.rehydration is not None:
            if (
                record.lifecycle == SessionLifecycle.REHYDRATION_REQUIRED
                and record.pending_context_digest
                and digest.digest == record.pending_context_digest
            ):
                # Proof: resumed turn still targets the pending packet/digest.
                record.context_digest = record.pending_context_digest
                if record.pending_source_head:
                    record.source_head = record.pending_source_head
                record.pending_context_digest = None
                record.pending_source_head = None
                record.rehydration_reason = None
                record.lifecycle = transition_lifecycle(
                    record.lifecycle, SessionLifecycle.ACTIVE
                )
                record.resume_method = "exact_id_rehydrated"
                if observed_model:
                    record.actual_model = observed_model
                if observed_cwd:
                    record.cwd = observed_cwd
                if observed_worktree:
                    record.worktree = observed_worktree
                if usage_payload is not None:
                    record.usage = parse_usage_payload(usage_payload)
                self.save(record)
                return record, drift

            record = self.mark_rehydration_required(
                session_id,
                drift.rehydration.reason,
                pending_context_digest=digest.digest,
                pending_source_head=observed_head,
            )
            if usage_payload is not None:
                record = self.get(session_id)
                record.usage = parse_usage_payload(usage_payload)
                self.save(record)
            return record, drift

        # Already waiting for rehydration proof with no new drift signal: still blocked
        # unless the observed digest matches the pending challenge.
        if record.lifecycle == SessionLifecycle.REHYDRATION_REQUIRED:
            if (
                record.pending_context_digest
                and digest.digest == record.pending_context_digest
            ):
                record.context_digest = record.pending_context_digest
                if record.pending_source_head:
                    record.source_head = record.pending_source_head
                record.pending_context_digest = None
                record.pending_source_head = None
                record.rehydration_reason = None
                record.lifecycle = transition_lifecycle(
                    record.lifecycle, SessionLifecycle.ACTIVE
                )
                record.resume_method = "exact_id_rehydrated"
                if observed_model:
                    record.actual_model = observed_model
                if observed_cwd:
                    record.cwd = observed_cwd
                if observed_worktree:
                    record.worktree = observed_worktree
                if usage_payload is not None:
                    record.usage = parse_usage_payload(usage_payload)
                self.save(record)
                return record, drift
            raise ValidationIssue(
                "rehydration_proof_required",
                (
                    f"Session `{session_id}` cannot activate until rehydration proof "
                    "matches the pending context digest"
                ),
                path=str(self._record_path(session_id)),
                hint=(
                    "Re-read canonical run documents, resume the exact session, and "
                    "acknowledge the pending digest before write reuse"
                ),
            )

        record.lifecycle = transition_lifecycle(record.lifecycle, SessionLifecycle.ACTIVE)
        record.resume_method = "exact_id"
        record.context_digest = digest.digest
        record.pending_context_digest = None
        record.pending_source_head = None
        record.rehydration_reason = None
        if observed_model:
            record.actual_model = observed_model
        if observed_cwd:
            record.cwd = observed_cwd
        if observed_worktree:
            record.worktree = observed_worktree
        if observed_head:
            record.source_head = observed_head
        if usage_payload is not None:
            record.usage = parse_usage_payload(usage_payload)
        self.save(record)
        return record, drift


# --- Grok parent → worktree child lineage ---------------------------------

GROK_HEADLESS_WORKTREE_RESUME_BROKEN_VERSIONS: frozenset[str] = frozenset({"0.2.93"})


@dataclass(frozen=True)
class GrokChildSummary:
    """Discovered Grok worktree child identity (not the same UUID as parent)."""

    child_id: str
    parent_id: str
    model: str | None
    cwd: str | None
    head: str | None
    worktree: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_grok_child_summary(payload: Mapping[str, Any]) -> GrokChildSummary:
    child_id = str(
        payload.get("child_id")
        or payload.get("session_id")
        or payload.get("id")
        or ""
    ).strip()
    parent_id = str(payload.get("parent_id") or payload.get("parent_session_id") or "").strip()
    if not child_id:
        raise ValidationIssue(
            "grok_child_missing_id",
            "Grok child summary is missing child session id",
        )
    if not parent_id:
        raise ValidationIssue(
            "grok_child_missing_parent",
            "Grok child summary is missing parent session id",
        )
    if child_id == parent_id:
        raise ValidationIssue(
            "grok_lineage_same_uuid",
            "Grok parent-to-child lineage must use a distinct child session id",
            hint="Interactive worktree fork assigns a new child UUID",
        )
    return GrokChildSummary(
        child_id=child_id,
        parent_id=parent_id,
        model=(str(payload["model"]) if payload.get("model") is not None else None),
        cwd=(str(payload["cwd"]) if payload.get("cwd") is not None else None),
        head=(str(payload["head"]) if payload.get("head") is not None else None),
        worktree=(str(payload["worktree"]) if payload.get("worktree") is not None else None),
    )


def verify_grok_child_summary(
    summary: GrokChildSummary,
    *,
    expected_parent_id: str,
    expected_model: str | None,
    expected_cwd: str | None,
    expected_head: str | None,
) -> None:
    if summary.parent_id != expected_parent_id:
        raise ValidationIssue(
            "grok_parent_mismatch",
            f"Child parent_id `{summary.parent_id}` != expected `{expected_parent_id}`",
        )
    if expected_model and summary.model and summary.model != expected_model:
        raise ValidationIssue(
            "grok_model_mismatch",
            f"Child model `{summary.model}` != expected `{expected_model}`",
        )
    if expected_cwd and summary.cwd:
        if Path(summary.cwd).resolve() != Path(expected_cwd).resolve():
            raise ValidationIssue(
                "grok_cwd_mismatch",
                f"Child cwd `{summary.cwd}` != expected `{expected_cwd}`",
            )
    if expected_head and summary.head and summary.head != expected_head:
        raise ValidationIssue(
            "grok_head_mismatch",
            f"Child head `{summary.head}` != expected `{expected_head}`",
        )


def grok_headless_worktree_resume_supported(version: str | None) -> bool:
    if not version:
        return False
    return version not in GROK_HEADLESS_WORKTREE_RESUME_BROKEN_VERSIONS


def assert_grok_worktree_isolation(
    *,
    version: str | None,
    cwd_verified: bool,
    worktree_registered: bool,
    used_headless_worktree_resume: bool,
) -> None:
    """Fail closed unless isolation is verified; mark 0.2.93 headless path broken."""
    if used_headless_worktree_resume and not grok_headless_worktree_resume_supported(version):
        raise ValidationIssue(
            "grok_headless_worktree_resume_broken",
            (
                f"Grok Build {version or 'unknown'} headless --worktree with --resume "
                "is behaviorally broken (retains source CWD). Do not treat it as isolation."
            ),
            hint="Discover the child session ID and resume it from the registered worktree CWD",
        )
    if not cwd_verified or not worktree_registered:
        raise ValidationIssue(
            "grok_worktree_unverified",
            "Grok write/reuse path requires verified CWD and git worktree registration",
            hint="Expand/canonicalize the worktree path and check git worktree list --porcelain",
        )


def register_grok_child(
    registry: SessionRegistry,
    *,
    summary: GrokChildSummary,
    profile: str,
    role: str,
    expected_parent_id: str,
    expected_model: str | None,
    expected_cwd: str | None,
    expected_head: str | None,
    plan_path: Path | None = None,
) -> SessionRecord:
    verify_grok_child_summary(
        summary,
        expected_parent_id=expected_parent_id,
        expected_model=expected_model,
        expected_cwd=expected_cwd,
        expected_head=expected_head,
    )
    return registry.create(
        session_id=summary.child_id,
        harness="grok-build",
        profile=profile,
        role=role,
        requested_model=expected_model,
        actual_model=summary.model or expected_model,
        parent_id=summary.parent_id,
        cwd=summary.cwd or expected_cwd,
        worktree=summary.worktree or summary.cwd or expected_cwd,
        source_head=summary.head or expected_head,
        creation_method=CreationMethod.FORK_CHILD,
        plan_path=plan_path,
        notes="Grok parent→worktree child lineage; resume exact child id from worktree CWD",
    )
