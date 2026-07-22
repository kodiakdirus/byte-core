from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
TEMPLATES = REPOSITORY_ROOT / "templates" / "canonical"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.canonical_documents import (  # noqa: E402
    ROLE_FILENAMES,
    validate_canonical_documents,
)


class CanonicalDocumentTests(unittest.TestCase):
    def test_starter_templates_are_valid_and_read_only(self) -> None:
        before = self._hashes(TEMPLATES)

        result = validate_canonical_documents(TEMPLATES)

        self.assertTrue(result.passed)
        self.assertEqual(result.errors, ())
        self.assertEqual(
            tuple(document.role for document in result.documents),
            tuple(ROLE_FILENAMES),
        )
        self.assertEqual(before, self._hashes(TEMPLATES))

    def test_missing_role_fails(self) -> None:
        with self._documents() as root:
            (root / "notebook.md").unlink()

            result = validate_canonical_documents(root)

        self.assertIn("missing_role", self._codes(result))

    def test_duplicate_role_and_unexpected_filename_fail(self) -> None:
        with self._documents() as root:
            shutil.copyfile(root / "manifest.md", root / "duplicate.md")

            result = validate_canonical_documents(root)

        self.assertIn("duplicate_role", self._codes(result))

    def test_schema_mismatch_and_unsupported_schema_fail(self) -> None:
        with self._documents() as root:
            path = root / "notebook.md"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "schema=1 role=notebook",
                    "schema=2 role=notebook",
                ),
                encoding="utf-8",
            )

            result = validate_canonical_documents(root)

        self.assertIn("schema_mismatch", self._codes(result))
        self.assertIn("unsupported_schema", self._codes(result))

    def test_invalid_marker_and_missing_title_fail(self) -> None:
        with self._documents() as root:
            manifest = root / "manifest.md"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "schema=1 role=manifest",
                    "role=manifest schema=1",
                ),
                encoding="utf-8",
            )
            runbook = root / "runbook.md"
            runbook.write_text(
                runbook.read_text(encoding="utf-8").replace(
                    "# Runbook",
                    "# Procedures",
                ),
                encoding="utf-8",
            )

            result = validate_canonical_documents(root)

        self.assertIn("invalid_marker", self._codes(result))
        self.assertIn("missing_role", self._codes(result))
        self.assertIn("missing_title", self._codes(result))

    def test_broken_local_link_fails_without_exposing_root(self) -> None:
        with self._documents() as root:
            manifest = root / "manifest.md"
            manifest.write_text(
                manifest.read_text(encoding="utf-8")
                + "\nSee [missing](missing.md).\n",
                encoding="utf-8",
            )

            result = validate_canonical_documents(root)

            self.assertIn("broken_link", self._codes(result))
            self.assertNotIn(str(root), repr(result))

    def test_escaping_external_and_broken_fragment_links_fail(self) -> None:
        with self._documents() as root:
            manifest = root / "manifest.md"
            manifest.write_text(
                manifest.read_text(encoding="utf-8")
                + "\n[escape](../outside.md)\n"
                + "[external](https://example.com/)\n"
                + "[fragment](runbook.md#missing-section)\n",
                encoding="utf-8",
            )

            result = validate_canonical_documents(root)

        self.assertIn("link_escape", self._codes(result))
        self.assertIn("external_link_forbidden", self._codes(result))
        self.assertIn("broken_fragment", self._codes(result))

    def test_valid_heading_fragment_passes(self) -> None:
        with self._documents() as root:
            manifest = root / "manifest.md"
            manifest.write_text(
                manifest.read_text(encoding="utf-8")
                + "\nSee [procedures](runbook.md#procedures).\n",
                encoding="utf-8",
            )

            result = validate_canonical_documents(root)

        self.assertTrue(result.passed, result.errors)

    def test_symlinked_document_fails_when_supported(self) -> None:
        with self._documents() as root:
            target = root / "manifest-target.md"
            (root / "manifest.md").replace(target)
            try:
                (root / "manifest.md").symlink_to(target.name)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are unavailable")

            result = validate_canonical_documents(root)

        self.assertIn("link_forbidden", self._codes(result))
        self.assertIn("unexpected_filename", self._codes(result))

    def test_invalid_root_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            result = validate_canonical_documents(missing)

        self.assertEqual(self._codes(result), {"invalid_root"})

    def _documents(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "documents"
        shutil.copytree(TEMPLATES, root)

        class Documents:
            def __enter__(self):
                return root

            def __exit__(self, *_args):
                temporary.cleanup()

        return Documents()

    def _codes(self, result) -> set[str]:
        return {error.code for error in result.errors}

    def _hashes(self, root: Path) -> dict[str, str]:
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.glob("*.md"))
        }


if __name__ == "__main__":
    unittest.main()
