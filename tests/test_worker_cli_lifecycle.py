"""End-to-end CLI coverage for the untrusted writer lease lifecycle."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
CLI = SCRIPTS / "cobbler_agents.py"
GROK_TEST_SESSION_ID = "22222222-2222-4222-8222-222222222222"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.leases import (  # noqa: E402
    host_qualification_evidence,
    LeaseState,
    LeaseStore,
    WriterLease,
)
from cobbler_runtime.schema import ValidationIssue  # noqa: E402
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

        session_id = GROK_TEST_SESSION_ID
        registry = SessionRegistry(host)
        registry.save(
            SessionRecord(
                session_id=session_id,
                harness="grok-build",
                profile="grok-build-write",
                role="implementer",
                requested_model="grok-4.5",
                actual_model="grok-4.5",
                parent_id="host-parent",
                cwd=str(worker.resolve()),
                worktree=str(worker.resolve()),
                source_head=head,
            )
        )
        registry.activate(session_id)
        qualification = host_qualification_evidence(
            adapter="grok-build",
            model="grok-4.5",
            profile="grok-build-write",
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
        grant_names: tuple[str, ...] = (),
    ) -> None:
        session_id = str(
            json.loads(qualification_path.read_text(encoding="utf-8"))["session_id"]
        )
        grant_args = [item for name in grant_names for item in ("--grant-env", name)]
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
            session_id,
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
            *grant_args,
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

    def test_refresh_retry_after_head_update_before_close_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-refresh-retry"
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
            patch_dir = root / "retry-patches"
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
            for patch_path in payload["patches"]:
                _git(host, "apply", "--index", patch_path)
            _git(host, "commit", "-m", "host integrates retry fixture")
            new_tip = _git(host, "rev-parse", "HEAD")

            store = LeaseStore(host)
            store.mark_integrated(lease_id, new_tip=new_tip)
            integrated = store.get(lease_id)
            audit_base = integrated.base_head
            audit_worker_tip = integrated.worker_tip
            integrated_revision = integrated.revision

            # Simulate a process dying after update-ref succeeds but before
            # the CLI can close the already-INTEGRATED lease.
            first = store.refresh_worker_to_tip(lease_id, new_tip=new_tip)
            self.assertFalse(first["already_current"])
            interrupted = store.get(lease_id)
            self.assertEqual(interrupted.state, LeaseState.INTEGRATED)
            self.assertEqual(interrupted.revision, integrated_revision)
            self.assertEqual(interrupted.base_head, audit_base)
            self.assertEqual(interrupted.worker_tip, audit_worker_tip)
            self.assertEqual(_git(worker, "rev-parse", "HEAD"), new_tip)

            worker_git_dir = Path(_git(worker, "rev-parse", "--git-dir"))
            if not worker_git_dir.is_absolute():
                worker_git_dir = worker / worker_git_dir
            unexpected_ref = worker_git_dir / "MERGE_HEAD"
            unexpected_ref.write_text(f"{audit_worker_tip}\n", encoding="ascii")
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
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.INTEGRATED)
            unexpected_ref.unlink()

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
            self.assertTrue(payload["refresh"]["already_current"])
            closed = LeaseStore(host).get(lease_id)
            self.assertEqual(closed.state, LeaseState.CLOSED)
            self.assertEqual(closed.integrated_tip, new_tip)
            self.assertEqual(closed.base_head, audit_base)
            self.assertEqual(closed.worker_tip, audit_worker_tip)
            self.assertEqual(_git(worker, "rev-parse", "HEAD"), new_tip)

    def test_missing_pre_snapshot_rejects_lease_and_releases_exclusivity(self) -> None:
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
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.REJECTED)
            self._prepare(
                host,
                worker,
                head,
                qualification_path,
                lease_id="lease-after-missing-pre",
            )

    def test_noncanonical_and_resource_pathological_pre_json_terminalize_lease(self) -> None:
        deep: object = "leaf"
        for _ in range(40):
            deep = [deep]
        cases = {
            "empty": "{}",
            "deep": json.dumps({"value": deep}),
            "huge-integer": '{"value": ' + ("9" * 256) + "}",
        }
        for label, raw in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                host, worker, head, qualification_path = self._checkout(root)
                lease_id = f"lease-bad-pre-{label}"
                self._prepare(
                    host,
                    worker,
                    head,
                    qualification_path,
                    lease_id=lease_id,
                )
                pre_path = LeaseStore(host).snapshot_dir(lease_id) / "pre.json"
                pre_path.write_text(raw, encoding="utf-8")
                pre_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

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
                self.assertIn(
                    payload["issues"][0]["code"],
                    {"audit_pre_snapshot_malformed", "storage_malformed_json"},
                )
                self.assertEqual(
                    LeaseStore(host).get(lease_id).state,
                    LeaseState.REJECTED,
                )

    def test_prepare_snapshot_failure_rejects_published_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-prepare-snapshot-failure"
            store = LeaseStore(host)
            snapshot_dir = store.snapshot_dir(lease_id)
            snapshot_dir.mkdir(parents=True, mode=0o700)
            outside = root / "outside-pre.json"
            outside.write_text("unchanged\n", encoding="utf-8")
            (snapshot_dir / "pre.json").symlink_to(outside)
            session_id = str(
                json.loads(qualification_path.read_text(encoding="utf-8"))[
                    "session_id"
                ]
            )

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
                session_id,
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

            self.assertNotEqual(result.returncode, 0, payload)
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.REJECTED)
            self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged\n")
            (snapshot_dir / "pre.json").unlink()
            self._prepare(
                host,
                worker,
                head,
                qualification_path,
                lease_id="lease-after-prepare-failure",
            )

    def test_audit_runtime_failure_rejects_auditing_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-audit-runtime-failure"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)
            git_link = worker / ".git"
            hidden_git_link = worker / ".git.audit-test"
            git_link.rename(hidden_git_link)
            try:
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
            finally:
                hidden_git_link.rename(git_link)

            self.assertNotEqual(result.returncode, 0, payload)
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.REJECTED)
            self._prepare(
                host,
                worker,
                head,
                qualification_path,
                lease_id="lease-after-audit-failure",
            )

    def test_forbidden_command_is_categorical_and_never_persists_raw_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-redacted-command"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)

            sentinel = "synthetic-audit-token-never-persist-123456"
            command = (
                "git push "
                f"https://worker:{sentinel}@example.invalid/repository HEAD"
            )
            result, payload = self._cli(
                host,
                "worker",
                "audit",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                lease_id,
                "--observed-command",
                command,
            )

            self.assertNotEqual(result.returncode, 0, payload)
            persisted = LeaseStore(host).get(lease_id).to_dict()
            surfaced = json.dumps(
                {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "payload": payload,
                    "persisted": persisted,
                },
                sort_keys=True,
            )
            self.assertNotIn(sentinel, surfaced)
            self.assertNotIn(command, surfaced)
            self.assertIn("git:push", surfaced)

    def test_audit_rejects_secret_shaped_commit_metadata_without_persisting_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-redacted-metadata"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)

            sentinel = "xai-SYNTHETIC1234567890"
            (worker / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
            _git(worker, "add", "--", "src/app.py")
            _git(
                worker,
                "commit",
                "-m",
                f"[worker · Implement] token {sentinel}",
                "--",
                "src/app.py",
            )

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
            evidence_path = LeaseStore(host).snapshot_dir(lease_id) / "audit_evidence.json"
            self.assertFalse(evidence_path.exists())
            surfaced = json.dumps(
                {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "payload": payload,
                    "lease": LeaseStore(host).get(lease_id).to_dict(),
                },
                sort_keys=True,
            )
            self.assertNotIn(sentinel, surfaced)
            self.assertIn("[REDACTED:xai_token]", surfaced)
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.REJECTED)

    def test_audit_text_output_omits_free_form_secret_bearing_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-redacted-text-metadata"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)

            sentinel = "xai-SYNTHETICTEXT1234567890"
            (worker / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
            _git(worker, "add", "--", "src/app.py")
            _git(
                worker,
                "commit",
                "-m",
                f"[worker · Implement] token {sentinel}",
                "--",
                "src/app.py",
            )

            result = _run(
                host,
                sys.executable,
                str(CLI),
                "worker",
                "audit",
                "--repo-root",
                str(host),
                "--lease-id",
                lease_id,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            surfaced = result.stdout + result.stderr
            self.assertNotIn(sentinel, surfaced)
            self.assertIn("audit finding(s)", surfaced)
            self.assertIn("use --json for redacted details", surfaced)
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.REJECTED)

    def test_audit_rejects_opaque_exact_secret_metadata_without_any_json_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-redacted-exact-metadata"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)

            sentinel = "opaque-commit-grant-value-771133"
            (worker / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
            _git(worker, "add", "--", "src/app.py")
            _git(
                worker,
                "commit",
                "-m",
                f"[worker · Implement] opaque marker {sentinel}",
                "--",
                "src/app.py",
            )

            with mock.patch.dict(
                os.environ,
                {"SYNTHETIC_AUDIT_API_KEY": sentinel},
            ):
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
            evidence_path = LeaseStore(host).snapshot_dir(lease_id) / "audit_evidence.json"
            self.assertFalse(evidence_path.exists())
            surfaced = json.dumps(
                {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "payload": payload,
                    "lease": LeaseStore(host).get(lease_id).to_dict(),
                },
                sort_keys=True,
            )
            self.assertNotIn(sentinel, surfaced)
            self.assertIn("[REDACTED:exact_grant]", surfaced)
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.REJECTED)

    def test_audit_rejects_noncanonical_pre_snapshot_without_echoing_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-redacted-exact-pre-snapshot"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)

            sentinel = "opaque-pre-snapshot-value-884422"
            store = LeaseStore(host)
            pre_path = store.snapshot_dir(lease_id) / "pre.json"
            pre = json.loads(pre_path.read_text(encoding="utf-8"))
            pre["synthetic_grant_context"] = {
                "nested": f"prefix:{sentinel}:suffix",
                f"key-{sentinel}": sentinel,
            }
            pre_path.write_text(json.dumps(pre), encoding="utf-8")
            pre_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            self._worker_commit(worker)

            with mock.patch.dict(
                os.environ,
                {"SYNTHETIC_AUDIT_API_KEY": sentinel},
            ):
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
            evidence_path = store.snapshot_dir(lease_id) / "audit_evidence.json"
            surfaced = json.dumps(
                {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "payload": payload,
                },
                sort_keys=True,
            )
            self.assertNotIn(sentinel, surfaced)
            self.assertFalse(evidence_path.exists())
            self.assertEqual(
                payload["issues"][0]["code"],
                "audit_pre_snapshot_malformed",
            )
            self.assertEqual(store.get(lease_id).state, LeaseState.REJECTED)

    def test_worker_grant_context_positive_verified_audit_never_persists_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-worker-grant-positive"
            grant_name = "SYNTHETIC_WORKER_API_KEY"
            sentinel = "opaque-worker-launch-grant-552211"
            with mock.patch.dict(os.environ, {grant_name: sentinel}, clear=False):
                self._prepare(
                    host,
                    worker,
                    head,
                    qualification_path,
                    lease_id=lease_id,
                    grant_names=(grant_name,),
                )
            self._worker_commit(worker)
            with mock.patch.dict(os.environ, {grant_name: sentinel}, clear=False):
                result, payload = self._cli(
                    host,
                    "worker",
                    "audit",
                    "--repo-root",
                    str(host),
                    "--json",
                    "--lease-id",
                    lease_id,
                    "--grant-env",
                    grant_name,
                )

            self.assertEqual(result.returncode, 0, payload)
            store = LeaseStore(host)
            context_path = store.snapshot_dir(lease_id) / "credential_grants.json"
            evidence_path = store.snapshot_dir(lease_id) / "audit_evidence.json"
            surfaced = json.dumps(
                {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "payload": payload,
                    "lease": store.get(lease_id).to_dict(),
                    "context": json.loads(context_path.read_text(encoding="utf-8")),
                    "evidence": json.loads(evidence_path.read_text(encoding="utf-8")),
                },
                sort_keys=True,
            )
            self.assertNotIn(sentinel, surfaced)
            self.assertEqual(store.get(lease_id).state, LeaseState.AUDITED_PASS)

    def test_worker_grant_unset_at_audit_rejects_leaking_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-worker-grant-unset"
            grant_name = "SYNTHETIC_WORKER_API_KEY"
            sentinel = "opaque-worker-unset-grant-663322"
            with mock.patch.dict(os.environ, {grant_name: sentinel}, clear=False):
                self._prepare(
                    host,
                    worker,
                    head,
                    qualification_path,
                    lease_id=lease_id,
                    grant_names=(grant_name,),
                )
            (worker / "src" / "app.py").write_text(
                f"value = {sentinel!r}\n",
                encoding="utf-8",
            )
            _git(worker, "add", "--", "src/app.py")
            _git(worker, "commit", "-m", f"opaque {sentinel}", "--", "src/app.py")

            with mock.patch.dict(os.environ, {grant_name: ""}, clear=False):
                result, payload = self._cli(
                    host,
                    "worker",
                    "audit",
                    "--repo-root",
                    str(host),
                    "--json",
                    "--lease-id",
                    lease_id,
                    "--grant-env",
                    grant_name,
                )

            self.assertNotEqual(result.returncode, 0, payload)
            self.assertEqual(
                payload["issues"][0]["code"],
                "worker_credential_grant_missing_at_audit",
            )
            store = LeaseStore(host)
            self.assertEqual(store.get(lease_id).state, LeaseState.REJECTED)
            self.assertFalse(
                (store.snapshot_dir(lease_id) / "audit_evidence.json").exists()
            )
            surfaced = json.dumps(
                {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "payload": payload,
                    "lease": store.get(lease_id).to_dict(),
                    "context": json.loads(
                        (store.snapshot_dir(lease_id) / "credential_grants.json").read_text(
                            encoding="utf-8"
                        )
                    ),
                },
                sort_keys=True,
            )
            self.assertNotIn(sentinel, surfaced)

    def test_worker_grant_value_mismatch_at_audit_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-worker-grant-mismatch"
            grant_name = "SYNTHETIC_WORKER_API_KEY"
            original = "opaque-worker-original-grant-774433"
            replacement = "opaque-worker-replaced-grant-885544"
            with mock.patch.dict(os.environ, {grant_name: original}, clear=False):
                self._prepare(
                    host,
                    worker,
                    head,
                    qualification_path,
                    lease_id=lease_id,
                    grant_names=(grant_name,),
                )
            self._worker_commit(worker)
            with mock.patch.dict(os.environ, {grant_name: replacement}, clear=False):
                result, payload = self._cli(
                    host,
                    "worker",
                    "audit",
                    "--repo-root",
                    str(host),
                    "--json",
                    "--lease-id",
                    lease_id,
                    "--grant-env",
                    grant_name,
                )
            self.assertNotEqual(result.returncode, 0, payload)
            self.assertEqual(
                payload["issues"][0]["code"],
                "worker_credential_grant_mismatch_at_audit",
            )
            surfaced = json.dumps(
                {"stdout": result.stdout, "stderr": result.stderr, "payload": payload},
                sort_keys=True,
            )
            self.assertNotIn(original, surfaced)
            self.assertNotIn(replacement, surfaced)
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.REJECTED)

    def test_worker_audit_requires_exact_prepare_grant_name_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-worker-grant-name-set"
            grant_name = "SYNTHETIC_WORKER_API_KEY"
            sentinel = "opaque-worker-name-set-grant-996655"
            with mock.patch.dict(os.environ, {grant_name: sentinel}, clear=False):
                self._prepare(
                    host,
                    worker,
                    head,
                    qualification_path,
                    lease_id=lease_id,
                    grant_names=(grant_name,),
                )
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
            self.assertNotEqual(result.returncode, 0, payload)
            self.assertEqual(
                payload["issues"][0]["code"],
                "worker_credential_grant_set_mismatch",
            )
            self.assertNotIn(sentinel, json.dumps(payload, sort_keys=True))
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.REJECTED)

    def test_audit_rejects_secret_shaped_diff_before_any_export_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-secret-diff"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)

            sentinel = "xai-SYNTHETICDIFF1234567890"
            (worker / "src" / "app.py").write_text(
                f"value = 2\nmarker = '{sentinel}'\n",
                encoding="utf-8",
            )
            _git(worker, "add", "--", "src/app.py")
            _git(
                worker,
                "commit",
                "-m",
                "[worker · Implement] update app",
                "--",
                "src/app.py",
            )

            audit_result, audit_payload = self._cli(
                host,
                "worker",
                "audit",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                lease_id,
            )
            patch_dir = root / "patches"
            export_result, export_payload = self._cli(
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

            self.assertNotEqual(audit_result.returncode, 0, audit_payload)
            self.assertNotEqual(export_result.returncode, 0, export_payload)
            self.assertFalse(patch_dir.exists())
            surfaced = json.dumps(
                {
                    "audit_stdout": audit_result.stdout,
                    "audit_stderr": audit_result.stderr,
                    "audit_payload": audit_payload,
                    "export_stdout": export_result.stdout,
                    "export_stderr": export_result.stderr,
                    "export_payload": export_payload,
                    "lease": LeaseStore(host).get(lease_id).to_dict(),
                },
                sort_keys=True,
            )
            self.assertNotIn(sentinel, surfaced)
            self.assertEqual(LeaseStore(host).get(lease_id).state, LeaseState.REJECTED)

    def test_worker_packet_redacts_secret_shaped_task_from_json_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-redacted-packet"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)
            sentinel = "SYNTHETICPACKETTOKEN12345"
            task = f"Review this request with Bearer {sentinel}"

            json_result, payload = self._cli(
                host,
                "worker",
                "packet",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                lease_id,
                "--task",
                task,
            )
            text_result = _run(
                host,
                sys.executable,
                str(CLI),
                "worker",
                "packet",
                "--repo-root",
                str(host),
                "--lease-id",
                lease_id,
                "--task",
                task,
                check=False,
            )

            self.assertEqual(json_result.returncode, 0, payload)
            self.assertEqual(text_result.returncode, 0, text_result.stderr)
            surfaced = json.dumps(
                {
                    "json_stdout": json_result.stdout,
                    "json_stderr": json_result.stderr,
                    "payload": payload,
                    "text_stdout": text_result.stdout,
                    "text_stderr": text_result.stderr,
                },
                sort_keys=True,
            )
            self.assertNotIn(sentinel, surfaced)
            self.assertIn("[REDACTED:bearer_token]", surfaced)

    def test_worker_validation_errors_are_redacted_in_json_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp)
            sentinel = "xai-SYNTHETICERROR1234567890"
            missing_lease = f"missing-{sentinel}"

            json_result, payload = self._cli(
                host,
                "worker",
                "packet",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                missing_lease,
                "--task",
                "review",
            )
            text_result = _run(
                host,
                sys.executable,
                str(CLI),
                "worker",
                "packet",
                "--repo-root",
                str(host),
                "--lease-id",
                missing_lease,
                "--task",
                "review",
                check=False,
            )

            self.assertNotEqual(json_result.returncode, 0, payload)
            self.assertNotEqual(text_result.returncode, 0)
            surfaced = json.dumps(
                {
                    "json_stdout": json_result.stdout,
                    "json_stderr": json_result.stderr,
                    "payload": payload,
                    "text_stdout": text_result.stdout,
                    "text_stderr": text_result.stderr,
                },
                sort_keys=True,
            )
            self.assertNotIn(sentinel, surfaced)
            self.assertNotIn("Traceback", surfaced)
            self.assertIn("No lease record for the requested exact id", surfaced)

    def test_malformed_lease_is_structured_and_never_echoes_record_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-malformed-record"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)
            store = LeaseStore(host)
            path = store._path(lease_id)
            record = json.loads(path.read_text(encoding="utf-8"))
            sentinel = "opaque-lease-state-secret-123456789"
            record["state"] = sentinel
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            result, payload = self._cli(
                host,
                "worker",
                "packet",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                lease_id,
                "--task",
                "review",
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["issues"][0]["code"], "lease_record_malformed")
            surfaced = result.stdout + result.stderr
            self.assertNotIn(sentinel, surfaced)
            self.assertNotIn("Traceback", surfaced)

    def test_lease_listing_binds_filename_and_get_rejects_duplicate_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-bound-record"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)
            store = LeaseStore(host)
            canonical = store._path(lease_id)
            misnamed = store.root / "attacker.json"
            canonical.rename(misnamed)
            with self.assertRaises(ValidationIssue) as ctx:
                store.list_leases_strict()
            self.assertEqual(ctx.exception.code, "lease_record_malformed")
            misnamed.rename(canonical)

            legacy = store._legacy_path(lease_id)
            legacy.write_bytes(canonical.read_bytes())
            with self.assertRaises(ValidationIssue) as ctx:
                store.list_leases_strict()
            self.assertEqual(ctx.exception.code, "lease_record_malformed")
            with self.assertRaises(ValidationIssue) as ctx:
                store.get(lease_id)
            self.assertEqual(ctx.exception.code, "lease_record_ambiguous")

    def test_lease_parser_preserves_empty_permissions_and_rejects_false_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-presence-aware-fields"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)
            payload = LeaseStore(host).get(lease_id).to_dict()

            payload["permitted_git_actions"] = []
            self.assertEqual(WriterLease.from_dict(payload).permitted_git_actions, [])
            for field_name, bad_value in (("state", ""), ("revision", False)):
                with self.subTest(field_name=field_name):
                    broken = dict(payload)
                    broken[field_name] = bad_value
                    with self.assertRaises((TypeError, ValueError)):
                        WriterLease.from_dict(broken)

    def test_legacy_lease_is_inventory_only_and_cannot_publish_canonical_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host, worker, head, qualification_path = self._checkout(root)
            lease_id = "lease-legacy-read-only"
            self._prepare(host, worker, head, qualification_path, lease_id=lease_id)
            store = LeaseStore(host)
            canonical = store._path(lease_id)
            legacy = store._legacy_path(lease_id)
            canonical.rename(legacy)
            before = {
                path.relative_to(host).as_posix(): path.read_bytes()
                for path in host.rglob("*")
                if path.is_file() and not path.is_symlink()
            }

            listed = store.list_leases_strict()
            self.assertEqual([lease.lease_id for lease in listed], [lease_id])
            with self.assertRaises(ValidationIssue) as ctx:
                store.get(lease_id)
            self.assertEqual(ctx.exception.code, "lease_legacy_read_only")

            result, response = self._cli(
                host,
                "worker",
                "packet",
                "--repo-root",
                str(host),
                "--json",
                "--lease-id",
                lease_id,
                "--task",
                "review",
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(
                response["issues"][0]["code"],
                "lease_legacy_read_only",
            )
            after = {
                path.relative_to(host).as_posix(): path.read_bytes()
                for path in host.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            self.assertEqual(after, before)
            self.assertFalse(canonical.exists())
            self.assertTrue(legacy.is_file())

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
