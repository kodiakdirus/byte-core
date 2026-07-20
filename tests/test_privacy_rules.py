from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.privacy import (  # noqa: E402
    ContentType,
    ScanPolicy,
    scan_text,
)


class PrivacyRuleTests(unittest.TestCase):
    def test_high_confidence_secret_rules(self) -> None:
        cases = {
            "PRIV-SECRET-001": "-----BEGIN "
            + "PRIVATE KEY-----\nsynthetic",
            "PRIV-SECRET-002": "Authorization: Bearer "
            + "synthetic-value",
            "PRIV-SECRET-003": "https://example-user:"
            + "synthetic-pass@example.test/resource",
            "PRIV-SECRET-004": "api_key=" + "synthetic-value",
            "PRIV-SECRET-005": "ghp_" + "D" * 36,
        }

        for expected_rule, text in cases.items():
            with self.subTest(rule=expected_rule):
                result = scan_text("fixture.txt", text)
                self.assertIn(
                    expected_rule,
                    {finding.rule_id for finding in result.findings},
                )

    def test_documentation_addresses_pass(self) -> None:
        text = "\n".join(
            (
                "192.0.2.42",
                "198.51.100.8",
                "203.0.113.99",
                "2001:db8::42",
            )
        )

        result = scan_text("fixture.txt", text)

        self.assertTrue(result.passed)
        self.assertEqual(result.findings, ())

    def test_non_documentation_addresses_are_detected(self) -> None:
        ipv4 = "10." + "42.0.8"
        ipv6 = "fd00" + "::42"
        result = scan_text("fixture.txt", f"{ipv4}\n{ipv6}")

        self.assertEqual(
            {finding.rule_id for finding in result.findings},
            {"PRIV-IDENTITY-001", "PRIV-IDENTITY-002"},
        )

    def test_user_home_paths_are_detected_without_being_reported(self) -> None:
        windows_path = "C:" + "\\Users\\ExampleUser\\project"
        unix_path = "/home/" + "example-user/project"
        text = f"{windows_path}\n{unix_path}"

        result = scan_text("fixture.txt", text)
        serialized = str(
            [dict(finding.to_dict()) for finding in result.findings]
        )

        self.assertEqual(
            {finding.rule_id for finding in result.findings},
            {"PRIV-IDENTITY-003", "PRIV-IDENTITY-004"},
        )
        self.assertNotIn(windows_path, serialized)
        self.assertNotIn(unix_path, serialized)

    def test_connection_user_information_is_detected(self) -> None:
        url = "ssh://" + "example-user@example.test/resource"
        result = scan_text("fixture.txt", url)

        self.assertIn(
            "PRIV-IDENTITY-005",
            {finding.rule_id for finding in result.findings},
        )

    def test_reserved_domains_pass_in_public_examples(self) -> None:
        policy = ScanPolicy(content_type=ContentType.PUBLIC_EXAMPLE)
        text = "example.com host.example.net service.example.org node.test"

        result = scan_text("fixture.txt", text, policy=policy)

        self.assertTrue(result.passed)

    def test_non_reserved_domain_is_scoped_to_public_examples(self) -> None:
        domain = "service." + "invalid-public-domain.dev"

        general = scan_text("documentation.md", domain)
        example = scan_text(
            "fixture.txt",
            domain,
            policy=ScanPolicy(content_type=ContentType.PUBLIC_EXAMPLE),
        )

        self.assertNotIn(
            "PRIV-IDENTITY-006",
            {finding.rule_id for finding in general.findings},
        )
        self.assertIn(
            "PRIV-IDENTITY-006",
            {finding.rule_id for finding in example.findings},
        )

    def test_near_misses_do_not_trigger_secret_rules(self) -> None:
        text = "\n".join(
            (
                "password=",
                "token placeholder",
                "Authorization is documented separately.",
                "-----BEGIN PUBLIC KEY-----",
            )
        )

        result = scan_text("fixture.txt", text)

        self.assertFalse(
            any(
                finding.rule_id.startswith("PRIV-SECRET-")
                for finding in result.findings
            )
        )


if __name__ == "__main__":
    unittest.main()
