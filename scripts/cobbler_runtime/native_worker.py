"""Host-native worker profiles and exact-session launch specifications."""

from __future__ import annotations

import json
import hashlib
import os
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .context import is_secret_env_name
from .schema import ValidationIssue


@dataclass(frozen=True)
class NativeWorkerSpec:
    host: str
    profile: str
    effort: str
    model_policy: str
    requested_model: str | None
    separate_session: bool
    cwd: str
    argv: tuple[str, ...]
    stdin_packet: bool
    session_id_source: str
    session_id: str | None = None
    resume_argv: tuple[str, ...] | None = None
    cache_handoff: bool = False
    visibility_ready: bool = False
    visibility_mode: str = "commit_only"
    watcher_command: str | None = None
    git_write_roots: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["argv"] = list(self.argv)
        payload["resume_argv"] = list(self.resume_argv) if self.resume_argv else None
        payload["git_write_roots"] = list(self.git_write_roots)
        return payload


def native_worker_profiles() -> dict[str, dict[str, Any]]:
    """Semantically matched profiles; transport syntax stays host-specific."""
    common = {
        "model_policy": "inherit_live_driver_model",
        "effort_policy": "plan_execution_reasoning",
        "separate_session": True,
        "full_packet": True,
        "visibility_ready": False,
        "visibility_mode": "commit_only",
        "worker_merge_authority": False,
        "cache_handoff": False,
    }
    return {
        "codex": {**common, "transport": "codex_exec", "worktree_binding": "-C create; OS cwd resume", "session_identity": "thread.started.thread_id"},
        "claude": {**common, "transport": "claude_code", "worktree_binding": "supervisor cwd or native isolated worktree", "session_identity": "caller-assigned UUID"},
    }


def _exact_session_id(session_id: str) -> str:
    value = session_id.strip()
    if not value or value in {"last", "latest", "--last"} or value.startswith("-"):
        raise ValidationIssue("invalid_exact_session_id", "An exact worker session id is required")
    return value


def _git_write_roots(worktree: Path) -> tuple[str, ...]:
    """Return the least-privilege Git roots needed to commit in a linked worktree.

    A linked checkout stores its index and HEAD under ``.git/worktrees`` while
    objects and branch refs live in the common repository. Granting the common
    ``.git`` directory would also grant config, hooks, tags, and protected refs.
    Keep the write surface to the linked-worktree metadata, object store, and
    the parent directories of the exact feature ref and its reflog. Terminal
    verification still rejects any sibling-ref movement.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree.resolve()),
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ()
    if result.returncode != 0:
        return ()
    rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != 2:
        return ()
    workspace = worktree.resolve()
    git_dir, common_dir = (Path(row).resolve() for row in rows)
    # A standalone checkout keeps its metadata under the workspace. This helper
    # exists only for linked-worktree metadata outside the sandbox root.
    try:
        git_dir.relative_to(workspace)
        return ()
    except ValueError:
        pass
    branch_result = subprocess.run(
        ["git", "-C", str(workspace), "symbolic-ref", "--quiet", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    branch_ref = branch_result.stdout.strip()
    if branch_result.returncode != 0 or not branch_ref.startswith("refs/heads/"):
        raise ValidationIssue(
            "native_worker_feature_branch_required",
            "Commit-capable native workers require an attached feature branch",
        )
    relative_ref = Path(branch_ref.removeprefix("refs/heads/"))
    if len(relative_ref.parts) < 2 or any(part in {"", ".", ".."} for part in relative_ref.parts):
        raise ValidationIssue(
            "native_worker_branch_namespace_required",
            "Commit-capable linked workers require a namespaced feature branch",
            hint="Use a branch such as codex/<task> or claude/<task>",
        )
    ref_parent = (common_dir / "refs" / "heads" / relative_ref.parent).resolve()
    log_parent = (common_dir / "logs" / "refs" / "heads" / relative_ref.parent).resolve()
    candidates = (git_dir, common_dir / "objects", ref_parent, log_parent)
    external: list[Path] = []
    for raw_candidate in candidates:
        candidate = raw_candidate.resolve()
        if not candidate.is_dir():
            raise ValidationIssue(
                "native_worker_git_metadata_unavailable",
                "Required linked-worktree Git metadata directory is unavailable",
                path=str(candidate),
            )
        try:
            candidate.relative_to(workspace)
            continue
        except ValueError:
            pass
        if candidate == common_dir:
            raise ValidationIssue(
                "native_worker_git_authority_too_broad",
                "Native worker Git access may not include the shared common directory",
            )
        if candidate in external:
            continue
        external.append(candidate)
    return tuple(str(path) for path in external)


def build_native_worker_spec(
    *,
    host: str,
    worktree: Path,
    effort: str,
    requested_model: str | None = None,
    session_id: str | None = None,
    visibility_mode: str = "commit_only",
    watcher_command: str | None = None,
    fixture_script: Path | None = None,
) -> NativeWorkerSpec:
    host_token = host.strip().lower().replace("_", "-")
    effort_token = effort.strip().lower()
    if effort_token not in {"low", "medium", "high"}:
        raise ValidationIssue("invalid_worker_effort", f"Invalid worker effort `{effort}`")
    if not requested_model or not requested_model.strip():
        raise ValidationIssue(
            "current_worker_model_required",
            "Supervised CLI fallback requires the host to pass its observed current model",
            path="requested_model",
        )
    requested_model = requested_model.strip()
    cwd = str(worktree.resolve())
    git_write_roots = _git_write_roots(worktree)
    model_policy = "host_pinned_current_model"
    if visibility_mode not in {"commit_only", "native_host_agent_view", "follow_log"}:
        raise ValidationIssue("invalid_visibility_mode", f"Unknown visibility mode `{visibility_mode}`")
    visibility_ready = visibility_mode in {"native_host_agent_view", "follow_log"}
    if visibility_mode == "follow_log" and not watcher_command:
        raise ValidationIssue("visibility_not_bound", "Follow-log visibility requires an exact watcher command")
    if host_token == "fixture":
        if fixture_script is None:
            raise ValidationIssue("fixture_script_required", "Fixture native worker requires --fixture-script")
        sid = _exact_session_id(session_id) if session_id else f"fixture-{hashlib.sha256(str(fixture_script).encode()).hexdigest()[:16]}"
        argv = (sys.executable, str(fixture_script.resolve(strict=True)))
        return NativeWorkerSpec(
            host="fixture", profile="elves-native-worker-fixture", effort=effort_token,
            model_policy=model_policy, requested_model=requested_model, separate_session=True,
            cwd=cwd, argv=argv, stdin_packet=True, session_id_source="fixture_session_id",
            session_id=sid, visibility_ready=visibility_ready, visibility_mode=visibility_mode,
            watcher_command=watcher_command,
        )

    if host_token == "codex":
        common = [
            "codex", "exec", "--json", "--ignore-user-config", "--ignore-rules",
            "--sandbox", "workspace-write", "-c", f'model_reasoning_effort="{effort_token}"',
        ]
        for root in git_write_roots:
            common.extend(["--add-dir", root])
        common.extend(["--model", requested_model])
        if session_id is None:
            argv = (*common, "-C", cwd, "-")
            resume = None
        else:
            sid = _exact_session_id(session_id)
            # Keep exec-level sandbox and additional-write-root options before
            # the resume subcommand. The supervisor binds the exact OS cwd.
            argv = tuple(common + ["resume", sid, "-"])
            resume = argv
        return NativeWorkerSpec(
            host="codex", profile="elves-native-worker", effort=effort_token,
            model_policy=model_policy, requested_model=requested_model,
            separate_session=True, cwd=cwd, argv=tuple(argv), stdin_packet=True,
            session_id_source="thread.started.thread_id", session_id=session_id, resume_argv=resume,
            visibility_ready=visibility_ready, visibility_mode=visibility_mode, watcher_command=watcher_command,
            git_write_roots=git_write_roots,
        )

    if host_token in {"claude", "claude-code"}:
        common = [
            "claude", "--print", "--output-format", "stream-json", "--input-format", "text",
            "--effort", effort_token, "--permission-mode", "acceptEdits",
        ]
        for root in git_write_roots:
            common.extend(["--add-dir", root])
        common.extend(["--model", requested_model])
        if session_id is None:
            # Claude accepts a caller-generated UUID, providing exact identity before launch.
            import uuid
            sid = str(uuid.uuid4())
            argv = tuple(common + ["--session-id", sid])
        else:
            sid = _exact_session_id(session_id)
            argv = tuple(common + ["--resume", sid])
        return NativeWorkerSpec(
            host="claude-code", profile="elves-native-worker", effort=effort_token,
            model_policy=model_policy, requested_model=requested_model,
            separate_session=True, cwd=cwd, argv=argv, stdin_packet=True,
            session_id_source="requested_session_id", session_id=sid, resume_argv=argv if session_id else None,
            visibility_ready=visibility_ready, visibility_mode=visibility_mode, watcher_command=watcher_command,
            git_write_roots=git_write_roots,
        )
    raise ValidationIssue("unsupported_host", f"Unsupported native worker host `{host}`")


def parse_codex_thread_id(jsonl: str) -> str:
    """Capture the exact Codex worker thread id from structured launch output."""
    for line in jsonl.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            return _exact_session_id(event["thread_id"])
    raise ValidationIssue("worker_session_id_missing", "Codex output did not contain thread.started.thread_id")


def _run_key(run_id: str) -> str:
    if not run_id.strip() or run_id.startswith("-"):
        raise ValidationIssue("invalid_native_worker_run_id", "An exact native worker run id is required")
    safe = "".join(ch if ch.isalnum() else "_" for ch in run_id)[:24]
    return f"{safe}-{hashlib.sha256(run_id.encode()).hexdigest()[:16]}"


def native_worker_paths(repo_root: Path, run_id: str) -> tuple[Path, Path]:
    root = repo_root.resolve() / ".elves" / "runtime" / "native-worker" / _run_key(run_id)
    return root / "state.json", root / "follow.jsonl"


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _process_start(pid: int) -> str | None:
    try:
        result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False)
        return result.stdout.strip() or None
    except OSError:
        return None


def _process_identity_matches(pid_value: object, expected_start: object) -> bool | None:
    """Return exact identity match, known process loss, or unavailable identity."""
    if not pid_value:
        return None
    pid = int(pid_value)
    observed_start = _process_start(pid)
    if observed_start is not None:
        return observed_start == expected_start if expected_start else None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    return None


def _native_git_contract(worktree: Path) -> dict[str, Any]:
    """Capture the feature-only Git authority contract without a network probe."""
    from .full_run import (  # imported lazily to keep module initialization acyclic
        _git_branch,
        _git_head,
        _origin_config_digest,
        snapshot_protected_refs,
    )

    branch = _git_branch(worktree)
    head = _git_head(worktree)
    if not branch or not head:
        raise ValidationIssue(
            "native_worker_feature_branch_required",
            "Native worker launch requires an attached feature branch with a valid tip",
        )
    return {
        "assigned_branch": branch,
        "start_head": head,
        "protected_refs": snapshot_protected_refs(
            worktree, feature_branch=branch, include_remote=False
        ),
        "origin_config_digest": _origin_config_digest(worktree),
    }


def _native_worker_child_env(
    *, host: str, worktree: Path, runtime_dir: Path
) -> dict[str, str]:
    """Project provider auth while removing ambient Git/network credentials."""
    from .full_run import _read_host_git_identity_value

    parent = dict(os.environ)
    provider_secret_names = {
        "codex": {"OPENAI_API_KEY", "CODEX_API_KEY"},
        "claude-code": {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"},
    }.get(host, set())
    forbidden_exact = {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GITLAB_TOKEN",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "GCM_INTERACTIVE",
    }
    env: dict[str, str] = {}
    for name, value in parent.items():
        if name in forbidden_exact or name.startswith("GIT_"):
            continue
        if is_secret_env_name(name) and name not in provider_secret_names:
            continue
        env[name] = value
    # Subscription-backed Codex and Claude may need their own explicit provider
    # token. No other secret-valued environment entry crosses the boundary.
    for name in provider_secret_names:
        if parent.get(name):
            env[name] = parent[name]

    empty_gh = runtime_dir / "empty-gh-config"
    empty_gh.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(empty_gh, 0o700)
    false_executable = "/usr/bin/false" if Path("/usr/bin/false").exists() else "false"
    env.update(
        {
            "GH_CONFIG_DIR": str(empty_gh),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": false_executable,
            "SSH_ASKPASS": false_executable,
            "GIT_SSH_COMMAND": false_executable,
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": "remote.origin.pushurl",
            "GIT_CONFIG_VALUE_1": "disabled://native-worker-no-push",
        }
    )
    if host != "fixture":
        name = _read_host_git_identity_value(worktree, "user.name", parent_env=parent)
        email = _read_host_git_identity_value(worktree, "user.email", parent_env=parent)
        env.update(
            {
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
            }
        )
    return env


def _verify_native_git_contract(worktree: Path, state: dict[str, Any]) -> list[str]:
    """Verify feature ancestry, origin config, and every protected local ref."""
    from .full_run import (
        _git_branch,
        _git_head,
        _is_ancestor,
        _origin_config_digest,
        verify_protected_refs_unchanged,
    )

    errors: list[str] = []
    branch = _git_branch(worktree)
    head = _git_head(worktree)
    expected_branch = str(state.get("assigned_branch") or "")
    start_head = str(state.get("start_head") or "")
    if branch != expected_branch:
        errors.append(
            f"assigned branch changed: expected {expected_branch or '<missing>'}, observed {branch or '<detached>'}"
        )
    if not head or not start_head or not _is_ancestor(worktree, start_head, head):
        errors.append("final feature tip is not a descendant of the registered start tip")
    if _origin_config_digest(worktree) != state.get("origin_config_digest"):
        errors.append("origin configuration changed during native worker execution")
    expected_refs = state.get("protected_refs")
    if not isinstance(expected_refs, dict):
        errors.append("protected-ref snapshot is missing")
    else:
        errors.extend(
            verify_protected_refs_unchanged(
                worktree,
                expected_refs,
                feature_branch=expected_branch,
                include_remote=False,
            )
        )
    return errors


def launch_native_worker(
    *, repo_root: Path, run_id: str, spec: NativeWorkerSpec, packet: Path, cli_path: Path
) -> dict[str, Any]:
    state_path, log_path = native_worker_paths(repo_root, run_id)
    if state_path.exists():
        raise ValidationIssue("native_worker_run_exists", f"Native worker run `{run_id}` already exists")
    watcher = shlex.join([sys.executable, str(cli_path.resolve()), "native-worker", "follow", "--repo-root", str(repo_root.resolve()), "--run-id", run_id])
    worktree = Path(spec.cwd).resolve()
    git_contract = (
        {
            "assigned_branch": None,
            "start_head": None,
            "protected_refs": {},
            "origin_config_digest": None,
        }
        if spec.host == "fixture"
        else _native_git_contract(worktree)
    )
    state = {
        "version": 2, "run_id": run_id, "status": "launching", "host": spec.host,
        "worktree": spec.cwd, "argv": list(spec.argv), "session_id": spec.session_id,
        "session_id_source": spec.session_id_source, "pid": None, "pid_start": None,
        "follow_log": str(log_path), "visibility_ready": True, "visibility_mode": "follow_log",
        "watcher_command": watcher, "exit_code": None,
        "git_write_roots": list(spec.git_write_roots),
        "git_network_push": "disabled",
        "git_authority_mode": "fixture" if spec.host == "fixture" else "feature_only",
        **git_contract,
    }
    _write_private_json(state_path, state)
    packet_path = packet.resolve(strict=True)
    supervisor_log = state_path.parent / "supervisor.log"
    with supervisor_log.open("a", encoding="utf-8") as supervisor_output:
        os.chmod(supervisor_log, 0o600)
        supervisor = subprocess.Popen(
            [sys.executable, str(cli_path.resolve()), "native-worker", "_supervise", "--repo-root", str(repo_root.resolve()), "--run-id", run_id, "--packet", str(packet_path)],
            cwd=str(repo_root.resolve()), stdin=subprocess.DEVNULL, stdout=supervisor_output,
            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
        )
    state["supervisor_pid"] = supervisor.pid
    state["supervisor_pid_start"] = _process_start(supervisor.pid)
    state["supervisor_log"] = str(supervisor_log)
    state["status"] = "running"
    _write_private_json(state_path, state)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        current = json.loads(state_path.read_text(encoding="utf-8"))
        identity_ready = current.get("pid") and (current.get("session_id") or current.get("host") != "codex")
        if identity_ready or current.get("status") in {"complete", "failed"}:
            return current
        time.sleep(0.02)
    try:
        os.killpg(supervisor.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    raise ValidationIssue("native_worker_identity_timeout", "Native worker did not publish its exact PID/session binding before launch returned")


def supervise_native_worker(*, repo_root: Path, run_id: str, packet: Path) -> int:
    from .context import redact_text
    state_path, log_path = native_worker_paths(repo_root, run_id)
    state: dict[str, Any] = {}
    for _ in range(50):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("supervisor_pid"):
            break
        time.sleep(0.02)
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    worktree = Path(str(state["worktree"])).resolve()
    child_env = _native_worker_child_env(
        host=str(state.get("host") or ""),
        worktree=worktree,
        runtime_dir=state_path.parent,
    )
    with packet.open("r", encoding="utf-8") as packet_handle, log_path.open("a", encoding="utf-8") as log:
        os.chmod(log_path, 0o600)
        child = subprocess.Popen(
            state["argv"],
            cwd=state["worktree"],
            env=child_env,
            stdin=packet_handle,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            close_fds=True,
        )
        state["pid"] = child.pid
        state["pid_start"] = _process_start(child.pid)
        _write_private_json(state_path, state)
        selector = selectors.DefaultSelector()
        assert child.stdout is not None and child.stderr is not None
        selector.register(child.stdout, selectors.EVENT_READ, "stdout")
        selector.register(child.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            for key, _ in selector.select(timeout=0.2):
                line = key.fileobj.readline()
                if not line:
                    selector.unregister(key.fileobj)
                    continue
                redacted = redact_text(line.rstrip("\n"))
                log.write(json.dumps({"stream": key.data, "line": redacted.text}, sort_keys=True) + "\n")
                log.flush()
                if state["host"] == "codex" and not state.get("session_id"):
                    try:
                        state["session_id"] = parse_codex_thread_id(line)
                    except ValidationIssue:
                        pass
                    else:
                        _write_private_json(state_path, state)
        code = child.wait()
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if state["host"] == "codex":
        try:
            structured = "\n".join(json.loads(line)["line"] for line in text.splitlines())
            state["session_id"] = parse_codex_thread_id(structured)
        except (ValidationIssue, KeyError, json.JSONDecodeError):
            pass
    authority_errors = (
        []
        if state.get("git_authority_mode") == "fixture"
        else _verify_native_git_contract(worktree, state)
    )
    state["exit_code"] = code
    state["authority_verified"] = not authority_errors
    state["authority_errors"] = authority_errors
    state["final_head"] = (
        subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        or None
    )
    if authority_errors:
        state["status"] = "failed"
        state["failure_reason"] = "native_worker_git_authority_violation"
    else:
        state["status"] = "complete" if code == 0 else "failed"
    _write_private_json(state_path, state)
    return code


def native_worker_status(repo_root: Path, run_id: str) -> dict[str, Any]:
    state_path, _ = native_worker_paths(repo_root, run_id)
    if not state_path.is_file() or stat.S_IMODE(state_path.stat().st_mode) & 0o077:
        raise ValidationIssue("native_worker_state_unavailable", "Private native worker state is missing or has unsafe permissions")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pid = state.get("pid")
    expected_start = state.get("pid_start")
    process_identity_matches: bool | None = None
    if state.get("status") == "running":
        process_identity_matches = _process_identity_matches(pid, expected_start)
    state["process_identity_matches"] = process_identity_matches
    if state.get("status") == "running":
        supervisor_pid = state.get("supervisor_pid")
        supervisor_start = state.get("supervisor_pid_start")
        supervisor_matches = _process_identity_matches(supervisor_pid, supervisor_start)
        state["supervisor_identity_matches"] = supervisor_matches
        if process_identity_matches is False and supervisor_matches is False:
            # A fast child can exit just before the supervisor atomically records
            # its terminal state. Give that final write a short grace window so a
            # status poll cannot turn a successful run into a false failure.
            latest = state
            for attempt in range(6):
                if attempt:
                    time.sleep(0.02)
                latest = json.loads(state_path.read_text(encoding="utf-8"))
                if latest.get("status") in {"complete", "failed"}:
                    return latest
            state["status"] = "failed"
            state["failure_reason"] = "supervisor_and_child_identity_lost"
    return state


def follow_native_worker(repo_root: Path, run_id: str, *, wait: bool = True, output: Any = sys.stdout) -> dict[str, Any]:
    state = native_worker_status(repo_root, run_id)
    log_path = Path(state["follow_log"])
    offset = 0
    while True:
        if log_path.is_file():
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                for line in handle:
                    event = json.loads(line)
                    output.write(f"[{event['stream']}] {event['line']}\n")
                offset = handle.tell()
        state = native_worker_status(repo_root, run_id)
        if not wait or state["status"] in {"complete", "failed"}:
            return state
        time.sleep(0.1)
