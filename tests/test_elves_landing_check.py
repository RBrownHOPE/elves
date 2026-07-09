from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "elves_landing_check.py"


def load_module():
    spec = importlib.util.spec_from_file_location("elves_landing_check_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load elves_landing_check module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ElvesLandingCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module()

    def _write_session(self, tmp: Path, payload: dict) -> Path:
        path = tmp / ".elves-session.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def test_complete_with_acceptance_passes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {
                "plan_path": "plan.md",
                "batches": [
                    {
                        "id": 1,
                        "name": "Extract facade",
                        "status": "complete",
                        "acceptance": [
                            {
                                "criterion": "gemini-service.ts under 400 LOC",
                                "met": True,
                                "evidence": "wc -l src/gemini-service.ts => 312",
                            }
                        ],
                    }
                ],
            }
            session_path = self._write_session(tmp, session)
            (tmp / "plan.md").write_text(
                "### Batch 1: Extract facade\n\n"
                "**Acceptance criteria:**\n"
                "- [x] gemini-service.ts under 400 LOC\n",
                encoding="utf-8",
            )
            code = self.mod.main(["--session", str(session_path)])
            self.assertEqual(code, 0)

    def test_complete_without_acceptance_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {
                "batches": [
                    {"id": 1, "name": "Almost", "status": "complete"},
                ]
            }
            session_path = self._write_session(tmp, session)
            code = self.mod.main(["--session", str(session_path)])
            self.assertEqual(code, 1)

    def test_acceptance_met_false_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {
                "batches": [
                    {
                        "id": 2,
                        "status": "complete",
                        "acceptance": [
                            {
                                "criterion": "facade exists",
                                "met": False,
                                "evidence": "still TODO",
                            }
                        ],
                    }
                ]
            }
            session_path = self._write_session(tmp, session)
            code = self.mod.main(["--session", str(session_path)])
            self.assertEqual(code, 1)

    def test_acceptance_missing_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {
                "batches": [
                    {
                        "id": 3,
                        "status": "complete",
                        "acceptance": [
                            {"criterion": "tests green", "met": True, "evidence": ""}
                        ],
                    }
                ]
            }
            session_path = self._write_session(tmp, session)
            code = self.mod.main(["--session", str(session_path)])
            self.assertEqual(code, 1)

    def test_incomplete_batch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {
                "batches": [
                    {
                        "id": 1,
                        "status": "in_progress",
                        "acceptance": [
                            {
                                "criterion": "done",
                                "met": True,
                                "evidence": "sha",
                            }
                        ],
                    }
                ]
            }
            session_path = self._write_session(tmp, session)
            code = self.mod.main(["--session", str(session_path)])
            self.assertEqual(code, 1)

    def test_god_file_lock_only_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {
                "batches": [
                    {
                        "id": 11,
                        "name": "Split god file",
                        "status": "complete",
                        "acceptance": [
                            {
                                "criterion": "structure already exists regex lock",
                                "met": True,
                                "evidence": "structure-only characterization tests pass",
                            }
                        ],
                    }
                ]
            }
            session_path = self._write_session(tmp, session)
            code = self.mod.main(["--session", str(session_path)])
            self.assertEqual(code, 1)

    def test_plan_open_acceptance_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {
                "plan_path": "plan.md",
                "batches": [
                    {
                        "id": 1,
                        "status": "complete",
                        "acceptance": [
                            {
                                "criterion": "facade cut",
                                "met": True,
                                "evidence": "loc 200",
                            }
                        ],
                    }
                ],
            }
            session_path = self._write_session(tmp, session)
            (tmp / "plan.md").write_text(
                "### Batch 1: Split\n\n"
                "**Acceptance criteria:**\n"
                "- [ ] gemini-service.ts under 400 LOC\n"
                "- [x] facade exists\n",
                encoding="utf-8",
            )
            code = self.mod.main(["--session", str(session_path)])
            self.assertEqual(code, 1)

    def test_multi_batch_close_without_validate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {
                "execution_log_path": "execution-log.md",
                "batches": [
                    {
                        "id": 1,
                        "status": "complete",
                        "acceptance": [
                            {
                                "criterion": "a",
                                "met": True,
                                "evidence": "e",
                            }
                        ],
                    },
                    {
                        "id": 2,
                        "status": "complete",
                        "acceptance": [
                            {
                                "criterion": "b",
                                "met": True,
                                "evidence": "e",
                            }
                        ],
                    },
                ],
            }
            session_path = self._write_session(tmp, session)
            (tmp / "execution-log.md").write_text(
                "## 2026-07-08\n\n"
                "**Batch:** close remaining batches 1-2\n"
                "Multi-batch close of unfinished work.\n",
                encoding="utf-8",
            )
            code = self.mod.main(["--session", str(session_path)])
            self.assertEqual(code, 1)

    def test_evidence_dirs_required(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {
                "batches": [
                    {
                        "id": 1,
                        "status": "complete",
                        "acceptance": [
                            {
                                "criterion": "ok",
                                "met": True,
                                "evidence": "e",
                            }
                        ],
                    }
                ]
            }
            session_path = self._write_session(tmp, session)
            evidence = tmp / "scratch"
            evidence.mkdir()
            batch_dir = evidence / "batch-1"
            batch_dir.mkdir()
            (batch_dir / "lint.log").write_text("ok", encoding="utf-8")
            # missing typecheck/test/build
            code = self.mod.main(
                [
                    "--session",
                    str(session_path),
                    "--evidence-root",
                    str(evidence),
                    "--require-evidence-dirs",
                ]
            )
            self.assertEqual(code, 1)

    def test_advisory_exits_zero_on_errors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {"batches": [{"id": 1, "status": "complete"}]}
            session_path = self._write_session(tmp, session)
            code = self.mod.main(["--session", str(session_path), "--advisory"])
            self.assertEqual(code, 0)

    def test_missing_session_exits_2(self) -> None:
        code = self.mod.main(["--session", "/tmp/definitely-missing-elves-session.json"])
        self.assertEqual(code, 2)

    def test_json_output_shape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            session = {
                "batches": [
                    {
                        "id": 1,
                        "status": "complete",
                        "acceptance": [
                            {
                                "criterion": "ok",
                                "met": True,
                                "evidence": "sha",
                            }
                        ],
                    }
                ]
            }
            session_path = self._write_session(tmp, session)
            # Capture via run_checks + print_json path
            args = self.mod.parse_args(["--session", str(session_path), "--json"])
            report = self.mod.run_checks(args)
            self.assertFalse(report.errors)


if __name__ == "__main__":
    unittest.main()
