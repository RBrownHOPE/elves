"""Python 3.10 fallback coverage for the shipped models.toml subset parser."""

from __future__ import annotations

import math
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from cobbler_runtime import config as config_mod  # noqa: E402
from cobbler_runtime import toml_compat  # noqa: E402
from cobbler_runtime.onboard import (  # noqa: E402
    apply_onboarding,
    load_models_toml_state,
    probe_routes,
)


class Python310TomlCompatTests(unittest.TestCase):
    def test_generated_subset_parser_covers_supported_values(self) -> None:
        text = """
sharing_policy = "local-only"
unknown_scalar = 17
unknown_bool = true
unknown_float = 3e2
unknown_special = +inf

[profiles."custom.route"]
adapter = "custom-cli"
extra_args = [
  "--foo",
  "--bar",
]
limits = { retries = 2, enabled = true, ratio = 1.5 }

[roles.review]
profile = "custom.route"
required = false
fallback_chain = [
  { profile = "host-native", reason = "fallback" },
]

[unknown.table]
value = "keep"
"""
        with mock.patch.object(toml_compat, "_tomllib", None):
            parsed = toml_compat.loads(text)

        self.assertEqual(parsed["unknown_scalar"], 17)
        self.assertTrue(parsed["unknown_bool"])
        self.assertEqual(parsed["unknown_float"], 300.0)
        self.assertTrue(math.isinf(parsed["unknown_special"]))
        self.assertEqual(
            parsed["profiles"]["custom.route"]["extra_args"],
            ["--foo", "--bar"],
        )
        self.assertEqual(parsed["profiles"]["custom.route"]["limits"]["retries"], 2)
        self.assertEqual(
            parsed["roles"]["review"]["fallback_chain"][0]["profile"],
            "host-native",
        )
        self.assertEqual(parsed["unknown"]["table"]["value"], "keep")

        with self.assertRaises(toml_compat.TomlCompatError):
            toml_compat.loads_compat("unsafe = __import__('os')")

    def test_config_and_partial_apply_preserve_unknown_values_without_tomllib(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".elves" / "models.toml"
            path.parent.mkdir(parents=True)
            path.write_text(
                """
sharing_policy = "local-only"
custom_threshold = 2.5

[extension.settings]
enabled = true
labels = ["one", "two"]

[profiles.my-wrapper]
adapter = "custom-cli"
executable = "scripts/my-wrapper"
extra_args = ["--preserve"]
limits = { retries = 3, strict = true }

[roles.review]
profile = "my-wrapper"
required = false
""".lstrip(),
                encoding="utf-8",
            )

            with mock.patch.object(toml_compat, "_tomllib", None):
                loaded = config_mod.load_toml_file(path)
                result = apply_onboarding(
                    root,
                    role_flags={"implement": "host-native"},
                    fake_presence={},
                )
                state = load_models_toml_state(root)
                rewritten = toml_compat.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(loaded["custom_threshold"], 2.5)
            self.assertTrue(result["ok"], result)
            self.assertTrue(state.parse_ok, state.warnings)
            self.assertEqual(state.unknown_top_level["custom_threshold"], 2.5)
            self.assertEqual(
                state.unknown_top_level["extension"]["settings"]["labels"],
                ["one", "two"],
            )
            self.assertEqual(state.profiles["my-wrapper"]["extra_args"], ["--preserve"])
            self.assertEqual(state.profiles["my-wrapper"]["limits"]["retries"], 3)
            self.assertEqual(rewritten["roles"]["review"]["profile"], "my-wrapper")

    def test_probe_uses_fallback_parser_and_configured_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "scripts" / "compat-wrapper"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("#!/bin/sh\necho compat-help\n", encoding="utf-8")
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
            path = root / ".elves" / "models.toml"
            path.parent.mkdir(parents=True)
            path.write_text(
                """
[profiles.compat]
adapter = "custom-cli"
executable = "scripts/compat-wrapper"
metadata = { enabled = true, weights = [1, 2.5, 3e2] }

[roles.review]
profile = "compat"
required = false
""".lstrip(),
                encoding="utf-8",
            )

            with mock.patch.object(toml_compat, "_tomllib", None):
                probe = probe_routes(root, environ={})

            self.assertTrue(probe["ok"], probe)
            self.assertEqual(probe["roles"]["review"], "compat")
            self.assertFalse(probe["warnings"])
            review = [
                item
                for item in probe["probes"]
                if item.get("purpose") == "review" and item["route"] == "compat"
            ]
            self.assertEqual(len(review), 1, probe)
            self.assertEqual(review[0]["status"], "pass", review[0])


if __name__ == "__main__":
    unittest.main()
