from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.privacy import (  # noqa: E402
    ScanError,
    ScanPolicy,
    scan_text,
)


class PrivacyScannerTests(unittest.TestCase):
    def test_empty_and_unicode_text_pass(self) -> None:
        self.assertTrue(scan_text("empty.txt", "").passed)
        self.assertTrue(
            scan_text("notes/unicode.txt", "A harmless snowman: ☃").passed
        )

    def test_source_identifier_must_be_logical_and_relative(self) -> None:
        invalid = (
            "",
            "/absolute/path",
            "../escape.txt",
            "docs/../escape.txt",
            "docs\\example.txt",
            "docs//example.txt",
            "C:/Users/example/file.txt",
        )

        for source_id in invalid:
            with self.subTest(source_id=source_id):
                with self.assertRaises(ScanError) as raised:
                    scan_text(source_id, "safe")
                self.assertEqual(raised.exception.code, "invalid_source_id")

    def test_source_identifier_accepts_conventional_public_names(self) -> None:
        for source_id in (
            ".gitignore",
            "src/package/__init__.py",
            "docs/example-file.md",
        ):
            with self.subTest(source_id=source_id):
                self.assertTrue(scan_text(source_id, "safe").passed)

    def test_source_identifier_length_is_bounded(self) -> None:
        with self.assertRaises(ScanError) as raised:
            scan_text("a" * 4_097, "safe")

        self.assertEqual(raised.exception.code, "invalid_source_id")

    def test_source_identifier_must_be_encodable(self) -> None:
        with self.assertRaises(ScanError) as raised:
            scan_text("invalid-\ud800.txt", "safe")

        self.assertEqual(raised.exception.code, "invalid_source_id")

    def test_input_limit_fails_without_echoing_input(self) -> None:
        sensitive = "private-value-fragment"
        with self.assertRaises(ScanError) as raised:
            scan_text(
                "input.txt",
                sensitive,
                policy=ScanPolicy(max_characters=4),
            )

        self.assertEqual(raised.exception.code, "input_too_large")
        self.assertNotIn(sensitive, str(raised.exception))

    def test_finding_limit_fails_safely(self) -> None:
        private_prefix = "10." + "0.0"
        text = "\n".join(
            (f"{private_prefix}.1", f"{private_prefix}.2")
        )
        with self.assertRaises(ScanError) as raised:
            scan_text(
                "input.txt",
                text,
                policy=ScanPolicy(max_findings=1),
            )

        self.assertEqual(raised.exception.code, "too_many_findings")
        self.assertNotIn(private_prefix, str(raised.exception))

    def test_findings_never_retain_or_serialize_matched_text(self) -> None:
        sensitive = "credential-" + "value-1234"
        result = scan_text("input.txt", f"password={sensitive}")

        self.assertFalse(result.passed)
        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertNotIn(sensitive, repr(finding))
        self.assertNotIn(sensitive, str(dict(finding.to_dict())))
        self.assertFalse(hasattr(finding, "match"))
        self.assertFalse(hasattr(finding, "context"))

    def test_fingerprints_are_stable_and_rule_separated(self) -> None:
        token = "ghp_" + "A" * 36
        text = f"token={token}"

        first = scan_text("input.txt", text)
        second = scan_text("input.txt", text)
        fingerprints = {
            finding.rule_id: finding.fingerprint
            for finding in first.findings
        }

        self.assertEqual(first, second)
        self.assertEqual(len(fingerprints), 2)
        self.assertNotEqual(
            fingerprints["PRIV-SECRET-004"],
            fingerprints["PRIV-SECRET-005"],
        )

    def test_fingerprint_does_not_encode_the_matched_value(self) -> None:
        first_value = "credential-" + "value-1234"
        second_value = "different-" + "value-5678"

        first = scan_text("input.txt", f"password={first_value}")
        second = scan_text("input.txt", f"password={second_value}")

        self.assertEqual(
            first.findings[0].fingerprint,
            second.findings[0].fingerprint,
        )

    def test_duplicate_findings_on_one_line_are_collapsed(self) -> None:
        address = "10." + "20.30.40"
        result = scan_text("input.txt", f"{address} and {address}")

        self.assertEqual(len(result.findings), 1)

    def test_findings_are_ordered_by_line_then_rule(self) -> None:
        token = "ghp_" + "B" * 36
        address = "172." + "20.30.40"
        result = scan_text(
            "input.txt",
            f"token={token}\nendpoint={address}",
        )

        coordinates = [
            (finding.line, finding.rule_id)
            for finding in result.findings
        ]
        self.assertEqual(coordinates, sorted(coordinates))

    def test_rules_can_be_narrowly_enabled(self) -> None:
        token = "ghp_" + "C" * 36
        result = scan_text(
            "input.txt",
            f"token={token}",
            policy=ScanPolicy(
                enabled_rule_ids=frozenset({"PRIV-SECRET-005"})
            ),
        )

        self.assertEqual(
            [finding.rule_id for finding in result.findings],
            ["PRIV-SECRET-005"],
        )

    def test_unknown_rule_fails_safely(self) -> None:
        with self.assertRaises(ScanError) as raised:
            scan_text(
                "input.txt",
                "safe",
                policy=ScanPolicy(
                    enabled_rule_ids=frozenset({"UNKNOWN"})
                ),
            )

        self.assertEqual(raised.exception.code, "unknown_rule")

    def test_non_string_input_fails_safely(self) -> None:
        with self.assertRaises(ScanError) as raised:
            scan_text("input.txt", b"unsafe")  # type: ignore[arg-type]

        self.assertEqual(raised.exception.code, "invalid_text")


if __name__ == "__main__":
    unittest.main()
