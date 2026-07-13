from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.executables import (  # noqa: E402
    resolve_executable,
    resolve_executable_for_launch,
)


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o100)


class ExecutableResolutionTests(unittest.TestCase):
    def test_user_installer_directory_fallback_when_path_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            agy = home / ".local" / "bin" / "agy"
            opencode = home / ".opencode" / "bin" / "opencode"
            _make_executable(agy)
            _make_executable(opencode)

            self.assertEqual(
                resolve_executable("agy", path="", home=home), str(agy.resolve())
            )
            self.assertEqual(
                resolve_executable_for_launch("opencode", path="", home=home),
                str(opencode.resolve()),
            )
            explicit = resolve_executable("~/.local/bin/agy", path="", home=home)
            self.assertIsNotNone(explicit)
            self.assertEqual(Path(explicit or "").resolve(), agy.resolve())

    def test_inherited_path_keeps_portable_bare_launch_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            tool = bin_dir / "agy"
            _make_executable(tool)

            self.assertEqual(
                resolve_executable_for_launch("agy", path=str(bin_dir), home=Path(tmp)),
                "agy",
            )

    def test_relative_wrapper_resolves_only_under_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "scripts" / "reviewer.py"
            _make_executable(wrapper)

            self.assertEqual(
                resolve_executable(
                    "scripts/reviewer.py", repo_root=root, path="", home=root
                ),
                str(wrapper.resolve()),
            )
            self.assertIsNone(
                resolve_executable("scripts/missing.py", repo_root=root, path="", home=root)
            )


if __name__ == "__main__":
    unittest.main()
