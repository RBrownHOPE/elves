from __future__ import annotations

import os
import json
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
NOTIFY_SCRIPT = REPO_ROOT / "scripts" / "notify.sh"


class NotifyScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()

    def write_executable(self, name: str, body: str) -> Path:
        path = self.bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def env(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("ELVES_"):
                env.pop(key)
        path_entries = [str(self.bin_dir)]
        existing_path = env.get("PATH")
        if existing_path:
            path_entries.append(existing_path)
        env.update(
            {
                "PATH": os.pathsep.join(path_entries),
                "TMPDIR": str(self.root),
            }
        )
        env.update(overrides)
        return env

    def test_env_path_uses_path_separator_without_empty_segments(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": ""}, clear=False):
            env = self.env()

        self.assertEqual(env["PATH"], str(self.bin_dir))
        self.assertNotIn(f"{os.pathsep}{os.pathsep}", env["PATH"])
        self.assertFalse(env["PATH"].endswith(os.pathsep))

    def run_notify(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(NOTIFY_SCRIPT), *args],
            cwd=REPO_ROOT,
            env=env or self.env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_missing_arguments_prints_usage_and_fails(self) -> None:
        result = self.run_notify()

        self.assertEqual(result.returncode, 1)
        self.assertIn('Usage:', result.stderr)

    def test_custom_command_receives_exported_notification_fields(self) -> None:
        output_path = self.root / "custom-output.txt"
        recorder = self.write_executable(
            "record-notify",
            """#!/usr/bin/env bash
{
  printf 'TITLE=%s\\n' "$TITLE"
  printf 'BODY=%s\\n' "$BODY"
  printf 'URL=%s\\n' "$URL"
} > "$1"
""",
        )

        result = self.run_notify(
            "Done",
            "Body text",
            "https://example.com/pr",
            env=self.env(ELVES_NOTIFY_CMD=f"{shlex.quote(str(recorder))} {shlex.quote(str(output_path))}"),
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Custom command: delivered", result.stderr)
        self.assertEqual(
            output_path.read_text(),
            "TITLE=Done\nBODY=Body text\nURL=https://example.com/pr\n",
        )

    def test_normal_mode_falls_back_to_stdout_when_channels_are_unavailable(self) -> None:
        self.write_executable("gh", "#!/usr/bin/env bash\nexit 1\n")

        result = self.run_notify("Done", "Body text", "https://example.com/pr")

        self.assertEqual(result.returncode, 0)
        self.assertIn("All notification channels failed or unconfigured", result.stderr)
        self.assertIn("Done", result.stdout)
        self.assertIn("Body text", result.stdout)
        self.assertIn("https://example.com/pr", result.stdout)

    def test_stdout_fallback_redacts_secret_shapes_and_credentialed_urls(self) -> None:
        self.write_executable("gh", "#!/usr/bin/env bash\nexit 1\n")
        title_secret = "notify-title-secret-123456789"
        bearer_secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        url_password = "notify-password-123456"
        query_secret = "notify-query-secret-123456789"

        result = self.run_notify(
            f"Done token={title_secret}",
            f"Authorization: Bearer {bearer_secret}",
            (
                "https://notify-user:"
                f"{url_password}@example.test/pr?token={query_secret}"
            ),
        )

        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, combined)
        for secret in (title_secret, bearer_secret, url_password, query_secret):
            self.assertNotIn(secret, combined)
        self.assertIn("[REDACTED]", combined)

    def test_test_mode_fails_when_no_real_channel_is_available(self) -> None:
        self.write_executable("gh", "#!/usr/bin/env bash\nexit 1\n")

        result = self.run_notify("--test")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Elves Notification Test", result.stdout)

    def test_slack_delivery_uses_webhook_before_other_channels(self) -> None:
        payload_path = self.root / "slack-payload.json"
        url_path = self.root / "slack-url.txt"
        gh_marker = self.root / "gh-called"
        self.write_executable(
            "curl",
            f"""#!/usr/bin/env bash
out_file=''
payload=''
url=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o)
      out_file="$2"
      shift 2
      ;;
    -d)
      payload="$2"
      shift 2
      ;;
    http*)
      url="$1"
      shift
      ;;
    *)
      shift
      ;;
  esac
done
[ -n "$out_file" ] && printf 'ok' > "$out_file"
printf '%s' "$payload" > {shlex.quote(str(payload_path))}
printf '%s' "$url" > {shlex.quote(str(url_path))}
printf '200'
""",
        )
        self.write_executable(
            "gh",
            f"#!/usr/bin/env bash\nprintf called > {shlex.quote(str(gh_marker))}\nexit 99\n",
        )

        result = self.run_notify(
            'Done "now"',
            "Body text with $dollars, `ticks`, and\nnewlines",
            "https://example.com/pr?x=[y]",
            env=self.env(ELVES_SLACK_WEBHOOK="https://hooks.slack.test/services/example"),
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Slack: delivered", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(gh_marker.exists())
        self.assertEqual(url_path.read_text(), "https://hooks.slack.test/services/example")
        payload = json.loads(payload_path.read_text())
        self.assertEqual(payload["text"], 'Done "now"')
        self.assertEqual(payload["blocks"][0]["text"]["text"], 'Done "now"')
        self.assertEqual(
            payload["blocks"][1]["text"]["text"],
            "Body text with $dollars, `ticks`, and\nnewlines",
        )
        self.assertEqual(payload["blocks"][2]["elements"][0]["url"], "https://example.com/pr?x=[y]")

    def test_slack_failure_response_is_redacted_without_network(self) -> None:
        response_secret = "slack-response-secret-123456789"
        response_password = "slack-password-123456"
        self.write_executable(
            "curl",
            f"""#!/usr/bin/env bash
out_file=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then
    out_file="$2"
    shift 2
  else
    shift
  fi
done
printf '%s' 'token={response_secret} https://slack-user:{response_password}@example.test' > "$out_file"
printf '500'
""",
        )
        self.write_executable("gh", "#!/usr/bin/env bash\nexit 1\n")

        result = self.run_notify(
            "Done",
            "Body text",
            env=self.env(
                ELVES_SLACK_WEBHOOK="https://hooks.slack.test/services/T/B/fake"
            ),
        )

        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, combined)
        self.assertNotIn(response_secret, combined)
        self.assertNotIn(response_password, combined)
        self.assertIn("Slack: HTTP 500", result.stderr)
        self.assertIn("[REDACTED]", result.stderr)

    def test_github_failure_stderr_is_redacted_without_network(self) -> None:
        gh_secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        gh_password = "github-password-123456"
        self.write_executable(
            "gh",
            f"""#!/usr/bin/env bash
if [ "$1 $2" = "pr view" ]; then
  printf '123\n'
  exit 0
fi
if [ "$1 $2" = "pr comment" ]; then
  printf '%s\n' 'token={gh_secret} https://github-user:{gh_password}@example.test' >&2
  exit 1
fi
exit 1
""",
        )

        result = self.run_notify("Done", "Body text")

        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, combined)
        self.assertNotIn(gh_secret, combined)
        self.assertNotIn(gh_password, combined)
        self.assertIn("gh pr comment failed", result.stderr)
        self.assertIn("[REDACTED]", result.stderr)

    def test_github_pr_comment_delivery_builds_comment_body(self) -> None:
        body_path = self.root / "gh-comment-body.md"
        self.write_executable(
            "gh",
            f"""#!/usr/bin/env bash
if [ "$1 $2" = "pr view" ]; then
  printf '123\\n'
  exit 0
fi
if [ "$1 $2" = "pr comment" ]; then
  shift 2
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--body" ]; then
      printf '%s' "$2" > {shlex.quote(str(body_path))}
      exit 0
    fi
    shift
  done
fi
exit 1
""",
        )

        title = 'Done "now"'
        body = "Body with $dollars, `ticks`, [label](target), and\nnewlines"
        url = "https://example.com/pr?x=[y]&q=(z)"

        result = self.run_notify(title, body, url)

        self.assertEqual(result.returncode, 0)
        self.assertIn("PR comment posted (PR #123)", result.stderr)
        self.assertEqual(
            body_path.read_text(),
            f"## {title}\n\n{body}\n\n[Open]({url})",
        )


if __name__ == "__main__":
    unittest.main()
