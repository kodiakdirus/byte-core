from __future__ import annotations

import hashlib
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
RELEASES = FIXTURES / "releases"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.installation import (  # noqa: E402
    InstallationError,
    apply_installation,
    apply_removal,
    apply_update,
    build_install_plan,
    build_removal_plan,
    build_update_plan,
    load_install_plan,
    load_removal_plan,
    load_update_plan,
    load_release_descriptor,
    load_installation_manifest,
    parse_installation_manifest,
    serialize,
    verify_installation,
    verify_removal,
    verify_update,
)


class InstallationTests(unittest.TestCase):
    def test_release_descriptors_bind_two_offline_fixtures(self) -> None:
        first = load_release_descriptor(RELEASES / "0.1.0" / "release.json")
        second = load_release_descriptor(RELEASES / "0.2.0" / "release.json")

        self.assertEqual(first.core_version, "0.1.0")
        self.assertEqual(second.core_version, "0.2.0")
        self.assertEqual(first.migration, "none")
        self.assertEqual(second.release_notes_path, "RELEASE_NOTES.md")
        self.assertNotEqual(first.descriptor_sha256, second.descriptor_sha256)
        self.assertNotIn("release.json", {item.relative_path for item in second.files})

    def test_release_descriptor_refuses_tampering_and_incompatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = RELEASES / "0.2.0" / "release.json"
            descriptor = root / "release.json"
            raw = json.loads(source.read_text(encoding="utf-8"))
            raw["core_version"] = "9.9.9"
            descriptor.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(
                InstallationError, "release_descriptor_integrity_failed"
            ):
                load_release_descriptor(descriptor)

            raw = json.loads(source.read_text(encoding="utf-8"))
            raw["configuration_schema_minimum"] = 2
            raw["configuration_schema_maximum"] = 2
            raw["descriptor_sha256"] = _descriptor_checksum(raw)
            descriptor.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(InstallationError, "incompatible_release"):
                load_release_descriptor(descriptor)

            raw = json.loads(source.read_text(encoding="utf-8"))
            raw["migration"] = "required"
            raw["descriptor_sha256"] = _descriptor_checksum(raw)
            descriptor.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(
                InstallationError, "unsupported_release_migration"
            ):
                load_release_descriptor(descriptor)

    def test_update_planning_refuses_release_payload_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            install = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            apply_installation(install)
            release = parent / "release"
            shutil.copytree(RELEASES / "0.2.0", release)
            (release / "share" / "README.txt").write_text(
                "changed without a descriptor update\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(
                InstallationError, "release_artifact_mismatch"
            ):
                build_update_plan(
                    parent / "state" / "installation.json", release
                )

    def _installed_update(self, parent: Path):
        install = build_install_plan(
            ARTIFACT, parent / "core", parent / "state", "0.1.0"
        )
        apply_installation(install)
        update = build_update_plan(
            parent / "state" / "installation.json",
            RELEASES / "0.2.0",
        )
        return install, update

    def test_update_apply_is_atomic_verifiable_and_replay_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            install, update = self._installed_update(parent)

            result = apply_update(update)

            self.assertEqual(result.code, "updated")
            self.assertEqual(verify_update(update).code, "verified")
            self.assertEqual(apply_update(update).code, "already_updated")
            state = parent / "state"
            generations = state / "manifests"
            self.assertTrue(
                (generations / f"{install.manifest.manifest_sha256}.json").is_file()
            )
            self.assertTrue(
                (generations / f"{update.next_manifest.manifest_sha256}.json").is_file()
            )
            self.assertTrue(
                (parent / "core" / install.manifest.release_relative_path).is_dir()
            )
            self.assertEqual(
                load_installation_manifest(state / "installation.json"),
                update.next_manifest,
            )
            self.assertFalse((state / "operations").exists())

    def test_update_apply_preserves_deployment_owned_tree_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            deployment = parent / "deployment"
            (deployment / "config").mkdir(parents=True)
            (deployment / "docs").mkdir()
            (deployment / "config" / "deployment.toml").write_bytes(
                b"schema_version = 1\n"
            )
            (deployment / "docs" / "runbook.md").write_bytes(
                b"# Fictional operator runbook\n"
            )
            before = _tree_snapshot(deployment)
            _, update = self._installed_update(parent)

            apply_update(update)

            self.assertEqual(_tree_snapshot(deployment), before)

    def test_update_plan_loader_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            _, update = self._installed_update(parent)
            plan_path = parent / "update.json"
            plan_path.write_text(serialize(update), encoding="utf-8")
            self.assertEqual(load_update_plan(plan_path), update)

            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            raw["activation"]["next_core_version"] = "9.9.9"
            plan_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(InstallationError, "invalid_plan"):
                load_update_plan(plan_path)

    def test_update_refuses_changed_artifact_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            _, update = self._installed_update(parent)
            before = (parent / "state" / "active.json").read_bytes()
            artifact = parent / "changed-release"
            shutil.copytree(Path(update.artifact_root), artifact)
            update = build_update_plan(
                parent / "state" / "installation.json", artifact
            )
            (artifact / "share" / "README.txt").write_text(
                "changed after approval\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(InstallationError, "artifact_changed"):
                apply_update(update)

            self.assertEqual(
                (parent / "state" / "active.json").read_bytes(), before
            )
            self.assertFalse(
                (parent / "core" / update.next_manifest.release_relative_path).exists()
            )

    def test_update_refuses_dirty_current_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            install, update = self._installed_update(parent)
            managed = (
                parent / "core" / install.manifest.release_relative_path
                / install.manifest.files[0].relative_path
            )
            managed.write_text("locally changed\n", encoding="utf-8")

            with self.assertRaisesRegex(InstallationError, "managed_file_modified"):
                apply_update(update)

            self.assertFalse(
                (parent / "core" / update.next_manifest.release_relative_path).exists()
            )

    def test_post_activation_failure_backs_out_and_preserves_new_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            install, update = self._installed_update(parent)
            state = parent / "state"
            previous_active = (state / "active.json").read_bytes()
            original_replace = __import__(
                "byte_core.installation", fromlist=["_replace_file"]
            )._replace_file

            def fail_compatibility(path, data, mode):
                if path == state / "installation.json" and b'"core_version":"0.2.0"' in data:
                    raise OSError("fictional interrupted compatibility refresh")
                return original_replace(path, data, mode)

            from unittest import mock
            with mock.patch(
                "byte_core.installation._replace_file",
                side_effect=fail_compatibility,
            ):
                with self.assertRaisesRegex(InstallationError, "apply_failed"):
                    apply_update(update)

            self.assertEqual((state / "active.json").read_bytes(), previous_active)
            self.assertEqual(
                load_installation_manifest(state / "installation.json"),
                install.manifest,
            )
            self.assertTrue(
                (parent / "core" / update.next_manifest.release_relative_path).is_dir()
            )

    def test_changed_activation_requires_recovery_and_preserves_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            _, update = self._installed_update(parent)
            state = parent / "state"
            original_replace = __import__(
                "byte_core.installation", fromlist=["_replace_file"]
            )._replace_file

            def interrupt_after_external_change(path, data, mode):
                if path == state / "installation.json" and b'"core_version":"0.2.0"' in data:
                    (state / "active.json").write_text(
                        '{"fictional":"external change"}\n', encoding="utf-8"
                    )
                    raise OSError("fictional interrupted compatibility refresh")
                return original_replace(path, data, mode)

            from unittest import mock
            with mock.patch(
                "byte_core.installation._replace_file",
                side_effect=interrupt_after_external_change,
            ):
                with self.assertRaisesRegex(InstallationError, "recovery_required"):
                    apply_update(update)

            self.assertTrue(
                (state / "operations" / f"{update.plan_id}.json").is_file()
            )
            self.assertTrue(
                (parent / "core" / update.next_manifest.release_relative_path).is_dir()
            )

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
            apply_installation(plan)
            manifest_path = state / "installation.json"

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

    def test_removal_apply_verify_and_replay_preserve_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            deployment = parent / "deployment"
            deployment.mkdir()
            sentinel = deployment / "manifest.toml"
            sentinel.write_text("fictional = true\n", encoding="utf-8")
            install = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            apply_installation(install)
            removal = build_removal_plan(
                parent / "state" / "installation.json",
                preserve_roots=(str(deployment),),
            )
            plan_path = parent / "remove-plan.json"
            plan_path.write_text(serialize(removal), encoding="utf-8")

            loaded = load_removal_plan(plan_path)
            self.assertEqual(apply_removal(loaded).code, "removed")
            self.assertEqual(verify_removal(loaded).code, "verified")
            self.assertEqual(apply_removal(loaded).code, "already_removed")
            self.assertFalse((parent / "core").exists())
            self.assertFalse((parent / "state").exists())
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"), "fictional = true\n"
            )

    def test_removal_after_update_removes_all_verified_generations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            _, update = self._installed_update(parent)
            apply_update(update)
            removal = build_removal_plan(
                parent / "state" / "installation.json"
            )

            self.assertGreaterEqual(
                sum(path.endswith(".json") for path in removal.remove_files), 4
            )
            self.assertEqual(apply_removal(removal).code, "removed")
            self.assertFalse((parent / "core").exists())
            self.assertFalse((parent / "state").exists())

    def test_removal_refuses_unowned_core_or_state_content(self) -> None:
        for location in ("core", "state"):
            with (
                self.subTest(location=location),
                tempfile.TemporaryDirectory() as temporary,
            ):
                parent = Path(temporary)
                install = build_install_plan(
                    ARTIFACT, parent / "core", parent / "state", "0.1.0"
                )
                apply_installation(install)
                (parent / location / "unowned.txt").write_text(
                    "preserve ambiguity\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    InstallationError, "managed_paths_changed"
                ):
                    build_removal_plan(
                        parent / "state" / "installation.json"
                    )

    def test_interrupted_removal_stops_with_exact_plan_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            deployment = parent / "deployment"
            deployment.mkdir()
            sentinel = deployment / "notebook.md"
            sentinel.write_text("preserve\n", encoding="utf-8")
            install = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            apply_installation(install)
            removal = build_removal_plan(
                parent / "state" / "installation.json",
                preserve_roots=(str(deployment),),
            )
            original_unlink = Path.unlink
            calls = 0

            def interrupt(path, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("fictional interruption")
                return original_unlink(path, *args, **kwargs)

            from unittest import mock
            with mock.patch.object(Path, "unlink", interrupt):
                with self.assertRaisesRegex(
                    InstallationError, "recovery_required"
                ):
                    apply_removal(removal)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
            self.assertTrue(any(Path(item).exists() for item in removal.remove_files))
            with self.assertRaisesRegex(InstallationError, "plan_stale"):
                apply_removal(removal)

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
            apply_installation(plan)
            state = parent / "state"
            manifest_path = state / "installation.json"
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

    def test_update_plan_is_deterministic_read_only_and_preserving(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            install = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            apply_installation(install)
            deployment = parent / "deployment"
            deployment.mkdir()
            sentinel = deployment / "operator-notes.txt"
            sentinel.write_text("fictional deployment content\n", encoding="utf-8")
            artifact = RELEASES / "0.2.0"
            before = _tree_snapshot(parent)

            first = build_update_plan(
                parent / "state" / "installation.json", artifact
            )
            second = build_update_plan(
                parent / "state" / "installation.json", artifact
            )

            self.assertEqual(first, second)
            self.assertEqual(_tree_snapshot(parent), before)
            self.assertEqual(first.operation, "update")
            self.assertEqual(first.from_version, "0.1.0")
            self.assertEqual(first.to_version, "0.2.0")
            self.assertEqual(first.activation.activation_plan_id, "$plan_id")
            self.assertEqual(
                first.backout_release_relative_path, "releases/0.1.0"
            )
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"),
                "fictional deployment content\n",
            )

    def test_update_plan_refuses_dirty_current_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            install = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            apply_installation(install)
            Path(install.actions[0].target).write_text("dirty\n", encoding="utf-8")

            with self.assertRaises(InstallationError) as raised:
                build_update_plan(
                    parent / "state" / "installation.json", RELEASES / "0.2.0"
                )

            self.assertEqual(raised.exception.code, "managed_file_modified")

    def test_update_plan_requires_newer_absent_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            install = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.2.0"
            )
            apply_installation(install)
            manifest = parent / "state" / "installation.json"
            for version in ("0.2.0", "0.1.0"):
                with self.subTest(version=version):
                    with self.assertRaises(InstallationError) as raised:
                        build_update_plan(manifest, RELEASES / version)
                    self.assertEqual(
                        raised.exception.code, "update_version_not_newer"
                    )

            parent = Path(temporary)
            install = build_install_plan(
                ARTIFACT, parent / "other-core", parent / "other-state", "0.1.0"
            )
            apply_installation(install)
            manifest = parent / "other-state" / "installation.json"
            existing = parent / "other-core" / "releases" / "0.2.0"
            existing.mkdir()
            with self.assertRaises(InstallationError) as raised:
                build_update_plan(manifest, RELEASES / "0.2.0")
            self.assertEqual(raised.exception.code, "target_exists")

    def test_update_plan_refuses_tampered_active_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            install = build_install_plan(
                ARTIFACT, parent / "core", parent / "state", "0.1.0"
            )
            apply_installation(install)
            active_path = parent / "state" / "active.json"
            active = json.loads(active_path.read_text(encoding="utf-8"))
            active["core_version"] = "0.0.9"
            active_path.write_text(json.dumps(active), encoding="utf-8")

            with self.assertRaises(InstallationError) as raised:
                build_update_plan(
                    parent / "state" / "installation.json", RELEASES / "0.2.0"
                )
            self.assertEqual(raised.exception.code, "active_integrity_failed")


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    snapshot = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            snapshot.append((relative, path.read_bytes().hex(), path.stat().st_mode))
        else:
            snapshot.append((relative, "directory", path.stat().st_mode))
    return tuple(snapshot)


def _descriptor_checksum(raw: dict) -> str:
    unsigned = dict(raw)
    unsigned.pop("descriptor_sha256", None)
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


if __name__ == "__main__":
    unittest.main()
