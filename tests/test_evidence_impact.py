"""Risk-directed proof and convergent review tests (B3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.evidence_review import (  # noqa: E402
    admit_new_rereview_blocker,
    build_impact_path,
    can_reuse_evidence,
    cleanup_only_preserves_product_proof,
    consolidate_findings,
    plan_cumulative_review,
    plan_delta_rereview,
    plan_review,
    record_evidence,
    should_stop_review_loop,
)
from cobbler_runtime.risk_policy import classify_risk_tier  # noqa: E402


class ImpactPathTests(unittest.TestCase):
    def test_changed_surface_to_consumer_to_test(self) -> None:
        path = build_impact_path(
            ["scripts/cobbler_runtime/landing_authority.py"]
        )
        self.assertEqual(len(path), 1)
        row = path[0]
        self.assertEqual(
            row["changed_surface"],
            "scripts/cobbler_runtime/landing_authority.py",
        )
        self.assertTrue(row["affected_consumers"])
        self.assertTrue(row["selected_tests"])
        plan = plan_review(
            changed_paths=["scripts/cobbler_runtime/landing_authority.py"]
        )
        self.assertTrue(plan.impact_path)
        self.assertIn("impact_path_applied", plan.reasons)
        self.assertFalse(plan.broad_gate_required)

    def test_evidence_records_inputs_and_reuse(self) -> None:
        rec = record_evidence(
            gate_id="unit:runtime",
            status="pass",
            changed_paths=["scripts/cobbler_runtime/risk_policy.py"],
            selected_tests=["tests/test_faster_goal_runs_policy.py"],
            head="abc",
        )
        self.assertTrue(rec.inputs_digest)
        self.assertTrue(rec.invalidation_scope)
        hit = can_reuse_evidence(rec, current_digest=rec.inputs_digest)
        self.assertTrue(hit["reuse"])
        miss = can_reuse_evidence(rec, current_digest="other")
        self.assertFalse(miss["reuse"])

    def test_cleanup_only_preserves_product_proof(self) -> None:
        self.assertTrue(
            cleanup_only_preserves_product_proof(
                [".elves-session.json", "docs/elves/log.md"],
                recorded_operational_paths=[
                    ".elves-session.json",
                    "docs/elves/log.md",
                ],
            )
        )
        self.assertFalse(
            cleanup_only_preserves_product_proof(
                ["scripts/x.py"],
                recorded_operational_paths=[".elves-session.json"],
            )
        )

    def test_cumulative_and_delta_rereview(self) -> None:
        cum = plan_cumulative_review(target_sha="tip1")
        self.assertEqual(cum.mode, "cumulative")
        self.assertIn("completeness_vs_plan_acceptance", cum.checks)
        self.assertIn("constitution_compliance", cum.checks)

        consolidated = consolidate_findings(
            [
                {"id": "b1", "severity": "blocking", "summary": "bug"},
                {"id": "a1", "severity": "info", "summary": "nit"},
            ]
        )
        self.assertEqual(len(consolidated["blocking"]), 1)
        self.assertEqual(len(consolidated["advisory"]), 1)
        self.assertFalse(consolidated["advisory_delays_readiness"])

        # Style suggestion must not become a new blocker.
        style = admit_new_rereview_blocker(
            {"id": "s1", "category": "style", "severity": "blocking"}
        )
        self.assertFalse(style["admitted"])
        serious = admit_new_rereview_blocker(
            {
                "id": "s2",
                "category": "security",
                "severity": "blocking",
            }
        )
        self.assertTrue(serious["admitted"])

        delta = plan_delta_rereview(
            last_reviewed_sha="tip1",
            target_sha="tip2",
            unresolved_blocker_ids=["b1"],
            new_findings=[
                {
                    "id": "s1",
                    "category": "style",
                    "severity": "blocking",
                },
                {
                    "id": "reg",
                    "category": "serious_regression",
                    "severity": "blocking",
                },
            ],
            revision_paths=["scripts/cobbler_runtime/landing_authority.py"],
        )
        self.assertEqual(delta.mode, "delta_rereview")
        self.assertIn("reg", delta.blocking)
        self.assertIn("s1", delta.advisory)
        self.assertIn("no_rescan_of_settled_untouched_work", delta.reasons)

        done = plan_delta_rereview(
            last_reviewed_sha="tip2",
            target_sha="tip2",
            unresolved_blocker_ids=[],
            new_findings=[],
        )
        self.assertTrue(done.stop)
        self.assertEqual(done.mode, "stop")

    def test_stop_on_sufficient_evidence_not_suggestion_absence(self) -> None:
        stop = should_stop_review_loop(
            exact_tip_evidence_sufficient=True,
            unresolved_blockers=[],
            reviewer_still_has_suggestions=True,
        )
        self.assertTrue(stop["stop"])
        self.assertTrue(stop["ignored_advisory_suggestions"])
        cont = should_stop_review_loop(
            exact_tip_evidence_sufficient=True,
            unresolved_blockers=["b1"],
            reviewer_still_has_suggestions=False,
        )
        self.assertFalse(cont["stop"])

    def test_risk_axes_on_classify(self) -> None:
        decision = classify_risk_tier(
            changed_paths=["README.md"], risk="low", trust_mode="trusted"
        )
        self.assertEqual(decision.risk, "low")
        self.assertEqual(decision.trust_mode, "trusted")
        untrusted = classify_risk_tier(is_untrusted_writer=True, risk="standard")
        self.assertEqual(untrusted.trust_mode, "untrusted")
        self.assertTrue(untrusted.broad_proof_required)


if __name__ == "__main__":
    unittest.main()
