"""Tests for scripts/openrouter_lens.py (no live network)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
LENS = SCRIPTS / "openrouter_lens.py"


def _ensure_path() -> None:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    if str(REPO_ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))


_ensure_path()


class OpenRouterLensUnitTests(unittest.TestCase):
    def test_parse_and_normalize(self) -> None:
        import openrouter_lens as lens  # type: ignore  # noqa: E402

        raw = '```json\n{"role":"review","verdict":"pass","confidence":0.8,"key_findings":["ok"]}\n```'
        report = lens._parse_model_json(raw)
        norm = lens._normalize_report(report, role="review", model="qwen/qwen3-max")
        self.assertEqual(norm["verdict"], "pass")
        self.assertEqual(norm["actual_model"], "qwen/qwen3-max")
        self.assertIsInstance(norm["key_findings"], list)

    def test_normalize_structured_evidence_to_contract_strings(self) -> None:
        import openrouter_lens as lens  # type: ignore  # noqa: E402

        norm = lens._normalize_report(
            {
                "role": "review",
                "verdict": "pass",
                "confidence": 0.8,
                "key_findings": ["ok"],
                "evidence": [{"file": "README.md", "quote": "native-first"}],
                "risks": [],
                "recommended_actions": [],
                "open_questions": [],
            },
            role="review",
            model="z-ai/glm-5",
        )

        self.assertEqual(len(norm["evidence"]), 1)
        self.assertIsInstance(norm["evidence"][0], str)
        self.assertIn("README.md", norm["evidence"][0])

    def test_ambiguous_session_rejected(self) -> None:
        import openrouter_lens as lens  # type: ignore  # noqa: E402

        with self.assertRaises(SystemExit):
            lens._assert_exact_session_id("latest")

    def test_cli_help_exits_zero(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(LENS), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("OpenRouter", proc.stdout)

    def test_missing_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in dict(**{**__import__("os").environ}).items() if k != "OPENROUTER_API_KEY"}
            env.pop("OPENROUTER_API_KEY", None)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(LENS),
                    "--repo-root",
                    tmp,
                    "--prompt",
                    "hello",
                    "--model",
                    "openrouter/auto",
                ],
                capture_output=True,
                text=True,
                check=False,
                env=env,
                cwd=tmp,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("OPENROUTER_API_KEY", proc.stderr)

    def test_session_file_roundtrip_mocked(self) -> None:
        import openrouter_lens as lens  # type: ignore  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_response = {
                "model": "qwen/qwen3-max",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "role": "review",
                                    "verdict": "warn",
                                    "confidence": 0.7,
                                    "key_findings": ["need more tests"],
                                    "evidence": [],
                                    "risks": [],
                                    "recommended_actions": [],
                                    "open_questions": [],
                                }
                            )
                        }
                    }
                ],
            }
            with mock.patch.object(lens, "_openrouter_chat", return_value=fake_response):
                with mock.patch.object(lens, "_api_key", return_value="sk-test-not-real"):
                    out = lens.run_lens(
                        repo_root=root,
                        model="qwen/qwen3-max",
                        role="review",
                        task="Review the plan for completeness.",
                        session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        context_files=[],
                        packet={"user_intent": "ship openrouter lens"},
                        max_tokens=256,
                        temperature=0.0,
                        timeout_s=5.0,
                    )
            self.assertEqual(out["actual_model"], "qwen/qwen3-max")
            self.assertEqual(out["role_report"]["verdict"], "warn")
            self.assertEqual(out["adapter_metadata"]["source"], "wrapper-transport")
            sess = root / ".elves" / "runtime" / "openrouter-sessions" / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
            self.assertTrue(sess.is_file())
            data = json.loads(sess.read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(data["messages"]), 2)

    def test_context_rejects_sensitive_and_external_paths_before_network(self) -> None:
        import openrouter_lens as lens  # type: ignore  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            sensitive = root / "credentials.json"
            sensitive.write_text('{"token":"do-not-send"}\n', encoding="utf-8")
            external = Path(outside) / "review.md"
            external.write_text("review context", encoding="utf-8")
            self.assertTrue(lens._is_sensitive_path(Path(".env.local")))
            for path in (sensitive, external):
                with self.subTest(path=path):
                    with mock.patch.object(lens, "_openrouter_chat") as chat:
                        with mock.patch.object(lens, "_api_key", return_value="sk-test-not-real"):
                            with self.assertRaises(SystemExit):
                                lens.run_lens(
                                    repo_root=root,
                                    model="qwen/qwen3-max",
                                    role="review",
                                    task="review",
                                    session_id=None,
                                    context_files=[path],
                                    packet=None,
                                    max_tokens=256,
                                    temperature=0.0,
                                    timeout_s=5.0,
                                )
                    chat.assert_not_called()

    def test_prompt_file_uses_same_path_boundary(self) -> None:
        import openrouter_lens as lens  # type: ignore  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            external = Path(outside) / "prompt.md"
            external.write_text("review", encoding="utf-8")
            with mock.patch.object(lens, "_openrouter_chat") as chat:
                with mock.patch.object(lens, "_api_key", return_value="sk-test-not-real"):
                    with self.assertRaises(SystemExit):
                        lens.run_lens(
                            repo_root=root,
                            model="qwen/qwen3-max",
                            role="review",
                            task="",
                            session_id=None,
                            context_files=[],
                            packet=None,
                            max_tokens=256,
                            temperature=0.0,
                            timeout_s=5.0,
                            prompt_file=external,
                        )
            chat.assert_not_called()

    def test_secrets_redacted_before_network_and_session_persistence(self) -> None:
        import openrouter_lens as lens  # type: ignore  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = root / "review.md"
            exact_marker = f"fixture-{uuid.uuid4().hex}"
            pattern_marker = "sk-" + uuid.uuid4().hex
            context.write_text(
                f"exact={exact_marker}\nshaped={pattern_marker}\n", encoding="utf-8"
            )
            session_path = (
                root
                / ".elves"
                / "runtime"
                / "openrouter-sessions"
                / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
            )
            session_path.parent.mkdir(parents=True)
            session_path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": f"legacy {exact_marker}"}
                        ],
                        "legacy_note": exact_marker,
                    }
                ),
                encoding="utf-8",
            )
            fake_response = {
                "model": "qwen/qwen3-max",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "role": "review",
                                    "verdict": "pass",
                                    "confidence": 0.9,
                                    "key_findings": [f"echo {exact_marker}"],
                                }
                            )
                        }
                    }
                ],
            }
            captured: dict[str, object] = {}

            def fake_chat(**kwargs: object) -> dict[str, object]:
                captured.update(kwargs)
                return fake_response

            with mock.patch.dict(
                os.environ,
                {"REVIEW_SECRET_TOKEN": exact_marker},
                clear=False,
            ):
                with mock.patch.object(lens, "_openrouter_chat", side_effect=fake_chat):
                    with mock.patch.object(lens, "_api_key", return_value="sk-test-not-real"):
                        result = lens.run_lens(
                            repo_root=root,
                            model="qwen/qwen3-max",
                            role="review",
                            task=f"review with {exact_marker}",
                            session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            context_files=[context],
                            packet={"constraints": [f"never expose {exact_marker}"]},
                            max_tokens=256,
                            temperature=0.0,
                            timeout_s=5.0,
                        )

            transmitted = json.dumps(captured.get("messages"))
            persisted = session_path.read_text(encoding="utf-8")
            returned = json.dumps(result)
            for material in (transmitted, persisted, returned):
                self.assertNotIn(exact_marker, material)
                self.assertNotIn(pattern_marker, material)
                self.assertIn("[REDACTED:", material)


class OpenRouterRecipeTests(unittest.TestCase):
    def test_recipes_apply_ready(self) -> None:
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        from cobbler_runtime.setup import (  # noqa: E402
            PROFILE_RECIPES,
            profile_is_apply_blocked,
            render_models_toml,
            preferences_from_flags,
        )

        self.assertTrue(profile_is_apply_blocked("openrouter"))
        self.assertFalse(profile_is_apply_blocked("openrouter-lens"))
        self.assertFalse(profile_is_apply_blocked("or-qwen-max"))
        self.assertIn("scripts/openrouter_lens.py", PROFILE_RECIPES["or-glm"]["executable"])
        text = render_models_toml(
            preferences_from_flags(review="or-qwen-max", planning="openrouter-lens")
        )
        self.assertIn("openrouter_lens.py", text)
        self.assertIn("or-qwen-max", text)
        self.assertNotIn("my-coding-agent", text)


if __name__ == "__main__":
    unittest.main()
