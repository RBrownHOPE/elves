"""Batch 3–5 tests: storage, isolation, redaction sentinels, delegated git, acceptance."""

from __future__ import annotations

import json
import errno
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.delegated_git import (  # noqa: E402
    assert_action_allowed,
    assert_descendant,
    create_rollback_ref,
    parse_plan_acceptance,
    reconcile_worker_report,
    rollback_ref_name,
    validate_acceptance_mapping,
    DelegatedGitContract,
)
from cobbler_runtime.isolation import (  # noqa: E402
    IsolationSpec,
    assert_no_host_secrets,
    create_tracked_snapshot,
    implement_min_env,
    isolated_lane,
)
from cobbler_runtime.leases import LeaseStore  # noqa: E402
from cobbler_runtime.schema import ValidationIssue  # noqa: E402
from cobbler_runtime.sessions import SessionRegistry  # noqa: E402
from cobbler_runtime.storage import (  # noqa: E402
    DEFAULT_JSON_MAX_BYTES,
    StorageError,
    atomic_write_json,
    digest_key,
    directory_lock,
    ensure_private_dir,
    guard_repo_path,
    open_repo_text,
    move_repo_regular_file,
    qualify_write_evidence,
    read_json,
    read_repo_regular_bytes,
    read_repo_text_tail,
    record_filename,
    repo_regular_file_exists,
    snapshot_path,
)
import cobbler_runtime.storage as storage_module  # noqa: E402
from cobbler_runtime.context import redact_structure, redact_text  # noqa: E402


class StoragePrimitiveTests(unittest.TestCase):
    def test_repo_anchor_rejects_escape_and_symlink_ancestor_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            outside = base / "outside"
            repo.mkdir()
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            (repo / ".elves").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(StorageError) as ctx:
                ensure_private_dir(
                    repo / ".elves" / "runtime" / "sessions",
                    repo_root=repo,
                )
            self.assertEqual(ctx.exception.code, "symlink_component")
            self.assertFalse((outside / "runtime").exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged\n")

            with self.assertRaises(StorageError) as ctx:
                guard_repo_path(repo, repo / ".." / "outside" / "new.json")
            self.assertEqual(ctx.exception.code, "path_escape")

    def test_repo_json_and_lock_reject_symlink_leaves_without_touching_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            outside = base / "outside"
            repo.mkdir()
            outside.mkdir()
            store = ensure_private_dir(
                repo / ".elves" / "runtime" / "sessions",
                repo_root=repo,
            )

            record_target = outside / "record.json"
            record_target.write_text('{"sentinel": "record"}\n', encoding="utf-8")
            record = store / "record.json"
            record.symlink_to(record_target)
            with self.assertRaises(StorageError):
                read_json(record, repo_root=repo)
            with self.assertRaises(StorageError):
                atomic_write_json(record, {"changed": True}, repo_root=repo)
            with self.assertRaises(StorageError):
                repo_regular_file_exists(repo, record)
            self.assertEqual(
                record_target.read_text(encoding="utf-8"),
                '{"sentinel": "record"}\n',
            )

            lock_target = outside / "lock.txt"
            lock_target.write_text("lock-sentinel\n", encoding="utf-8")
            (store / "store.lock").symlink_to(lock_target)
            with self.assertRaises(StorageError):
                with directory_lock(store, repo_root=repo):
                    self.fail("unsafe lock unexpectedly acquired")
            self.assertEqual(lock_target.read_text(encoding="utf-8"), "lock-sentinel\n")

    def test_repo_anchored_json_and_lock_happy_path_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            store = ensure_private_dir(
                repo / ".elves" / "runtime" / "sessions",
                repo_root=repo,
            )
            record = store / "record.json"
            atomic_write_json(record, {"ok": True}, repo_root=repo)
            self.assertEqual(read_json(record, repo_root=repo), {"ok": True})
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
            with directory_lock(store, repo_root=repo) as lock_path:
                self.assertEqual(lock_path, store / "store.lock")
            self.assertEqual(stat.S_IMODE((store / "store.lock").stat().st_mode), 0o600)

    def test_bounded_byte_reader_rejects_oversize_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            outside = base / "outside"
            repo.mkdir()
            outside.mkdir()
            store = ensure_private_dir(repo / ".elves" / "runtime" / "reads", repo_root=repo)
            record = store / "record.bin"
            record.write_bytes(b"12345")
            self.assertEqual(
                read_repo_regular_bytes(repo, record, max_bytes=5),
                b"12345",
            )
            with self.assertRaises(StorageError) as ctx:
                read_repo_regular_bytes(repo, record, max_bytes=4)
            self.assertEqual(ctx.exception.code, "record_too_large")
            with self.assertRaises(StorageError) as ctx:
                read_repo_regular_bytes(repo, record, max_bytes=-1)
            self.assertEqual(ctx.exception.code, "invalid_size_limit")

            target = outside / "secret.bin"
            target.write_bytes(b"outside-sentinel")
            link = store / "linked.bin"
            link.symlink_to(target)
            with self.assertRaises(StorageError):
                read_repo_regular_bytes(repo, link, max_bytes=100)
            self.assertEqual(target.read_bytes(), b"outside-sentinel")

            hardlink = store / "hardlinked.bin"
            os.link(target, hardlink)
            with self.assertRaises(StorageError) as ctx:
                read_repo_regular_bytes(repo, hardlink, max_bytes=100)
            self.assertEqual(ctx.exception.code, "unsafe_link_count")
            self.assertEqual(target.read_bytes(), b"outside-sentinel")

    def test_read_json_is_bounded_and_reports_encoding_and_io_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            store = ensure_private_dir(repo / ".elves" / "runtime" / "json", repo_root=repo)
            record = store / "record.json"
            record.write_text('{"value": "0123456789"}\n', encoding="utf-8")

            with self.assertRaises(StorageError) as ctx:
                read_json(record, repo_root=repo, max_bytes=8)
            self.assertEqual(ctx.exception.code, "record_too_large")
            self.assertGreater(DEFAULT_JSON_MAX_BYTES, 8)

            record.write_bytes(b'{"value":"\xff"}')
            with self.assertRaises(StorageError) as ctx:
                read_json(record, repo_root=repo)
            self.assertEqual(ctx.exception.code, "invalid_utf8")

            with mock.patch.object(
                storage_module,
                "read_repo_regular_bytes",
                side_effect=OSError(errno.EIO, "forced read failure"),
            ), self.assertRaises(StorageError) as ctx:
                read_json(record, repo_root=repo)
            self.assertEqual(ctx.exception.code, "read_failed")

    def test_repo_text_tail_is_bounded_and_rejects_linked_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            outside = base / "outside"
            repo.mkdir()
            outside.mkdir()
            store = ensure_private_dir(repo / ".elves" / "runtime" / "tails", repo_root=repo)
            transcript = store / "transcript.log"
            transcript.write_bytes(b"x" * 128 + b"\nalpha\nbeta\n")

            self.assertEqual(
                read_repo_text_tail(
                    repo,
                    transcript,
                    max_bytes=12,
                    max_lines=2,
                ),
                ["alpha", "beta"],
            )
            transcript.write_bytes(b"\xff\nok\n")
            self.assertEqual(
                read_repo_text_tail(repo, transcript, max_bytes=32, max_lines=2),
                ["�", "ok"],
            )
            with self.assertRaises(StorageError) as ctx:
                read_repo_text_tail(repo, transcript, max_bytes=-1)
            self.assertEqual(ctx.exception.code, "invalid_size_limit")
            with self.assertRaises(StorageError) as ctx:
                read_repo_text_tail(repo, transcript, max_lines=-1)
            self.assertEqual(ctx.exception.code, "invalid_line_limit")

            target = outside / "outside.log"
            original = b"outside-sentinel\n"
            target.write_bytes(original)
            transcript.unlink()
            transcript.symlink_to(target)
            with self.assertRaises(StorageError):
                read_repo_text_tail(repo, transcript)
            self.assertEqual(target.read_bytes(), original)

            transcript.unlink()
            os.link(target, transcript)
            with self.assertRaises(StorageError) as ctx:
                read_repo_text_tail(repo, transcript)
            self.assertEqual(ctx.exception.code, "unsafe_link_count")
            self.assertEqual(target.read_bytes(), original)

    def test_repo_regular_move_checks_source_destination_and_replace_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            outside = base / "outside"
            repo.mkdir()
            outside.mkdir()
            source_dir = ensure_private_dir(repo / "source", repo_root=repo)
            destination_dir = ensure_private_dir(repo / "destination", repo_root=repo)
            source = source_dir / "state.json"
            destination = destination_dir / "state.json"
            source.write_text('{"ok": true}\n', encoding="utf-8")

            moved = move_repo_regular_file(repo, source, destination)
            self.assertEqual(moved, destination)
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_text(encoding="utf-8"), '{"ok": true}\n')

            source.write_text("replacement\n", encoding="utf-8")
            with self.assertRaises(StorageError) as ctx:
                move_repo_regular_file(repo, source, destination)
            self.assertEqual(ctx.exception.code, "destination_exists")
            self.assertEqual(source.read_text(encoding="utf-8"), "replacement\n")
            move_repo_regular_file(repo, source, destination, replace=True)
            self.assertEqual(destination.read_text(encoding="utf-8"), "replacement\n")

            outside_target = outside / "sentinel.txt"
            original = "outside-sentinel\n"
            outside_target.write_text(original, encoding="utf-8")
            destination.unlink()
            destination.symlink_to(outside_target)
            source.write_text("do-not-move\n", encoding="utf-8")
            with self.assertRaises(StorageError):
                move_repo_regular_file(repo, source, destination, replace=True)
            self.assertEqual(outside_target.read_text(encoding="utf-8"), original)
            self.assertEqual(source.read_text(encoding="utf-8"), "do-not-move\n")

            destination.unlink()
            os.link(outside_target, destination)
            with self.assertRaises(StorageError) as ctx:
                move_repo_regular_file(repo, source, destination, replace=True)
            self.assertEqual(ctx.exception.code, "unsafe_link_count")
            self.assertEqual(outside_target.read_text(encoding="utf-8"), original)
            self.assertEqual(source.read_text(encoding="utf-8"), "do-not-move\n")

    def test_ancestor_swap_after_dirfd_open_fails_before_tail_or_move(self) -> None:
        for operation in ("tail", "move"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                repo = base / "repo"
                outside = base / "outside"
                repo.mkdir()
                outside.mkdir()
                live = ensure_private_dir(repo / "runtime" / "live", repo_root=repo)
                source = live / "source.log"
                source.write_text("inside-source\n", encoding="utf-8")
                destination_dir = ensure_private_dir(
                    repo / "runtime" / "archive",
                    repo_root=repo,
                )
                destination = destination_dir / "source.log"
                sentinel = outside / "sentinel.txt"
                sentinel.write_text("outside-sentinel\n", encoding="utf-8")
                displaced = outside / "displaced-live"
                original_open = storage_module._open_repo_directory
                swapped = False

                def swap_after_open(repo_root, path, *, create, mode=0o700):
                    nonlocal swapped
                    result = original_open(repo_root, path, create=create, mode=mode)
                    if not swapped and Path(path) == live:
                        swapped = True
                        live.rename(displaced)
                        live.symlink_to(outside, target_is_directory=True)
                    return result

                with mock.patch.object(
                    storage_module,
                    "_open_repo_directory",
                    side_effect=swap_after_open,
                ), self.assertRaises(StorageError) as ctx:
                    if operation == "tail":
                        read_repo_text_tail(repo, source)
                    else:
                        move_repo_regular_file(repo, source, destination)

                self.assertIn(
                    ctx.exception.code,
                    {"symlink_component", "directory_identity_changed"},
                )
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside-sentinel\n")
                self.assertEqual(
                    (displaced / "source.log").read_text(encoding="utf-8"),
                    "inside-source\n",
                )
                self.assertFalse(destination.exists())

    def test_repo_text_open_supports_private_write_append_read_and_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            outside = base / "outside"
            repo.mkdir()
            outside.mkdir()
            text_path = repo / ".elves" / "runtime" / "full-run" / "events.jsonl"
            with self.assertRaises(StorageError) as ctx:
                with open_repo_text(repo, text_path, mode="r+"):
                    pass
            self.assertEqual(ctx.exception.code, "invalid_open_mode")
            with self.assertRaises(StorageError) as ctx:
                with open_repo_text(repo, text_path, mode="w", permissions=0o1777):
                    pass
            self.assertEqual(ctx.exception.code, "invalid_permissions")
            with open_repo_text(repo, text_path, mode="w") as handle:
                handle.write("one\n")
            with open_repo_text(repo, text_path, mode="a") as handle:
                handle.write("two\n")
            with open_repo_text(repo, text_path, mode="r") as handle:
                self.assertEqual(handle.read(), "one\ntwo\n")
            self.assertEqual(stat.S_IMODE(text_path.stat().st_mode), 0o600)

            target = outside / "events.jsonl"
            original = "outside-sentinel\n"
            target.write_text(original, encoding="utf-8")
            text_path.unlink()
            text_path.symlink_to(target)
            for mode in ("r", "a", "w"):
                with self.subTest(mode=mode), self.assertRaises(StorageError):
                    with open_repo_text(repo, text_path, mode=mode):
                        self.fail("unsafe text leaf unexpectedly opened")
            self.assertEqual(target.read_text(encoding="utf-8"), original)

            text_path.unlink()
            os.link(target, text_path)
            for mode in ("r", "a", "w"):
                with self.subTest(hardlink_mode=mode), self.assertRaises(StorageError) as ctx:
                    with open_repo_text(repo, text_path, mode=mode):
                        self.fail("hardlinked text leaf unexpectedly opened")
                self.assertEqual(ctx.exception.code, "unsafe_link_count")
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_directory_lock_retries_eintr_and_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            flock = mock.Mock(
                side_effect=[OSError(errno.EINTR, "interrupted"), None, None]
            )
            with mock.patch.object(storage_module.fcntl, "flock", flock):
                with directory_lock(Path(tmp)):
                    pass
            self.assertEqual(flock.call_count, 3)

    def test_directory_lock_times_out_contention_and_fails_other_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                storage_module.fcntl,
                "flock",
                side_effect=BlockingIOError(errno.EAGAIN, "busy"),
            ):
                with self.assertRaises(StorageError) as ctx:
                    with directory_lock(Path(tmp), timeout=0):
                        pass
                self.assertEqual(ctx.exception.code, "lock_timeout")
            with mock.patch.object(
                storage_module.fcntl,
                "flock",
                side_effect=OSError(errno.EIO, "io failure"),
            ):
                with self.assertRaises(StorageError) as ctx:
                    with directory_lock(Path(tmp)):
                        pass
                self.assertEqual(ctx.exception.code, "lock_failed")
            with mock.patch.object(
                storage_module.fcntl,
                "flock",
                side_effect=[None, OSError(errno.EIO, "unlock failure")],
            ):
                with self.assertRaises(StorageError) as ctx:
                    with directory_lock(Path(tmp)):
                        pass
                self.assertEqual(ctx.exception.code, "lock_release_failed")

    def test_directory_lock_requires_unix_fcntl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            storage_module, "fcntl", None
        ):
            with self.assertRaises(StorageError) as ctx:
                with directory_lock(Path(tmp)):
                    pass
            self.assertEqual(ctx.exception.code, "lock_unsupported")

    def test_digest_keys_avoid_collision_and_traversal(self) -> None:
        a = digest_key("../etc/passwd")
        b = digest_key(".._etc_passwd")
        c = digest_key("../etc/passwd")
        self.assertEqual(a, c)
        self.assertNotEqual(a, b)
        self.assertNotIn("..", record_filename("../etc/passwd"))
        self.assertNotIn("/", record_filename("a/b/c"))

    def test_snapshot_path_stays_under_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            root.mkdir()
            path = snapshot_path(root, "../../escape", kind="sess")
            self.assertTrue(str(path).startswith(str(root.resolve())))
            self.assertIn("snapshots", path.parts)

    def test_session_embedded_id_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            reg = SessionRegistry(repo)
            rec = reg.create(
                session_id="real-id-1",
                harness="grok-build",
                profile="grok-build",
                role="implement",
            )
            path = reg._record_path("real-id-1")
            data = json.loads(path.read_text())
            data["session_id"] = "other-id"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValidationIssue) as ctx:
                reg.get("real-id-1")
            self.assertEqual(ctx.exception.code, "session_embedded_id_mismatch")

    def test_readonly_list_does_not_create_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "empty-repo"
            repo.mkdir()
            reg = SessionRegistry.open_readonly(repo)
            self.assertEqual(reg.list_sessions(), [])
            self.assertFalse((repo / ".elves" / "runtime" / "sessions").exists())

    def test_qualification_fail_closed_and_success(self) -> None:
        ok, reasons = qualify_write_evidence(None)
        self.assertFalse(ok)
        ok, reasons = qualify_write_evidence(
            {
                "adapter": "grok-build",
                "model": "grok-4.5",
                "profile": "grok-build",
                "version": "0.2.93",
                "sandbox": "workspace",
                "worktree": "/wt",
                "cwd": "/wt",
                "parent": "p1",
                "source_head": "abc",
                "capabilities": {"write": True},
                "evidence_kind": "host_observed",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "host_observed": True,
            }
        )
        self.assertFalse(ok)
        self.assertTrue(any("unsupported_sandbox" in r for r in reasons))

        ok, reasons = qualify_write_evidence(
            {
                "adapter": "grok-build",
                "model": "grok-4.5",
                "profile": "grok-build",
                "version": "0.2.93",
                "sandbox": "devbox",
                "worktree": "/wt",
                "cwd": "/wt",
                "parent": "p1",
                "source_head": "abc",
                "session_id": "sess-1",
                "capabilities": {"write": True},
                "evidence_kind": "host_observed",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "host_observed": True,
            }
        )
        self.assertTrue(ok, reasons)

    def test_malformed_lease_blocks_exclusivity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            store = LeaseStore(repo)
            bad = store.root / "broken.json"
            bad.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(ValidationIssue) as ctx:
                store.list_leases_strict()
            self.assertEqual(ctx.exception.code, "lease_record_malformed")

    def test_concurrent_saves_leave_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            reg = SessionRegistry(repo)
            errors: list[BaseException] = []

            def worker(i: int) -> None:
                try:
                    reg.create(
                        session_id=f"sess-concurrent-{i}",
                        harness="grok-build",
                        profile="p",
                        role="implement",
                    )
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            records = reg.list_sessions_strict()
            self.assertEqual(len(records), 8)
            for rec in records:
                path = reg._record_path(rec.session_id)
                mode = path.stat().st_mode & 0o777
                self.assertEqual(mode & 0o077, 0)


class IsolationAndRedactionTests(unittest.TestCase):
    def test_hostile_fixture_cannot_read_secrets_or_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            # Minimal git repo with tracked + ignored secrets.
            os.system(f"git -C {repo} init -q")
            os.system(f"git -C {repo} config user.email t@t")
            os.system(f"git -C {repo} config user.name t")
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("print('ok')\n")
            (repo / "AGENTS.md").write_text("SECRET_INSTRUCTION\n")
            (repo / ".env").write_text("SECRET_SENTINEL=super-secret-value\n")
            (repo / ".env").chmod(0o600)
            (repo / ".elves").mkdir()
            (repo / ".elves" / "models.toml").write_text('token="SECRET"\n')
            os.system(f"git -C {repo} add src/app.py AGENTS.md")
            os.system(f"git -C {repo} commit -q -m init")

            host_home = Path(tmp) / "host-home"
            host_home.mkdir()
            (host_home / ".secret").write_text("HOST_HOME_SENTINEL\n")

            with isolated_lane(
                IsolationSpec(
                    repo_root=repo,
                    lane_id="lens1",
                    include_instructions_as_data=True,
                    credential_grants={"ALLOWED_KEY": "allowed-only"},
                )
            ) as lane:
                snap = Path(lane.env["ELVES_ISOLATED_SNAPSHOT"])
                self.assertTrue((snap / "src" / "app.py").is_file())
                self.assertFalse((snap / ".env").exists())
                self.assertFalse((snap / "AGENTS.md").exists())
                self.assertTrue(any("AGENTS" in p for p in lane.instruction_data_files))
                # Host home not mounted.
                self.assertNotEqual(lane.env["HOME"], str(host_home))
                self.assertFalse((Path(lane.env["HOME"]) / ".secret").exists())
                # Unrelated secrets absent.
                leaks = assert_no_host_secrets(
                    lane.env,
                    forbidden_keys=["SECRET_SENTINEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
                )
                self.assertEqual(leaks, [])
                self.assertIn("ALLOWED_KEY", lane.env)
            # Cleanup on exit.
            self.assertFalse(lane.root.exists())

    def test_implement_min_env_grants_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "wt"
            home = Path(tmp) / "home"
            scratch = Path(tmp) / "scratch"
            wt.mkdir()
            env = implement_min_env(
                adapter="grok-build",
                worktree=wt,
                credential_grants={"XAI_API_KEY": "grant-only"},
                home=home,
                tmp=scratch,
            )
            self.assertEqual(env.get("XAI_API_KEY"), "grant-only")
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_managed_implement_env_preserves_caller_owned_directories(self) -> None:
        from cobbler_runtime.isolation import _managed_implement_env

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            caller_home = root / "caller-home"
            caller_tmp = root / "caller-tmp"
            worktree.mkdir()
            caller_home.mkdir()
            caller_tmp.mkdir()
            (caller_home / "keep.txt").write_text("home\n", encoding="utf-8")
            (caller_tmp / "keep.txt").write_text("tmp\n", encoding="utf-8")

            with _managed_implement_env(
                adapter="grok-build",
                worktree=worktree,
                home=caller_home,
                tmp=caller_tmp,
            ) as env:
                self.assertEqual(env["HOME"], str(caller_home))
                self.assertEqual(env["TMPDIR"], str(caller_tmp))

            self.assertEqual(
                (caller_home / "keep.txt").read_text(encoding="utf-8"), "home\n"
            )
            self.assertEqual(
                (caller_tmp / "keep.txt").read_text(encoding="utf-8"), "tmp\n"
            )

    def test_redaction_removes_secret_sentinels(self) -> None:
        secret = "sk-test-SECRET_SENTINEL_12345"
        text = f"Authorization: Bearer {secret} and token={secret}"
        redacted = redact_text(text, exact_values={secret}).text
        self.assertNotIn(secret, redacted)
        payload = {"token": secret, "nested": {"url": f"https://x?key={secret}"}}
        clean = redact_structure(payload, exact_values={secret})
        blob = json.dumps(clean)
        self.assertNotIn(secret, blob)


class DelegatedGitAndAcceptanceTests(unittest.TestCase):
    def test_protected_actions_fail_closed(self) -> None:
        contract = DelegatedGitContract(
            feature_branch="feat/x",
            base_branch="main",
            start_head="abc",
            session_id="s",
            run_id="r",
        )
        with self.assertRaises(ValidationIssue):
            assert_action_allowed(contract, "merge")
        with self.assertRaises(ValidationIssue):
            assert_action_allowed(contract, "force_push")
        assert_action_allowed(contract, "commit")

    def test_rollback_refs_are_run_scoped_and_distinct(self) -> None:
        a = rollback_ref_name(run_id="run-1", session_id="sess-a", batch=1)
        b = rollback_ref_name(run_id="run-2", session_id="sess-a", batch=1)
        self.assertNotEqual(a, b)
        self.assertIn("refs/elves/rollback/", a)
        self.assertNotEqual(a, "elves/pre-batch-1")

    def test_create_rollback_ref_creates_local_ref_before_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            remote = root / "remote.git"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
            (repo / "f.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)

            result = create_rollback_ref(
                repo,
                run_id="run/with/slashes",
                session_id="session-1",
                batch=3,
                push_remote="origin",
            )
            local = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", result["ref"]],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            remote_tip = subprocess.run(
                ["git", f"--git-dir={remote}", "rev-parse", result["ref"]],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(local, result["head"])
            self.assertEqual(remote_tip, result["head"])
            self.assertTrue(result["pushed"])
            self.assertTrue(result["local_ref_created"])

            repeated = create_rollback_ref(
                repo,
                run_id="run/with/slashes",
                session_id="session-1",
                batch=3,
                push_remote="origin",
            )
            self.assertTrue(repeated["idempotent"])
            self.assertTrue(repeated["remote_idempotent"])
            self.assertFalse(repeated["local_ref_created"])
            self.assertFalse(repeated["pushed"])

            (repo / "f.txt").write_text("new tip\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "new tip"],
                check=True,
            )
            with self.assertRaises(ValidationIssue) as ctx:
                create_rollback_ref(
                    repo,
                    run_id="run/with/slashes",
                    session_id="session-1",
                    batch=3,
                    push_remote="origin",
                )
            self.assertEqual(ctx.exception.code, "delegated_git_rollback_ref_collision")
            unchanged = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", result["ref"]],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(unchanged, result["head"])

    def test_report_reconciliation_preserves_host_controls(self) -> None:
        host = {
            "merge_on_green": False,
            "stop_allowed": False,
            "run_mode": "finite",
            "pr_number": 67,
            "batches": [],
        }
        worker = {
            "session_id": "sess-1",
            "branch": "feat/x",
            "start_head": "aaa",
            "final_head": "bbb",
            "status": "complete",
            "batches": [{"id": 1, "status": "complete"}],
            "merge_on_green": True,  # hostile rewrite attempt
            "stop_allowed": True,
        }
        merged = reconcile_worker_report(
            host,
            worker,
            expected_session_id="sess-1",
            expected_branch="feat/x",
            expected_start_head="aaa",
        )
        self.assertFalse(merged["merge_on_green"])
        self.assertFalse(merged["stop_allowed"])
        self.assertEqual(merged["pr_number"], 67)
        self.assertEqual(merged["final_head"], "bbb")
        with self.assertRaises(ValidationIssue):
            reconcile_worker_report(
                host,
                {**worker, "session_id": "other"},
                expected_session_id="sess-1",
                expected_branch="feat/x",
            )

    def test_acceptance_mapping_one_to_one(self) -> None:
        plan = """
### Acceptance
- [ ] B1-A1 — Fresh bundles work
- [ ] B1-A2 — Recursive package ships
- [ ] M-A1 — Trusted full-run parks driver
"""
        items = parse_plan_acceptance(plan)
        self.assertEqual(len(items), 3)
        evidence = [
            {
                "id": "B1-A1",
                "criterion": "Fresh bundles work",
                "met": True,
                "evidence": "smoke ok",
            },
            {
                "id": "B1-A2",
                "criterion": "Recursive package ships",
                "met": True,
                "evidence": "nested module present",
            },
            {
                "id": "M-A1",
                "criterion": "Trusted full-run parks driver",
                "met": True,
                "evidence": "parked-monitor",
            },
        ]
        self.assertEqual(validate_acceptance_mapping(items, evidence), [])

        # Unrelated green evidence fails.
        bad = evidence + [
            {"id": "tests-green", "criterion": "tests green", "met": True, "evidence": "ok"}
        ]
        errors = validate_acceptance_mapping(items, bad)
        self.assertTrue(any("unrelated" in e for e in errors))

        # Swapped criterion text fails.
        swapped = [
            {**evidence[0], "criterion": "wrong text"},
            evidence[1],
            evidence[2],
        ]
        errors = validate_acceptance_mapping(items, swapped)
        self.assertTrue(any("mismatch" in e for e in errors))

        with self.assertRaises(ValidationIssue):
            parse_plan_acceptance("no acceptance here")

    def test_bare_remote_feature_branch_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "remote.git"
            work = Path(tmp) / "work"
            os.system(f"git init --bare -q {bare}")
            os.system(f"git clone -q {bare} {work}")
            os.system(f"git -C {work} config user.email t@t")
            os.system(f"git -C {work} config user.name t")
            (work / "f.txt").write_text("1\n")
            os.system(f"git -C {work} add f.txt && git -C {work} commit -q -m init")
            os.system(f"git -C {work} branch -M main")
            os.system(f"git -C {work} push -q -u origin main")
            os.system(f"git -C {work} checkout -q -b feat/worker")
            start = subprocess.run(
                ["git", "-C", str(work), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (work / "f.txt").write_text("2\n")
            os.system(f"git -C {work} add f.txt && git -C {work} commit -q -m w1")
            (work / "f.txt").write_text("3\n")
            os.system(f"git -C {work} add f.txt && git -C {work} commit -q -m w2")
            tip = subprocess.run(
                ["git", "-C", str(work), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            assert_descendant(work, ancestor=start, head=tip)
            contract = DelegatedGitContract(
                feature_branch="feat/worker",
                base_branch="main",
                start_head=start,
                session_id="s",
                run_id="r",
            )
            from cobbler_runtime.delegated_git import push_feature_branch  # noqa: PLC0415

            result = push_feature_branch(work, contract, previous_tip=start)
            self.assertTrue(result["ok"])
            # Base branch on remote still main tip = start
            remote_main = subprocess.run(
                ["git", f"--git-dir={bare}", "rev-parse", "refs/heads/main"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            remote_feat = subprocess.run(
                ["git", f"--git-dir={bare}", "rev-parse", "refs/heads/feat/worker"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(remote_main, start)
            self.assertEqual(remote_feat, tip)
            # Non-descendant fails.
            with self.assertRaises(ValidationIssue):
                assert_descendant(work, ancestor=tip, head=start)
            # Protected merge fails.
            with self.assertRaises(ValidationIssue):
                assert_action_allowed(contract, "merge")


if __name__ == "__main__":
    unittest.main()
