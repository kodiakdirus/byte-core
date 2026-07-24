from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
BUILDER = REPOSITORY_ROOT / "scripts" / "build_release_artifact.py"
GATE = REPOSITORY_ROOT / "scripts" / "check_v01_release.py"
PACKAGER = REPOSITORY_ROOT / "scripts" / "package_release_candidate.py"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.installation import load_release_descriptor  # noqa: E402
from byte_core.privacy.adapters import scan_artifact_directory  # noqa: E402


def _load_builder():
    spec = importlib.util.spec_from_file_location("release_builder", BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError("builder import unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_gate():
    spec = importlib.util.spec_from_file_location("release_gate", GATE)
    if spec is None or spec.loader is None:
        raise RuntimeError("gate import unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_packager():
    spec = importlib.util.spec_from_file_location(
        "release_packager", PACKAGER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("packager import unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseArtifactTests(unittest.TestCase):
    def test_build_is_reproducible_integrity_valid_and_runnable(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            first = builder.build("0.1.0", parent / "first")
            second = builder.build("0.1.0", parent / "second")

            first_files = self._contents(first)
            second_files = self._contents(second)
            self.assertEqual(first_files, second_files)
            descriptor = load_release_descriptor(first / "release.json")
            self.assertEqual(descriptor.core_version, "0.1.0")
            self.assertEqual(
                {item.relative_path for item in descriptor.files},
                set(first_files) - {"release.json"},
            )
            completed = subprocess.run(
                [str(first / "bin" / "byte"), "--help"],
                cwd=first,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("self-managed Byte deployment", completed.stdout)

    def test_candidate_artifact_passes_privacy_gate(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as temporary:
            artifact = builder.build("0.1.0", Path(temporary) / "artifact")
            result = scan_artifact_directory(artifact)

            self.assertTrue(
                result.passed,
                json.dumps(
                    {
                        "errors": [error.code for error in result.errors],
                        "findings": [
                            [finding.source_id, finding.line, finding.rule_id]
                            for scan in result.scans for finding in scan.findings
                        ],
                    },
                    sort_keys=True,
                ),
            )

    def test_refuses_invalid_version_and_existing_output(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            existing = parent / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(builder.BuildError, "invalid_version"):
                builder.build("v0.1", parent / "new")
            with self.assertRaisesRegex(builder.BuildError, "output_exists"):
                builder.build("0.1.0", existing)

    def test_release_gate_reports_pending_and_requires_records(self) -> None:
        builder = _load_builder()
        gate = _load_gate()
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            artifact = builder.build("0.1.0", parent / "artifact")
            evidence = (
                REPOSITORY_ROOT / "release" / "v0.1" / "manual-evidence.json"
            )

            statuses = gate.check(
                artifact, evidence, require_complete=False
            )
            self.assertEqual(len(statuses), 4)
            self.assertTrue(all(item.endswith(": pending") for item in statuses))
            with self.assertRaisesRegex(
                gate.GateError, "manual_evidence_pending"
            ):
                gate.check(artifact, evidence, require_complete=True)

    def test_release_gate_accepts_complete_reviewable_records(self) -> None:
        builder = _load_builder()
        gate = _load_gate()
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            artifact = builder.build("0.1.0", parent / "artifact")
            records = parent / "evidence"
            records.mkdir()
            headings = "\n".join(
                (
                    "## Installation", "passed",
                    "## Verification", "passed",
                    "## Backout", "passed",
                    "## Preservation", "passed",
                    "## Offline", "passed",
                )
            )
            targets = []
            for operating_system, architecture in sorted(gate.EXPECTED_TARGETS):
                record = f"{operating_system}-{architecture}.md"
                (records / record).write_text(headings, encoding="utf-8")
                targets.append(
                    {
                        "operating_system": operating_system,
                        "architecture": architecture,
                        "status": "passed",
                        "evidence": {
                            "completed_on": "2026-07-23",
                            "commit_sha": "a" * 40,
                            "record": record,
                        },
                    }
                )
            ledger = records / "manual-evidence.json"
            fresh_record = "fresh-user-review.md"
            (records / fresh_record).write_text(
                "\n".join(
                    (
                        "## Environment", "fresh checkout",
                        "## Steps followed", "public README",
                        "## Findings", "none",
                        "## Outcome", "passed",
                    )
                ),
                encoding="utf-8",
            )
            ledger.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "release": "0.1.0",
                        "targets": targets,
                        "fresh_user_review": {
                            "status": "passed",
                            "evidence": {
                                "completed_on": "2026-07-23",
                                "commit_sha": "a" * 40,
                                "record": fresh_record,
                                "reviewer_role": "independent reviewer",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            statuses = gate.check(
                artifact, ledger, require_complete=True
            )
            self.assertTrue(all(item.endswith(": passed") for item in statuses))

    def test_candidate_archive_is_reproducible_bounded_and_complete(self) -> None:
        builder = _load_builder()
        packager = _load_packager()
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            artifact = builder.build("0.1.0", parent / "artifact")
            first, first_digest = packager.package(
                artifact, parent / "first.tar.gz"
            )
            second, second_digest = packager.package(
                artifact, parent / "second.tar.gz"
            )

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_digest, second_digest)
            expected = {
                f"byte-core-0.1.0/{path.relative_to(artifact).as_posix()}":
                    path.read_bytes()
                for path in artifact.rglob("*")
                if path.is_file()
            }
            with tarfile.open(first, "r:gz") as archive:
                members = archive.getmembers()
                self.assertTrue(
                    all(
                        not member.issym()
                        and not member.islnk()
                        and not PurePosixPath(member.name).is_absolute()
                        and ".." not in PurePosixPath(member.name).parts
                        and member.uid == 0
                        and member.gid == 0
                        and member.mtime == 0
                        for member in members
                    )
                )
                actual = {
                    member.name: archive.extractfile(member).read()
                    for member in members
                    if member.isfile()
                }
            self.assertEqual(actual, expected)

    @staticmethod
    def _contents(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }


if __name__ == "__main__":
    unittest.main()
