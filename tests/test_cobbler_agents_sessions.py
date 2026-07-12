from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _ensure_import_path() -> None:
    scripts = str(SCRIPTS)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


_ensure_import_path()

from cobbler_runtime.adapters import (  # noqa: E402
    AMBIGUOUS_SESSION_FLAG_PATTERNS,
    assert_no_ambiguous_session_flags,
    build_session_create_invocation,
    build_session_resume_invocation,
)
from cobbler_runtime.schema import ValidationIssue  # noqa: E402
from cobbler_runtime.sessions import (  # noqa: E402
    CreationMethod,
    SessionLifecycle,
    SessionRegistry,
    assert_grok_worktree_isolation,
    compute_context_digest,
    evaluate_session_continuity,
    grok_headless_worktree_resume_supported,
    parse_grok_child_summary,
    parse_usage_payload,
    register_grok_child,
    transition_lifecycle,
)


class LifecycleTests(unittest.TestCase):
    def test_valid_and_invalid_transitions(self) -> None:
        self.assertEqual(
            transition_lifecycle(SessionLifecycle.NEW, SessionLifecycle.ACTIVE),
            SessionLifecycle.ACTIVE,
        )
        self.assertEqual(
            transition_lifecycle(SessionLifecycle.ACTIVE, SessionLifecycle.REHYDRATION_REQUIRED),
            SessionLifecycle.REHYDRATION_REQUIRED,
        )
        with self.assertRaises(ValidationIssue) as ctx:
            transition_lifecycle(SessionLifecycle.CLOSED, SessionLifecycle.ACTIVE)
        self.assertEqual(ctx.exception.code, "invalid_lifecycle_transition")
        with self.assertRaises(ValidationIssue):
            transition_lifecycle(SessionLifecycle.DRIFTED, SessionLifecycle.ACTIVE)


class DigestAndContinuityTests(unittest.TestCase):
    def test_digest_changes_when_plan_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "plan.md"
            plan.write_text("batch 1\n", encoding="utf-8")
            d1 = compute_context_digest(
                session_id="s1",
                harness="claude-code",
                profile="claude-code",
                role="planning",
                requested_model="m",
                actual_model="m",
                parent_id=None,
                cwd=str(root),
                worktree=None,
                source_head="abc",
                plan_path=plan,
            )
            plan.write_text("batch 1\nbatch 2\n", encoding="utf-8")
            d2 = compute_context_digest(
                session_id="s1",
                harness="claude-code",
                profile="claude-code",
                role="planning",
                requested_model="m",
                actual_model="m",
                parent_id=None,
                cwd=str(root),
                worktree=None,
                source_head="abc",
                plan_path=plan,
            )
        self.assertNotEqual(d1.digest, d2.digest)
        self.assertIn("plan_sha256", d1.components)

    def test_expected_head_change_requests_rehydration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = SessionRegistry(Path(tmp))
            rec = reg.create(
                session_id="sess-1",
                harness="claude-code",
                profile="claude-code",
                role="review",
                actual_model="model-a",
                cwd=str(Path(tmp)),
                source_head="head-old",
            )
            rec = reg.activate("sess-1")
            digest = compute_context_digest(
                session_id=rec.session_id,
                harness=rec.harness,
                profile=rec.profile,
                role=rec.role,
                requested_model=rec.requested_model,
                actual_model=rec.actual_model,
                parent_id=rec.parent_id,
                cwd=rec.cwd,
                worktree=rec.worktree,
                source_head="head-new",
            )
            result = evaluate_session_continuity(
                rec,
                observed_model=rec.actual_model,
                observed_cwd=rec.cwd,
                observed_worktree=None,
                observed_parent_id=None,
                observed_head="head-new",
                current_digest=digest,
            )
        self.assertTrue(result.ok)
        self.assertTrue(result.expected_change)
        self.assertIsNotNone(result.rehydration)
        self.assertFalse(result.write_reuse_blocked)

    def test_model_or_cwd_drift_blocks_write_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = SessionRegistry(Path(tmp))
            rec = reg.create(
                session_id="sess-2",
                harness="grok-build",
                profile="grok-build",
                role="implement",
                actual_model="grok-model",
                cwd="/work/a",
                source_head="h1",
            )
            digest = compute_context_digest(
                session_id=rec.session_id,
                harness=rec.harness,
                profile=rec.profile,
                role=rec.role,
                requested_model=None,
                actual_model="other-model",
                parent_id=None,
                cwd="/work/b",
                worktree=None,
                source_head="h1",
            )
            result = evaluate_session_continuity(
                rec,
                observed_model="other-model",
                observed_cwd="/work/b",
                observed_worktree=None,
                observed_parent_id=None,
                observed_head="h1",
                current_digest=digest,
            )
        self.assertFalse(result.ok)
        self.assertTrue(result.write_reuse_blocked)
        self.assertTrue(any("actual_model" in r for r in result.reasons))
        self.assertTrue(any("cwd" in r for r in result.reasons))


class RegistryLifecycleTests(unittest.TestCase):
    def test_exact_create_resume_and_closed_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = SessionRegistry(Path(tmp))
            rec = reg.create(
                session_id="exact-1",
                harness="claude-code",
                profile="claude-code",
                role="planning",
                requested_model="m1",
                actual_model="m1",
                cwd=str(Path(tmp) / "wt"),
                source_head="abc",
            )
            self.assertEqual(rec.lifecycle, SessionLifecycle.NEW)
            rec = reg.activate("exact-1")
            self.assertEqual(rec.lifecycle, SessionLifecycle.ACTIVE)
            updated, drift = reg.resume_exact(
                "exact-1",
                observed_model="m1",
                observed_cwd=str(Path(tmp) / "wt"),
                observed_head="abc",
            )
            self.assertTrue(drift.ok)
            self.assertEqual(updated.resume_method, "exact_id")
            reg.close("exact-1")
            with self.assertRaises(ValidationIssue) as ctx:
                reg.resume_exact(
                    "exact-1",
                    observed_model="m1",
                    observed_cwd=str(Path(tmp) / "wt"),
                )
            self.assertEqual(ctx.exception.code, "session_closed")

    def test_missing_session_and_stale_rehydration_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = SessionRegistry(Path(tmp))
            with self.assertRaises(ValidationIssue) as ctx:
                reg.get("no-such-session")
            self.assertEqual(ctx.exception.code, "session_not_found")

            plan = Path(tmp) / "plan.md"
            plan.write_text("v1\n", encoding="utf-8")
            reg.create(
                session_id="s-rehyd",
                harness="codex-fugu",
                profile="codex-fugu",
                role="review",
                actual_model="fugu",
                cwd=str(tmp),
                source_head="h1",
                plan_path=plan,
            )
            reg.activate("s-rehyd")
            original = reg.get("s-rehyd")
            original_digest = original.context_digest
            plan.write_text("v2\n", encoding="utf-8")
            rec, drift = reg.resume_exact(
                "s-rehyd",
                observed_model="fugu",
                observed_cwd=str(tmp),
                observed_head="h1",
                plan_path=plan,
            )
            self.assertTrue(drift.expected_change)
            self.assertEqual(rec.lifecycle, SessionLifecycle.REHYDRATION_REQUIRED)
            # Active digest must stay frozen until rehydration proof.
            self.assertEqual(rec.context_digest, original_digest)
            self.assertIsNotNone(rec.pending_context_digest)
            self.assertNotEqual(rec.pending_context_digest, original_digest)

    def test_resume_cannot_activate_until_rehydration_proof_matches_pending_digest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = SessionRegistry(Path(tmp))
            plan = Path(tmp) / "plan.md"
            plan.write_text("v1\n", encoding="utf-8")
            reg.create(
                session_id="proof-1",
                harness="claude-code",
                profile="claude-code",
                role="review",
                actual_model="m",
                cwd=str(tmp),
                source_head="h1",
                plan_path=plan,
            )
            reg.activate("proof-1")
            before = reg.get("proof-1")
            plan.write_text("v2\n", encoding="utf-8")
            rec, drift = reg.resume_exact(
                "proof-1",
                observed_model="m",
                observed_cwd=str(tmp),
                observed_head="h1",
                plan_path=plan,
            )
            self.assertEqual(rec.lifecycle, SessionLifecycle.REHYDRATION_REQUIRED)
            self.assertEqual(rec.context_digest, before.context_digest)
            pending = rec.pending_context_digest
            self.assertTrue(pending)
            self.assertNotEqual(pending, before.context_digest)

            # Wrong model while rehydration is pending blocks write reuse.
            rec_bad, drift_bad = reg.resume_exact(
                "proof-1",
                observed_model="wrong-model",
                observed_cwd=str(tmp),
                observed_head="h1",
                plan_path=plan,
            )
            self.assertTrue(drift_bad.write_reuse_blocked)
            self.assertEqual(rec_bad.lifecycle, SessionLifecycle.DRIFTED)
            self.assertEqual(rec_bad.context_digest, before.context_digest)

            # Fresh session for the successful proof path.
            plan.write_text("v1\n", encoding="utf-8")
            reg.create(
                session_id="proof-2",
                harness="claude-code",
                profile="claude-code",
                role="review",
                actual_model="m",
                cwd=str(tmp),
                source_head="h1",
                plan_path=plan,
            )
            reg.activate("proof-2")
            plan.write_text("v2\n", encoding="utf-8")
            rec2, _ = reg.resume_exact(
                "proof-2",
                observed_model="m",
                observed_cwd=str(tmp),
                observed_head="h1",
                plan_path=plan,
            )
            pending2 = rec2.pending_context_digest
            self.assertEqual(rec2.lifecycle, SessionLifecycle.REHYDRATION_REQUIRED)
            # Second exact resume with matching pending digest promotes to active.
            rec3, drift3 = reg.resume_exact(
                "proof-2",
                observed_model="m",
                observed_cwd=str(tmp),
                observed_head="h1",
                plan_path=plan,
            )
            self.assertTrue(drift3.ok or drift3.expected_change)
            self.assertEqual(rec3.lifecycle, SessionLifecycle.ACTIVE)
            self.assertEqual(rec3.context_digest, pending2)
            self.assertIsNone(rec3.pending_context_digest)
            self.assertEqual(rec3.resume_method, "exact_id_rehydrated")

    def test_readonly_list_does_not_create_runtime_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reg = SessionRegistry.open_readonly(root)
            sessions = reg.list_sessions()
            self.assertEqual(sessions, [])
            self.assertFalse((root / ".elves" / "runtime" / "sessions").exists())

    def test_malformed_session_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = SessionRegistry(Path(tmp))
            reg.create(
                session_id="good",
                harness="claude-code",
                profile="claude-code",
                role="review",
                actual_model="m",
                cwd=str(tmp),
                source_head="h",
            )
            bad = reg.root / "broken.json"
            bad.write_text("{not-json", encoding="utf-8")
            records = reg.list_sessions()
            self.assertEqual(len(records), 1)
            self.assertEqual(len(reg.malformed_records), 1)
            with self.assertRaises(ValidationIssue) as ctx:
                reg.list_sessions_strict()
            self.assertEqual(ctx.exception.code, "session_record_malformed")

    def test_parent_child_validation_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = SessionRegistry(Path(tmp))
            reg.create(
                session_id="child-1",
                harness="grok-build",
                profile="grok-build",
                role="implement",
                actual_model="m",
                parent_id="parent-1",
                cwd=str(tmp),
                source_head="h",
                creation_method=CreationMethod.FORK_CHILD,
            )
            reg.activate("child-1")
            rec, drift = reg.resume_exact(
                "child-1",
                observed_model="m",
                observed_cwd=str(tmp),
                observed_parent_id="wrong-parent",
                observed_head="h",
            )
            self.assertTrue(drift.write_reuse_blocked)
            self.assertEqual(rec.lifecycle, SessionLifecycle.DRIFTED)
            self.assertTrue(rec.write_reuse_blocked)


class UsageLedgerTests(unittest.TestCase):
    def test_unknown_quota_not_zero(self) -> None:
        usage = parse_usage_payload(
            {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}
        )
        self.assertEqual(usage.input_tokens, 10)
        self.assertEqual(usage.output_tokens, 5)
        self.assertEqual(usage.total_tokens, 15)
        self.assertEqual(usage.cost_usd, 0.01)
        self.assertFalse(usage.quota_known)
        self.assertEqual(usage.to_dict()["remaining_quota"], "unknown")
        self.assertNotEqual(usage.to_dict()["remaining_quota"], 0)

    def test_quota_known_only_when_explicit(self) -> None:
        usage = parse_usage_payload(
            {"input_tokens": 1, "remaining_quota": 99, "quota_known": True}
        )
        self.assertTrue(usage.quota_known)
        self.assertEqual(usage.remaining_quota, 99)
        # Token counts alone never imply known quota.
        usage2 = parse_usage_payload({"total_tokens": 1000, "remaining_quota": 0})
        self.assertFalse(usage2.quota_known)
        self.assertEqual(usage2.remaining_quota, "unknown")


class GrokLineageTests(unittest.TestCase):
    def test_discover_and_register_child_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = SessionRegistry(Path(tmp))
            summary = parse_grok_child_summary(
                {
                    "child_id": "child-uuid-2",
                    "parent_id": "parent-uuid-1",
                    "model": "grok-model",
                    "cwd": str(Path(tmp) / "worktree"),
                    "head": "deadbeef",
                    "worktree": str(Path(tmp) / "worktree"),
                }
            )
            rec = register_grok_child(
                reg,
                summary=summary,
                profile="grok-build",
                role="implement",
                expected_parent_id="parent-uuid-1",
                expected_model="grok-model",
                expected_cwd=str(Path(tmp) / "worktree"),
                expected_head="deadbeef",
            )
            self.assertEqual(rec.session_id, "child-uuid-2")
            self.assertEqual(rec.parent_id, "parent-uuid-1")
            self.assertEqual(rec.creation_method, CreationMethod.FORK_CHILD)
            self.assertNotEqual(rec.session_id, rec.parent_id)

    def test_same_uuid_lineage_rejected(self) -> None:
        with self.assertRaises(ValidationIssue) as ctx:
            parse_grok_child_summary(
                {"child_id": "same", "parent_id": "same", "model": "m", "cwd": "/x", "head": "h"}
            )
        self.assertEqual(ctx.exception.code, "grok_lineage_same_uuid")

    def test_headless_worktree_resume_broken_on_0_2_93(self) -> None:
        self.assertFalse(grok_headless_worktree_resume_supported("0.2.93"))
        with self.assertRaises(ValidationIssue) as ctx:
            assert_grok_worktree_isolation(
                version="0.2.93",
                cwd_verified=True,
                worktree_registered=True,
                used_headless_worktree_resume=True,
            )
        self.assertEqual(ctx.exception.code, "grok_headless_worktree_resume_broken")

    def test_fail_closed_without_cwd_worktree_verification(self) -> None:
        with self.assertRaises(ValidationIssue) as ctx:
            assert_grok_worktree_isolation(
                version="0.3.0",
                cwd_verified=False,
                worktree_registered=False,
                used_headless_worktree_resume=False,
            )
        self.assertEqual(ctx.exception.code, "grok_worktree_unverified")


class SessionCommandBuilderTests(unittest.TestCase):
    def test_exact_create_and_resume_forbid_ambiguous_flags(self) -> None:
        adapters = ("claude-code", "grok-build", "codex-fugu", "custom-cli")
        forbidden_substrings = ("--continue", "--last")
        for adapter in adapters:
            with self.subTest(adapter=adapter, op="create"):
                inv = build_session_create_invocation(
                    adapter=adapter,
                    profile=adapter,
                    executable="tool" if adapter == "custom-cli" else None,
                    requested_model="example-model",
                )
                joined = " ".join(inv.argv)
                for token in forbidden_substrings:
                    self.assertNotIn(token, inv.argv)
                    self.assertNotIn(token, joined)
                assert_no_ambiguous_session_flags(inv.argv)
            with self.subTest(adapter=adapter, op="resume"):
                inv = build_session_resume_invocation(
                    adapter=adapter,
                    profile=adapter,
                    session_id="exact-session-id-123",
                    executable="tool" if adapter == "custom-cli" else None,
                    requested_model="example-model",
                    cwd="/verified/worktree",
                )
                joined = " ".join(inv.argv)
                self.assertIn("exact-session-id-123", joined)
                for token in forbidden_substrings:
                    self.assertNotIn(token, inv.argv)
                # Bare --resume without id is forbidden; exact --resume <id> is required.
                assert_no_ambiguous_session_flags(inv.argv)
                # Ensure we never emit continue/last patterns from the corpus.
                self.assertTrue(AMBIGUOUS_SESSION_FLAG_PATTERNS)

    def test_bare_resume_without_id_is_rejected(self) -> None:
        with self.assertRaises(ValidationIssue) as ctx:
            assert_no_ambiguous_session_flags(["claude", "--resume"])
        self.assertEqual(ctx.exception.code, "ambiguous_session_flag")
        with self.assertRaises(ValidationIssue):
            assert_no_ambiguous_session_flags(["claude", "--continue"])
        with self.assertRaises(ValidationIssue):
            assert_no_ambiguous_session_flags(["claude", "--last"])

    def test_resume_requires_exact_session_id(self) -> None:
        with self.assertRaises(ValidationIssue) as ctx:
            build_session_resume_invocation(
                adapter="claude-code",
                profile="claude-code",
                session_id="",
            )
        self.assertEqual(ctx.exception.code, "missing_session_id")

    def test_claude_and_fugu_preserve_exact_ids_in_argv(self) -> None:
        for adapter in ("claude-code", "codex-fugu"):
            inv = build_session_resume_invocation(
                adapter=adapter,
                profile=adapter,
                session_id="preserve-me-uuid",
                requested_model="model-x",
            )
            self.assertIn("preserve-me-uuid", inv.argv)
            self.assertIn("model-x", inv.argv)


class DoctorSessionFieldsTests(unittest.TestCase):
    def test_doctor_json_separates_discovery_and_session_fields(self) -> None:
        from cobbler_runtime.capabilities import doctor_inventory

        inv = doctor_inventory(
            profiles={
                "host-native": __import__(
                    "cobbler_runtime.adapters", fromlist=["default_profiles"]
                ).default_profiles()["host-native"],
                "grok-build": __import__(
                    "cobbler_runtime.adapters", fromlist=["default_profiles"]
                ).default_profiles()["grok-build"],
            }
        )
        self.assertIn("adapters", inv)
        grok = inv["adapters"]["grok-build"]
        for key in (
            "executable",
            "version",
            "auth",
            "discovered_models",
            "qualification_freshness",
            "session_support",
        ):
            self.assertIn(key, grok)
        self.assertIn("remaining_quota", grok)
        self.assertEqual(grok["remaining_quota"], "unknown")


if __name__ == "__main__":
    unittest.main()
