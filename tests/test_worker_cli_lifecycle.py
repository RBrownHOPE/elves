"""End-to-end CLI coverage for the untrusted writer lease lifecycle."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
CLI = SCRIPTS / "cobbler_agents.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.leases import (  # noqa: E402
    host_qualification_evidence,
    LeaseState,
    LeaseStore,
)
from cobbler_runtime.sessions import SessionRecord, SessionRegistry  # noqa: E402


def _run(cwd: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(cwd),
        check=check,
        capture_output=True,
        text=True,
    )


def _git(cwd: Path, *argv: str) -> str:
    return _run(cwd, "git", *argv).stdout.strip()


class WorkerCliLifecycleTests(unittest.TestCase):
    def _checkout(self, root: Path) -> tuple[Path, Path, str, Path]:
        host = root / "host"
        worker = root / "worker"
        host.mkdir()
        _git(host, "init")
        _git(host, "config", "user.email", "cli-lifecycle@example.test")
        _git(host, "config", "user.name", "CLI Lifecycle")
        (host / ".gitignore").write_text(".elves/\n", encoding="utf-8")
        (host / "src").mkdir()
        (host / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
        _git(host, "add", "-A")
        _git(host, "commit", "-m", "base")
        head = _git(host, "rev-parse", "HEAD")
        _git(host, "worktree", "add", "--detach", str(worker), head)

        session_id = "cli-lifecycle-session"
        SessionRegistry(host).save(
            SessionRecord(
                session_id=session_id,
                harness="grok-build",
                profile="grok-build",
                role="implementer",
                requested_model="grok-4.5",
                actual_model="grok-4.5",
                parent_id="host-parent",
                cwd=str(worker.resolve()),
                worktree=str(worker.resolve()),
                source_head=head,
            )
        )
        qualification = host_qualification_evidence(
            adapter="grok-build",
            model="grok-4.5",
            profile="grok-build",
            version="0.2.93",
            sandbox="devbox",
            worktree=str(worker.resolve()),
            cwd=str(worker.resolve()),
            parent="host-parent",
            source_head=head,
            session_id=session_id,
        )
        qualification_path = root / "qualification.json"
        qualification_path.write_text(json.dumps(qualification), encoding="utf-8")
        qualification_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return host, worker, head, qualification_path

    def _cli(self, host: Path, *argv: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = _run(
            host,
            sys.executable,
            str(CLI),
            *argv,
            check=False,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover - diagnostic guard
            self.fail(f"CLI did not emit JSON: stdout={result.stdout!r} stderr={result.stderr!r}: {exc}")
        return result, payload

    def _prepare(
        self,
        host: Path,
        worker: Path,
        head: str,
        qualification_path: Path,
        *,
        lease_id: str,
    ) -> None:
        result, payload = self._cli(
            host,
            "worker",
            "prepare",
            "--repo-root",
            str(host),
            "--json",
            "--lease-id",
            lease_id,
            "--host-checkout",
            str(host),
            "--worker-checkout",
            str(worker),
            "--session-id",
            "cli-lifecycle-session",
            "--base-head",
            head,
            "--adapter",
            "grok-build",
            "--profile",
            "grok-build-write",
            "--sandbox-profile",
            "devbox",
            "--grok-version",
            "0.2.93",
            "--allowed-path",
            "src/",
            "--qualification-file",
            str(qualification_path),
        )
        self.assertEqual(result.returncode, 0, payload)
        self.assertTrue(payload["ok"])

    def _worker_commit(self, worker: Path) -> None:
        (worker / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
        _git(worker, "add", "--", "src/app.py")
        _git(worker, "commit", "-m", "[worker · Implement] update app", "--", "src/app.py")

    def test_prepare_audit_export_apply_check_integrate_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-happy"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)
            self._worker_commit(worker)

            result, payload = self._cli(
                host,
                "worker",
                "audit",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                lease_id,
            )
            self.assertEqual(result.returncode, 0, payload)
            self.assertTrue(payload["audit"]["ok"])

            patch_dir = root / "patches"
            result, payload = self._cli(
                host,
                "worker",
                "export",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                lease_id,
                "--output-dir",
                str(patch_dir),
                "--host-apply-check",
            )
            self.assertEqual(result.returncode, 0, payload)
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.APPLY_CHECKED)

            for patch_path in payload["patches"]:
                _git(host, "apply", "--index", patch_path)
            _git(host, "commit", "-m", "host integrates audited worker patch")
            new_tip = _git(host, "rev-parse", "HEAD")

            # A failed refresh must preserve APPLY_CHECKED rather than claiming integration.
            (worker / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            result, payload = self._cli(
                host,
                "worker",
                "refresh",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                lease_id,
                "--new-tip",
                new_tip,
            )
            self.assertNotEqual(result.returncode, 0, payload)
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.APPLY_CHECKED)
            (worker / "dirty.txt").unlink()

            result, payload = self._cli(
                host,
                "worker",
                "refresh",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                lease_id,
                "--new-tip",
                new_tip,
            )
            self.assertEqual(result.returncode, 0, payload)
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.CLOSED)
            self.assertEqual(_git(worker, "rev-parse", "HEAD"), new_tip)

    def test_missing_pre_snapshot_fails_without_audit_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-missing-pre"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)
            pre_path = LeaseStore(host).snapshot_dir(lease_id) / "pre.json"
            pre_path.unlink()
            result, payload = self._cli(
                host,
                "worker",
                "audit",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                lease_id,
            )
            self.assertNotEqual(result.returncode, 0, payload)
            self.assertEqual(payload["issues"][0]["code"], "audit_missing_pre_snapshot")
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.ACTIVE)

    def test_tampered_or_missing_audit_digest_cannot_export(self) -> None:
        for mutation in ("missing", "changed"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                host, worker, head, qualification_path = self._checkout(root)
                lease_id = f"lease-digest-{mutation}"
                self._prepare(host, worker, head, qualification_path, lease_id=lease_id)
                self._worker_commit(worker)
                result, payload = self._cli(
                    host,
                    "worker",
                    "audit",
                    "--repo-root",
                    str(host),
                    "--json",
                    "--lease-id",
                    lease_id,
                )
                self.assertEqual(result.returncode, 0, payload)
                evidence_path = LeaseStore(host).snapshot_dir(lease_id) / "audit_evidence.json"
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                if mutation == "missing":
                    evidence.pop("evidence_digest", None)
                else:
                    evidence["worker_tip"] = "0" * 40
                evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

                result, payload = self._cli(
                    host,
                    "worker",
                    "export",
                    "--repo-root",
                    str(host),
                    "--json",
                    "--lease-id",
                    lease_id,
                    "--output-dir",
                    str(root / "patches"),
                )
                self.assertNotEqual(result.returncode, 0, payload)
                self.assertIn(
                    payload["issues"][0]["code"],
                    {
                        "export_evidence_incomplete",
                        "export_evidence_digest_missing",
                        "export_evidence_digest_mismatch",
                        "export_evidence_lease_digest_mismatch",
                        "export_evidence_tip_mismatch",
                    },
                )
                self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.AUDITED_PASS)


if __name__ == "__main__":
    unittest.main()
