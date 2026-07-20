from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GATE = REPOSITORY_ROOT / "scripts" / "check_release_privacy.py"


class PrivacyReleaseGateTests(unittest.TestCase):
    def test_safe_release_artifact_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text(
                "Fictional endpoint: service.example.test at 192.0.2.10",
                encoding="utf-8",
            )

            completed = self._run(root)

        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("findings=0", completed.stdout)

    def test_known_release_leakage_is_blocked_without_echoing_it(self) -> None:
        secret = "password=" + "synthetic-ci-fixture"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "release.txt").write_text(secret, encoding="utf-8")

            completed = self._run(root)

        rendered = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 1, rendered)
        self.assertIn("PRIV-SECRET-004", rendered)
        self.assertNotIn(secret, rendered)

    def test_invalid_invocation_fails_closed(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(GATE)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

        self.assertEqual(completed.returncode, 2)

    def _run(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GATE), str(root)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )


if __name__ == "__main__":
    unittest.main()
