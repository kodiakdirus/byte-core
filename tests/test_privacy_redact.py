from __future__ import annotations

import pickle
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.privacy import (  # noqa: E402
    RedactionContext,
    RedactionError,
)

_PLACEHOLDER = re.compile(
    r"^<(?:path|identity|address):[0-9a-f]{16}>$"
)


class PrivacyRedactionTests(unittest.TestCase):
    def test_same_value_and_class_are_stable_within_context(self) -> None:
        context = self._context(b"A" * 32)
        value = "/home/" + "example-user/private"

        first = context.redact_path(value)
        second = context.redact_path(value)

        self.assertEqual(first, second)
        self.assertRegex(first, _PLACEHOLDER)
        self.assertNotIn("example-user", first)
        self.assertNotIn("private", first)

    def test_value_classes_use_separate_domains(self) -> None:
        context = self._context(b"B" * 32)
        value = "shared-" + "fictional-value"

        outputs = {
            context.redact_path(value),
            context.redact_identity(value),
            context.redact_address(value),
        }

        self.assertEqual(len(outputs), 3)
        self.assertTrue(all(_PLACEHOLDER.fullmatch(item) for item in outputs))

    def test_different_contexts_cannot_be_correlated_by_default(self) -> None:
        first = self._context(b"C" * 32)
        second = self._context(b"D" * 32)
        value = "10." + "20.30.40"

        self.assertNotEqual(
            first.redact_address(value),
            second.redact_address(value),
        )

    def test_values_are_hashed_exactly_without_normalization(self) -> None:
        context = self._context(b"E" * 32)

        self.assertNotEqual(
            context.redact_identity("Example-User"),
            context.redact_identity("example-user"),
        )
        self.assertNotEqual(
            context.redact_path("path/to/example"),
            context.redact_path("path\\to\\example"),
        )

    def test_invalid_values_fail_without_echoing_them(self) -> None:
        context = self._context(b"F" * 32)
        invalid = (None, b"bytes", "", " \t\n")

        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(RedactionError) as raised:
                    context.redact_identity(value)  # type: ignore[arg-type]
                self.assertEqual(
                    raised.exception.code,
                    "invalid_redaction_value",
                )

    def test_oversized_value_fails_without_echoing_it(self) -> None:
        context = self._context(b"G" * 32)
        marker = "private-marker"
        value = marker + "x" * 1_000_000

        with self.assertRaises(RedactionError) as raised:
            context.redact_identity(value)

        self.assertEqual(
            raised.exception.code,
            "redaction_value_too_large",
        )
        self.assertNotIn(marker, str(raised.exception))

    def test_unencodable_unicode_fails_safely(self) -> None:
        context = self._context(b"H" * 32)

        with self.assertRaises(RedactionError) as raised:
            context.redact_identity("\ud800")

        self.assertEqual(raised.exception.code, "invalid_redaction_value")

    def test_representation_and_serialization_do_not_expose_key(self) -> None:
        key = b"I" * 32
        context = self._context(key)

        self.assertEqual(repr(context), "RedactionContext(<ephemeral>)")
        self.assertNotIn(key.hex(), repr(context))
        self.assertFalse(hasattr(context, "to_dict"))
        with self.assertRaisesRegex(
            TypeError,
            "redaction_context_not_serializable",
        ):
            pickle.dumps(context)

    def test_key_generation_failure_is_sanitized(self) -> None:
        marker = "provider-private-detail"
        with patch(
            "byte_core.privacy.redact.secrets.token_bytes",
            side_effect=RuntimeError(marker),
        ):
            with self.assertRaises(RedactionError) as raised:
                RedactionContext.create()

        self.assertEqual(raised.exception.code, "key_generation_failed")
        self.assertNotIn(marker, str(raised.exception))

    def test_invalid_provider_result_is_rejected(self) -> None:
        with patch(
            "byte_core.privacy.redact.secrets.token_bytes",
            return_value=b"short",
        ):
            with self.assertRaises(RedactionError) as raised:
                RedactionContext.create()

        self.assertEqual(raised.exception.code, "key_generation_failed")

    def _context(self, key: bytes) -> RedactionContext:
        with patch(
            "byte_core.privacy.redact.secrets.token_bytes",
            return_value=key,
        ):
            return RedactionContext.create()


if __name__ == "__main__":
    unittest.main()
