from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.privacy.adapters import (  # noqa: E402
    AdapterLimits,
    scan_diagnostic_payload,
)


class PrivacyDiagnosticAdapterTests(unittest.TestCase):
    def test_scans_exact_already_constructed_payload(self) -> None:
        payload = '{"component":"configuration","error":"invalid_schema"}'

        result = scan_diagnostic_payload(payload)

        self.assertTrue(result.passed)
        self.assertEqual(len(result.scans), 1)
        self.assertEqual(result.scans[0].source_id, "diagnostic/report.json")

    def test_detects_secret_without_echoing_payload(self) -> None:
        marker = "synthetic-" + "credential-value"
        payload = f'{{"token":"{marker}"}}'

        result = scan_diagnostic_payload(payload)

        self.assertFalse(result.passed)
        self.assertNotIn(marker, repr(result))

    def test_rejects_invalid_ids_values_and_sizes(self) -> None:
        invalid_id = scan_diagnostic_payload("safe", logical_id="report.json")
        empty = scan_diagnostic_payload("")
        binary = scan_diagnostic_payload("safe\0payload")
        invalid_type = scan_diagnostic_payload(None)  # type: ignore[arg-type]
        invalid_unicode = scan_diagnostic_payload("\ud800")
        oversized = scan_diagnostic_payload(
            "x" * 6,
            limits=AdapterLimits(max_file_bytes=5),
        )

        self.assertEqual(self._codes(invalid_id), {"invalid_diagnostic_id"})
        self.assertEqual(self._codes(empty), {"invalid_diagnostic_payload"})
        self.assertEqual(self._codes(binary), {"binary_payload"})
        self.assertEqual(
            self._codes(invalid_type),
            {"invalid_diagnostic_payload"},
        )
        self.assertEqual(self._codes(invalid_unicode), {"invalid_utf8"})
        self.assertEqual(self._codes(oversized), {"payload_too_large"})

    def test_has_no_allowlist_or_collection_surface(self) -> None:
        self.assertNotIn(
            "allowlist",
            scan_diagnostic_payload.__annotations__,
        )
        self.assertNotIn("root", scan_diagnostic_payload.__annotations__)

    def _codes(self, result) -> set[str]:
        return {error.code for error in result.errors}


if __name__ == "__main__":
    unittest.main()
