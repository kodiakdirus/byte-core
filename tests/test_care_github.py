from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.care import build_diagnostic_report  # noqa: E402
from byte_core.care_github import (  # noqa: E402
    OFFICIAL_REPOSITORY,
    CareTransportError,
    plan_github_action,
    render_github_markdown,
    submit_github_action,
)


class FakeRunner:
    def __init__(self, issues=(), submit_stdout="") -> None:
        self.issues = list(issues)
        self.submit_stdout = submit_stdout
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.authenticated = True
        self.fail_submission = False

    def __call__(self, arguments, input_text):
        args = tuple(arguments)
        self.calls.append((args, input_text))
        if args[:3] == ("gh", "auth", "status"):
            return self._completed(0 if self.authenticated else 1, "")
        if args[:3] == ("gh", "issue", "list"):
            return self._completed(0, json.dumps(self.issues))
        if args[:3] in (
            ("gh", "issue", "create"),
            ("gh", "issue", "comment"),
        ):
            return self._completed(
                1 if self.fail_submission else 0,
                self.submit_stdout,
            )
        raise AssertionError(f"unexpected arguments: {args}")

    def _completed(self, status: int, stdout: str):
        return subprocess.CompletedProcess(
            args=[], returncode=status, stdout=stdout, stderr=""
        )


class CareGithubTests(unittest.TestCase):
    def _report(self):
        return build_diagnostic_report(
            component="update",
            phase="apply",
            error_code="verification_failed",
            exit_code=6,
            platform="linux",
            architecture="x86_64",
            python_version="3.11",
            configuration_schema_version=1,
        )

    def test_dry_run_plans_create_without_mutation(self) -> None:
        runner = FakeRunner()
        report = self._report()

        action = plan_github_action(
            report, repository=OFFICIAL_REPOSITORY, runner=runner
        )

        self.assertEqual(action.action, "create")
        self.assertIsNone(action.issue_number)
        self.assertEqual(action.label, "byte-care")
        self.assertIn(
            f"<!-- byte-care:{report.fingerprint} -->", action.markdown
        )
        self.assertFalse(any(call[0][2] == "create" for call in runner.calls))

    def test_existing_fingerprint_plans_comment_not_duplicate(self) -> None:
        report = self._report()
        runner = FakeRunner(
            issues=[
                {
                    "number": 42,
                    "body": (
                        f"<!-- byte-care:{report.fingerprint} -->\n"
                        "existing fictional report"
                    ),
                }
            ]
        )

        action = plan_github_action(
            report, repository=OFFICIAL_REPOSITORY, runner=runner
        )

        self.assertEqual(action.action, "comment")
        self.assertEqual(action.issue_number, 42)

    def test_submit_requires_exact_approval_and_preserves_markdown(self) -> None:
        report = self._report()
        runner = FakeRunner(
            submit_stdout="https://github.com/kodiakdirus/byte-core/issues/123\n"
        )
        action = plan_github_action(
            report, repository=OFFICIAL_REPOSITORY, runner=runner
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "transport"
            with self.assertRaisesRegex(
                CareTransportError, "submission_not_approved"
            ):
                submit_github_action(
                    action,
                    approval_fingerprint="wrong",
                    transport_root=root,
                    runner=runner,
                    now=100,
                )
            self.assertFalse(root.exists())

            result = submit_github_action(
                action,
                approval_fingerprint=report.fingerprint,
                transport_root=root,
                runner=runner,
                now=100,
            )

            self.assertEqual(result.issue_number, 123)
            markdown = root / f"{report.fingerprint}.100.md"
            self.assertEqual(
                markdown.read_text(encoding="utf-8"), action.markdown
            )
            submit_calls = [
                call for call in runner.calls if call[0][:3]
                == ("gh", "issue", "create")
            ]
            self.assertEqual(submit_calls[-1][1], action.markdown)
            self.assertIn("--body-file", submit_calls[-1][0])
            self.assertNotIn(action.markdown, submit_calls[-1][0])

    def test_rate_limit_and_stale_create_prevent_retry_storms(self) -> None:
        report = self._report()
        runner = FakeRunner(
            submit_stdout="https://github.com/kodiakdirus/byte-core/issues/9\n"
        )
        action = plan_github_action(
            report, repository=OFFICIAL_REPOSITORY, runner=runner
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "transport"
            submit_github_action(
                action,
                approval_fingerprint=report.fingerprint,
                transport_root=root,
                runner=runner,
                now=100,
            )
            with self.assertRaisesRegex(
                CareTransportError, "submission_rate_limited"
            ):
                submit_github_action(
                    action,
                    approval_fingerprint=report.fingerprint,
                    transport_root=root,
                    runner=runner,
                    now=101,
                )
            with self.assertRaisesRegex(CareTransportError, "action_stale"):
                submit_github_action(
                    action,
                    approval_fingerprint=report.fingerprint,
                    transport_root=root,
                    runner=runner,
                    now=5000,
                )

    def test_failed_auth_or_submission_keeps_offline_markdown(self) -> None:
        report = self._report()
        planning_runner = FakeRunner()
        action = plan_github_action(
            report, repository=OFFICIAL_REPOSITORY, runner=planning_runner
        )
        for failure in ("auth", "submit"):
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary) / "transport"
                    runner = FakeRunner()
                    if failure == "auth":
                        runner.authenticated = False
                        expected = "github_authentication_required"
                    else:
                        runner.fail_submission = True
                        expected = "github_submission_failed"
                    with self.assertRaisesRegex(CareTransportError, expected):
                        submit_github_action(
                            action,
                            approval_fingerprint=report.fingerprint,
                            transport_root=root,
                            runner=runner,
                            now=100,
                        )
                    self.assertEqual(
                        (root / f"{report.fingerprint}.100.md").read_text(),
                        action.markdown,
                    )

    def test_wrong_repository_and_ambiguous_duplicates_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            CareTransportError, "repository_not_official"
        ):
            plan_github_action(
                self._report(),
                repository="example/not-byte-core",
                runner=FakeRunner(),
            )
        report = self._report()
        marker = f"<!-- byte-care:{report.fingerprint} -->"
        runner = FakeRunner(
            issues=[
                {"number": 1, "body": marker},
                {"number": 2, "body": marker},
            ]
        )
        with self.assertRaisesRegex(
            CareTransportError, "duplicate_issue_ambiguous"
        ):
            plan_github_action(
                report, repository=OFFICIAL_REPOSITORY, runner=runner
            )

    def test_markdown_contains_only_schema_fields_and_disclaimer(self) -> None:
        markdown = render_github_markdown(self._report())
        self.assertIn("No logs, environment variables", markdown)
        self.assertNotIn("hostname", markdown)
        self.assertNotIn("username", markdown)


if __name__ == "__main__":
    unittest.main()
