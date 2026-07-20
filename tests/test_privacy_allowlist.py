from __future__ import annotations

import hashlib
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.privacy import (  # noqa: E402
    ContentType,
    ScanPolicy,
    SourceOwnership,
)
from byte_core.privacy.allowlist import (  # noqa: E402
    AllowlistEntry,
    AllowlistPolicy,
    scan_text_with_allowlist,
)

TODAY = date(2026, 7, 20)
SOURCE_ID = "docs/fictional-example.md"
TEXT = "endpoint = service." + "fictional.invalid"
POLICY = ScanPolicy(content_type=ContentType.PUBLIC_EXAMPLE)


class PrivacyAllowlistTests(unittest.TestCase):
    def test_exact_public_false_positive_is_visible_and_allowed(self) -> None:
        review = self._review(self._entry())

        self.assertTrue(review.passed)
        self.assertEqual(review.active_findings, ())
        self.assertEqual(len(review.allowed_findings), 1)
        self.assertEqual(
            review.allowed_findings[0].rule_id,
            "PRIV-IDENTITY-006",
        )
        self.assertEqual(review.errors, ())

    def test_allowlist_models_copy_entries_and_are_immutable(self) -> None:
        entries = [self._entry()]
        policy = AllowlistPolicy(1, entries)  # type: ignore[arg-type]
        entries.clear()

        self.assertEqual(len(policy.entries), 1)
        with self.assertRaises(FrozenInstanceError):
            policy.schema_version = 2  # type: ignore[misc]

    def test_non_public_sources_cannot_use_allowlisting(self) -> None:
        for ownership in (
            SourceOwnership.DEPLOYMENT_OWNED,
            SourceOwnership.DIAGNOSTIC,
        ):
            with self.subTest(ownership=ownership):
                review = self._review(
                    self._entry(),
                    ownership=ownership,
                )
                self.assertFalse(review.passed)
                self.assertEqual(review.allowed_findings, ())
                self.assertIn(
                    "allowlist_scope_forbidden",
                    self._error_codes(review),
                )

    def test_secret_rule_cannot_be_allowlisted(self) -> None:
        secret = "password=" + "synthetic-value"
        entry = replace(
            self._entry(text=secret),
            rule_id="PRIV-SECRET-004",
        )

        review = self._review(entry, text=secret)

        self.assertFalse(review.passed)
        self.assertEqual(review.allowed_findings, ())
        self.assertIn("forbidden_rule", self._error_codes(review))

    def test_changed_source_invalidates_entry(self) -> None:
        changed = TEXT + "\nadditional public text"

        review = self._review(self._entry(), text=changed)

        self.assertFalse(review.passed)
        self.assertIn("source_digest_mismatch", self._error_codes(review))
        self.assertEqual(review.allowed_findings, ())

    def test_moved_finding_invalidates_entry(self) -> None:
        moved = "heading\n" + TEXT
        entry = self._entry(text=moved)

        review = self._review(entry, text=moved)

        self.assertFalse(review.passed)
        self.assertIn("unused_entry", self._error_codes(review))

    def test_expired_and_future_entries_fail_closed(self) -> None:
        expired = replace(
            self._entry(),
            reviewed_on=date(2025, 1, 1),
            expires_on=date(2026, 7, 19),
        )
        future = replace(
            self._entry(),
            reviewed_on=date(2026, 7, 21),
            expires_on=date(2027, 1, 1),
        )

        expired_review = self._review(expired)
        future_review = self._review(future)

        self.assertIn("expired_entry", self._error_codes(expired_review))
        self.assertIn(
            "future_review_date",
            self._error_codes(future_review),
        )

    def test_duplicate_entries_fail_closed(self) -> None:
        entry = self._entry()
        review = scan_text_with_allowlist(
            SOURCE_ID,
            TEXT,
            source_ownership=SourceOwnership.CORE_PUBLIC,
            allowlist=AllowlistPolicy(1, (entry, entry)),
            as_of=TODAY,
            scan_policy=POLICY,
        )

        self.assertFalse(review.passed)
        self.assertIn("duplicate_entry", self._error_codes(review))
        self.assertEqual(review.allowed_findings, ())

    def test_unknown_schema_rule_and_invalid_fields_fail_closed(self) -> None:
        invalid = replace(
            self._entry(),
            rule_id="UNKNOWN",
            source_id="../private.txt",
            line=0,
            source_sha256="not-a-digest",
            justification=" ",
        )
        review = scan_text_with_allowlist(
            SOURCE_ID,
            TEXT,
            source_ownership=SourceOwnership.CORE_PUBLIC,
            allowlist=AllowlistPolicy(2, (invalid,)),
            as_of=TODAY,
            scan_policy=POLICY,
        )

        self.assertEqual(
            self._error_codes(review),
            {
                "unsupported_allowlist_schema",
                "unknown_rule",
                "invalid_source_id",
                "invalid_line",
                "invalid_source_digest",
                "missing_justification",
            },
        )
        self.assertEqual(review.allowed_findings, ())

    def test_unrelated_source_entry_does_not_fail_single_source_scan(self) -> None:
        unrelated = replace(
            self._entry(),
            source_id="docs/another-example.md",
        )

        review = self._review(unrelated)

        self.assertFalse(review.passed)
        self.assertEqual(review.errors, ())
        self.assertEqual(len(review.active_findings), 1)
        self.assertEqual(review.allowed_findings, ())

    def test_errors_do_not_echo_policy_or_source_content(self) -> None:
        sensitive = "private-" + "policy-fragment"
        invalid = replace(
            self._entry(),
            source_id=f"../{sensitive}.txt",
            justification=sensitive,
        )

        review = self._review(invalid)
        rendered = repr(review.errors)

        self.assertNotIn(sensitive, rendered)
        self.assertNotIn(TEXT, rendered)

    def _entry(self, *, text: str = TEXT) -> AllowlistEntry:
        return AllowlistEntry(
            rule_id="PRIV-IDENTITY-006",
            source_id=SOURCE_ID,
            line=1,
            source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            justification="Verified fictional public example.",
            reviewed_on=TODAY,
            expires_on=date(2027, 1, 20),
        )

    def _review(
        self,
        entry: AllowlistEntry,
        *,
        text: str = TEXT,
        ownership: SourceOwnership = SourceOwnership.CORE_PUBLIC,
    ):
        return scan_text_with_allowlist(
            SOURCE_ID,
            text,
            source_ownership=ownership,
            allowlist=AllowlistPolicy(1, (entry,)),
            as_of=TODAY,
            scan_policy=POLICY,
        )

    def _error_codes(self, review) -> set[str]:
        return {error.code for error in review.errors}


if __name__ == "__main__":
    unittest.main()
