from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.privacy import (  # noqa: E402
    ContentType,
    DataClass,
    Finding,
    ScanPolicy,
    ScanResult,
    Severity,
)


class PrivacyModelTests(unittest.TestCase):
    def test_finding_is_immutable_and_serializes_allowlisted_fields(self) -> None:
        finding = Finding(
            source_id="docs/example.md",
            line=4,
            rule_id="PRIV-SECRET-001",
            data_class=DataClass.SECRET,
            severity=Severity.ERROR,
            fingerprint="0123456789abcdef",
            description="Possible private-key material detected.",
            remediation="Remove the key material.",
        )

        with self.assertRaises(FrozenInstanceError):
            finding.line = 5  # type: ignore[misc]

        serialized = finding.to_dict()
        self.assertEqual(
            set(serialized),
            {
                "source_id",
                "line",
                "rule_id",
                "data_class",
                "severity",
                "fingerprint",
                "description",
                "remediation",
            },
        )
        with self.assertRaises(TypeError):
            serialized["match"] = "unsafe"  # type: ignore[index]

    def test_scan_result_passes_only_without_errors(self) -> None:
        warning = self._finding(Severity.WARNING)
        error = self._finding(Severity.ERROR)

        self.assertTrue(ScanResult("safe.txt", ()).passed)
        self.assertTrue(ScanResult("safe.txt", (warning,)).passed)
        self.assertFalse(ScanResult("safe.txt", (error,)).passed)

    def test_policy_requires_positive_input_limit(self) -> None:
        with self.assertRaises(ValueError):
            ScanPolicy(max_characters=0)
        with self.assertRaises(ValueError):
            ScanPolicy(max_findings=0)

        policy = ScanPolicy(content_type=ContentType.PUBLIC_EXAMPLE)
        self.assertEqual(policy.max_characters, 1_000_000)
        self.assertEqual(policy.max_findings, 10_000)

    def test_policy_and_result_copy_mutable_collections(self) -> None:
        enabled = {"PRIV-SECRET-001"}
        policy = ScanPolicy(enabled_rule_ids=enabled)  # type: ignore[arg-type]
        findings = [self._finding(Severity.ERROR)]
        result = ScanResult("safe.txt", findings)  # type: ignore[arg-type]

        enabled.clear()
        findings.clear()

        self.assertEqual(policy.enabled_rule_ids, {"PRIV-SECRET-001"})
        self.assertEqual(len(result.findings), 1)

    def _finding(self, severity: Severity) -> Finding:
        return Finding(
            source_id="safe.txt",
            line=1,
            rule_id="TEST",
            data_class=DataClass.PUBLIC,
            severity=severity,
            fingerprint="0123456789abcdef",
            description="Description.",
            remediation="Remediation.",
        )


if __name__ == "__main__":
    unittest.main()
