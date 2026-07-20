from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.privacy import (  # noqa: E402
    SourceOwnership,
)
from byte_core.privacy.adapters import (  # noqa: E402
    AdapterLimits,
    AdapterResult,
    scan_files,
)


class PrivacyFileAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scans_only_explicit_files_in_deterministic_order(self) -> None:
        self._write("z-last.txt", "safe text")
        self._write("docs/a-first.md", "safe documentation")
        ignored_content = "password=" + "synthetic-value"
        ignored = self._write("not-selected.txt", ignored_content)
        before = self._hashes()

        result = self._scan(("z-last.txt", "docs/a-first.md"))

        self.assertTrue(result.passed)
        self.assertEqual(
            [scan.source_id for scan in result.scans],
            ["docs/a-first.md", "z-last.txt"],
        )
        self.assertEqual(before, self._hashes())
        self.assertEqual(
            ignored.read_text(encoding="utf-8"),
            ignored_content,
        )

    def test_scanner_findings_use_only_logical_relative_source(self) -> None:
        value = "password=" + "synthetic-value"
        self._write("docs/example.md", value)

        result = self._scan(("docs/example.md",))

        self.assertFalse(result.passed)
        finding = result.scans[0].findings[0]
        self.assertEqual(finding.source_id, "docs/example.md")
        self.assertNotIn(str(self.root), repr(finding))
        self.assertNotIn(value, repr(finding))

    def test_non_public_ownership_is_rejected_before_root_access(self) -> None:
        missing = self.root / "does-not-exist"

        result = scan_files(
            missing,
            ("file.txt",),
            ownership=SourceOwnership.DIAGNOSTIC,
        )

        self.assertEqual(self._codes(result), {"ownership_forbidden"})
        self.assertEqual(result.scans, ())

    def test_root_and_input_collection_are_required(self) -> None:
        missing = self.root / "missing"

        invalid_root = self._scan(("file.txt",), root=missing)
        no_inputs = self._scan(())
        string_paths = scan_files(
            self.root,
            "file.txt",  # type: ignore[arg-type]
            ownership=SourceOwnership.CORE_PUBLIC,
        )

        self.assertEqual(self._codes(invalid_root), {"invalid_root"})
        self.assertEqual(self._codes(no_inputs), {"no_inputs"})
        self.assertEqual(self._codes(string_paths), {"invalid_paths"})

    def test_rejects_invalid_duplicate_deep_and_unsupported_paths(self) -> None:
        self._write("safe.txt", "safe")
        self._write("image.png", "not really an image")
        deep = "one/two/three/file.txt"
        self._write(deep, "safe")

        result = self._scan(
            (
                "../escape.txt",
                str(self.root / "absolute.txt"),
                "safe.txt",
                "safe.txt",
                deep,
                "image.png",
            ),
            limits=AdapterLimits(max_depth=3),
        )

        self.assertEqual(
            self._codes(result),
            {
                "invalid_path",
                "duplicate_path",
                "path_too_deep",
                "unsupported_file_type",
            },
        )

    def test_rejects_directories_missing_files_and_too_many_inputs(self) -> None:
        (self.root / "directory.txt").mkdir()
        self._write("safe.txt", "safe")

        result = self._scan(
            ("directory.txt", "missing.txt", "safe.txt"),
            limits=AdapterLimits(max_files=2),
        )

        self.assertEqual(self._codes(result), {"too_many_files"})

        bounded = self._scan(("directory.txt", "missing.txt"))
        self.assertEqual(
            self._codes(bounded),
            {"not_regular_file", "path_not_found"},
        )

    def test_stops_consuming_unbounded_iterable_at_limit_plus_one(self) -> None:
        consumed = 0

        def endless_paths():
            nonlocal consumed
            while True:
                consumed += 1
                yield f"file-{consumed}.txt"

        result = self._scan(
            endless_paths(),
            limits=AdapterLimits(max_files=2),
        )

        self.assertEqual(self._codes(result), {"too_many_files"})
        self.assertEqual(consumed, 3)

    def test_sanitizes_iterable_failures(self) -> None:
        private_detail = "do-not-echo-this-iterator-detail"

        def broken_paths():
            yield "safe.txt"
            raise RuntimeError(private_detail)

        result = self._scan(broken_paths())

        self.assertEqual(self._codes(result), {"invalid_paths"})
        self.assertNotIn(private_detail, repr(result))

    def test_rejects_oversized_aggregate_binary_and_invalid_utf8(self) -> None:
        self._write_bytes("large.txt", b"a" * 6)
        self._write_bytes("second.txt", b"b" * 4)
        self._write_bytes("binary.txt", b"safe\0binary")
        self._write_bytes("invalid.txt", b"\xff\xfe")

        oversized = self._scan(
            ("large.txt",),
            limits=AdapterLimits(max_file_bytes=5),
        )
        aggregate = self._scan(
            ("large.txt", "second.txt"),
            limits=AdapterLimits(max_total_bytes=8),
        )
        binary = self._scan(("binary.txt",))
        invalid = self._scan(("invalid.txt",))

        self.assertEqual(self._codes(oversized), {"file_too_large"})
        self.assertEqual(self._codes(aggregate), {"aggregate_too_large"})
        self.assertEqual(self._codes(binary), {"binary_file"})
        self.assertEqual(self._codes(invalid), {"invalid_utf8"})

    def test_rejects_symlink_file_and_symlink_component(self) -> None:
        target = self._write("target.txt", "safe")
        directory = self.root / "real-directory"
        directory.mkdir()
        self._write("real-directory/nested.txt", "safe")
        file_link = self.root / "linked.txt"
        directory_link = self.root / "linked-directory"

        try:
            file_link.symlink_to(target)
            directory_link.symlink_to(directory, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")

        result = self._scan(
            ("linked.txt", "linked-directory/nested.txt")
        )

        self.assertEqual(self._codes(result), {"symlink_forbidden"})
        self.assertEqual(result.scans, ())

    def test_rejects_symlink_root(self) -> None:
        real_root = self.root / "real-root"
        real_root.mkdir()
        link_root = self.root / "link-root"
        try:
            link_root.symlink_to(real_root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")

        result = self._scan(("safe.txt",), root=link_root)

        self.assertEqual(self._codes(result), {"root_link_forbidden"})

    def test_link_rejection_paths_are_exercised_without_os_support(self) -> None:
        self._write("linked.txt", "safe")

        with patch(
            "byte_core.privacy.adapters.files._is_link_like",
            side_effect=lambda path: path.name == "linked.txt",
        ):
            linked = self._scan(("linked.txt",))
        with patch(
            "byte_core.privacy.adapters.files._is_link_like",
            return_value=True,
        ):
            linked_root = self._scan(("linked.txt",))

        self.assertEqual(self._codes(linked), {"symlink_forbidden"})
        self.assertEqual(
            self._codes(linked_root),
            {"root_link_forbidden"},
        )

    def test_limits_and_results_are_immutable_and_bounded(self) -> None:
        with self.assertRaises(ValueError):
            AdapterLimits(max_files=0)
        with self.assertRaises(ValueError):
            AdapterLimits(max_file_bytes=16_000_001)
        with self.assertRaises(ValueError):
            AdapterLimits(max_total_bytes=64_000_001)
        with self.assertRaises(ValueError):
            AdapterLimits(max_depth=101)

        scans = []
        errors = []
        result = AdapterResult(scans, errors)  # type: ignore[arg-type]
        scans.append("unsafe")
        errors.append("unsafe")

        self.assertEqual(result.scans, ())
        self.assertEqual(result.errors, ())
        with self.assertRaises(FrozenInstanceError):
            result.errors = ()  # type: ignore[misc]

    def _scan(
        self,
        paths,
        *,
        root: Path | None = None,
        limits: AdapterLimits | None = None,
    ):
        return scan_files(
            root or self.root,
            paths,
            ownership=SourceOwnership.CORE_PUBLIC,
            limits=limits,
        )

    def _write(self, relative: str, content: str) -> Path:
        path = self.root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_bytes(self, relative: str, content: bytes) -> Path:
        path = self.root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _hashes(self) -> dict[str, str]:
        return {
            path.relative_to(self.root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
        }

    def _codes(self, result) -> set[str]:
        return {error.code for error in result.errors}


if __name__ == "__main__":
    unittest.main()
