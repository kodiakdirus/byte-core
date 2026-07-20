from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.privacy.adapters import scan_staged  # noqa: E402
from byte_core.privacy.adapters.staged import (  # noqa: E402
    _run_git_limited,
)


class PrivacyStagedAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("Git is unavailable")
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self._git("init", "-q")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scans_index_blob_not_changed_worktree_content(self) -> None:
        path = self.root / "example.txt"
        path.write_text("safe staged content", encoding="utf-8")
        self._git("add", "--", "example.txt")
        path.write_text(
            "password=" + "synthetic-value",
            encoding="utf-8",
        )
        before = self._state()

        result = scan_staged(self.root)

        self.assertTrue(result.passed)
        self.assertEqual(len(result.scans), 1)
        self.assertEqual(result.scans[0].findings, ())
        self.assertEqual(before, self._state())

    def test_detects_secret_in_staged_content_without_echoing_it(self) -> None:
        value = "password=" + "synthetic-value"
        (self.root / "example.txt").write_text(value, encoding="utf-8")
        self._git("add", "--", "example.txt")

        result = scan_staged(self.root)

        self.assertFalse(result.passed)
        finding = result.scans[0].findings[0]
        self.assertEqual(finding.source_id, "example.txt")
        self.assertNotIn(value, repr(finding))

    def test_rename_scans_new_path_and_deletion_has_no_content(self) -> None:
        self._commit_file("old.txt", "safe")
        self._git("mv", "old.txt", "new.txt")

        renamed = scan_staged(self.root)

        self.assertTrue(renamed.passed)
        self.assertEqual(
            [scan.source_id for scan in renamed.scans],
            ["new.txt"],
        )

        self._git("reset", "--hard", "-q", "HEAD")
        self._git("rm", "-q", "old.txt")
        deleted = scan_staged(self.root)

        self.assertTrue(deleted.passed)
        self.assertEqual(deleted.scans, ())

    def test_copy_scans_new_staged_path(self) -> None:
        self._commit_file("source.txt", "safe copied content")
        shutil.copyfile(self.root / "source.txt", self.root / "copy.txt")
        self._git("add", "--", "copy.txt")

        result = scan_staged(self.root)

        self.assertTrue(result.passed)
        self.assertEqual(
            [scan.source_id for scan in result.scans],
            ["copy.txt"],
        )

    def test_rejects_binary_unsupported_and_oversized_staged_files(self) -> None:
        (self.root / "binary.txt").write_bytes(b"a\0")
        (self.root / "image.png").write_bytes(b"not-an-image")
        (self.root / "large.txt").write_bytes(b"x" * 6)
        self._git("add", "--", "binary.txt", "image.png", "large.txt")

        result = scan_staged(
            self.root,
            limits=self._limits(max_file_bytes=5),
        )

        self.assertEqual(
            {error.code for error in result.errors},
            {"binary_file", "unsupported_file_type", "file_too_large"},
        )

    def test_rejects_staged_gitlink_mode(self) -> None:
        self._commit_file("base.txt", "safe")
        head = self._git("rev-parse", "HEAD").stdout.decode("ascii").strip()
        self._git(
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{head},vendor/module",
        )

        result = scan_staged(self.root)

        self.assertEqual(
            {error.code for error in result.errors},
            {"unsupported_git_mode"},
        )
        self.assertEqual(result.scans, ())

    def test_invalid_repository_and_git_timeout_are_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            invalid = scan_staged(outside)
        with patch(
            "byte_core.privacy.adapters.staged.subprocess.run",
            side_effect=subprocess.TimeoutExpired("private-command", 1),
        ):
            timed_out = scan_staged(self.root)

        self.assertEqual(
            {error.code for error in invalid.errors},
            {"not_git_repository"},
        )
        self.assertEqual(
            {error.code for error in timed_out.errors},
            {"git_timeout"},
        )
        self.assertNotIn("private-command", repr(timed_out.errors))

    def test_git_path_listing_output_is_bounded_before_materialization(self) -> None:
        (self.root / "example.txt").write_text("safe", encoding="utf-8")
        self._git("add", "--", "example.txt")

        result, error = _run_git_limited(
            self.root,
            (
                "diff",
                "--cached",
                "--name-only",
                "-z",
                "--diff-filter=ACMR",
                "--no-ext-diff",
            ),
            max_stdout=1,
        )

        self.assertEqual(error, "git_output_too_large")
        self.assertEqual(result.stdout, b"")

    def _commit_file(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._git("add", "--", relative)
        self._git("-c", "user.name=Example", "-c", "user.email=example@example.com", "commit", "-qm", "fixture")

    def _state(self) -> tuple[bytes, bytes]:
        return (
            self._git("status", "--porcelain=v1", "-z").stdout,
            self._git("diff", "--cached", "--raw", "-z").stdout,
        )

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ("git", "-C", str(self.root), *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def _limits(self, **changes):
        from byte_core.privacy.adapters import AdapterLimits

        return AdapterLimits(**changes)


if __name__ == "__main__":
    unittest.main()
