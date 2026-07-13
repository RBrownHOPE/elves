"""Isolated external-lane subprocess lifecycle for council dispatch.

Checks platform filesystem-sandbox availability before isolated external
launch, creates the tracked-source snapshot *before* building adapter argv,
rewrites repo/CWD flags to the snapshot, and guarantees cleanup on every exit
path. Optional isolation failure skips that external attempt so its configured
fallback chain can continue; required isolation fails closed.
"""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .adapters import (
    ADAPTER_CONTRACT_PAIRS,
    AdapterInvocation,
    build_readonly_invocation,
    default_decoder_for_adapter,
    validate_adapter_contract_pair,
    validate_extra_args,
)
from .context import redact_text
from .dispatch_models import LaneSpec
from .isolation import (
    IsolationSpec,
    IsolatedLane,
    copy_isolated_transport_inputs,
    create_tracked_snapshot,
    resolve_fs_sandbox_backend,
    rewrite_argv_repo_paths,
    wrap_argv_with_sandbox,
)
from .schema import EffectiveAttempt, ValidationIssue


PROCESS_GROUP_GRACE_SECONDS = 0.5
PROCESS_GROUP_VERIFY_POLL_SECONDS = 0.05
PROCESS_GROUP_VERIFY_ATTEMPTS = 20
PROCESS_GROUP_SETTLE_SECONDS = 0.25
DESCENDANT_POLL_SECONDS = 0.03
DESCENDANT_VERIFY_ATTEMPTS = 24


@dataclass(frozen=True)
class _ProcessRecord:
    pid: int
    ppid: int
    pgid: int
    command: str


@dataclass
class _DescendantSupervisor:
    """Host-owned macOS descendant tracker, including reparented sessions."""

    executable: str
    token: str
    root_pid: int
    known_pids: set[int]
    error: str | None = None

    @classmethod
    def for_lane(cls, lane: IsolatedLane, root_pid: int) -> _DescendantSupervisor:
        if not lane.supervisor_executable or not lane.supervision_token:
            raise ValidationIssue(
                "isolation_supervision_unavailable",
                "macOS isolation is missing qualified descendant supervision",
            )
        return cls(
            executable=lane.supervisor_executable,
            token=lane.supervision_token,
            root_pid=root_pid,
            known_pids={root_pid},
        )

    async def scan(self) -> dict[int, _ProcessRecord]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.executable,
                "e",
                "-axo",
                "pid=,ppid=,pgid=,command=",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            self.error = f"descendant_scan_failed:{type(exc).__name__}:{exc}"
            return {}
        if proc.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            self.error = f"descendant_scan_exit:{proc.returncode}:{message[:160]}"
            return {}
        records: dict[int, _ProcessRecord] = {}
        for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
            fields = raw_line.strip().split(None, 3)
            if len(fields) < 3:
                continue
            try:
                pid, ppid, pgid = (int(fields[index]) for index in range(3))
            except ValueError:
                continue
            command = fields[3] if len(fields) == 4 else ""
            records[pid] = _ProcessRecord(pid, ppid, pgid, command)

        # The opaque token survives setsid and ordinary double-fork reparenting.
        token_marker = f"ELVES_ISOLATION_MARKER={self.token}"
        discovered = {
            record.pid for record in records.values() if token_marker in record.command
        }
        discovered.add(self.root_pid)
        changed = True
        while changed:
            before = len(discovered)
            discovered.update(
                record.pid
                for record in records.values()
                if record.ppid in discovered or record.ppid in self.known_pids
            )
            changed = len(discovered) != before
        self.known_pids.update(discovered)
        return records

    async def alive(self) -> set[int]:
        records = await self.scan()
        if self.error:
            return set()
        marker = f"ELVES_ISOLATION_MARKER={self.token}"
        return {
            pid
            for pid, record in records.items()
            if pid in self.known_pids or marker in record.command
        }


async def _monitor_descendants(
    supervisor: _DescendantSupervisor,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set() and supervisor.error is None:
        await supervisor.scan()
        try:
            await asyncio.wait_for(stop.wait(), timeout=DESCENDANT_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _terminate_supervised_descendants(
    supervisor: _DescendantSupervisor,
) -> dict[str, Any]:
    """Signal and prove absence of every supervised/reparented descendant."""
    cleanup: dict[str, Any] = {
        "descendant_supervised": True,
        "descendant_sigterm_sent": False,
        "descendant_sigkill_sent": False,
        "descendants_absent": False,
        "supervised_pids": sorted(supervisor.known_pids),
        "supervision_error": supervisor.error,
    }
    if supervisor.error:
        return cleanup

    alive = await supervisor.alive()
    if supervisor.error:
        cleanup["supervision_error"] = supervisor.error
        return cleanup
    targets = {pid for pid in alive if pid != os.getpid()}
    cleanup["descendants_found"] = sorted(targets)

    def _signal_targets(pids: set[int], sig: int) -> bool:
        sent = False
        for pid in sorted(pids, reverse=True):
            try:
                os.kill(pid, sig)
                sent = True
            except ProcessLookupError:
                continue
            except OSError as exc:
                supervisor.error = f"descendant_signal_failed:pid={pid}:{exc}"
        return sent

    cleanup["descendant_sigterm_sent"] = _signal_targets(targets, signal.SIGTERM)
    for _ in range(8):
        await asyncio.sleep(DESCENDANT_POLL_SECONDS)
        alive = await supervisor.alive()
        if supervisor.error or not alive:
            break
    if alive:
        cleanup["descendant_sigkill_sent"] = _signal_targets(alive, signal.SIGKILL)

    for _ in range(DESCENDANT_VERIFY_ATTEMPTS):
        alive = await supervisor.alive()
        if supervisor.error:
            break
        if not alive:
            cleanup["descendants_absent"] = True
            break
        _signal_targets(alive, signal.SIGKILL)
        cleanup["descendant_sigkill_sent"] = True
        await asyncio.sleep(DESCENDANT_POLL_SECONDS)
    cleanup["supervised_pids"] = sorted(supervisor.known_pids)
    cleanup["supervision_error"] = supervisor.error
    return cleanup


def _linux_process_group_states(pgid: int) -> list[str] | None:
    """Return Linux member states, treating zombies as inert containment residue."""
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return None
    states: list[str] = []
    try:
        entries = tuple(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8")
            close_paren = raw.rfind(")")
            fields = raw[close_paren + 2 :].split()
            state = fields[0]
            process_group = int(fields[2])
        except (OSError, ValueError, IndexError):
            continue
        if process_group == pgid:
            states.append(state)
    return states


def pgid_alive(pgid: int) -> bool:
    """Return whether a process group has any executable (non-zombie) member."""
    linux_states = _linux_process_group_states(pgid)
    if linux_states:
        return any(state not in {"Z", "X", "x"} for state in linux_states)
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but is not signalable as us; treat it as still present.
        return True
    except OSError:
        return False


async def wait_for_process_group_settle(
    pgid: int,
    *,
    timeout: float = PROCESS_GROUP_SETTLE_SECONDS,
) -> bool:
    """Allow bwrap/init helpers a bounded natural-reap window after leader exit."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while pgid_alive(pgid):
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(PROCESS_GROUP_VERIFY_POLL_SECONDS)
    return True


async def terminate_process_group(
    proc: asyncio.subprocess.Process,
    *,
    grace_seconds: float = PROCESS_GROUP_GRACE_SECONDS,
    known_pgid: int | None = None,
) -> dict[str, Any]:
    """Terminate an entire external lane process group and verify absence."""
    cleanup: dict[str, Any] = {
        "signaled_group": False,
        "sigterm_sent": False,
        "sigkill_sent": False,
        "reaped": False,
        "group_absent": False,
        "pid": proc.pid,
        "pgid": known_pgid,
        "error": None,
    }
    pgid = known_pgid
    if pgid is None and proc.pid is not None:
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            cleanup["reaped"] = True
            pgid = None
        except OSError as exc:
            cleanup["error"] = f"getpgid_failed:{exc}"
            pgid = None
    elif proc.pid is None and pgid is None:
        cleanup["error"] = "no_pid"
        cleanup["group_absent"] = True
        return cleanup

    cleanup["pgid"] = pgid
    try:
        host_pgid = os.getpgid(0)
    except OSError:
        host_pgid = None
    if pgid is not None and host_pgid is not None and pgid == host_pgid:
        cleanup["error"] = "refused_to_signal_host_process_group"
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
            cleanup["reaped"] = True
        except Exception as exc:  # noqa: BLE001
            cleanup["error"] = f"{cleanup['error']};reap:{exc}"
        return cleanup

    def _kill_group(sig: int) -> bool:
        if pgid is None:
            return False
        try:
            os.killpg(pgid, sig)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            return False

    cleanup["signaled_group"] = True
    if _kill_group(signal.SIGTERM):
        cleanup["sigterm_sent"] = True
    else:
        try:
            proc.terminate()
            cleanup["sigterm_sent"] = True
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        cleanup["reaped"] = True
    except (asyncio.TimeoutError, ProcessLookupError):
        if _kill_group(signal.SIGKILL):
            cleanup["sigkill_sent"] = True
        else:
            try:
                proc.kill()
                cleanup["sigkill_sent"] = True
            except ProcessLookupError:
                pass
        try:
            await proc.wait()
            cleanup["reaped"] = True
        except Exception as exc:  # noqa: BLE001
            cleanup["error"] = f"reap_failed:{exc}"

    # Verify group members are gone; do not succeed while descendants live.
    if pgid is not None:
        for _ in range(PROCESS_GROUP_VERIFY_ATTEMPTS):
            if not pgid_alive(pgid):
                cleanup["group_absent"] = True
                break
            _kill_group(signal.SIGKILL)
            cleanup["sigkill_sent"] = True
            await asyncio.sleep(PROCESS_GROUP_VERIFY_POLL_SECONDS)
        if not cleanup["group_absent"]:
            cleanup["error"] = (
                (cleanup.get("error") or "")
                + f";process_group_still_alive:pgid={pgid}"
            ).lstrip(";")
    else:
        cleanup["group_absent"] = cleanup.get("reaped", False)
    return cleanup


@dataclass
class ExternalLaunchPlan:
    argv: list[str]
    cwd: str
    env: dict[str, str]
    isolated: IsolatedLane | None
    isolation_meta: dict[str, Any]
    # Compatibility name: true means this external attempt was skipped and the
    # configured attempt chain should continue. It does not prove native ran.
    fallback_host_native: bool
    invocation: AdapterInvocation | None
    stdin_bytes: bytes | None

    @property
    def external_attempt_skipped(self) -> bool:
        """Truthful name for the retained compatibility storage field."""
        return self.fallback_host_native


def prepare_external_launch(
    *,
    spec: LaneSpec,
    attempt: EffectiveAttempt,
    attempt_index: int,
    repo_root: Path,
    packet_path: Path,
    prompt_path: Path,
    packet_dict: Mapping[str, Any],
    redacted_task: str,
    exact_secret_values: frozenset[str],
    grants: Sequence[str],
    scrub_env: Mapping[str, str],
    command_override: tuple[str, ...] | None,
    parent_env: Mapping[str, str] | None,
) -> ExternalLaunchPlan:
    """Build isolated launch plan. Isolation happens before argv construction."""
    isolation_required = bool(spec.required) or bool(attempt.required)
    # A command override still launches a subprocess and receives the same
    # production isolation boundary. There is no public unsafe bypass.
    use_isolation = attempt.adapter != "host-native" or command_override is not None
    isolated: IsolatedLane | None = None
    isolation_meta: dict[str, Any] = {"enabled": False}
    launch_repo = Path(repo_root)

    def _skip_external_attempt(reason: str) -> ExternalLaunchPlan:
        return ExternalLaunchPlan(
            argv=[],
            cwd=str(repo_root),
            env=dict(scrub_env),
            isolated=None,
            isolation_meta={
                "enabled": False,
                "external_attempt": "skipped",
                "fallback_chain": "continue",
                "reason": reason,
            },
            fallback_host_native=True,
            invocation=None,
            stdin_bytes=None,
        )

    if use_isolation:
        try:
            backend = resolve_fs_sandbox_backend()
            # A tracked snapshot alone cannot prevent absolute host/sibling reads.
            # Required routes block; optional routes skip this external attempt.
            if backend is None:
                reason = (
                    "filesystem sandbox backend not available "
                    "(sandbox-exec on macOS or bwrap on Linux)"
                )
                if isolation_required:
                    raise ValidationIssue(
                        "isolation_sandbox_unavailable",
                        f"Required {reason}",
                    )
                return _skip_external_attempt(reason)
            isolated = create_tracked_snapshot(
                IsolationSpec(
                    repo_root=Path(repo_root),
                    lane_id=str(spec.lane_id),
                    include_instructions_as_data=spec.include_instructions_as_data,
                    credential_grants={
                        name: scrub_env[name]
                        for name in grants
                        if name in scrub_env and scrub_env[name]
                    },
                    base_env={
                        "PATH": scrub_env.get("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
                    },
                    # Every real external launch gets the OS boundary. Required
                    # controls fail-closed versus continuing the attempt chain.
                    require_fs_sandbox=True,
                    qualified_backend=backend,
                )
            )
            launch_repo = isolated.snapshot
            isolation_meta = {
                "enabled": True,
                "snapshot": str(isolated.snapshot),
                "sandbox_backend": isolated.sandbox_backend,
                "process_containment": isolated.process_containment,
                "instruction_data_files": list(isolated.instruction_data_files),
            }
        except Exception as exc:  # noqa: BLE001
            if isolated is not None:
                isolated.cleanup()
                isolated = None
            if isolation_required:
                raise ValidationIssue(
                    "required_isolation_failed",
                    f"Required isolation failed: {type(exc).__name__}: {exc}; "
                    "refusing repo-root external launch",
                ) from exc
            # Optional setup failures skip this attempt so the configured chain
            # can continue; they never launch in the original repository.
            return _skip_external_attempt(f"{type(exc).__name__}: {exc}")

    # Any error after snapshot creation but before ownership passes to the
    # launch plan must clean the disposable tree here.
    try:
        launch_packet_path = packet_path
        launch_prompt_path = prompt_path
        if isolated is not None:
            launch_packet_path, launch_prompt_path = copy_isolated_transport_inputs(
                isolated,
                packet_path=packet_path,
                prompt_path=prompt_path,
            )

        defaults = ADAPTER_CONTRACT_PAIRS.get(
            attempt.adapter, ("json-stdio", "custom-json-envelope")
        )
        attempt_input_contract = (attempt.input_contract or defaults[0]).strip()
        attempt_output_contract = (attempt.output_contract or defaults[1]).strip()
        if attempt_output_contract == "json-role-report":
            attempt_output_contract = "custom-json-envelope"

        validate_extra_args(attempt.adapter, attempt.extra_args)
        if command_override is None:
            validate_adapter_contract_pair(
                attempt.adapter,
                input_contract=attempt_input_contract,
                output_contract=attempt_output_contract,
            )

        if command_override is not None and attempt_index == 0:
            command = list(command_override)
            invocation = AdapterInvocation(
                adapter=attempt.adapter,
                executable=command[0] if command else "",
                argv=tuple(command),
                read_only=True,
                notes="command_override",
                input_mode="none",
                decoder=default_decoder_for_adapter(attempt.adapter)
                if attempt.adapter != "custom-cli"
                else "custom-json-envelope",
                cwd=str(launch_repo),
            )
        else:
            attempt_session = attempt.session_id or spec.session_id
            invocation = build_readonly_invocation(
                adapter=attempt.adapter,
                profile=attempt.profile,
                executable=attempt.executable,
                packet_path=launch_packet_path,
                prompt_path=launch_prompt_path,
                requested_model=attempt.requested_model,
                extra_args=attempt.extra_args,
                packet=dict(packet_dict),
                task=redacted_task,
                role=spec.role,
                input_contract=attempt_input_contract,
                output_contract=attempt_output_contract,
                repo_root=launch_repo,
                session_id=attempt_session,
                cwd=str(launch_repo),
            )
            command = list(invocation.argv)

        if invocation.unavailable:
            raise ValidationIssue(
                "adapter_unavailable",
                invocation.unavailable_reason or "adapter unavailable",
            )
        if not command:
            raise ValidationIssue("empty_command", "empty command")

        if isolated is not None:
            # Grok reads its full prompt from a file. Put the final body at the
            # already-sandboxed path before the read-only mount is launched.
            if invocation.prompt_file_body is not None:
                launch_prompt_path.write_text(
                    redact_text(
                        invocation.prompt_file_body,
                        exact_values=exact_secret_values,
                    ).text,
                    encoding="utf-8",
                )
                launch_prompt_path.chmod(0o600)
            command = rewrite_argv_repo_paths(
                command, original_repo=Path(repo_root), snapshot=isolated.snapshot
            )
            command_executable = Path(command[0]).expanduser()
            if command_executable.is_absolute() and not command_executable.is_file():
                raise ValidationIssue(
                    "launch_executable_not_found",
                    f"Launch executable not found inside isolation boundary: {command[0]}",
                    path=str(command_executable),
                )
            if isolated.sandbox_backend:
                command = wrap_argv_with_sandbox(command, isolated)
            child_env = dict(isolated.env)
            for key in ("PATH", "LANG", "LC_ALL", "TERM"):
                if key in scrub_env and key not in child_env:
                    child_env[key] = scrub_env[key]
            child_cwd = str(isolated.snapshot)
        else:
            child_env = dict(scrub_env)
            child_cwd = str(invocation.cwd or repo_root)

        stdin_bytes = None
        if invocation.stdin_text is not None:
            stdin_bytes = redact_text(
                invocation.stdin_text, exact_values=exact_secret_values
            ).text.encode("utf-8")

        return ExternalLaunchPlan(
            argv=command,
            cwd=child_cwd,
            env=child_env,
            isolated=isolated,
            isolation_meta=isolation_meta,
            fallback_host_native=False,
            invocation=invocation,
            stdin_bytes=stdin_bytes,
        )
    except BaseException:
        if isolated is not None:
            isolated.cleanup()
        raise


async def run_external_subprocess(
    *,
    plan: ExternalLaunchPlan,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Launch an external process and always clean up its isolation boundary."""
    isolated = plan.isolated

    def _cleanup() -> dict[str, Any]:
        meta = {"isolation_cleaned": True}
        if isolated is not None:
            root = isolated.root
            try:
                isolated.cleanup()
            except Exception as exc:  # noqa: BLE001
                meta["isolation_cleanup_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
            meta["isolation_cleaned"] = not root.exists() and not root.is_symlink()
            meta["isolation_root"] = str(root)
        return meta

    def _finalize(result: dict[str, Any]) -> dict[str, Any]:
        cleanup = result.setdefault("cleanup", {})
        cleanup.update(_cleanup())
        if cleanup.get("isolation_cleanup_error") or not cleanup.get(
            "isolation_cleaned", False
        ):
            reason = cleanup.get("isolation_cleanup_error") or "isolation residue remains"
            result.update(
                {
                    "ok": False,
                    "failure_class": "isolation_failure",
                    "reason": f"isolation_cleanup_failed: {reason}",
                }
            )
        return result

    needs_descendant_supervision = bool(
        isolated is not None and isolated.sandbox_backend == "sandbox-exec"
    )
    if needs_descendant_supervision and (
        not isolated.supervisor_executable or not isolated.supervision_token
    ):
        return _finalize(
            {
                "ok": False,
                "failure_class": "isolation_failure",
                "reason": "qualified macOS descendant supervision unavailable",
                "cleanup": {"descendants_absent": False},
                "process_launched": False,
            }
        )

    launch_task = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *plan.argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if plan.stdin_bytes is not None else None,
            env=plan.env,
            cwd=plan.cwd,
            start_new_session=True,
        )
    )
    try:
        # Shield the launch so cancellation cannot lose the process handle in
        # the narrow fork/exec window. If the caller cancels, finish acquiring
        # the handle, terminate the containment root, then remove the snapshot.
        proc = await asyncio.shield(launch_task)
    except asyncio.CancelledError as cancelled:
        try:
            proc = await asyncio.shield(launch_task)
        except BaseException:
            cleanup_meta = _cleanup()
            if cleanup_meta.get("isolation_cleanup_error") or not cleanup_meta.get(
                "isolation_cleaned", False
            ):
                raise ValidationIssue(
                    "isolation_cleanup_failed",
                    str(cleanup_meta.get("isolation_cleanup_error") or "residue remains"),
                )
            raise cancelled
        try:
            pgid = os.getpgid(proc.pid) if proc.pid is not None else None
        except OSError:
            pgid = None
        group_cleanup = await asyncio.shield(
            terminate_process_group(proc, known_pgid=pgid)
        )
        descendant_cleanup: dict[str, Any] = {"descendants_absent": True}
        if needs_descendant_supervision:
            supervisor = _DescendantSupervisor.for_lane(isolated, proc.pid)
            descendant_cleanup = await asyncio.shield(
                _terminate_supervised_descendants(supervisor)
            )
        cleanup_meta = _cleanup()
        containment_ok = bool(group_cleanup.get("group_absent")) and bool(
            descendant_cleanup.get("descendants_absent")
        )
        cleanup_ok = bool(cleanup_meta.get("isolation_cleaned")) and not cleanup_meta.get(
            "isolation_cleanup_error"
        )
        if not containment_ok or not cleanup_ok:
            raise ValidationIssue(
                "isolation_cancellation_cleanup_failed",
                "Cancellation could not prove process and filesystem cleanup",
            )
        raise
    except FileNotFoundError as exc:
        return _finalize({
            "ok": False,
            "failure_class": "launch_error",
            "reason": f"executable not found: {exc}",
            "cleanup": {},
            "process_launched": False,
        })
    except OSError as exc:
        return _finalize({
            "ok": False,
            "failure_class": "launch_error",
            "reason": f"launch error: {exc}",
            "cleanup": {},
            "process_launched": False,
        })
    except BaseException:
        cleanup_meta = _cleanup()
        if cleanup_meta.get("isolation_cleanup_error") or not cleanup_meta.get(
            "isolation_cleaned", False
        ):
            raise ValidationIssue(
                "isolation_cleanup_failed",
                str(cleanup_meta.get("isolation_cleanup_error") or "residue remains"),
            )
        raise

    launched_pgid: int | None
    try:
        launched_pgid = os.getpgid(proc.pid) if proc.pid is not None else None
    except OSError:
        launched_pgid = None

    timed_out = False
    cleanup: dict[str, Any] = {"pgid": launched_pgid}
    stdout_b = b""
    stderr_b = b""
    supervisor: _DescendantSupervisor | None = None
    monitor_stop: asyncio.Event | None = None
    monitor_task: asyncio.Task[None] | None = None
    if needs_descendant_supervision:
        supervisor = _DescendantSupervisor.for_lane(isolated, proc.pid)
        monitor_stop = asyncio.Event()
        monitor_task = asyncio.create_task(
            _monitor_descendants(supervisor, monitor_stop)
        )

    async def _stop_monitor() -> None:
        if monitor_stop is not None:
            monitor_stop.set()
        if monitor_task is not None:
            await asyncio.shield(monitor_task)

    async def _contain_processes(*, force_group: bool) -> dict[str, Any]:
        naturally_settled = False
        group_alive = launched_pgid is not None and pgid_alive(launched_pgid)
        if group_alive and not force_group:
            naturally_settled = await wait_for_process_group_settle(launched_pgid)
            group_alive = not naturally_settled
        if force_group or group_alive:
            group = await terminate_process_group(proc, known_pgid=launched_pgid)
        else:
            group = {
                "pid": proc.pid,
                "pgid": launched_pgid,
                "reaped": proc.returncode is not None,
                "group_absent": True,
                "error": None,
                "settled_without_signal": naturally_settled,
            }
        await _stop_monitor()
        descendants: dict[str, Any] = {
            "descendant_supervised": False,
            "descendants_absent": True,
            "descendants_found": [],
        }
        if supervisor is not None:
            descendants = await _terminate_supervised_descendants(supervisor)
        group.update(descendants)
        group["pid_namespace_teardown"] = bool(
            isolated is not None
            and isolated.sandbox_backend == "bwrap"
            and group.get("signaled_group")
            and group.get("group_absent")
        )
        return group

    result: dict[str, Any]
    try:
        runtime_error: Exception | None = None
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=plan.stdin_bytes),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            cleanup = await _contain_processes(force_group=True)
            stdout_b, stderr_b = b"", b""
        except asyncio.CancelledError:
            cleanup = await asyncio.shield(_contain_processes(force_group=True))
            cleanup_meta = _cleanup()
            containment_ok = bool(cleanup.get("group_absent")) and bool(
                cleanup.get("descendants_absent")
            ) and not cleanup.get("supervision_error")
            cleanup_ok = bool(cleanup_meta.get("isolation_cleaned")) and not cleanup_meta.get(
                "isolation_cleanup_error"
            )
            if not containment_ok or not cleanup_ok:
                raise ValidationIssue(
                    "isolation_cancellation_cleanup_failed",
                    "Cancellation could not prove process and filesystem cleanup",
                )
            raise
        except Exception as exc:  # noqa: BLE001
            cleanup = await _contain_processes(force_group=True)
            runtime_error = exc

        if runtime_error is not None:
            result = {
                "ok": False,
                "failure_class": "execution_failure",
                "reason": (
                    "execution_runtime_error: "
                    f"{type(runtime_error).__name__}: {runtime_error}"
                ),
                "cleanup": cleanup,
                "process_launched": True,
                "exit_code": proc.returncode,
                "stdout_raw": "",
                "stderr_raw": "",
            }
        else:
            if not timed_out:
                cleanup = await _contain_processes(force_group=False)
            stdout_raw = (stdout_b or b"").decode("utf-8", errors="replace")
            stderr_raw = (stderr_b or b"").decode("utf-8", errors="replace")
            containment_failed = (
                not cleanup.get("group_absent", False)
                or not cleanup.get("descendants_absent", False)
                or bool(cleanup.get("supervision_error"))
                or bool(cleanup.get("error"))
            )
            descendants_found = bool(cleanup.get("descendants_found"))
            if containment_failed:
                result = {
                    "ok": False,
                    "failure_class": "isolation_failure",
                    "reason": "descendant containment could not prove absence",
                    "cleanup": cleanup,
                    "process_launched": True,
                    "exit_code": proc.returncode,
                    "timeout": timed_out,
                    "stdout_raw": stdout_raw,
                    "stderr_raw": stderr_raw,
                }
            elif timed_out:
                result = {
                    "ok": False,
                    "failure_class": "timeout",
                    "reason": f"timeout after {timeout_seconds}s",
                    "cleanup": cleanup,
                    "process_launched": True,
                    "exit_code": proc.returncode,
                    "timeout": True,
                    "stdout_raw": stdout_raw,
                    "stderr_raw": stderr_raw,
                }
            elif descendants_found or cleanup.get("sigterm_sent") or cleanup.get("sigkill_sent"):
                result = {
                    "ok": False,
                    "failure_class": "execution_failure",
                    "reason": "external leader exited with live descendants",
                    "cleanup": cleanup,
                    "process_launched": True,
                    "exit_code": proc.returncode,
                    "stdout_raw": stdout_raw,
                    "stderr_raw": stderr_raw,
                }
            else:
                result = {
                    "ok": True,
                    "process_launched": True,
                    "exit_code": proc.returncode,
                    "stdout_raw": stdout_raw,
                    "stderr_raw": stderr_raw,
                    "cleanup": cleanup,
                    "timeout": False,
                }
        return _finalize(result)
    except BaseException:
        # Cancellation performs its own cleanup above. Any other exceptional
        # path still stops the monitor, contains the process, and removes disk.
        if proc.returncode is None:
            try:
                await asyncio.shield(_contain_processes(force_group=True))
            except BaseException:
                pass
        if isolated is not None and isolated.root.exists():
            cleanup_meta = _cleanup()
            if cleanup_meta.get("isolation_cleanup_error") or not cleanup_meta.get(
                "isolation_cleaned", False
            ):
                raise ValidationIssue(
                    "isolation_cleanup_failed",
                    str(cleanup_meta.get("isolation_cleanup_error") or "residue remains"),
                )
        raise
