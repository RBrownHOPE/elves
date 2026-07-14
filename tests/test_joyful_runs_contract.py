"""Semantic contract tests for Elves 2.3 joyful runs (B0–B5 scenarios)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.behavior_policy import (  # noqa: E402
    FORBIDDEN_FULL_RUN_WAKE_TRIGGERS,
    PARKED_MONITOR_WAKE_CONDITIONS,
    policy_snapshot,
    resolve_land_pr_grant,
)
from cobbler_runtime.canonical_contract import (  # noqa: E402
    DRIVER_WAKE_CONDITIONS,
    MIGRATION_LEDGER,
    RISK_LEVELS,
    RUN_STATES,
    SAFETY_KERNEL,
    TERMINAL_OUTCOMES,
    TRUST_MODES,
    V2_2_NORMATIVE_REQUIREMENT_IDS,
    actor_may,
    classify_risk_and_trust,
    contract_snapshot,
    hosts_share_semantics,
    migration_ledger_snapshot,
    safety_kernel_snapshot,
    transition_allowed,
)


class CanonicalContractTests(unittest.TestCase):
    def test_normal_flow_defined_once(self) -> None:
        snap = contract_snapshot()
        self.assertEqual(snap["policy_version"], "2.3.0")
        self.assertEqual(
            list(RUN_STATES),
            [
                "staging",
                "executing",
                "reconciling",
                "reviewing",
                "revising",
                "ready",
                "terminal",
            ],
        )
        self.assertTrue(transition_allowed("staging", "executing"))
        self.assertTrue(transition_allowed("reviewing", "revising"))
        self.assertTrue(transition_allowed("revising", "reviewing"))
        self.assertFalse(transition_allowed("ready", "executing"))
        self.assertIn("landable_pr", TERMINAL_OUTCOMES)
        self.assertIn("complete_and_merge", TERMINAL_OUTCOMES)
        self.assertIn("worker_death", DRIVER_WAKE_CONDITIONS)
        self.assertIn("material_scope_or_assumption_change", DRIVER_WAKE_CONDITIONS)
        self.assertIn("timed_chat_update", FORBIDDEN_FULL_RUN_WAKE_TRIGGERS)
        self.assertIn("impact_path", snap["proof_rules"])
        for invariant in (
            "ready_true_never_grants_merge_permission",
            "driver_authorized_true_never_proves_readiness",
            "merge_requires_ready_and_driver_authorized_at_same_exact_head",
            "worker_evidence_cannot_grant_merge_or_change_landing_outcome",
        ):
            self.assertIn(invariant, snap["independence_invariants"])

    def test_worker_has_no_merge_or_landing_authority(self) -> None:
        self.assertFalse(actor_may("worker", "perform_merge"))
        self.assertFalse(actor_may("worker", "modify_landing_outcome"))
        self.assertFalse(actor_may("worker", "grant_driver_merge_authorization"))
        self.assertFalse(actor_may("worker", "attest_readiness"))
        self.assertFalse(actor_may("worker", "modify_protected_refs"))
        self.assertTrue(actor_may("driver", "attest_readiness"))
        self.assertTrue(actor_may("user", "grant_driver_merge_authorization"))

    def test_trust_mode_narrows_worker_git_authority(self) -> None:
        self.assertTrue(
            actor_may("worker", "commit_feature_branch", trust_mode="trusted")
        )
        self.assertTrue(
            actor_may("worker", "push_feature_branch", trust_mode="trusted")
        )
        self.assertFalse(
            actor_may("worker", "commit_feature_branch", trust_mode="untrusted")
        )
        self.assertFalse(
            actor_may("worker", "push_feature_branch", trust_mode="untrusted")
        )
        self.assertTrue(
            actor_may("worker", "edit_product_code", trust_mode="untrusted")
        )

    def test_safety_kernel_has_destination_and_proof(self) -> None:
        snap = safety_kernel_snapshot()
        self.assertEqual(len(SAFETY_KERNEL), 6)
        self.assertEqual(len(snap["safety_kernel"]), 6)
        for item in SAFETY_KERNEL:
            self.assertTrue(item.destinations, msg=item.id)
            self.assertTrue(item.proving_tests, msg=item.id)
            for dest in item.destinations:
                # Destinations are path-like references, not empty strings.
                self.assertTrue(len(dest) > 3)

    def test_risk_and_trust_independent(self) -> None:
        self.assertEqual(RISK_LEVELS, ("low", "standard", "high"))
        self.assertEqual(TRUST_MODES, ("trusted", "untrusted"))
        # high risk trusted is not untrusted
        hi_trust = classify_risk_and_trust(risk="high", trust_mode="trusted")
        self.assertEqual(hi_trust["risk"], "high")
        self.assertEqual(hi_trust["trust_mode"], "trusted")
        self.assertEqual(hi_trust["legacy_tier"], "high_risk_trusted")
        # untrusted can pair with any risk mapping to legacy untrusted
        low_un = classify_risk_and_trust(risk="low", trust_mode="untrusted")
        self.assertEqual(low_un["risk"], "low")
        self.assertEqual(low_un["trust_mode"], "untrusted")
        self.assertEqual(low_un["legacy_tier"], "untrusted")
        # legacy map round-trip
        for legacy, (risk, trust) in {
            "trivial_docs": ("low", "trusted"),
            "standard_trusted": ("standard", "trusted"),
            "high_risk_trusted": ("high", "trusted"),
            "untrusted": ("high", "untrusted"),
        }.items():
            got = classify_risk_and_trust(legacy_tier=legacy)
            self.assertEqual(got["risk"], risk)
            self.assertEqual(got["trust_mode"], trust)

    def test_host_parity_claude_and_codex(self) -> None:
        parity = hosts_share_semantics()
        self.assertTrue(parity["workflow_semantics_identical"])
        self.assertFalse(parity["optional_providers_required"])
        self.assertFalse(parity["grok_required"])
        self.assertEqual(parity["canonical_docs"]["workflow"], "SKILL.md")
        self.assertEqual(parity["canonical_docs"]["codex_adapter"], "AGENTS.md")

    def test_migration_ledger_covers_dispositions(self) -> None:
        snap = migration_ledger_snapshot()
        self.assertGreaterEqual(snap["counts"]["retained"], 5)
        self.assertGreaterEqual(snap["counts"]["changed"], 3)
        self.assertGreaterEqual(snap["counts"]["retired"], 1)
        ids = {e.id for e in MIGRATION_LEDGER}
        self.assertIn("four-risk-tiers", ids)
        self.assertIn("default-follow-stream", ids)
        self.assertIn("merge-authority-false", ids)
        for entry in MIGRATION_LEDGER:
            self.assertIn(entry.disposition, {"retained", "changed", "retired"})
            self.assertTrue(entry.new_location)
            self.assertTrue(entry.proof)
        self.assertEqual(
            set(V2_2_NORMATIVE_REQUIREMENT_IDS),
            ids,
            "every inventoried v2.2 normative cluster must have a disposition",
        )
        self.assertGreaterEqual(len(ids), 30)


class BehaviorPolicyJoyfulTests(unittest.TestCase):
    def test_wake_and_follow_policy(self) -> None:
        snap = policy_snapshot()
        self.assertEqual(snap["policy_version"], "2.3.0")
        self.assertIn("worker_death", PARKED_MONITOR_WAKE_CONDITIONS)
        self.assertIn("timed_chat_update", FORBIDDEN_FULL_RUN_WAKE_TRIGGERS)
        self.assertTrue(snap["follow_mode"]["default"])
        self.assertFalse(snap["follow_mode"]["model_inference"])
        self.assertTrue(snap["follow_mode"]["replaces_timed_chat_updates"])

    def test_land_pr_grant_does_not_restart_readiness(self) -> None:
        grant = resolve_land_pr_grant(active_run=True)
        self.assertTrue(grant["grants_driver_authorized"])
        self.assertFalse(grant["sets_ready"])
        self.assertFalse(grant["restarts_readiness"])
        self.assertEqual(grant["landing_outcome"], "complete_and_merge")


if __name__ == "__main__":
    unittest.main()
