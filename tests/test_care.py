from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.care import (  # noqa: E402
    CareError,
    build_diagnostic_report,
    prepare_local_report,
    serialize_diagnostic_report,
)


class CareTests(unittest.TestCase):
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

    def test_report_is_minimal_deterministic_and_allowlisted(self) -> None:
        first = self._report()
        second = self._report()

        self.assertEqual(first, second)
        payload = json.loads(serialize_diagnostic_report(first))
        self.assertEqual(
            set(payload),
            {
                "schema_version", "byte_core_version", "component", "phase",
                "error_code", "exit_code", "platform", "architecture",
                "python_version", "configuration_schema_version",
                "fingerprint",
            },
        )
        for forbidden in (
            "hostname", "username", "home", "environment", "transcript",
            "prompt", "inventory", "log", "path", "credential",
        ):
            self.assertNotIn(forbidden, payload)

    def test_off_mode_never_creates_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "reports"
            result = prepare_local_report(
                self._report(), mode="off", report_root=None
            )
            self.assertEqual(result.code, "reporting_off")
            self.assertFalse(result.stored)
            self.assertFalse(target.exists())

    def test_local_mode_stores_exclusively_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "reports"
            report = self._report()
            first = prepare_local_report(
                report, mode="local-only", report_root=root
            )
            second = prepare_local_report(
                report, mode="local-only", report_root=root
            )

            target = root / f"{report.fingerprint}.json"
            self.assertEqual(first.code, "report_saved_local")
            self.assertEqual(second.code, "report_already_local")
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                serialize_diagnostic_report(report),
            )
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_ask_mode_requires_approval_before_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "reports"
            report = self._report()
            pending = prepare_local_report(
                report,
                mode="ask-before-reporting",
                report_root=root,
            )
            self.assertEqual(pending.code, "approval_required")
            self.assertFalse(root.exists())
            saved = prepare_local_report(
                report,
                mode="ask-before-reporting",
                report_root=root,
                approved=True,
            )
            self.assertEqual(saved.code, "report_saved_local")

    def test_invalid_fields_and_tampered_reports_fail_closed(self) -> None:
        with self.assertRaisesRegex(CareError, "invalid_report"):
            build_diagnostic_report(
                component="update",
                phase="apply",
                error_code="/private/path",
                exit_code=6,
                platform="linux",
                architecture="x86_64",
                python_version="3.11",
                configuration_schema_version=1,
            )
        with self.assertRaisesRegex(CareError, "report_integrity_failed"):
            serialize_diagnostic_report(
                replace(self._report(), error_code="different_error")
            )

    def test_privacy_failure_never_writes_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "reports"
            blocked_scan = mock.Mock(passed=False)
            with mock.patch(
                "byte_core.care.scan_diagnostic_payload",
                return_value=blocked_scan,
            ):
                with self.assertRaisesRegex(CareError, "privacy_scan_failed"):
                    prepare_local_report(
                        self._report(),
                        mode="local-only",
                        report_root=root,
                    )
            self.assertFalse(root.exists())

    def test_automatic_outbound_mode_is_explicitly_unsupported(self) -> None:
        with self.assertRaisesRegex(
            CareError, "automatic_reporting_unsupported"
        ):
            prepare_local_report(
                self._report(),
                mode="automatic-sanitized",
                report_root="/fictional/reports",
            )

    def test_insecure_existing_report_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "reports"
            root.mkdir(mode=0o755)
            with self.assertRaisesRegex(CareError, "invalid_report_root"):
                prepare_local_report(
                    self._report(), mode="local-only", report_root=root
                )


if __name__ == "__main__":
    unittest.main()
