"""CLI regressions for fail-closed repo-anchored runtime storage."""

from __future__ import annotations

import json
import os
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

from cobbler_agents import _write_worker_snapshot  # noqa: E402
from cobbler_runtime.leases import LeaseStore, WriterLease  # noqa: E402
from cobbler_runtime.storage import StorageError  # noqa: E402


class CobblerAgentsCliStorageTests(unittest.TestCase):
    def _run(self, repo: Path, *args: str, env: dict[str, str] | None = None):
        return subprocess.run(
            [
                sys.executable,
                str(CLI),
                *args,
                "--repo-root",
                str(repo),
            ],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def test_registry_and_store_construction_symlink_errors_are_structured(self) -> None:
        secret = "arbitrary-storage-path-sentinel-f5a64d"
        for command in (
            ("session", "list", "--json"),
            ("worker", "packet", "--lease-id", "lease-1", "--task", "x", "--json"),
        ):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as raw:
                base = Path(raw)
                secret_parent = base / secret
                repo = secret_parent / "repo"
                outside = base / "outside"
                repo.mkdir(parents=True)
                outside.mkdir()
                sentinel = outside / "sentinel.txt"
                sentinel.write_text("unchanged\n", encoding="utf-8")
                (repo / ".elves").symlink_to(outside, target_is_directory=True)
                env = dict(os.environ)
                env["ELVES_STORAGE_SECRET"] = secret

                proc = self._run(repo, *command, env=env)

                combined = proc.stdout + proc.stderr
                self.assertEqual(proc.returncode, 1, combined)
                self.assertNotIn("Traceback", combined)
                self.assertNotIn(secret, combined)
                payload = json.loads(proc.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(
                    payload["issues"][0]["code"],
                    "storage_symlink_component",
                )
                self.assertEqual(payload["issues"][0]["category"], "storage")
                self.assertIn("[REDACTED:exact_grant]", payload["issues"][0]["message"])
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged\n")
                self.assertFalse((outside / "runtime").exists())

    def test_worker_snapshot_symlink_read_is_structured_and_outside_safe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = base / "repo"
            outside = base / "outside"
            repo.mkdir()
            outside.mkdir()
            store = LeaseStore(repo)
            lease_id = "snapshot-read"
            store.save(
                WriterLease(
                    lease_id=lease_id,
                    host_checkout=str(repo / "host"),
                    worker_checkout=str(repo / "worker"),
                    session_id="session-1",
                    base_head="a" * 40,
                    adapter="grok-build",
                    profile="grok-build-write",
                )
            )
            store.activate(lease_id)
            snapshot_dir = store.snapshot_dir(lease_id)
            snapshot_dir.mkdir(parents=True)
            outside_snapshot = outside / "pre.json"
            original = '{"sentinel": "unchanged"}\n'
            outside_snapshot.write_text(original, encoding="utf-8")
            (snapshot_dir / "pre.json").symlink_to(outside_snapshot)

            proc = self._run(
                repo,
                "worker",
                "audit",
                "--lease-id",
                lease_id,
                "--json",
            )

            combined = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 1, combined)
            self.assertNotIn("Traceback", combined)
            payload = json.loads(proc.stdout)
            self.assertEqual(
                payload["issues"][0]["code"],
                "storage_symlink_component",
            )
            self.assertEqual(outside_snapshot.read_text(encoding="utf-8"), original)

    def test_worker_snapshot_symlink_write_does_not_replace_outside_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            repo = base / "repo"
            outside = base / "outside"
            repo.mkdir()
            outside.mkdir()
            destination_dir = repo / ".elves" / "runtime" / "leases" / "snapshots"
            destination_dir.mkdir(parents=True)
            outside_snapshot = outside / "pre.json"
            original = '{"sentinel": "unchanged"}\n'
            outside_snapshot.write_text(original, encoding="utf-8")
            destination = destination_dir / "pre.json"
            destination.symlink_to(outside_snapshot)

            with self.assertRaises(StorageError) as caught:
                _write_worker_snapshot(repo, destination, {"changed": True})

            self.assertIn(caught.exception.code, {"symlink_component", "symlink_leaf"})
            self.assertEqual(outside_snapshot.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
