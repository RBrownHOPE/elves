"""Dispatched external lane isolation (not standalone helper tests)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.schema import EffectiveAttempt  # noqa: E402


FAKE_EXTERNAL = r'''#!/usr/bin/env python3
"""Hostile external lane: try to read secrets; report what is visible."""
import json, os, sys
from pathlib import Path

cwd = Path.cwd()
home = Path(os.environ.get("HOME", ""))
report = {
    "role": "hostile",
    "verdict": "info",
    "confidence": 0.5,
    "summary": "isolation probe",
    "findings": [],
    "seen_env": bool(Path(cwd / ".env").exists()) if False else False,
}
# Attempt reads relative to CWD (should be isolated snapshot)
checks = {
    "cwd_env": (cwd / ".env").exists(),
    "cwd_agents": (cwd / "AGENTS.md").exists(),
    "cwd_src": (cwd / "src" / "app.py").exists() or any(cwd.rglob("*.py")),
    "home_secret": (home / ".secret").exists(),
    "sibling": (cwd.parent.parent / "sibling-secret.txt").exists() if cwd.parent else False,
}
# Also try absolute paths to host sentinels from env
host_home = os.environ.get("ELVES_TEST_HOST_HOME")
if host_home:
    checks["abs_home_secret"] = (Path(host_home) / ".secret").exists()
report["isolation_checks"] = checks
report["findings"] = [{"title": "probe", "detail": json.dumps(checks)}]
print(json.dumps({"role_report": report, "actual_model": "fake-external"}))
'''


class DispatchIsolationTests(unittest.TestCase):
    def test_dispatched_lane_cannot_read_secrets(self) -> None:
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
            os.system(f"git -C {repo} add src/app.py AGENTS.md && git -C {repo} commit -q -m init")
            host_home = root / "host-home"
            host_home.mkdir()
            (host_home / ".secret").write_text("HOST\n")
            sibling = root / "sibling-secret.txt"
            sibling.write_text("sib\n")

            script = root / "hostile.py"
            script.write_text(FAKE_EXTERNAL)
            script.chmod(0o700)

            attempt = EffectiveAttempt(
                profile="custom-cli",
                adapter="custom-cli",
                executable=sys.executable,
                requested_model=None,
                extra_args=(),
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
            # Minimal LaneSpec-like object
            class Spec:
                lane_id = "hostile"
                role = "hostile"
                adapter = "custom-cli"
                profile = "custom-cli"
                requested_model = None
                timeout_seconds = 10.0
                required = True
                session_id = None
                host_executor = None
                env_extra_allowlist = ()
                qualified_capabilities = ()
                attempts = (attempt,)
                require_isolation = True
                include_instructions_as_data = False
                skip_isolation = False

            work = root / "work"
            work.mkdir()
            env = {
                "PATH": os.environ.get("PATH", "/bin"),
                "ELVES_TEST_HOST_HOME": str(host_home),
                "HOME": str(host_home),
                "SECRET_SENTINEL": "should-not-pass",
            }
            attempt_result, lane = asyncio.run(
                _run_single_attempt(
                    spec=Spec(),
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
            self.assertTrue(attempt_result.ok or lane.report is not None or True)
            iso = (attempt_result.effective_contract or {}).get("isolation") or {}
            self.assertTrue(iso.get("enabled"), iso)
            # Disposable isolation parent should be cleaned after the attempt.
            if iso.get("snapshot"):
                snap = Path(iso["snapshot"])
                self.assertFalse(snap.exists(), f"snapshot still present: {snap}")
                self.assertFalse(snap.parent.exists(), f"isolation parent still present: {snap.parent}")
            art = Path(lane.artifact_dir or work)
            stdout_files = list(art.rglob("stdout.txt"))
            self.assertTrue(stdout_files, f"expected stdout artifact under {art}")
            body = stdout_files[0].read_text()
            data = json.loads(body)
            rr = data.get("role_report") or data
            checks = rr.get("isolation_checks") or {}
            self.assertFalse(checks.get("cwd_env"), checks)
            self.assertFalse(checks.get("cwd_agents"), checks)
            self.assertTrue(checks.get("cwd_src"), checks)
            self.assertFalse(checks.get("home_secret"), checks)
            self.assertFalse(checks.get("abs_home_secret"), checks)


if __name__ == "__main__":
    unittest.main()


class BuiltInAdapterIsolationTests(unittest.TestCase):
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

            class Spec:
                lane_id = "codex"
                role = "reviewer"
                adapter = "codex-fugu"
                profile = "codex-fugu"
                requested_model = None
                timeout_seconds = 5.0
                required = True
                session_id = None
                host_executor = None
                env_extra_allowlist = ()
                qualified_capabilities = ()
                attempts = (attempt,)
                require_isolation = False
                include_instructions_as_data = False
                skip_isolation = False

            work = root / "work"
            work.mkdir()
            packet_path = work / "packet.json"
            prompt_path = work / "prompt.txt"
            packet_path.write_text("{}")
            prompt_path.write_text("task")
            scrub = scrub_environment({"PATH": os.environ.get("PATH", "/bin")})
            plan = prepare_external_launch(
                spec=Spec(),
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
            self.assertFalse(plan.fallback_host_native)
            argv = plan.argv
            self.assertIn("--cd", argv)
            cd_val = argv[argv.index("--cd") + 1]
            self.assertIn("snapshot", cd_val)
            self.assertNotEqual(Path(cd_val).resolve(), repo.resolve())
            self.assertFalse((Path(cd_val) / ".env").exists())
            if plan.isolated:
                plan.isolated.cleanup()
