from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
LAUNCHER = REPOSITORY_ROOT / "bin" / "byte"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core import cli  # noqa: E402
from byte_core.lifecycle import build_initialization_plan  # noqa: E402


class CliTests(unittest.TestCase):
    def test_check_text_success(self) -> None:
        report = self._report(supported=True)
        output = io.StringIO()

        with mock.patch.object(cli, "collect_check_report", return_value=report):
            status = cli.main(["check"], stdout=output)

        self.assertEqual(status, cli.ExitStatus.SUCCESS)
        self.assertIn("Byte environment check", output.getvalue())
        self.assertIn("Result: supported", output.getvalue())

    def test_check_json_is_deterministic_and_machine_readable(self) -> None:
        report = self._report(supported=True)
        first = io.StringIO()
        second = io.StringIO()

        with mock.patch.object(cli, "collect_check_report", return_value=report):
            first_status = cli.main(["check", "--format", "json"], stdout=first)
            second_status = cli.main(["check", "--format", "json"], stdout=second)

        self.assertEqual(first_status, cli.ExitStatus.SUCCESS)
        self.assertEqual(second_status, cli.ExitStatus.SUCCESS)
        self.assertEqual(first.getvalue(), second.getvalue())
        payload = json.loads(first.getvalue())
        self.assertEqual(payload["command"], "check")
        self.assertTrue(payload["supported"])
        self.assertEqual(payload["checks"][0]["name"], "python")

    def test_unsupported_environment_returns_documented_status(self) -> None:
        output = io.StringIO()
        with mock.patch.object(
            cli,
            "collect_check_report",
            return_value=self._report(supported=False),
        ):
            status = cli.main(["check"], stdout=output)

        self.assertEqual(status, cli.ExitStatus.UNSUPPORTED)
        self.assertIn("Result: unsupported", output.getvalue())

    def test_reserved_command_is_clear_and_does_not_run_check(self) -> None:
        errors = io.StringIO()
        with mock.patch.object(cli, "collect_check_report") as collect:
            status = cli.main(["update"], stderr=errors)

        self.assertEqual(status, cli.ExitStatus.UNSUPPORTED)
        self.assertEqual(errors.getvalue(), "byte: command is not implemented\n")
        collect.assert_not_called()

    def test_usage_error_returns_two(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            cli.main(["check", "--unknown"])

        self.assertEqual(raised.exception.code, cli.ExitStatus.USAGE)
        self.assertIn("usage error", errors.getvalue())

    def test_plan_apply_and_verify_commands_share_one_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            plan_path = Path(temporary) / "plan.json"
            plan_output = io.StringIO()
            self.assertEqual(
                cli.main(
                    ["plan", "init", "--deployment-root", str(root)],
                    stdout=plan_output,
                ),
                cli.ExitStatus.SUCCESS,
            )
            plan_path.write_text(plan_output.getvalue(), encoding="utf-8")

            applied = io.StringIO()
            verified = io.StringIO()
            self.assertEqual(
                cli.main(["apply", "--plan", str(plan_path)], stdout=applied),
                cli.ExitStatus.SUCCESS,
            )
            self.assertEqual(
                cli.main(
                    ["verify", "--plan", str(plan_path), "--format", "json"],
                    stdout=verified,
                ),
                cli.ExitStatus.SUCCESS,
            )

        self.assertIn("Result: initialized", applied.getvalue())
        self.assertEqual(json.loads(verified.getvalue())["code"], "verified")

    def test_guided_init_requires_exact_plan_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            errors = io.StringIO()
            status = cli.main(
                ["init", "--deployment-root", str(root)],
                stdout=io.StringIO(),
                stderr=errors,
                stdin=io.StringIO("no\n"),
            )

            self.assertEqual(status, cli.ExitStatus.REFUSED)
            self.assertFalse(root.exists())

            plan = build_initialization_plan(root)
            output = io.StringIO()
            status = cli.main(
                ["init", "--deployment-root", str(root)],
                stdout=output,
                stdin=io.StringIO(plan.plan_id + "\n"),
            )

            self.assertEqual(status, cli.ExitStatus.SUCCESS)
            self.assertTrue(root.is_dir())
            self.assertIn("Result: initialized", output.getvalue())

    def test_remove_reports_preservation_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "deployment"
            plan = build_initialization_plan(root)
            from byte_core.lifecycle import apply_initialization

            apply_initialization(plan)
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            output = io.StringIO()

            status = cli.main(
                [
                    "remove",
                    "--deployment-root",
                    str(root),
                    "--format",
                    "json",
                ],
                stdout=output,
            )

            after = {path.name: path.read_bytes() for path in root.iterdir()}
            self.assertEqual(status, cli.ExitStatus.SUCCESS)
            self.assertEqual(before, after)
            self.assertEqual(
                json.loads(output.getvalue())["code"],
                "core_integration_absent",
            )

    def test_internal_failure_is_sanitized(self) -> None:
        errors = io.StringIO()
        private_detail = "unexpected-private-detail"
        with mock.patch.object(
            cli,
            "collect_check_report",
            side_effect=RuntimeError(private_detail),
        ):
            status = cli.main(["check"], stderr=errors)

        self.assertEqual(status, cli.ExitStatus.INTERNAL_ERROR)
        self.assertEqual(errors.getvalue(), "byte: internal error\n")
        self.assertNotIn(private_detail, errors.getvalue())

    def test_collect_report_uses_safe_bounded_values(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["git", "--version"],
            returncode=0,
            stdout="git version 2.50.1 (Example Build)\n",
            stderr="",
        )
        with (
            mock.patch.object(cli.platform, "system", return_value="Linux"),
            mock.patch.object(cli.platform, "machine", return_value="x86_64"),
            mock.patch.object(cli.shutil, "which", return_value="git"),
            mock.patch.object(cli.subprocess, "run", return_value=completed),
        ):
            report = cli.collect_check_report()

        values = {check.name: check.value for check in report.checks}
        self.assertEqual(values["platform"], "linux")
        self.assertEqual(values["architecture"], "x86_64")
        self.assertEqual(values["git"], "2.50.1")

    def test_launcher_help_is_useful(self) -> None:
        completed = subprocess.run(
            [str(LAUNCHER), "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("self-managed Byte deployment", completed.stdout)
        self.assertIn("check", completed.stdout)
        self.assertIn("init", completed.stdout)

    def test_launcher_check_does_not_modify_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            before = tuple(Path(temporary).iterdir())
            completed = subprocess.run(
                [str(LAUNCHER), "check", "--format", "json"],
                cwd=temporary,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            after = tuple(Path(temporary).iterdir())

        self.assertIn(completed.returncode, (0, 3), completed.stderr)
        self.assertEqual(before, ())
        self.assertEqual(after, ())
        self.assertEqual(json.loads(completed.stdout)["command"], "check")

    def test_posix_launcher_syntax(self) -> None:
        completed = subprocess.run(
            ["sh", "-n", str(LAUNCHER)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def _report(self, *, supported: bool) -> cli.CheckReport:
        status = "pass" if supported else "fail"
        return cli.CheckReport(
            command="check",
            supported=supported,
            checks=(cli.CheckResult("python", status, "3.11"),),
        )


if __name__ == "__main__":
    unittest.main()
