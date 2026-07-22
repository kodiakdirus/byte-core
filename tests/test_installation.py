from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "installation"
ARTIFACT = FIXTURES / "artifact"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.installation import (  # noqa: E402
    InstallationError,
    build_install_plan,
    build_removal_plan,
    load_installation_manifest,
    parse_installation_manifest,
    serialize,
)


class InstallationTests(unittest.TestCase):
    def test_install_plan_is_deterministic_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            core = parent / "core"
            state = parent / "state"
            before = tuple(parent.iterdir())

            first = build_install_plan(ARTIFACT, core, state, "0.1.0")
            second = build_install_plan(ARTIFACT, core, state, "0.1.0")

            self.assertEqual(first, second)
            self.assertEqual(tuple(parent.iterdir()), before)
            self.assertFalse(core.exists())
            self.assertFalse(state.exists())
            self.assertEqual(first.manifest.state, "active")
            self.assertTrue(first.actions)

    def test_manifest_round_trip_and_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            manifest_path = parent / "installation.json"
            manifest_path.write_text(serialize(plan.manifest), encoding="utf-8")

            loaded = load_installation_manifest(manifest_path)
            self.assertEqual(loaded, plan.manifest)

            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["core_version"] = "0.2.0"
            with self.assertRaises(InstallationError) as raised:
                parse_installation_manifest(payload)
            self.assertEqual(raised.exception.code, "manifest_integrity_failed")

    def test_invalid_public_fixture_fails(self) -> None:
        with self.assertRaises(InstallationError):
            load_installation_manifest(FIXTURES / "invalid-manifest.json")

    def test_removal_plan_uses_only_verified_manifest_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            core = parent / "core"
            state = parent / "state"
            deployment = parent / "deployment"
            deployment.mkdir()
            plan = build_install_plan(ARTIFACT, core, state, "0.1.0")
            for action in plan.actions:
                target = Path(action.target)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = ARTIFACT / action.source_relative_path
                shutil.copyfile(source, target)
                target.chmod(action.mode)
            state.mkdir()
            manifest_path = state / "installation.json"
            manifest_path.write_text(serialize(plan.manifest), encoding="utf-8")

            removal = build_removal_plan(
                manifest_path, preserve_roots=(str(deployment),)
            )

            self.assertIn(str(manifest_path), removal.remove_files)
            self.assertEqual(removal.preserve_roots, (str(deployment),))
            self.assertTrue(
                all(path.startswith(str(core)) or path == str(manifest_path)
                    for path in removal.remove_files)
            )

    def test_modified_managed_file_refuses_removal_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            for action in plan.actions:
                target = Path(action.target)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ARTIFACT / action.source_relative_path).read_bytes())
                target.chmod(action.mode)
            state = parent / "state"
            state.mkdir()
            manifest_path = state / "installation.json"
            manifest_path.write_text(serialize(plan.manifest), encoding="utf-8")
            Path(plan.actions[0].target).write_text("modified", encoding="utf-8")

            with self.assertRaises(InstallationError) as raised:
                build_removal_plan(manifest_path)

            self.assertEqual(raised.exception.code, "managed_file_modified")

    def test_overlapping_roots_and_artifact_links_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            with self.assertRaises(InstallationError) as raised:
                build_install_plan(
                    ARTIFACT, parent / "root", parent / "root", "0.1.0"
                )
            self.assertEqual(raised.exception.code, "overlapping_roots")

            artifact = parent / "artifact"
            artifact.mkdir()
            target = artifact / "target.txt"
            target.write_text("fictional", encoding="utf-8")
            link = artifact / "link.txt"
            try:
                link.symlink_to(target.name)
            except (NotImplementedError, OSError):
                return
            with self.assertRaises(InstallationError) as raised:
                build_install_plan(
                    artifact, parent / "core", parent / "state", "0.1.0"
                )
            self.assertEqual(raised.exception.code, "artifact_link_forbidden")


if __name__ == "__main__":
    unittest.main()
