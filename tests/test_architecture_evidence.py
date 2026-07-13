"""Batch 6: attempt components, preflight cache, evidence review, public API gate."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.dispatch_attempt import (  # noqa: E402
    build_effective_contract,
    classify_failure,
    prepare_transport,
    record_command_digests,
)
from cobbler_runtime.evidence_review import plan_review  # noqa: E402
from cobbler_runtime.preflight_cache import (  # noqa: E402
    compute_preflight_key,
    record_passing_preflight,
    reuse_preflight,
)
from cobbler_runtime.public_api_snapshot import (  # noqa: E402
    capture_snapshot,
    compatibility_gate,
    diff_snapshots,
)
from cobbler_runtime.schema import EffectiveAttempt  # noqa: E402
from cobbler_runtime.adapters import AdapterInvocation  # noqa: E402


class DispatchAttemptComponentTests(unittest.TestCase):
    def test_prepare_transport_scrubs_secrets(self) -> None:
        transport = prepare_transport(
            parent_env={
                "PATH": "/bin",
                "OPENAI_API_KEY": "secret-value",
                "ALLOWED": "yes",
            },
            env_extra_allowlist=("ALLOWED",),
            grants=(),
        )
        self.assertNotIn("OPENAI_API_KEY", transport.scrub.env)
        self.assertIn("PATH", transport.scrub.env)

    def test_effective_contract_and_command_digests(self) -> None:
        attempt = EffectiveAttempt(
            profile="grok-build",
            adapter="grok-build",
            executable="grok",
            requested_model="grok-4.5",
            extra_args=(),
            input_contract="prompt-file",
            output_contract="grok-json",
            capabilities=("read",),
            reason="test",
            required=True,
            enabled=True,
            source="test",
        )
        contract = build_effective_contract(
            attempt,
            grants=(),
            repo_root=REPO_ROOT,
            exact_secret_values=frozenset(),
            qualified_capabilities=(),
        )
        self.assertEqual(contract["adapter"], "grok-build")
        inv = AdapterInvocation(
            adapter="grok-build",
            executable="grok",
            argv=("grok", "--help"),
            decoder="grok-json",
            input_mode="none",
        )
        redacted = record_command_digests(
            contract,
            raw_command=["grok", "--help"],
            exact_secret_values=frozenset(),
            invocation=inv,
        )
        self.assertEqual(redacted, ["grok", "--help"])
        self.assertIn("argv_digest", contract)

    def test_classify_failure_categories(self) -> None:
        self.assertEqual(classify_failure(timeout=True, exit_code=None, error=""), "timeout")
        self.assertEqual(
            classify_failure(timeout=False, exit_code=127, error="executable not found"),
            "launch_error",
        )


class PreflightCacheTests(unittest.TestCase):
    def test_reuse_and_invalidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Seed files used in default key.
            (repo / "SKILL.md").write_text("v\n")
            (repo / "AGENTS.md").write_text("v\n")
            (repo / "config.json.example").write_text("{}\n")
            (repo / "scripts").mkdir()
            (repo / "scripts" / "verify_repo.py").write_text("x\n")
            (repo / "scripts" / "check_repo_consistency.py").write_text("x\n")
            head = "abc123"
            record_passing_preflight(repo, head=head, gates={"unit": "ok"})
            decision = reuse_preflight(repo, head=head)
            self.assertTrue(decision["reuse"])
            self.assertFalse(decision["final_readiness_accepts_cache_alone"])
            # Config change invalidates.
            (repo / "SKILL.md").write_text("changed\n")
            decision2 = reuse_preflight(repo, head=head)
            self.assertFalse(decision2["reuse"])
            self.assertEqual(decision2["reason"], "key_mismatch")
            # Head change invalidates even if we re-record wrong.
            record_passing_preflight(repo, head="newhead")
            decision3 = reuse_preflight(repo, head=head)
            self.assertFalse(decision3["reuse"])


class EvidenceReviewTests(unittest.TestCase):
    def test_deterministic_selection_and_escalation(self) -> None:
        plan = plan_review(
            changed_paths=["scripts/cobbler_runtime/leases.py", "tests/test_x.py"]
        )
        self.assertIn("unit:runtime", plan.focused_checks)
        self.assertTrue(plan.reasons)
        self.assertIn(plan.risk_level, {"medium", "high"})

        final = plan_review(changed_paths=["README.md"], is_final_readiness=True)
        self.assertTrue(final.broad_gate_required)
        self.assertIn("final_readiness_requires_broad_gate", final.reasons)

        secure = plan_review(changed_paths=["scripts/cobbler_runtime/isolation.py"])
        self.assertTrue(secure.broad_gate_required)
        self.assertEqual(secure.risk_level, "high")

        # Determinism
        a = plan_review(changed_paths=["a.py", "b.py"]).to_dict()
        b = plan_review(changed_paths=["a.py", "b.py"]).to_dict()
        self.assertEqual(a, b)


class PublicApiSnapshotTests(unittest.TestCase):
    def test_capture_and_compat_gate(self) -> None:
        snap = capture_snapshot(REPO_ROOT)
        self.assertIn(snap.status, {"captured", "unavailable"})
        if snap.status == "captured":
            self.assertGreater(len(snap.entries), 0)
            self.assertEqual(snap.digest(), snap.digest())

        with tempfile.TemporaryDirectory() as tmp:
            # Use a tiny synthetic repo with export surface.
            root = Path(tmp)
            (root / "scripts" / "cobbler_runtime").mkdir(parents=True)
            (root / "scripts" / "cobbler_runtime" / "__init__.py").write_text(
                '__all__ = ["ValidationIssue", "RoleName"]\n'
            )
            (root / "scripts" / "cobbler_agents.py").write_text(
                'sub.add_parser("doctor")\nsub.add_parser("implement")\n'
            )
            first = compatibility_gate(root, required=False)
            self.assertTrue(first["ok"])
            # Internal-only: no public change.
            second = compatibility_gate(root, required=True)
            self.assertTrue(second["ok"], second)
            # Breaking: remove an export.
            (root / "scripts" / "cobbler_runtime" / "__init__.py").write_text(
                '__all__ = ["ValidationIssue"]\n'
            )
            third = compatibility_gate(root, required=True)
            self.assertFalse(third["ok"])
            self.assertTrue(third["breaking"])


if __name__ == "__main__":
    unittest.main()
