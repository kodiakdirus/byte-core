from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.privacy.adapters import (  # noqa: E402
    AdapterLimits,
    scan_artifact_directory,
)


class PrivacyArtifactAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scans_complete_tree_deterministically_without_mutation(self) -> None:
        self._write("z-last.txt", "safe")
        self._write("docs/a-first.md", "safe")
        before = self._hashes()

        result = scan_artifact_directory(self.root)

        self.assertTrue(result.passed)
        self.assertEqual(
            [scan.source_id for scan in result.scans],
            ["docs/a-first.md", "z-last.txt"],
        )
        self.assertEqual(before, self._hashes())

    def test_empty_unsupported_binary_and_deep_artifacts_fail_closed(self) -> None:
        empty = scan_artifact_directory(self.root)

        self._write("archive.zip", "unsupported")
        unsupported = scan_artifact_directory(self.root)

        (self.root / "archive.zip").unlink()
        self._write_bytes("binary.txt", b"safe\0binary")
        binary = scan_artifact_directory(self.root)

        (self.root / "binary.txt").unlink()
        self._write("one/two/three/file.txt", "safe")
        deep = scan_artifact_directory(
            self.root,
            limits=AdapterLimits(max_depth=3),
        )

        self.assertEqual(self._codes(empty), {"no_inputs"})
        self.assertEqual(self._codes(unsupported), {"unsupported_file_type"})
        self.assertEqual(self._codes(binary), {"binary_file"})
        self.assertEqual(self._codes(deep), {"path_too_deep"})

    def test_bounds_directory_entries_as_well_as_files(self) -> None:
        for index in range(5):
            (self.root / f"empty-{index}").mkdir()

        result = scan_artifact_directory(
            self.root,
            limits=AdapterLimits(max_files=1),
        )

        self.assertEqual(self._codes(result), {"too_many_entries"})
        self.assertEqual(result.scans, ())

    def test_simulated_link_is_rejected_without_platform_support(self) -> None:
        self._write("linked.txt", "safe")

        with patch(
            "byte_core.privacy.adapters.artifacts._is_link_like",
            side_effect=lambda path: path.name == "linked.txt",
        ):
            result = scan_artifact_directory(self.root)

        self.assertEqual(self._codes(result), {"symlink_forbidden"})
        self.assertEqual(result.scans, ())

    def _write(self, relative: str, content: str) -> None:
        path = self.root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_bytes(self, relative: str, content: bytes) -> None:
        path = self.root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

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
