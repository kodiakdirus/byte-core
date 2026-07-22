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
    apply_installation,
    build_install_plan,
    build_removal_plan,
    load_install_plan,
    load_installation_manifest,
    parse_installation_manifest,
    serialize,
    verify_installation,
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

            self.assertIn(str(manifest_path.resolve()), removal.remove_files)
            self.assertEqual(removal.preserve_roots, (str(deployment.resolve()),))
            self.assertTrue(
                all(
                    path.startswith(str(core.resolve()))
                    or path.startswith(str(state.resolve()))
                    for path in removal.remove_files)
            )

    def test_install_apply_verify_and_exact_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            plan_path = parent / "plan.json"
            plan_path.write_text(serialize(plan), encoding="utf-8")

            loaded = load_install_plan(plan_path)
            applied = apply_installation(loaded)
            verified = verify_installation(loaded)
            replayed = apply_installation(loaded)

            self.assertEqual(applied.code, "installed")
            self.assertEqual(verified.code, "verified")
            self.assertEqual(replayed.code, "already_installed")
            self.assertFalse((parent / "state" / "operations").exists())
            active = json.loads(
                (parent / "state" / "active.json").read_text(encoding="utf-8")
            )
            self.assertEqual(active["activation_plan_id"], plan.plan_id)
            self.assertEqual(active["manifest_sha256"], plan.manifest.manifest_sha256)

    def test_changed_artifact_refuses_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            artifact = parent / "artifact"
            shutil.copytree(ARTIFACT, artifact)
            plan = build_install_plan(
                artifact, parent / "core", parent / "state", "0.1.0"
            )
            (artifact / "share" / "README.txt").write_text(
                "changed\n", encoding="utf-8"
            )

            with self.assertRaises(InstallationError) as raised:
                apply_installation(plan)

            self.assertEqual(raised.exception.code, "artifact_changed")
            self.assertFalse((parent / "core").exists())
            self.assertFalse((parent / "state").exists())

    def test_tampered_active_and_mode_drift_fail_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            apply_installation(plan)
            active_path = parent / "state" / "active.json"
            original_active = active_path.read_text(encoding="utf-8")
            active = json.loads(original_active)
            active["core_version"] = "0.2.0"
            active_path.write_text(json.dumps(active), encoding="utf-8")

            with self.assertRaises(InstallationError) as raised:
                verify_installation(plan)
            self.assertEqual(raised.exception.code, "verification_failed")

            active_path.write_text(original_active, encoding="utf-8")
            Path(plan.actions[0].target).chmod(0o600 if plan.actions[0].mode == 0o700 else 0o700)
            with self.assertRaises(InstallationError) as raised:
                verify_installation(plan)
            self.assertEqual(raised.exception.code, "verification_failed")

    def test_pre_activation_failure_rolls_back_only_created_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            sentinel = parent / "deployment-owned.txt"
            sentinel.write_text("preserve\n", encoding="utf-8")
            plan = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            import byte_core.installation as installation

            original = installation._write_exclusive
            calls = 0

            def fail_during_copy(path, data, mode):
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("fictional write failure")
                return original(path, data, mode)

            from unittest import mock
            with mock.patch.object(installation, "_write_exclusive", fail_during_copy):
                with self.assertRaises(InstallationError) as raised:
                    apply_installation(plan)

            self.assertEqual(raised.exception.code, "apply_failed")
            self.assertFalse((parent / "core").exists())
            self.assertFalse((parent / "state").exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")

    def test_changed_partial_state_preserves_journal_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            import byte_core.installation as installation
            from unittest import mock

            def fail_manifest_publish(path, data, mode):
                Path(plan.actions[0].target).write_text("ambiguous\n", encoding="utf-8")
                raise OSError("fictional publish failure")

            with mock.patch.object(
                installation, "_publish_exclusive", fail_manifest_publish
            ):
                with self.assertRaises(InstallationError) as raised:
                    apply_installation(plan)

            self.assertEqual(raised.exception.code, "recovery_required")
            journals = tuple((parent / "state" / "operations").glob("*.json"))
            self.assertEqual(len(journals), 1)
            self.assertEqual(
                json.loads(journals[0].read_text(encoding="utf-8"))["plan_id"],
                plan.plan_id,
            )

    def test_post_activation_failure_never_guesses_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            import byte_core.installation as installation
            from unittest import mock

            with mock.patch.object(
                installation,
                "_verify_installation",
                side_effect=InstallationError("managed_file_modified"),
            ):
                with self.assertRaises(InstallationError) as raised:
                    apply_installation(plan)

            self.assertEqual(raised.exception.code, "recovery_required")
            self.assertTrue((parent / "state" / "active.json").is_file())
            self.assertTrue(tuple((parent / "state" / "operations").glob("*.json")))

    def test_tampered_plan_and_unexpected_release_link_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            plan = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            plan_path = parent / "plan.json"
            payload = json.loads(serialize(plan))
            payload["core_version"] = "fictional"
            plan_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(InstallationError):
                load_install_plan(plan_path)

            apply_installation(plan)
            link = Path(plan.core_root) / plan.manifest.release_relative_path / "extra"
            try:
                link.symlink_to(Path(plan.actions[0].target))
            except (NotImplementedError, OSError):
                return
            with self.assertRaises(InstallationError) as raised:
                verify_installation(plan)
            self.assertEqual(raised.exception.code, "verification_failed")

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

    def test_parent_aliases_are_canonicalized_but_target_links_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            real = parent / "real"
            real.mkdir()
            artifact = real / "artifact"
            shutil.copytree(ARTIFACT, artifact)
            alias = parent / "alias"
            try:
                alias.symlink_to(real, target_is_directory=True)
            except (NotImplementedError, OSError):
                return

            plan = build_install_plan(
                alias / "artifact",
                alias / "core",
                alias / "state",
                "0.1.0",
            )

            self.assertEqual(plan.artifact_root, str(artifact.resolve()))
            self.assertEqual(plan.core_root, str((real / "core").resolve()))
            self.assertEqual(plan.state_root, str((real / "state").resolve()))
            apply_installation(plan)
            self.assertEqual(verify_installation(plan).code, "verified")

            linked_manifest = parent / "manifest-link.json"
            linked_manifest.symlink_to(real / "state" / "installation.json")
            with self.assertRaises(InstallationError) as raised:
                load_installation_manifest(linked_manifest)
            self.assertEqual(raised.exception.code, "manifest_read_error")


if __name__ == "__main__":
    unittest.main()
