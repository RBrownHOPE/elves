"""Dispatched external lane isolation (not standalone helper tests)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.dispatch_models import LaneSpec  # noqa: E402
from cobbler_runtime.isolation import (  # noqa: E402
    IsolationSpec,
    IsolatedLane,
    QualifiedSandboxBackend,
    create_tracked_snapshot,
    prepare_fs_sandbox,
    resolve_fs_sandbox_backend,
    rewrite_argv_repo_paths,
    wrap_argv_with_sandbox,
)
from cobbler_runtime.schema import EffectiveAttempt, ValidationIssue  # noqa: E402


def _init_repo(path: Path) -> None:
    os.system(f"git -C {path} init -q")
    os.system(f"git -C {path} config user.email t@t")
    os.system(f"git -C {path} config user.name t")


def _commit_all(path: Path) -> None:
    os.system(f"git -C {path} add -A && git -C {path} commit -q -m fixture")


def _write_tool_stub(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


FAKE_EXTERNAL = r'''#!/usr/bin/env python3
"""Hostile external lane: try to read secrets; report what is visible."""
import json, os, sys
from pathlib import Path

cwd = Path.cwd()
home = Path(os.environ.get("HOME", ""))

def can_read(path):
    try:
        path.read_bytes()
        return True
    except OSError:
        return False

report = {
    "role": "hostile",
    "verdict": "info",
    "confidence": 0.5,
    "key_findings": [],
    "evidence": [],
    "risks": [],
    "recommended_actions": [],
    "open_questions": [],
}
# Attempt reads relative to CWD (should be isolated snapshot)
checks = {
    "cwd_env": can_read(cwd / ".env"),
    "cwd_agents": can_read(cwd / "AGENTS.md"),
    "cwd_src": can_read(cwd / "src" / "app.py"),
    "home_secret": can_read(home / ".secret"),
    "sibling": can_read(cwd.parent.parent / "sibling-secret.txt") if cwd.parent else False,
}
# Also try absolute paths to host sentinels from env
host_home = os.environ.get("ELVES_TEST_HOST_HOME")
if host_home:
    checks["abs_home_secret"] = can_read(Path(host_home) / ".secret")
host_write = os.environ.get("ELVES_TEST_HOST_WRITE")
if host_write:
    try:
        Path(host_write).write_text("HOST_MUTATED\n")
        checks["abs_host_write"] = True
    except OSError:
        checks["abs_host_write"] = False
report["evidence"] = [json.dumps({"isolation_checks": checks}, sort_keys=True)]
print(json.dumps({"role_report": report, "actual_model": "fake-external"}))
'''


class DispatchIsolationTests(unittest.TestCase):
    def test_dispatched_lane_cannot_read_secrets(self) -> None:
        if resolve_fs_sandbox_backend() is None:
            self.skipTest("usable filesystem sandbox backend not available")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            os.system(f"git -C {repo} init -q")
            os.system(f"git -C {repo} config user.email t@t")
            os.system(f"git -C {repo} config user.name t")
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("print('ok')\n")
            (repo / "AGENTS.md").write_text("SECRET_INSTRUCTION\n")
            (repo / ".env").write_text("SECRET_SENTINEL=x\n")

            # Keep the hostile executable tracked so bwrap can run the rewritten
            # snapshot path instead of requiring a host-temp bind.
            script = repo / "hostile.py"
            script.write_text(FAKE_EXTERNAL)
            script.chmod(0o700)
            os.system(
                f"git -C {repo} add src/app.py AGENTS.md hostile.py "
                f"&& git -C {repo} commit -q -m init"
            )
            host_home = root / "host-home"
            host_home.mkdir()
            (host_home / ".secret").write_text("HOST\n")
            host_write = root / "host-write.txt"
            sibling = root / "sibling-secret.txt"
            sibling.write_text("sib\n")

            attempt = EffectiveAttempt(
                profile="custom-cli",
                adapter="custom-cli",
                executable=sys.executable,
                requested_model=None,
                extra_args=(),
                env_grants=("ELVES_TEST_HOST_HOME", "ELVES_TEST_HOST_WRITE"),
                input_contract="json-stdio",
                output_contract="custom-json-envelope",
                capabilities=(),
                reason="isolation test",
                required=True,
                enabled=True,
                source="test",
            )
            # Build a fake lane that runs our script as the "adapter executable"
            # by command_override via council isn't direct — use _run_single_attempt path
            # through run_council with a custom profile is heavy. Use asyncio direct.
            from cobbler_runtime.dispatch import (  # noqa: PLC0415
                ContextPacket,
                _HostEvidenceLedger,
                _run_single_attempt,
            )
            from cobbler_runtime.context import build_context_packet  # noqa: PLC0415

            packet = build_context_packet(
                task="probe isolation",
                role="hostile",
                mode="read-only",
                scope="test",
                relevant_files=[],
                plan_path=None,
                head_sha=None,
                requested_model=None,
                profile="custom-cli",
                adapter="custom-cli",
                run_id="iso-test",
            )
            spec = LaneSpec(
                lane_id="hostile",
                role="hostile",
                adapter="custom-cli",
                profile="custom-cli",
                required=True,
                timeout_seconds=10.0,
                attempts=(attempt,),
            )

            work = root / "work"
            work.mkdir()
            env = {
                "PATH": os.environ.get("PATH", "/bin"),
                "ELVES_TEST_HOST_HOME": str(host_home),
                "ELVES_TEST_HOST_WRITE": str(host_write),
                "HOME": str(host_home),
                "SECRET_SENTINEL": "should-not-pass",
            }
            attempt_result, lane = asyncio.run(
                _run_single_attempt(
                    spec=spec,
                    attempt=attempt,
                    attempt_index=0,
                    packet=packet,
                    work_dir=work,
                    parent_env=env,
                    command_override=(sys.executable, str(script)),
                    repo_root=repo,
                    host_ledger=_HostEvidenceLedger("iso-test"),
                    task="probe isolation",
                )
            )
            self.assertTrue(
                attempt_result.ok,
                f"{attempt_result.reason}; stderr={lane.stderr_summary}",
            )
            self.assertTrue(lane.ok, lane.error)
            self.assertIsNotNone(lane.report)
            self.assertTrue(attempt_result.process_launched)
            self.assertTrue(lane.process_launched)
            iso = (attempt_result.effective_contract or {}).get("isolation") or {}
            self.assertTrue(iso.get("enabled"), iso)
            self.assertIn(iso.get("sandbox_backend"), {"sandbox-exec", "bwrap"}, iso)
            # Disposable isolation parent should be cleaned after the attempt.
            if iso.get("snapshot"):
                snap = Path(iso["snapshot"])
                self.assertFalse(snap.exists(), f"snapshot still present: {snap}")
                self.assertFalse(
                    snap.parent.exists(),
                    f"isolation parent still present: {snap.parent}",
                )
            art = Path(lane.artifact_dir or work)
            stdout_files = list(art.rglob("stdout.txt"))
            self.assertTrue(stdout_files, f"expected stdout artifact under {art}")
            body = stdout_files[0].read_text()
            data = json.loads(body)
            rr = data.get("role_report") or data
            evidence = rr.get("evidence") or []
            self.assertTrue(evidence, rr)
            checks = json.loads(evidence[0]).get("isolation_checks") or {}
            self.assertFalse(checks.get("cwd_env"), checks)
            self.assertFalse(checks.get("cwd_agents"), checks)
            self.assertTrue(checks.get("cwd_src"), checks)
            self.assertFalse(checks.get("home_secret"), checks)
            self.assertIn("abs_home_secret", checks)
            self.assertFalse(checks.get("abs_home_secret"), checks)
            self.assertIn("abs_host_write", checks)
            self.assertFalse(checks.get("abs_host_write"), checks)
            self.assertFalse(host_write.exists(), "hostile lane wrote outside isolation")

    def test_required_isolation_fails_closed_without_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("print('ok')\n")
            work = root / "work"
            work.mkdir()

            attempt = EffectiveAttempt(
                profile="custom-cli",
                adapter="custom-cli",
                executable=sys.executable,
                requested_model=None,
                extra_args=(),
                input_contract="json-stdio",
                output_contract="custom-json-envelope",
                capabilities=(),
                reason="no-backend isolation test",
                required=True,
                enabled=True,
                source="test",
            )

            spec = LaneSpec(
                lane_id="no-backend",
                role="hostile",
                adapter="custom-cli",
                profile="custom-cli",
                required=True,
                timeout_seconds=10.0,
                attempts=(attempt,),
            )

            from cobbler_runtime.context import build_context_packet  # noqa: PLC0415
            from cobbler_runtime.dispatch import (  # noqa: PLC0415
                _HostEvidenceLedger,
                _run_single_attempt,
            )

            packet = build_context_packet(
                task="must not launch",
                role="hostile",
                mode="read-only",
                scope="test",
                relevant_files=[],
                plan_path=None,
                head_sha=None,
                requested_model=None,
                profile="custom-cli",
                adapter="custom-cli",
                run_id="no-backend-test",
            )
            with mock.patch(
                "cobbler_runtime.dispatch_external.resolve_fs_sandbox_backend",
                return_value=None,
            ), mock.patch.object(
                asyncio,
                "create_subprocess_exec",
                new_callable=mock.AsyncMock,
            ) as launch:
                attempt_result, lane = asyncio.run(
                    _run_single_attempt(
                        spec=spec,
                        attempt=attempt,
                        attempt_index=0,
                        packet=packet,
                        work_dir=work,
                        parent_env={"PATH": os.environ.get("PATH", "/bin")},
                        command_override=(
                            sys.executable,
                            "-c",
                            "raise SystemExit('must not launch')",
                        ),
                        repo_root=repo,
                        host_ledger=_HostEvidenceLedger("no-backend-test"),
                        task="must not launch",
                    )
                )

            self.assertFalse(attempt_result.ok)
            self.assertEqual(attempt_result.failure_class, "isolation_failure")
            self.assertIn("Required isolation failed", attempt_result.reason or "")
            self.assertFalse(attempt_result.process_launched)
            self.assertFalse(attempt_result.model_call_made)
            self.assertFalse(lane.ok)
            self.assertEqual(lane.failure_class, "isolation_failure")
            self.assertFalse(lane.process_launched)
            self.assertFalse(lane.model_call_made)
            self.assertEqual(list(work.rglob("stdout.txt")), [])
            launch.assert_not_awaited()

class BuiltInAdapterIsolationTests(unittest.TestCase):
    @staticmethod
    def _prepare_transport_plan(prepare_external_launch, **kwargs):
        """Exercise snapshot/transport construction without a live OS backend."""
        import cobbler_runtime.dispatch_external as external

        real_create = external.create_tracked_snapshot

        def create_without_live_backend(specification: IsolationSpec) -> IsolatedLane:
            return real_create(
                replace(
                    specification,
                    require_fs_sandbox=False,
                    qualified_backend=None,
                )
            )

        with mock.patch.object(
            external,
            "resolve_fs_sandbox_backend",
            return_value=QualifiedSandboxBackend("bwrap", Path("/usr/bin/bwrap")),
        ), mock.patch.object(
            external,
            "create_tracked_snapshot",
            side_effect=create_without_live_backend,
        ):
            return prepare_external_launch(**kwargs)

    def test_codex_argv_uses_snapshot_cd_not_original_repo(self) -> None:
        """Built-in codex-fugu argv must embed --cd <snapshot>, not original repo."""
        from cobbler_runtime.dispatch_external import prepare_external_launch
        from cobbler_runtime.schema import EffectiveAttempt
        from cobbler_runtime.context import scrub_environment, build_context_packet
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            os.system(f"git -C {repo} init -q")
            os.system(f"git -C {repo} config user.email t@t && git -C {repo} config user.name t")
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("print(1)\n")
            (repo / ".env").write_text("SECRET=1\n")
            os.system(f"git -C {repo} add src/app.py && git -C {repo} commit -q -m i")
            attempt = EffectiveAttempt(
                profile="codex-fugu",
                adapter="codex-fugu",
                executable="codex",
                requested_model=None,
                extra_args=(),
                input_contract="stdin",
                output_contract="codex-jsonl",
                capabilities=(),
                reason="test",
                required=True,
                enabled=True,
                source="test",
            )

            spec = LaneSpec(
                lane_id="codex",
                role="reviewer",
                adapter="codex-fugu",
                profile="codex-fugu",
                required=True,
                timeout_seconds=5.0,
                attempts=(attempt,),
            )

            work = root / "work"
            work.mkdir()
            packet_path = work / "packet.json"
            prompt_path = work / "prompt.txt"
            packet_path.write_text("{}")
            prompt_path.write_text("task")
            tool_bin = root / "tool-bin"
            _write_tool_stub(tool_bin / "codex")
            scrub = scrub_environment(
                {"PATH": f"{tool_bin}{os.pathsep}{os.environ.get('PATH', '/bin')}"}
            )
            plan = self._prepare_transport_plan(
                prepare_external_launch,
                spec=spec,
                attempt=attempt,
                attempt_index=0,
                repo_root=repo,
                packet_path=packet_path,
                prompt_path=prompt_path,
                packet_dict={"task": "x"},
                redacted_task="x",
                exact_secret_values=frozenset(),
                grants=(),
                scrub_env=scrub.env,
                command_override=None,
                parent_env=scrub.env,
            )
            self.assertFalse(plan.external_attempt_skipped)
            argv = plan.argv
            self.assertIn("--cd", argv)
            cd_val = argv[argv.index("--cd") + 1]
            self.assertIn("snapshot", cd_val)
            self.assertNotEqual(Path(cd_val).resolve(), repo.resolve())
            self.assertFalse((Path(cd_val) / ".env").exists())
            if plan.isolated:
                plan.isolated.cleanup()

    def test_grok_prompt_file_is_inside_snapshot_with_full_body(self) -> None:
        from cobbler_runtime.context import scrub_environment
        from cobbler_runtime.dispatch_external import prepare_external_launch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / "app.py").write_text("print('ok')\n")
            (repo / "GEMINI.md").write_text("INERT_ONLY\n")
            _commit_all(repo)
            work = root / "work"
            work.mkdir()
            packet = work / "packet.json"
            prompt = work / "prompt.txt"
            packet.write_text('{"task":"review"}\n')
            prompt.write_text("short placeholder\n")
            tool_bin = root / "tool-bin"
            _write_tool_stub(tool_bin / "grok")
            attempt = EffectiveAttempt(
                profile="grok-build",
                adapter="grok-build",
                executable="grok",
                input_contract="prompt-file",
                output_contract="grok-json",
                required=True,
                source="test",
            )
            spec = LaneSpec(
                lane_id="grok",
                role="reviewer",
                adapter="grok-build",
                profile="grok-build",
                required=True,
                attempts=(attempt,),
                include_instructions_as_data=True,
            )
            scrub = scrub_environment(
                {"PATH": f"{tool_bin}{os.pathsep}{os.environ.get('PATH', '/bin')}"}
            )
            plan = self._prepare_transport_plan(
                prepare_external_launch,
                spec=spec,
                attempt=attempt,
                attempt_index=0,
                repo_root=repo,
                packet_path=packet,
                prompt_path=prompt,
                packet_dict={"task": "review", "constraints": ["read-only"]},
                redacted_task="review",
                exact_secret_values=frozenset(),
                grants=(),
                scrub_env=scrub.env,
                command_override=None,
                parent_env=scrub.env,
            )
            try:
                self.assertFalse(plan.external_attempt_skipped)
                prompt_index = plan.argv.index("--prompt-file") + 1
                sandbox_prompt = Path(plan.argv[prompt_index])
                self.assertTrue(sandbox_prompt.is_file(), plan.argv)
                self.assertEqual(plan.isolated.snapshot, sandbox_prompt.parents[1])
                body = sandbox_prompt.read_text()
                self.assertIn("read-only", body)
                self.assertNotEqual(body, "short placeholder\n")
                self.assertTrue(plan.isolation_meta["instruction_data_files"])
                self.assertFalse((plan.isolated.snapshot / "GEMINI.md").exists())
            finally:
                if plan.isolated:
                    plan.isolated.cleanup()

    def test_custom_adapter_packet_path_is_inside_snapshot(self) -> None:
        from cobbler_runtime.context import scrub_environment
        from cobbler_runtime.dispatch_external import prepare_external_launch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            executable = repo / "agent.py"
            executable.write_text("print('{}')\n")
            _commit_all(repo)
            work = root / "work"
            work.mkdir()
            packet = work / "packet.json"
            prompt = work / "prompt.txt"
            packet.write_text('{"task":"review"}\n')
            prompt.write_text("review\n")
            attempt = EffectiveAttempt(
                profile="custom",
                adapter="custom-cli",
                executable=str(executable),
                input_contract="json-stdio",
                output_contract="custom-json-envelope",
                required=True,
            )
            spec = LaneSpec(
                lane_id="custom",
                role="reviewer",
                adapter="custom-cli",
                profile="custom",
                required=True,
                attempts=(attempt,),
            )
            scrub = scrub_environment({"PATH": os.environ.get("PATH", "/bin")})
            plan = self._prepare_transport_plan(
                prepare_external_launch,
                spec=spec,
                attempt=attempt,
                attempt_index=0,
                repo_root=repo,
                packet_path=packet,
                prompt_path=prompt,
                packet_dict={"task": "review"},
                redacted_task="review",
                exact_secret_values=frozenset(),
                grants=(),
                scrub_env=scrub.env,
                command_override=None,
                parent_env=scrub.env,
            )
            try:
                envelope = json.loads(plan.stdin_bytes.decode("utf-8"))
                sandbox_packet = Path(envelope["packet_path"])
                self.assertTrue(sandbox_packet.is_file())
                self.assertEqual(plan.isolated.snapshot, sandbox_packet.parents[1])
                self.assertNotIn(str(work.resolve()), plan.stdin_bytes.decode("utf-8"))
            finally:
                if plan.isolated:
                    plan.isolated.cleanup()


class IsolationSnapshotRegressionTests(unittest.TestCase):
    def test_argv_rewrite_preserves_ordinary_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            snapshot = root / "snapshot"
            repo.mkdir()
            snapshot.mkdir()
            absolute = repo / "src" / "app.py"
            argv = [
                "tool",
                "review docs",
                "relative.txt",
                "--message=src/app.py",
                "--cd",
                "src",
                "--cwd=./pkg",
                ".",
                str(absolute),
            ]
            rewritten = rewrite_argv_repo_paths(
                argv, original_repo=repo, snapshot=snapshot
            )
            resolved_snapshot = snapshot.resolve()
            self.assertEqual(rewritten[1:4], argv[1:4])
            self.assertEqual(rewritten[5], str(resolved_snapshot / "src"))
            self.assertEqual(rewritten[6], f"--cwd={resolved_snapshot / 'pkg'}")
            self.assertEqual(rewritten[7], str(resolved_snapshot))
            self.assertEqual(rewritten[8], str(resolved_snapshot / "src" / "app.py"))

    def test_empty_tracked_repo_never_copies_untracked_or_nested_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / "untracked.py").write_text("secret\n")
            (repo / ".aws").mkdir()
            (repo / ".aws" / "credentials").write_text("TOKEN\n")
            lane = create_tracked_snapshot(IsolationSpec(repo_root=repo, lane_id="empty"))
            try:
                self.assertEqual(lane.tracked_file_count, 0)
                self.assertFalse((lane.snapshot / "untracked.py").exists())
                self.assertFalse((lane.snapshot / ".aws" / "credentials").exists())
            finally:
                lane.cleanup()

    def test_non_git_workspace_fails_closed_and_cleans_partial_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".aws").mkdir()
            (repo / ".aws" / "credentials").write_text("TOKEN\n")
            isolation_root = root / "known-isolation-root"
            with mock.patch(
                "cobbler_runtime.isolation.tempfile.mkdtemp",
                side_effect=lambda **_kwargs: str(isolation_root.mkdir() or isolation_root),
            ):
                with self.assertRaises(ValidationIssue) as ctx:
                    create_tracked_snapshot(
                        IsolationSpec(repo_root=repo, lane_id="non-git")
                    )
            self.assertEqual(ctx.exception.code, "isolation_git_ls_files_failed")
            self.assertFalse(isolation_root.exists())

    def test_tracked_symlink_is_rejected_and_partial_snapshot_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            outside = root / "outside-secret"
            outside.write_text("SECRET\n")
            (repo / "link.txt").symlink_to(outside)
            _commit_all(repo)
            isolation_root = root / "known-isolation-root"
            with mock.patch(
                "cobbler_runtime.isolation.tempfile.mkdtemp",
                side_effect=lambda **_kwargs: str(isolation_root.mkdir() or isolation_root),
            ):
                with self.assertRaises(ValidationIssue) as ctx:
                    create_tracked_snapshot(
                        IsolationSpec(repo_root=repo, lane_id="symlink")
                    )
            self.assertEqual(ctx.exception.code, "isolation_tracked_symlink")
            self.assertFalse(isolation_root.exists())

    def test_provider_instructions_are_inert_and_executable_config_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / "src.py").write_text("print(1)\n")
            (repo / "GEMINI.md").write_text("AUTOLOAD_SENTINEL\n")
            (repo / ".github" / "instructions").mkdir(parents=True)
            (repo / ".github" / "copilot-instructions.md").write_text("COPILOT\n")
            (repo / ".github" / "instructions" / "secure.instructions.md").write_text(
                "NESTED_COPILOT\n"
            )
            (repo / ".gemini").mkdir()
            (repo / ".gemini" / "settings.json").write_text(
                '{"mcpServers":{"evil":{"command":"steal"}}}\n'
            )
            (repo / ".agent" / "rules").mkdir(parents=True)
            (repo / ".agent" / "rules" / "autoload.md").write_text(
                "ANTIGRAVITY_AUTOLOAD\n"
            )
            (repo / ".mcp.json").write_text('{"token":"SECRET"}\n')
            (repo / ".vscode").mkdir()
            (repo / ".vscode" / "mcp.json").write_text('{"command":"steal"}\n')
            (repo / "opencode.json").write_text('{"command":"steal"}\n')
            for protected in (".claude", ".codex", ".grok", ".gemini", ".agent"):
                nested = repo / "packages" / "demo" / protected
                nested.mkdir(parents=True, exist_ok=True)
                (nested / "settings.json").write_text('{"command":"nested-steal"}\n')
            nested_vscode = repo / "packages" / "demo" / ".vscode"
            nested_vscode.mkdir(parents=True, exist_ok=True)
            (nested_vscode / "mcp.json").write_text('{"command":"nested-mcp"}\n')
            _commit_all(repo)
            lane = create_tracked_snapshot(
                IsolationSpec(
                    repo_root=repo,
                    lane_id="instructions",
                    include_instructions_as_data=True,
                )
            )
            try:
                self.assertTrue((lane.snapshot / "src.py").is_file())
                for active in (
                    "GEMINI.md",
                    ".github/copilot-instructions.md",
                    ".github/instructions/secure.instructions.md",
                    ".gemini/settings.json",
                    ".agent/rules/autoload.md",
                    ".mcp.json",
                    ".vscode/mcp.json",
                    "opencode.json",
                    "packages/demo/.claude/settings.json",
                    "packages/demo/.codex/settings.json",
                    "packages/demo/.grok/settings.json",
                    "packages/demo/.gemini/settings.json",
                    "packages/demo/.agent/settings.json",
                    "packages/demo/.vscode/mcp.json",
                ):
                    self.assertFalse((lane.snapshot / active).exists(), active)
                evidence = "\n".join(lane.instruction_data_files)
                self.assertIn("GEMINI", evidence)
                self.assertIn("copilot", evidence.lower())
                self.assertNotIn("mcp", evidence.lower())
                self.assertNotIn("opencode", evidence.lower())
                combined = "\n".join(
                    path.read_text()
                    for path in (lane.snapshot / "_instruction_evidence").glob("*.txt")
                )
                self.assertNotIn("SECRET", combined)
                self.assertNotIn("steal", combined)
            finally:
                lane.cleanup()

    def test_extra_exclude_globs_are_enforced_and_must_be_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / "keep.py").write_text("ok\n")
            (repo / "generated").mkdir()
            (repo / "generated" / "bundle.js").write_text("generated\n")
            (repo / "server.pem").write_text("PRIVATE\n")
            _commit_all(repo)
            lane = create_tracked_snapshot(
                IsolationSpec(
                    repo_root=repo,
                    lane_id="globs",
                    extra_exclude_globs=("generated/**", "*.pem"),
                )
            )
            try:
                self.assertTrue((lane.snapshot / "keep.py").is_file())
                self.assertFalse((lane.snapshot / "generated" / "bundle.js").exists())
                self.assertFalse((lane.snapshot / "server.pem").exists())
            finally:
                lane.cleanup()
            with self.assertRaises(ValidationIssue) as ctx:
                create_tracked_snapshot(
                    IsolationSpec(
                        repo_root=repo,
                        lane_id="bad-glob",
                        extra_exclude_globs=("../escape",),
                    )
                )
            self.assertEqual(ctx.exception.code, "invalid_isolation_exclude_glob")

    def test_cleanup_repairs_read_only_tree_and_rejects_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / "app.py").write_text("ok\n")
            _commit_all(repo)
            lane = create_tracked_snapshot(IsolationSpec(repo_root=repo, lane_id="cleanup"))
            (lane.snapshot / "app.py").chmod(0o400)
            lane.snapshot.chmod(0o500)
            lane.root.chmod(0o500)
            lane.cleanup()
            self.assertFalse(lane.root.exists())

            lane = create_tracked_snapshot(IsolationSpec(repo_root=repo, lane_id="residue"))
            try:
                with mock.patch("cobbler_runtime.isolation.shutil.rmtree", return_value=None):
                    with self.assertRaises(ValidationIssue) as ctx:
                        lane.cleanup()
                self.assertEqual(ctx.exception.code, "isolation_cleanup_failed")
                self.assertTrue(lane.root.exists())
            finally:
                lane.cleanup()


class IsolationSandboxRegressionTests(unittest.TestCase):
    @staticmethod
    def _lane(root: Path, *, backend: str) -> IsolatedLane:
        snapshot = root / "snapshot"
        home = root / "home"
        tmp = root / "tmp"
        config = root / "config"
        cache = root / "cache"
        data = root / "data"
        for path in (snapshot, home, tmp, config, cache, data):
            path.mkdir(parents=True, exist_ok=True)
        return IsolatedLane(
            lane_id="test",
            root=root,
            snapshot=snapshot,
            home=home,
            tmp=tmp,
            xdg_config=config,
            xdg_cache=cache,
            xdg_data=data,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            tracked_file_count=0,
            sandbox_backend=backend,
            sandbox_executable=(
                "/usr/bin/sandbox-exec" if backend == "sandbox-exec" else "/usr/bin/bwrap"
            ),
        )

    def test_bwrap_binds_user_local_install_narrowly_without_run_or_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host_home = root / "host-home"
            user_bin = host_home / ".local" / "bin"
            user_bin.mkdir(parents=True)
            executable = user_bin / "fake-agent"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            lane = self._lane(root / "lane", backend="bwrap")
            lane.env["PATH"] = f"{user_bin}:/usr/bin:/bin"
            argv = wrap_argv_with_sandbox(["fake-agent", "--version"], lane)
            bind_pairs = list(zip(argv, argv[1:], argv[2:]))
            self.assertIn(
                ("--ro-bind", str(executable.resolve()), str(executable.resolve())),
                bind_pairs,
            )
            self.assertNotIn(("--ro-bind", str(host_home), str(host_home)), bind_pairs)
            self.assertNotIn(("--ro-bind-try", "/run", "/run"), bind_pairs)
            self.assertNotIn(("--ro-bind-try", "/etc", "/etc"), bind_pairs)
            self.assertIn("--die-with-parent", argv)
            self.assertIn("--unshare-all", argv)

    def test_bwrap_binds_complete_hostedtoolcache_runtime_and_creates_parents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "opt" / "hostedtoolcache" / "Python" / "3.12.9" / "x64"
            executable = runtime / "bin" / "python3"
            library = runtime / "lib" / "libpython3.12.so"
            executable.parent.mkdir(parents=True)
            library.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            library.write_text("fixture\n")
            lane = self._lane(root / "lane", backend="bwrap")
            lane.env["PATH"] = f"{executable.parent}:/usr/bin:/bin"
            argv = wrap_argv_with_sandbox(["python3", "--version"], lane)
            triples = list(zip(argv, argv[1:], argv[2:]))
            self.assertIn(
                ("--ro-bind", str(runtime.resolve()), str(runtime.resolve())), triples
            )
            self.assertIn("--dir", argv)
            self.assertIn(str(runtime.resolve().parent), argv)
            self.assertNotIn(("--ro-bind-try", "/run", "/run"), triples)
            self.assertNotIn(("--ro-bind-try", "/etc", "/etc"), triples)

    def test_bwrap_never_mounts_entire_hidden_agent_install_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_root = root / "host-home" / ".grok"
            executable = agent_root / "bin" / "grok"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            (agent_root / "credentials.json").write_text("SECRET\n")
            lane = self._lane(root / "lane", backend="bwrap")
            lane.env["PATH"] = f"{executable.parent}:/usr/bin:/bin"
            argv = wrap_argv_with_sandbox(["grok", "--version"], lane)
            triples = list(zip(argv, argv[1:], argv[2:]))
            self.assertIn(
                ("--ro-bind", str(executable.resolve()), str(executable.resolve())),
                triples,
            )
            self.assertNotIn(("--ro-bind", str(agent_root), str(agent_root)), triples)

    def test_bwrap_canonicalizes_symlink_and_binds_only_standalone_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host_home = root / "host-home"
            target = host_home / "secrets" / "agent-real"
            target.parent.mkdir(parents=True)
            target.write_text("#!/bin/sh\nexit 0\n")
            target.chmod(0o700)
            user_bin = host_home / "bin"
            user_bin.mkdir()
            (user_bin / "agent").symlink_to(target)
            lane = self._lane(root / "lane", backend="bwrap")
            lane.env["PATH"] = f"{user_bin}:/usr/bin:/bin"
            argv = wrap_argv_with_sandbox(["agent"], lane)
            triples = list(zip(argv, argv[1:], argv[2:]))
            resolved = target.resolve()
            self.assertEqual(argv[-1], str(resolved))
            self.assertIn(("--ro-bind", str(resolved), str(resolved)), triples)
            self.assertNotIn(("--ro-bind", str(host_home), str(host_home)), triples)
            self.assertNotIn(
                ("--ro-bind", str(target.parent), str(target.parent)), triples
            )

    def test_backend_resolution_ignores_fake_path_executables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "sandbox-exec"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": str(fake.parent)}):
                backend = resolve_fs_sandbox_backend()
            if backend is not None:
                self.assertIn(
                    backend,
                    {
                        QualifiedSandboxBackend(
                            "sandbox-exec", Path("/usr/bin/sandbox-exec")
                        ),
                        QualifiedSandboxBackend("bwrap", Path("/usr/bin/bwrap")),
                    },
                )
                self.assertNotEqual(backend.executable, fake)

    def test_backend_resolution_rejects_present_but_unusable_backend(self) -> None:
        candidate = Path("/usr/bin/bwrap")
        with mock.patch(
            "cobbler_runtime.isolation._SANDBOX_BACKEND_CANDIDATES",
            (("bwrap", candidate),),
        ), mock.patch(
            "cobbler_runtime.isolation._qualified_system_executable",
            return_value=candidate,
        ), mock.patch(
            "cobbler_runtime.isolation._probe_fs_sandbox_backend",
            return_value=False,
        ) as probe:
            self.assertIsNone(resolve_fs_sandbox_backend())
        probe.assert_called_once_with("bwrap", candidate)

    def test_prepare_backend_capability_failure_is_optional_or_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lane = self._lane(Path(tmp) / "lane", backend="bwrap")
            selected = QualifiedSandboxBackend("bwrap", Path("/usr/bin/bwrap"))
            with mock.patch(
                "cobbler_runtime.isolation._validate_qualified_backend",
                return_value=selected,
            ), mock.patch(
                "cobbler_runtime.isolation._probe_fs_sandbox_backend",
                return_value=False,
            ):
                self.assertEqual(
                    prepare_fs_sandbox(
                        lane,
                        required=False,
                        qualified_backend=selected,
                    ),
                    (None, None),
                )
                with self.assertRaises(ValidationIssue) as caught:
                    prepare_fs_sandbox(
                        lane,
                        required=True,
                        qualified_backend=selected,
                    )
            self.assertEqual(caught.exception.code, "isolation_sandbox_unusable")

    def test_sandbox_exec_profile_records_narrow_child_tool_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lane = self._lane(root / "lane", backend="sandbox-exec")
            profile = lane.root / "sandbox.sb"
            profile.write_text(
                "(version 1)\n;; ELVES_EXECUTABLE_ALLOWLIST\n",
                encoding="utf-8",
            )
            lane.sandbox_profile_path = str(profile)
            exact_tool = (root / "tools" / "git").resolve()
            runtime = (root / "runtime").resolve()
            with mock.patch(
                "cobbler_runtime.isolation._macos_executable_access",
                return_value=([exact_tool], [runtime]),
            ):
                argv = wrap_argv_with_sandbox([sys.executable, "--version"], lane)

            body = profile.read_text(encoding="utf-8")
            self.assertIn(
                f'(allow file-read* (literal "{exact_tool}"))',
                body,
            )
            self.assertIn(
                f'(allow file-read* (subpath "{runtime}"))',
                body,
            )
            self.assertEqual(argv[:3], ["/usr/bin/sandbox-exec", "-f", str(profile)])

    def test_malicious_lane_id_is_digested_and_real_sandbox_profile_parses(self) -> None:
        qualified = resolve_fs_sandbox_backend()
        if qualified is None:
            self.skipTest("filesystem sandbox backend not available")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / "app.py").write_text("SNAPSHOT_OK\n")
            _commit_all(repo)
            malicious = 'lane\")\n(allow file-read* (subpath "/"))\n;'
            lane = create_tracked_snapshot(
                IsolationSpec(
                    repo_root=repo,
                    lane_id=malicious,
                    require_fs_sandbox=True,
                    qualified_backend=qualified,
                )
            )
            try:
                self.assertRegex(lane.root.name, r"^elves-iso-[0-9a-f]{12}-")
                self.assertNotIn(malicious, str(lane.root))
                command = wrap_argv_with_sandbox(
                    [
                        sys.executable,
                        "-c",
                        "import pathlib; assert pathlib.Path('app.py').read_text().strip() == 'SNAPSHOT_OK'",
                    ],
                    lane,
                )
                result = subprocess.run(
                    command,
                    cwd=lane.snapshot,
                    env=lane.env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                if lane.sandbox_profile_path:
                    profile = Path(lane.sandbox_profile_path).read_text()
                    self.assertNotIn(malicious, profile)
            finally:
                lane.cleanup()

    def test_sandbox_exec_allows_snapshot_and_denies_host_sentinels(self) -> None:
        qualified = resolve_fs_sandbox_backend()
        if qualified is None or qualified.name != "sandbox-exec":
            self.skipTest("sandbox-exec not available")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lane = self._lane(root / "lane", backend="sandbox-exec")
            sentinel = root / "host-sentinel.txt"
            sentinel.write_text("HOST_SECRET\n")
            source = lane.snapshot / "source.txt"
            source.write_text("SNAPSHOT_OK\n")
            backend, profile = prepare_fs_sandbox(
                lane,
                required=True,
                qualified_backend=qualified,
            )
            lane.sandbox_backend = backend
            lane.sandbox_profile_path = profile
            lane.process_containment = "host-supervised"
            command = wrap_argv_with_sandbox(
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib; "
                        "assert pathlib.Path('source.txt').read_text().strip() == 'SNAPSHOT_OK'; "
                        f"p=pathlib.Path({str(sentinel)!r}); "
                        "\ntry: p.read_text(); raise SystemExit(41)\nexcept OSError: pass\n"
                        "try: p.write_text('MUTATED'); raise SystemExit(42)\nexcept OSError: pass\n"
                    ),
                ],
                lane,
            )
            profile_body = Path(profile).read_text()
            self.assertNotIn("(deny process-fork)", profile_body)
            self.assertNotIn('(subpath "/opt")', profile_body)
            self.assertTrue(lane.supervisor_executable)
            result = subprocess.run(
                command,
                cwd=lane.snapshot,
                env=lane.env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sentinel.read_text(), "HOST_SECRET\n")

    def test_macos_supervisor_kills_setsid_double_fork_before_success(self) -> None:
        qualified = resolve_fs_sandbox_backend()
        if qualified is None or qualified.name != "sandbox-exec":
            self.skipTest("macOS sandbox-exec not available")
        from cobbler_runtime.dispatch_external import (  # noqa: PLC0415
            ExternalLaunchPlan,
            run_external_subprocess,
        )

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            script = repo / "daemonizer.py"
            script.write_text(
                """import json, os, pathlib, subprocess, sys, time
marker = pathlib.Path(os.environ['HOME']) / 'daemon.pid'
helper = r'''import os, pathlib, sys, time
if os.fork(): os._exit(0)
os.setsid()
if os.fork(): os._exit(0)
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
time.sleep(30)
'''
subprocess.Popen(
    [sys.executable, '-c', helper, str(marker)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
for _ in range(100):
    if marker.exists(): break
    time.sleep(0.01)
pid = int(marker.read_text())
print(json.dumps({'daemon_pid': pid}))
""",
                encoding="utf-8",
            )
            _commit_all(repo)
            lane = create_tracked_snapshot(
                IsolationSpec(
                    repo_root=repo,
                    lane_id="double-fork",
                    require_fs_sandbox=True,
                    qualified_backend=qualified,
                )
            )
            command = wrap_argv_with_sandbox(
                [sys.executable, str(lane.snapshot.resolve() / "daemonizer.py")], lane
            )
            plan = ExternalLaunchPlan(
                argv=command,
                cwd=str(lane.snapshot),
                env=dict(lane.env),
                isolated=lane,
                isolation_meta={"enabled": True},
                fallback_host_native=False,
                invocation=None,
                stdin_bytes=None,
            )
            result = asyncio.run(
                run_external_subprocess(plan=plan, timeout_seconds=5.0)
            )
            self.assertFalse(result["ok"], result)
            self.assertEqual(result.get("failure_class"), "execution_failure")
            cleanup = result.get("cleanup") or {}
            self.assertTrue(cleanup.get("descendants_absent"), cleanup)
            self.assertTrue(cleanup.get("descendants_found"), cleanup)
            self.assertTrue(cleanup.get("isolation_cleaned"), cleanup)
            daemon_pid = json.loads(result["stdout_raw"])["daemon_pid"]
            with self.assertRaises(ProcessLookupError):
                os.kill(daemon_pid, 0)
            self.assertFalse(lane.root.exists())

    def test_post_snapshot_validation_error_cleans_snapshot(self) -> None:
        from cobbler_runtime.context import scrub_environment
        from cobbler_runtime.dispatch_external import prepare_external_launch
        import cobbler_runtime.dispatch_external as external

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            (repo / "app.py").write_text("ok\n")
            _commit_all(repo)
            work = root / "work"
            work.mkdir()
            packet = work / "packet.json"
            prompt = work / "prompt.txt"
            packet.write_text("{}\n")
            prompt.write_text("task\n")
            attempt = EffectiveAttempt(
                profile="grok-build",
                adapter="grok-build",
                executable="grok",
                extra_args=("--permission-mode", "bypassPermissions"),
                input_contract="prompt-file",
                output_contract="grok-json",
                required=True,
            )
            spec = LaneSpec(
                lane_id="cleanup",
                role="reviewer",
                adapter="grok-build",
                profile="grok-build",
                required=True,
                attempts=(attempt,),
            )
            captured: list[IsolatedLane] = []
            real_create = external.create_tracked_snapshot

            def capture(specification: IsolationSpec) -> IsolatedLane:
                # This test owns prelaunch cleanup, not backend availability.
                lane = real_create(
                    replace(
                        specification,
                        require_fs_sandbox=False,
                        qualified_backend=None,
                    )
                )
                captured.append(lane)
                return lane

            scrub = scrub_environment({"PATH": os.environ.get("PATH", "/bin")})
            with mock.patch.object(
                external,
                "resolve_fs_sandbox_backend",
                return_value=QualifiedSandboxBackend(
                    "bwrap", Path("/usr/bin/bwrap")
                ),
            ), mock.patch.object(
                external,
                "create_tracked_snapshot",
                side_effect=capture,
            ):
                with self.assertRaises(ValidationIssue):
                    prepare_external_launch(
                        spec=spec,
                        attempt=attempt,
                        attempt_index=0,
                        repo_root=repo,
                        packet_path=packet,
                        prompt_path=prompt,
                        packet_dict={},
                        redacted_task="task",
                        exact_secret_values=frozenset(),
                        grants=(),
                        scrub_env=scrub.env,
                        command_override=None,
                        parent_env=scrub.env,
                    )
            self.assertEqual(len(captured), 1)
            self.assertFalse(captured[0].root.exists())

    def test_caller_prelaunch_evidence_error_cleans_snapshot(self) -> None:
        from cobbler_runtime.context import build_context_packet
        from cobbler_runtime.dispatch import _HostEvidenceLedger, _run_single_attempt
        import cobbler_runtime.dispatch_external as external

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            _init_repo(repo)
            script = repo / "agent.py"
            script.write_text("print('{}')\n")
            _commit_all(repo)
            work = root / "work"
            work.mkdir()
            attempt = EffectiveAttempt(
                profile="custom",
                adapter="custom-cli",
                executable=sys.executable,
                input_contract="json-stdio",
                output_contract="custom-json-envelope",
                required=True,
            )
            spec = LaneSpec(
                lane_id="prelaunch-cleanup",
                role="reviewer",
                adapter="custom-cli",
                profile="custom",
                required=True,
                attempts=(attempt,),
            )
            packet = build_context_packet(
                task="review",
                role="reviewer",
                mode="read-only",
                scope="test",
                relevant_files=[],
                plan_path=None,
                head_sha=None,
                requested_model=None,
                profile="custom",
                adapter="custom-cli",
                run_id="prelaunch-cleanup",
            )
            captured: list[IsolatedLane] = []
            real_create = external.create_tracked_snapshot

            def capture(specification: IsolationSpec) -> IsolatedLane:
                # This test owns caller cleanup, not backend availability.
                lane = real_create(
                    replace(
                        specification,
                        require_fs_sandbox=False,
                        qualified_backend=None,
                    )
                )
                captured.append(lane)
                return lane

            with mock.patch.object(
                external,
                "resolve_fs_sandbox_backend",
                return_value=QualifiedSandboxBackend(
                    "bwrap", Path("/usr/bin/bwrap")
                ),
            ), mock.patch.object(
                external,
                "create_tracked_snapshot",
                side_effect=capture,
            ), mock.patch(
                "cobbler_runtime.dispatch_lane_attempt.record_command_digests",
                side_effect=RuntimeError("evidence write failed"),
            ):
                with self.assertRaises(RuntimeError):
                    asyncio.run(
                        _run_single_attempt(
                            spec=spec,
                            attempt=attempt,
                            attempt_index=0,
                            packet=packet,
                            work_dir=work,
                            parent_env={"PATH": os.environ.get("PATH", "/bin")},
                            command_override=(sys.executable, str(script)),
                            repo_root=repo,
                            host_ledger=_HostEvidenceLedger("prelaunch-cleanup"),
                            task="review",
                        )
                    )
            self.assertEqual(len(captured), 1)
            self.assertFalse(captured[0].root.exists())

    def test_cancel_during_subprocess_creation_reaps_and_cleans(self) -> None:
        from cobbler_runtime.dispatch_external import ExternalLaunchPlan, run_external_subprocess

        async def scenario() -> tuple[IsolatedLane, list[asyncio.subprocess.Process]]:
            root = Path(tempfile.mkdtemp(prefix="cancel-launch-"))
            lane = self._lane(root, backend="")
            plan = ExternalLaunchPlan(
                argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=str(lane.snapshot),
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                isolated=lane,
                isolation_meta={"enabled": True},
                fallback_host_native=False,
                invocation=None,
                stdin_bytes=None,
            )
            started = asyncio.Event()
            release = asyncio.Event()
            processes: list[asyncio.subprocess.Process] = []
            real_launch = asyncio.create_subprocess_exec

            async def delayed_launch(*args, **kwargs):
                started.set()
                await release.wait()
                proc = await real_launch(*args, **kwargs)
                processes.append(proc)
                return proc

            with mock.patch.object(asyncio, "create_subprocess_exec", side_effect=delayed_launch):
                task = asyncio.create_task(
                    run_external_subprocess(plan=plan, timeout_seconds=30)
                )
                await started.wait()
                task.cancel()
                asyncio.get_running_loop().call_later(0.02, release.set)
                with self.assertRaises(asyncio.CancelledError):
                    await task
            return lane, processes

        lane, processes = asyncio.run(scenario())
        self.assertFalse(lane.root.exists())
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)

    def test_cleanup_error_blocks_lane_even_when_tree_was_removed(self) -> None:
        from cobbler_runtime.dispatch_external import (  # noqa: PLC0415
            ExternalLaunchPlan,
            run_external_subprocess,
        )

        root = Path(tempfile.mkdtemp(prefix="cleanup-error-"))
        lane = self._lane(root, backend="")
        plan = ExternalLaunchPlan(
            argv=[sys.executable, "-c", "print('ok')"],
            cwd=str(lane.snapshot),
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            isolated=lane,
            isolation_meta={"enabled": True},
            fallback_host_native=False,
            invocation=None,
            stdin_bytes=None,
        )

        def remove_then_error() -> None:
            shutil.rmtree(root)
            raise RuntimeError("cleanup audit failed")

        with mock.patch.object(lane, "cleanup", side_effect=remove_then_error):
            result = asyncio.run(
                run_external_subprocess(plan=plan, timeout_seconds=5.0)
            )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result.get("failure_class"), "isolation_failure")
        self.assertIn("isolation_cleanup_failed", result.get("reason", ""))
        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
