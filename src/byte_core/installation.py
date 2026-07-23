"""Installation planning, transactional apply, and verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_SCHEMA_VERSION = 1
INSTALL_PLAN_SCHEMA_VERSION = 1
RELEASE_DESCRIPTOR_SCHEMA_VERSION = 1
SUPPORTED_CONFIGURATION_SCHEMA_VERSION = 1
MAX_ARTIFACT_FILES = 512
MAX_ARTIFACT_FILE_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_INSTALL_PLAN_BYTES = 1024 * 1024
ACTIVE_SCHEMA_VERSION = 1
JOURNAL_SCHEMA_VERSION = 1
MANIFEST_STORE_DIRECTORY = "manifests"
_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
)
_DIGEST = re.compile(r"[0-9a-f]{64}")


class InstallationError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ManagedFile:
    relative_path: str
    sha256: str
    mode: int


@dataclass(frozen=True)
class ReleaseDescriptor:
    schema_version: int
    core_version: str
    configuration_schema_minimum: int
    configuration_schema_maximum: int
    migration: str
    release_notes_path: str
    files: tuple[ManagedFile, ...]
    artifact_sha256: str
    descriptor_sha256: str


@dataclass(frozen=True)
class InstallationManifest:
    schema_version: int
    core_version: str
    state: str
    core_root: str
    state_root: str
    release_relative_path: str
    artifact_sha256: str
    files: tuple[ManagedFile, ...]
    generated_state_paths: tuple[str, ...]
    removable_directories: tuple[str, ...]
    manifest_sha256: str


@dataclass(frozen=True)
class InstallAction:
    source_relative_path: str
    target: str
    sha256: str
    mode: int


@dataclass(frozen=True)
class InstallPlan:
    schema_version: int
    operation: str
    artifact_root: str
    core_root: str
    state_root: str
    manifest_target: str
    actions: tuple[InstallAction, ...]
    manifest: InstallationManifest
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    backout: tuple[str, ...]
    plan_id: str


@dataclass(frozen=True)
class RemovalPlan:
    schema_version: int
    operation: str
    manifest_path: str
    remove_files: tuple[str, ...]
    remove_directories: tuple[str, ...]
    preserve_roots: tuple[str, ...]
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    plan_id: str


@dataclass(frozen=True)
class ActivationTransition:
    current_metadata_sha256: str
    next_core_version: str
    next_release_relative_path: str
    next_manifest_sha256: str
    activation_plan_id: str


@dataclass(frozen=True)
class UpdatePlan:
    schema_version: int
    operation: str
    manifest_path: str
    artifact_root: str
    core_root: str
    state_root: str
    from_version: str
    to_version: str
    release_descriptor_sha256: str
    current_manifest_sha256: str
    actions: tuple[InstallAction, ...]
    next_manifest: InstallationManifest
    activation: ActivationTransition
    backout_release_relative_path: str
    backout_manifest_sha256: str
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    backout: tuple[str, ...]
    plan_id: str


@dataclass(frozen=True)
class ActiveRelease:
    schema_version: int
    core_version: str
    release_relative_path: str
    manifest_sha256: str
    activation_plan_id: str
    metadata_sha256: str


@dataclass(frozen=True)
class InstallationResult:
    code: str
    plan_id: str
    core_root: str
    state_root: str


def build_install_plan(
    artifact_root: str | os.PathLike[str],
    core_root: str | os.PathLike[str],
    state_root: str | os.PathLike[str],
    core_version: str,
) -> InstallPlan:
    artifact = _existing_root(artifact_root, "invalid_artifact_root")
    core = _new_root(core_root, "invalid_core_root")
    state = _new_root(state_root, "invalid_state_root")
    if core == state or _contains(core, state) or _contains(state, core):
        raise InstallationError("overlapping_roots")
    if not _VERSION.fullmatch(core_version):
        raise InstallationError("invalid_core_version")

    files = _scan_artifact(artifact)
    release_relative = f"releases/{core_version}"
    release_root = core / "releases" / core_version
    actions = tuple(
        InstallAction(item.relative_path, str(release_root / item.relative_path),
                      item.sha256, item.mode)
        for item in files
    )
    artifact_digest = _digest(_canonical_json([asdict(item) for item in files]).encode())
    manifest = _make_manifest(
        core_version, core, state, release_relative, artifact_digest, files
    )
    unsigned = {
        "schema_version": INSTALL_PLAN_SCHEMA_VERSION,
        "operation": "install",
        "artifact_root": str(artifact),
        "core_root": str(core),
        "state_root": str(state),
        "manifest_target": str(state / "installation.json"),
        "actions": [asdict(item) for item in actions],
        "manifest": asdict(manifest),
        "preconditions": ["targets_absent", "artifact_hashes_match"],
        "postconditions": ["managed_files_match", "manifest_active"],
        "backout": ["remove_unchanged_created_paths"],
    }
    return InstallPlan(
        INSTALL_PLAN_SCHEMA_VERSION, "install", str(artifact), str(core),
        str(state), unsigned["manifest_target"], actions, manifest,
        tuple(unsigned["preconditions"]),
        tuple(unsigned["postconditions"]), tuple(unsigned["backout"]),
        _digest(_canonical_json(unsigned).encode()),
    )


def build_removal_plan(
    manifest_path: str | os.PathLike[str],
    *,
    preserve_roots: tuple[str, ...] = (),
) -> RemovalPlan:
    path = _regular_file(manifest_path, "manifest_read_error")
    manifest = load_installation_manifest(path)
    release_root = _existing_root(
        Path(manifest.core_root) / manifest.release_relative_path,
        "managed_root_invalid",
    )
    remove_files: list[str] = []
    for item in manifest.files:
        target = release_root / item.relative_path
        _require_matching_file(target, item)
        remove_files.append(str(target))
    for generated in manifest.generated_state_paths:
        generated_path = Path(manifest.state_root) / generated
        if generated_path != path and generated_path.exists():
            _regular_file(generated_path, "managed_state_invalid")
        remove_files.append(str(generated_path))
    generation = _manifest_generation_path(Path(manifest.state_root), manifest)
    if generation.exists():
        _regular_file(generation, "managed_state_invalid")
    remove_files.append(str(generation))
    normalized_preserve = tuple(sorted(str(_existing_root(root, "invalid_preserve_root"))
                                       for root in preserve_roots))
    for preserved in normalized_preserve:
        if any(
            _contains(Path(preserved), owned) or _contains(owned, Path(preserved))
            for owned in (Path(manifest.core_root), Path(manifest.state_root))
        ):
            raise InstallationError("ownership_overlap")
    unsigned = {
        "schema_version": INSTALL_PLAN_SCHEMA_VERSION,
        "operation": "remove",
        "manifest_path": str(path),
        "remove_files": sorted(remove_files, reverse=True),
        "remove_directories": sorted(
            (
                *(str(Path(manifest.core_root) / item)
                  for item in manifest.removable_directories),
                str(Path(manifest.state_root) / MANIFEST_STORE_DIRECTORY),
            ),
            key=lambda value: (value.count(os.sep), value), reverse=True,
        ),
        "preserve_roots": list(normalized_preserve),
        "preconditions": ["manifest_active", "managed_files_unchanged"],
        "postconditions": ["managed_paths_absent", "preserved_roots_unchanged"],
    }
    return RemovalPlan(
        INSTALL_PLAN_SCHEMA_VERSION, "remove", str(path),
        tuple(unsigned["remove_files"]), tuple(unsigned["remove_directories"]),
        normalized_preserve, tuple(unsigned["preconditions"]),
        tuple(unsigned["postconditions"]),
        _digest(_canonical_json(unsigned).encode()),
    )


def build_update_plan(
    manifest_path: str | os.PathLike[str],
    artifact_root: str | os.PathLike[str],
) -> UpdatePlan:
    path = _regular_file(manifest_path, "manifest_read_error")
    compatibility = load_installation_manifest(path)
    current = _load_active_manifest(Path(compatibility.state_root))
    if compatibility != current:
        raise InstallationError("manifest_mismatch")
    expected_manifest_path = Path(current.state_root) / "installation.json"
    if path != expected_manifest_path:
        raise InstallationError("manifest_path_mismatch")
    active = _load_active_release(Path(current.state_root) / "active.json")
    _verify_active_manifest(active, current)
    _verify_release(current)

    artifact = _existing_root(artifact_root, "invalid_artifact_root")
    descriptor = load_release_descriptor(artifact / "release.json")
    files = _scan_release_artifact(artifact, descriptor)
    core_version = descriptor.core_version
    if _version_tuple(core_version) <= _version_tuple(current.core_version):
        raise InstallationError("update_version_not_newer")
    release_relative = f"releases/{core_version}"
    release_root = Path(current.core_root) / release_relative
    if _is_link_like(release_root) or release_root.exists():
        raise InstallationError("target_exists")

    actions = tuple(
        InstallAction(
            item.relative_path,
            str(release_root / item.relative_path),
            item.sha256,
            item.mode,
        )
        for item in files
    )
    artifact_digest = _digest(
        _canonical_json([asdict(item) for item in files]).encode()
    )
    next_manifest = _make_manifest(
        core_version,
        Path(current.core_root),
        Path(current.state_root),
        release_relative,
        artifact_digest,
        files,
    )
    activation = ActivationTransition(
        active.metadata_sha256,
        core_version,
        release_relative,
        next_manifest.manifest_sha256,
        "$plan_id",
    )
    unsigned = {
        "schema_version": INSTALL_PLAN_SCHEMA_VERSION,
        "operation": "update",
        "manifest_path": str(path),
        "artifact_root": str(artifact),
        "core_root": current.core_root,
        "state_root": current.state_root,
        "from_version": current.core_version,
        "to_version": core_version,
        "release_descriptor_sha256": descriptor.descriptor_sha256,
        "current_manifest_sha256": current.manifest_sha256,
        "actions": [asdict(item) for item in actions],
        "next_manifest": asdict(next_manifest),
        "activation": asdict(activation),
        "backout_release_relative_path": current.release_relative_path,
        "backout_manifest_sha256": current.manifest_sha256,
        "preconditions": [
            "current_installation_verified",
            "new_release_absent",
            "release_descriptor_verified",
            "artifact_hashes_match",
        ],
        "postconditions": [
            "new_release_verified",
            "activation_matches_plan",
            "previous_release_preserved",
        ],
        "backout": [
            "reactivate_previous_verified_release",
            "remove_only_unchanged_new_release_paths",
        ],
    }
    return UpdatePlan(
        INSTALL_PLAN_SCHEMA_VERSION,
        "update",
        str(path),
        str(artifact),
        current.core_root,
        current.state_root,
        current.core_version,
        core_version,
        descriptor.descriptor_sha256,
        current.manifest_sha256,
        actions,
        next_manifest,
        activation,
        current.release_relative_path,
        current.manifest_sha256,
        tuple(unsigned["preconditions"]),
        tuple(unsigned["postconditions"]),
        tuple(unsigned["backout"]),
        _digest(_canonical_json(unsigned).encode()),
    )


def load_release_descriptor(
    path: str | os.PathLike[str],
) -> ReleaseDescriptor:
    descriptor_path = _regular_file(path, "release_descriptor_read_error")
    try:
        raw = json.loads(
            _read_bounded(descriptor_path, MAX_MANIFEST_BYTES).decode("utf-8")
        )
    except InstallationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallationError("invalid_release_descriptor") from error
    keys = {
        "schema_version", "core_version", "configuration_schema_minimum",
        "configuration_schema_maximum", "migration", "release_notes_path",
        "files", "artifact_sha256", "descriptor_sha256",
    }
    if type(raw) is not dict or set(raw) != keys or type(raw["files"]) is not list:
        raise InstallationError("invalid_release_descriptor")
    try:
        files = tuple(
            ManagedFile(item["relative_path"], item["sha256"], item["mode"])
            for item in raw["files"]
            if type(item) is dict
            and set(item) == {"relative_path", "sha256", "mode"}
        )
        descriptor = ReleaseDescriptor(
            raw["schema_version"], raw["core_version"],
            raw["configuration_schema_minimum"],
            raw["configuration_schema_maximum"], raw["migration"],
            raw["release_notes_path"], files, raw["artifact_sha256"],
            raw["descriptor_sha256"],
        )
    except (KeyError, TypeError) as error:
        raise InstallationError("invalid_release_descriptor") from error
    if len(files) != len(raw["files"]):
        raise InstallationError("invalid_release_descriptor")
    _validate_release_descriptor(descriptor)
    return descriptor


def load_installation_manifest(
    path: str | os.PathLike[str],
) -> InstallationManifest:
    manifest_path = _regular_file(path, "manifest_read_error")
    try:
        data = _read_bounded(manifest_path, MAX_MANIFEST_BYTES)
        raw = json.loads(data.decode("utf-8"))
    except InstallationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallationError("invalid_manifest") from error
    return parse_installation_manifest(raw)


def parse_installation_manifest(raw: Any) -> InstallationManifest:
    keys = {
        "schema_version", "core_version", "state", "core_root", "state_root",
        "release_relative_path", "artifact_sha256", "files",
        "generated_state_paths", "removable_directories", "manifest_sha256",
    }
    if type(raw) is not dict or set(raw) != keys or type(raw.get("files")) is not list:
        raise InstallationError("invalid_manifest")
    try:
        files = tuple(ManagedFile(item["relative_path"], item["sha256"], item["mode"])
                      for item in raw["files"]
                      if type(item) is dict and set(item) == {"relative_path", "sha256", "mode"})
    except (KeyError, TypeError) as error:
        raise InstallationError("invalid_manifest") from error
    if len(files) != len(raw["files"]):
        raise InstallationError("invalid_manifest")
    directories = _string_tuple(raw["removable_directories"])
    generated = _string_tuple(raw["generated_state_paths"])
    manifest = InstallationManifest(
        raw["schema_version"], raw["core_version"], raw["state"],
        raw["core_root"], raw["state_root"], raw["release_relative_path"],
        raw["artifact_sha256"], files, generated, directories,
        raw["manifest_sha256"],
    )
    _validate_manifest(manifest)
    return manifest


def load_install_plan(path: str | os.PathLike[str]) -> InstallPlan:
    plan_path = _plan_file(path)
    try:
        raw = json.loads(_read_bounded(plan_path, MAX_INSTALL_PLAN_BYTES).decode("utf-8"))
    except InstallationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallationError("invalid_plan") from error
    keys = {
        "schema_version", "operation", "artifact_root", "core_root",
        "state_root", "manifest_target", "actions", "manifest",
        "preconditions", "postconditions", "backout", "plan_id",
    }
    if type(raw) is not dict or set(raw) != keys or type(raw["actions"]) is not list:
        raise InstallationError("invalid_plan")
    try:
        actions = tuple(
            InstallAction(
                item["source_relative_path"], item["target"],
                item["sha256"], item["mode"],
            )
            for item in raw["actions"]
            if type(item) is dict
            and set(item) == {"source_relative_path", "target", "sha256", "mode"}
        )
        manifest = parse_installation_manifest(raw["manifest"])
        plan = InstallPlan(
            raw["schema_version"], raw["operation"], raw["artifact_root"],
            raw["core_root"], raw["state_root"], raw["manifest_target"],
            actions, manifest, _plan_string_tuple(raw["preconditions"]),
            _plan_string_tuple(raw["postconditions"]),
            _plan_string_tuple(raw["backout"]), raw["plan_id"],
        )
    except (KeyError, TypeError) as error:
        raise InstallationError("invalid_plan") from error
    if len(actions) != len(raw["actions"]):
        raise InstallationError("invalid_plan")
    _validate_install_plan(plan)
    return plan


def load_update_plan(path: str | os.PathLike[str]) -> UpdatePlan:
    plan_path = _plan_file(path)
    try:
        raw = json.loads(
            _read_bounded(plan_path, MAX_INSTALL_PLAN_BYTES).decode("utf-8")
        )
    except InstallationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallationError("invalid_plan") from error
    keys = {
        "schema_version", "operation", "manifest_path", "artifact_root",
        "core_root", "state_root", "from_version", "to_version",
        "release_descriptor_sha256", "current_manifest_sha256",
        "actions", "next_manifest", "activation",
        "backout_release_relative_path", "backout_manifest_sha256",
        "preconditions", "postconditions", "backout", "plan_id",
    }
    if type(raw) is not dict or set(raw) != keys or type(raw["actions"]) is not list:
        raise InstallationError("invalid_plan")
    try:
        actions = tuple(
            InstallAction(
                item["source_relative_path"], item["target"],
                item["sha256"], item["mode"],
            )
            for item in raw["actions"]
            if type(item) is dict
            and set(item) == {"source_relative_path", "target", "sha256", "mode"}
        )
        activation_raw = raw["activation"]
        if type(activation_raw) is not dict or set(activation_raw) != {
            "current_metadata_sha256", "next_core_version",
            "next_release_relative_path", "next_manifest_sha256",
            "activation_plan_id",
        }:
            raise InstallationError("invalid_plan")
        activation = ActivationTransition(
            activation_raw["current_metadata_sha256"],
            activation_raw["next_core_version"],
            activation_raw["next_release_relative_path"],
            activation_raw["next_manifest_sha256"],
            activation_raw["activation_plan_id"],
        )
        plan = UpdatePlan(
            raw["schema_version"], raw["operation"], raw["manifest_path"],
            raw["artifact_root"], raw["core_root"], raw["state_root"],
            raw["from_version"], raw["to_version"],
            raw["release_descriptor_sha256"],
            raw["current_manifest_sha256"], actions,
            parse_installation_manifest(raw["next_manifest"]), activation,
            raw["backout_release_relative_path"],
            raw["backout_manifest_sha256"],
            _plan_string_tuple(raw["preconditions"]),
            _plan_string_tuple(raw["postconditions"]),
            _plan_string_tuple(raw["backout"]), raw["plan_id"],
        )
    except (KeyError, TypeError) as error:
        raise InstallationError("invalid_plan") from error
    if len(actions) != len(raw["actions"]):
        raise InstallationError("invalid_plan")
    _validate_update_plan(plan)
    return plan


def apply_installation(plan: InstallPlan) -> InstallationResult:
    _validate_install_plan(plan)
    core = Path(plan.core_root)
    state = Path(plan.state_root)
    if core.exists() or state.exists():
        try:
            _verify_installation(plan)
        except InstallationError as error:
            raise InstallationError("target_exists") from error
        return InstallationResult("already_installed", plan.plan_id, str(core), str(state))

    scanned = _scan_artifact(_existing_root(plan.artifact_root, "invalid_artifact_root"))
    if scanned != plan.manifest.files:
        raise InstallationError("artifact_changed")

    created_files: dict[Path, bytes] = {}
    created_directories: list[Path] = []
    activated = False
    journal_path = state / "operations" / f"{plan.plan_id}.json"
    try:
        _mkdir_exclusive(core, created_directories)
        _mkdir_exclusive(state, created_directories)
        _mkdir_exclusive(journal_path.parent, created_directories)
        journal = _journal_bytes(plan, "prepared", (), False, False)
        _write_exclusive(journal_path, journal, 0o600)
        created_files[journal_path] = journal

        release_root = core / plan.manifest.release_relative_path
        for directory in (core / "releases", release_root):
            _mkdir_exclusive(directory, created_directories)
        for action in plan.actions:
            target = Path(action.target)
            relative_parent = PurePosixPath(action.source_relative_path).parent
            if relative_parent != PurePosixPath("."):
                current = release_root
                for part in relative_parent.parts:
                    current /= part
                    if not current.exists():
                        _mkdir_exclusive(current, created_directories)
                    elif current not in created_directories:
                        raise InstallationError("target_exists")
            data = _read_bounded(
                Path(plan.artifact_root) / action.source_relative_path,
                MAX_ARTIFACT_FILE_BYTES,
            )
            if _digest(data) != action.sha256:
                raise InstallationError("artifact_changed")
            _write_exclusive(target, data, action.mode)
            created_files[target] = data
            journal = _journal_bytes(
                plan, "copying",
                tuple(str(path) for path in created_files if path != journal_path),
                False, False,
            )
            _replace_file(journal_path, journal, 0o600)
            created_files[journal_path] = journal

        for action in plan.actions:
            _require_matching_file(
                Path(action.target),
                ManagedFile(action.source_relative_path, action.sha256, action.mode),
            )
        manifest_data = serialize(plan.manifest).encode("utf-8")
        manifest_store = state / MANIFEST_STORE_DIRECTORY
        _mkdir_exclusive(manifest_store, created_directories)
        generation_path = _manifest_generation_path(state, plan.manifest)
        _publish_exclusive(generation_path, manifest_data, 0o600)
        created_files[generation_path] = manifest_data
        _publish_exclusive(Path(plan.manifest_target), manifest_data, 0o600)
        created_files[Path(plan.manifest_target)] = manifest_data
        journal = _journal_bytes(
            plan, "manifest_published",
            tuple(str(path) for path in created_files if path != journal_path), True, False,
        )
        _replace_file(journal_path, journal, 0o600)
        created_files[journal_path] = journal

        journal = _journal_bytes(
            plan, "activating",
            tuple(str(path) for path in created_files if path != journal_path), True, False,
        )
        _replace_file(journal_path, journal, 0o600)
        created_files[journal_path] = journal
        active = _make_active_release(plan)
        active_path = state / "active.json"
        active_data = serialize(active).encode("utf-8")
        _publish_exclusive(active_path, active_data, 0o600)
        created_files[active_path] = active_data
        activated = True

        _verify_installation(plan)
        journal_path.unlink()
        created_files.pop(journal_path)
        journal_path.parent.rmdir()
        created_directories.remove(journal_path.parent)
        return InstallationResult("installed", plan.plan_id, str(core), str(state))
    except Exception as error:
        if activated or not _rollback_created(created_files, created_directories):
            raise InstallationError("recovery_required") from error
        if isinstance(error, InstallationError):
            raise
        raise InstallationError("apply_failed") from error


def verify_installation(plan: InstallPlan) -> InstallationResult:
    _validate_install_plan(plan)
    try:
        _verify_installation(plan)
    except InstallationError as error:
        raise InstallationError("verification_failed") from error
    return InstallationResult(
        "verified", plan.plan_id, plan.core_root, plan.state_root
    )


def apply_update(plan: UpdatePlan) -> InstallationResult:
    _validate_update_plan(plan)
    core = Path(plan.core_root)
    state = Path(plan.state_root)
    next_active = _make_active(
        plan.next_manifest, plan.plan_id
    )
    try:
        _verify_update(plan, next_active)
    except InstallationError:
        pass
    else:
        return InstallationResult(
            "already_updated", plan.plan_id, plan.core_root, plan.state_root
        )

    artifact = _existing_root(plan.artifact_root, "invalid_artifact_root")
    try:
        descriptor = load_release_descriptor(artifact / "release.json")
        scanned = _scan_release_artifact(artifact, descriptor)
    except InstallationError as error:
        raise InstallationError("artifact_changed") from error
    if (
        descriptor.descriptor_sha256 != plan.release_descriptor_sha256
        or scanned != plan.next_manifest.files
    ):
        raise InstallationError("artifact_changed")
    active_path = state / "active.json"
    compatibility_path = state / "installation.json"
    previous_active = _load_active_release(active_path)
    previous_manifest = _load_active_manifest(state)
    _verify_update_preconditions(plan, previous_active, previous_manifest)
    previous_active_data = _read_bounded(active_path, MAX_MANIFEST_BYTES)
    previous_manifest_data = serialize(previous_manifest).encode("utf-8")
    next_active_data = serialize(next_active).encode("utf-8")
    next_manifest_data = serialize(plan.next_manifest).encode("utf-8")

    created_files: dict[Path, bytes] = {}
    created_directories: list[Path] = []
    activated = False
    journal_path = state / "operations" / f"{plan.plan_id}.json"
    try:
        if journal_path.parent.exists():
            if not journal_path.parent.is_dir() or _is_link_like(journal_path.parent):
                raise InstallationError("managed_state_invalid")
        else:
            _mkdir_exclusive(journal_path.parent, created_directories)
        journal = _update_journal_bytes(plan, "prepared", (), False)
        _write_exclusive(journal_path, journal, 0o600)
        created_files[journal_path] = journal

        release_root = core / plan.next_manifest.release_relative_path
        _mkdir_exclusive(release_root, created_directories)
        for action in plan.actions:
            target = Path(action.target)
            parent = PurePosixPath(action.source_relative_path).parent
            if parent != PurePosixPath("."):
                current = release_root
                for part in parent.parts:
                    current /= part
                    if not current.exists():
                        _mkdir_exclusive(current, created_directories)
                    elif current not in created_directories:
                        raise InstallationError("target_exists")
            data = _read_bounded(
                artifact / action.source_relative_path, MAX_ARTIFACT_FILE_BYTES
            )
            if _digest(data) != action.sha256:
                raise InstallationError("artifact_changed")
            _write_exclusive(target, data, action.mode)
            created_files[target] = data
            journal = _update_journal_bytes(
                plan, "copying",
                tuple(str(item) for item in created_files if item != journal_path),
                False,
            )
            _replace_file(journal_path, journal, 0o600)
            created_files[journal_path] = journal
        _verify_release(plan.next_manifest)

        store = state / MANIFEST_STORE_DIRECTORY
        if store.exists():
            if not store.is_dir() or _is_link_like(store):
                raise InstallationError("managed_state_invalid")
        else:
            _mkdir_exclusive(store, created_directories)
        for manifest, data in (
            (previous_manifest, previous_manifest_data),
            (plan.next_manifest, next_manifest_data),
        ):
            generation = _manifest_generation_path(state, manifest)
            if generation.exists():
                if _read_bounded(generation, MAX_MANIFEST_BYTES) != data:
                    raise InstallationError("manifest_mismatch")
            else:
                _publish_exclusive(generation, data, 0o600)
                created_files[generation] = data

        _verify_release(previous_manifest)
        journal = _update_journal_bytes(
            plan, "activating",
            tuple(str(item) for item in created_files if item != journal_path),
            False,
        )
        _replace_file(journal_path, journal, 0o600)
        created_files[journal_path] = journal
        _replace_file(active_path, next_active_data, 0o600)
        activated = True
        journal = _update_journal_bytes(
            plan, "activated",
            tuple(str(item) for item in created_files if item != journal_path),
            True,
        )
        _replace_file(journal_path, journal, 0o600)
        created_files[journal_path] = journal
        _verify_active_state(state, next_active, plan.next_manifest)
        _replace_file(compatibility_path, next_manifest_data, 0o600)
        _verify_update(plan, next_active)

        journal_path.unlink()
        created_files.pop(journal_path)
        if journal_path.parent in created_directories:
            journal_path.parent.rmdir()
            created_directories.remove(journal_path.parent)
        return InstallationResult(
            "updated", plan.plan_id, plan.core_root, plan.state_root
        )
    except Exception as error:
        if activated:
            if _backout_activation(
                state, next_active_data, previous_active_data,
                previous_active, previous_manifest, previous_manifest_data,
            ):
                try:
                    journal_path.unlink()
                    if journal_path.parent in created_directories:
                        journal_path.parent.rmdir()
                except OSError:
                    raise InstallationError("recovery_required") from error
                if isinstance(error, InstallationError):
                    raise InstallationError("apply_failed") from error
                raise InstallationError("apply_failed") from error
            raise InstallationError("recovery_required") from error
        if not _rollback_created(created_files, created_directories):
            raise InstallationError("recovery_required") from error
        if isinstance(error, InstallationError):
            raise
        raise InstallationError("apply_failed") from error


def verify_update(plan: UpdatePlan) -> InstallationResult:
    _validate_update_plan(plan)
    try:
        _verify_update(plan, _make_active(plan.next_manifest, plan.plan_id))
    except InstallationError as error:
        raise InstallationError("verification_failed") from error
    return InstallationResult(
        "verified", plan.plan_id, plan.core_root, plan.state_root
    )


def serialize(
    value: (
        InstallPlan
        | RemovalPlan
        | UpdatePlan
        | ReleaseDescriptor
        | InstallationManifest
        | ActiveRelease
    ),
) -> str:
    return _canonical_json(asdict(value)) + "\n"


def _validate_install_plan(plan: InstallPlan) -> None:
    if plan.schema_version != INSTALL_PLAN_SCHEMA_VERSION or plan.operation != "install":
        raise InstallationError("unsupported_plan")
    artifact = _existing_root(plan.artifact_root, "invalid_plan")
    core = _absolute_text_path(plan.core_root, "invalid_plan")
    state = _absolute_text_path(plan.state_root, "invalid_plan")
    if core == state or _contains(core, state) or _contains(state, core):
        raise InstallationError("overlapping_roots")
    _validate_manifest(plan.manifest)
    if (
        plan.manifest.core_root != str(core)
        or plan.manifest.state_root != str(state)
        or plan.manifest_target != str(state / "installation.json")
    ):
        raise InstallationError("invalid_plan")
    release_root = core / plan.manifest.release_relative_path
    expected_actions = tuple(
        InstallAction(
            item.relative_path, str(release_root / item.relative_path),
            item.sha256, item.mode,
        )
        for item in plan.manifest.files
    )
    if plan.actions != expected_actions:
        raise InstallationError("invalid_plan")
    for action in plan.actions:
        _safe_relative(action.source_relative_path)
        if (
            type(action.sha256) is not str
            or not _DIGEST.fullmatch(action.sha256)
            or type(action.mode) is not int
        ):
            raise InstallationError("invalid_plan")
        if not _contains(release_root, _absolute_text_path(action.target, "invalid_plan")):
            raise InstallationError("invalid_plan")
        if not _contains(artifact, artifact / action.source_relative_path):
            raise InstallationError("invalid_plan")
    unsigned = asdict(plan)
    claimed = unsigned.pop("plan_id")
    if type(claimed) is not str or claimed != _digest(_canonical_json(unsigned).encode()):
        raise InstallationError("plan_integrity_failed")


def _make_active_release(plan: InstallPlan) -> ActiveRelease:
    return _make_active(plan.manifest, plan.plan_id)


def _make_active(
    manifest: InstallationManifest, plan_id: str
) -> ActiveRelease:
    unsigned = {
        "schema_version": ACTIVE_SCHEMA_VERSION,
        "core_version": manifest.core_version,
        "release_relative_path": manifest.release_relative_path,
        "manifest_sha256": manifest.manifest_sha256,
        "activation_plan_id": plan_id,
    }
    return ActiveRelease(
        ACTIVE_SCHEMA_VERSION, manifest.core_version,
        manifest.release_relative_path, manifest.manifest_sha256,
        plan_id, _digest(_canonical_json(unsigned).encode()),
    )


def _validate_update_plan(plan: UpdatePlan) -> None:
    if plan.schema_version != INSTALL_PLAN_SCHEMA_VERSION or plan.operation != "update":
        raise InstallationError("unsupported_plan")
    artifact = _existing_root(plan.artifact_root, "invalid_plan")
    core = _absolute_text_path(plan.core_root, "invalid_plan")
    state = _absolute_text_path(plan.state_root, "invalid_plan")
    _validate_manifest(plan.next_manifest)
    if (
        any(
            type(value) is not str
            for value in (
                plan.manifest_path, plan.from_version, plan.to_version,
                plan.release_descriptor_sha256,
                plan.current_manifest_sha256,
                plan.activation.current_metadata_sha256,
                plan.activation.next_core_version,
                plan.activation.next_release_relative_path,
                plan.activation.next_manifest_sha256,
                plan.activation.activation_plan_id,
                plan.backout_release_relative_path,
                plan.backout_manifest_sha256,
            )
        )
        or
        plan.manifest_path != str(state / "installation.json")
        or plan.next_manifest.core_root != str(core)
        or plan.next_manifest.state_root != str(state)
        or plan.next_manifest.core_version != plan.to_version
        or plan.next_manifest.release_relative_path != f"releases/{plan.to_version}"
        or plan.activation != ActivationTransition(
            plan.activation.current_metadata_sha256,
            plan.to_version,
            plan.next_manifest.release_relative_path,
            plan.next_manifest.manifest_sha256,
            "$plan_id",
        )
        or plan.current_manifest_sha256 != plan.backout_manifest_sha256
        or not _DIGEST.fullmatch(plan.release_descriptor_sha256)
        or not _DIGEST.fullmatch(plan.current_manifest_sha256)
        or not _DIGEST.fullmatch(plan.activation.current_metadata_sha256)
        or not _VERSION.fullmatch(plan.from_version)
        or not _VERSION.fullmatch(plan.to_version)
        or _version_tuple(plan.to_version) <= _version_tuple(plan.from_version)
    ):
        raise InstallationError("invalid_plan")
    release_root = core / plan.next_manifest.release_relative_path
    expected_actions = tuple(
        InstallAction(
            item.relative_path, str(release_root / item.relative_path),
            item.sha256, item.mode,
        )
        for item in plan.next_manifest.files
    )
    if plan.actions != expected_actions:
        raise InstallationError("invalid_plan")
    for action in plan.actions:
        if not _contains(artifact, artifact / action.source_relative_path):
            raise InstallationError("invalid_plan")
    unsigned = asdict(plan)
    claimed = unsigned.pop("plan_id")
    if type(claimed) is not str or claimed != _digest(_canonical_json(unsigned).encode()):
        raise InstallationError("plan_integrity_failed")


def _parse_active_release(raw: Any) -> ActiveRelease:
    keys = {
        "schema_version", "core_version", "release_relative_path",
        "manifest_sha256", "activation_plan_id", "metadata_sha256",
    }
    if type(raw) is not dict or set(raw) != keys:
        raise InstallationError("invalid_active_metadata")
    active = ActiveRelease(
        raw["schema_version"], raw["core_version"],
        raw["release_relative_path"], raw["manifest_sha256"],
        raw["activation_plan_id"], raw["metadata_sha256"],
    )
    unsigned = asdict(active)
    claimed = unsigned.pop("metadata_sha256")
    if (
        active.schema_version != ACTIVE_SCHEMA_VERSION
        or type(active.core_version) is not str
        or not _VERSION.fullmatch(active.core_version)
        or type(active.release_relative_path) is not str
        or type(active.manifest_sha256) is not str
        or not _DIGEST.fullmatch(active.manifest_sha256)
        or type(active.activation_plan_id) is not str
        or not _DIGEST.fullmatch(active.activation_plan_id)
        or type(claimed) is not str
        or claimed != _digest(_canonical_json(unsigned).encode())
    ):
        raise InstallationError("active_integrity_failed")
    _safe_relative(active.release_relative_path)
    return active


def _load_active_release(path: Path) -> ActiveRelease:
    try:
        raw = json.loads(
            _read_bounded(
                _regular_file(path, "active_read_error"), MAX_MANIFEST_BYTES
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallationError("invalid_active_metadata") from error
    return _parse_active_release(raw)


def _verify_active_manifest(
    active: ActiveRelease, manifest: InstallationManifest
) -> None:
    if (
        active.core_version != manifest.core_version
        or active.release_relative_path != manifest.release_relative_path
        or active.manifest_sha256 != manifest.manifest_sha256
    ):
        raise InstallationError("active_mismatch")


def _verify_installation(plan: InstallPlan) -> None:
    manifest_path = Path(plan.manifest_target)
    compatibility = load_installation_manifest(manifest_path)
    manifest = _load_active_manifest(Path(plan.state_root))
    if compatibility != plan.manifest or manifest != plan.manifest:
        raise InstallationError("manifest_mismatch")
    active = _load_active_release(Path(plan.state_root) / "active.json")
    _verify_active_manifest(active, manifest)
    if active != _make_active_release(plan):
        raise InstallationError("active_mismatch")
    _verify_release(manifest)


def _manifest_generation_path(
    state: Path, manifest: InstallationManifest
) -> Path:
    return state / MANIFEST_STORE_DIRECTORY / f"{manifest.manifest_sha256}.json"


def _load_active_manifest(state: Path) -> InstallationManifest:
    active = _load_active_release(state / "active.json")
    generation = (
        state / MANIFEST_STORE_DIRECTORY / f"{active.manifest_sha256}.json"
    )
    path = generation if generation.exists() else state / "installation.json"
    manifest = load_installation_manifest(path)
    _verify_active_manifest(active, manifest)
    return manifest


def _verify_active_state(
    state: Path,
    expected_active: ActiveRelease,
    expected_manifest: InstallationManifest,
) -> None:
    active = _load_active_release(state / "active.json")
    if active != expected_active:
        raise InstallationError("active_mismatch")
    manifest = _load_active_manifest(state)
    if manifest != expected_manifest:
        raise InstallationError("manifest_mismatch")
    _verify_release(manifest)


def _verify_update_preconditions(
    plan: UpdatePlan,
    active: ActiveRelease,
    manifest: InstallationManifest,
) -> None:
    if (
        active.metadata_sha256 != plan.activation.current_metadata_sha256
        or manifest.manifest_sha256 != plan.current_manifest_sha256
        or manifest.core_version != plan.from_version
        or manifest.release_relative_path != plan.backout_release_relative_path
    ):
        raise InstallationError("active_mismatch")
    compatibility = load_installation_manifest(plan.manifest_path)
    if compatibility != manifest:
        raise InstallationError("manifest_mismatch")
    _verify_active_manifest(active, manifest)
    _verify_release(manifest)
    target = Path(plan.core_root) / plan.next_manifest.release_relative_path
    if target.exists() or _is_link_like(target):
        raise InstallationError("target_exists")


def _verify_update(plan: UpdatePlan, expected_active: ActiveRelease) -> None:
    state = Path(plan.state_root)
    _verify_active_state(state, expected_active, plan.next_manifest)
    compatibility = load_installation_manifest(plan.manifest_path)
    if compatibility != plan.next_manifest:
        raise InstallationError("manifest_mismatch")
    previous_path = (
        state / MANIFEST_STORE_DIRECTORY
        / f"{plan.backout_manifest_sha256}.json"
    )
    previous = load_installation_manifest(previous_path)
    if (
        previous.manifest_sha256 != plan.backout_manifest_sha256
        or previous.core_version != plan.from_version
        or previous.release_relative_path != plan.backout_release_relative_path
    ):
        raise InstallationError("manifest_mismatch")
    _verify_release(previous)


def _backout_activation(
    state: Path,
    expected_active_data: bytes,
    previous_active_data: bytes,
    previous_active: ActiveRelease,
    previous_manifest: InstallationManifest,
    previous_manifest_data: bytes,
) -> bool:
    active_path = state / "active.json"
    try:
        if _read_bounded(active_path, MAX_MANIFEST_BYTES) != expected_active_data:
            return False
        immutable_previous = load_installation_manifest(
            _manifest_generation_path(state, previous_manifest)
        )
        if immutable_previous != previous_manifest:
            return False
        _verify_active_manifest(previous_active, previous_manifest)
        _verify_release(previous_manifest)
        _replace_file(active_path, previous_active_data, 0o600)
        _replace_file(
            state / "installation.json", previous_manifest_data, 0o600
        )
        _verify_active_state(state, previous_active, previous_manifest)
    except (InstallationError, OSError):
        return False
    return True


def _verify_release(manifest: InstallationManifest) -> None:
    release_root = _existing_root(
        Path(manifest.core_root) / manifest.release_relative_path,
        "managed_root_invalid",
    )
    for item in manifest.files:
        _require_matching_file(release_root / item.relative_path, item)
    expected_files = {item.relative_path for item in manifest.files}
    expected_directories = {
        PurePosixPath(directory).relative_to(manifest.release_relative_path).as_posix()
        for directory in manifest.removable_directories
        if directory != manifest.release_relative_path
        and PurePosixPath(manifest.release_relative_path) in PurePosixPath(directory).parents
    }
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for path in sorted(release_root.rglob("*"), key=lambda item: item.as_posix()):
        if _is_link_like(path):
            raise InstallationError("managed_path_link_forbidden")
        relative = path.relative_to(release_root).as_posix()
        if path.is_file():
            actual_files.add(relative)
        elif path.is_dir():
            actual_directories.add(relative)
        else:
            raise InstallationError("managed_path_type_forbidden")
    if actual_files != expected_files or actual_directories != expected_directories:
        raise InstallationError("managed_paths_changed")


def _version_tuple(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _journal_bytes(
    plan: InstallPlan,
    phase: str,
    created_paths: tuple[str, ...],
    manifest_published: bool,
    active_published: bool,
) -> bytes:
    value = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "operation": "install",
        "plan_id": plan.plan_id,
        "phase": phase,
        "created_paths": list(created_paths),
        "manifest_published": manifest_published,
        "active_published": active_published,
        "rollback": "remove_only_unchanged_created_paths",
    }
    return (_canonical_json(value) + "\n").encode("utf-8")


def _update_journal_bytes(
    plan: UpdatePlan,
    phase: str,
    created_paths: tuple[str, ...],
    active_replaced: bool,
) -> bytes:
    value = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "operation": "update",
        "plan_id": plan.plan_id,
        "phase": phase,
        "created_paths": list(created_paths),
        "active_replaced": active_replaced,
        "previous_manifest_sha256": plan.backout_manifest_sha256,
        "next_manifest_sha256": plan.next_manifest.manifest_sha256,
        "rollback": (
            "reactivate_previous_only_if_activation_and_previous_release_verify"
        ),
    }
    return (_canonical_json(value) + "\n").encode("utf-8")


def _mkdir_exclusive(path: Path, created: list[Path]) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as error:
        raise InstallationError("target_exists") from error
    created.append(path)


def _write_exclusive(path: Path, data: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    path.chmod(mode)


def _replace_file(path: Path, data: bytes, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    _write_exclusive(temporary, data, mode)
    try:
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _publish_exclusive(path: Path, data: bytes, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.publish")
    _write_exclusive(temporary, data, mode)
    try:
        os.link(temporary, path, follow_symlinks=False)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _rollback_created(files: dict[Path, bytes], directories: list[Path]) -> bool:
    for path, expected in reversed(tuple(files.items())):
        try:
            if _read_bounded(path, max(len(expected), 1)) != expected:
                return False
        except InstallationError:
            return False
    try:
        for path in reversed(tuple(files)):
            path.unlink()
        for path in reversed(directories):
            path.rmdir()
    except OSError:
        return False
    return True


def _plan_string_tuple(value: Any) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise InstallationError("invalid_plan")
    return tuple(value)


def _make_manifest(version: str, core: Path, state: Path, release: str,
                   artifact_digest: str, files: tuple[ManagedFile, ...]) -> InstallationManifest:
    directory_set = {PurePosixPath(release), PurePosixPath("releases")}
    release_path = PurePosixPath(release)
    for item in files:
        parent = release_path / PurePosixPath(item.relative_path).parent
        while parent != PurePosixPath(".") and parent != PurePosixPath("releases"):
            directory_set.add(parent)
            parent = parent.parent
    directories = tuple(sorted(
        (item.as_posix() for item in directory_set),
        key=lambda value: (value.count("/"), value), reverse=True,
    ))
    unsigned = {
        "schema_version": MANIFEST_SCHEMA_VERSION, "core_version": version,
        "state": "active", "core_root": str(core), "state_root": str(state),
        "release_relative_path": release, "artifact_sha256": artifact_digest,
        "files": [asdict(item) for item in files],
        "generated_state_paths": ["active.json", "installation.json"],
        "removable_directories": list(directories),
    }
    return InstallationManifest(
        MANIFEST_SCHEMA_VERSION, version, "active", str(core), str(state),
        release, artifact_digest, files,
        ("active.json", "installation.json"), directories,
        _digest(_canonical_json(unsigned).encode()),
    )


def _validate_manifest(manifest: InstallationManifest) -> None:
    if manifest.schema_version != 1 or manifest.state != "active":
        raise InstallationError("unsupported_manifest")
    if (
        type(manifest.core_version) is not str
        or type(manifest.artifact_sha256) is not str
        or not _VERSION.fullmatch(manifest.core_version)
        or not _DIGEST.fullmatch(manifest.artifact_sha256)
    ):
        raise InstallationError("invalid_manifest")
    core = _absolute_text_path(manifest.core_root, "invalid_manifest")
    state = _absolute_text_path(manifest.state_root, "invalid_manifest")
    if core == state or _contains(core, state) or _contains(state, core):
        raise InstallationError("ownership_overlap")
    _safe_relative(manifest.release_relative_path)
    seen: set[str] = set()
    for item in manifest.files:
        _safe_relative(item.relative_path)
        if (
            item.relative_path in seen
            or type(item.sha256) is not str
            or not _DIGEST.fullmatch(item.sha256)
        ):
            raise InstallationError("invalid_manifest")
        if type(item.mode) is not int or item.mode not in (0o600, 0o700):
            raise InstallationError("invalid_manifest")
        seen.add(item.relative_path)
    if len(set(manifest.removable_directories)) != len(manifest.removable_directories):
        raise InstallationError("invalid_manifest")
    if manifest.generated_state_paths != ("active.json", "installation.json"):
        raise InstallationError("invalid_manifest")
    release = PurePosixPath(manifest.release_relative_path)
    for directory in manifest.removable_directories:
        candidate = _safe_relative(directory)
        if candidate != PurePosixPath("releases") and not (
            candidate == release or release in candidate.parents
        ):
            raise InstallationError("invalid_manifest")
    unsigned = asdict(manifest)
    claimed = unsigned.pop("manifest_sha256")
    if not isinstance(claimed, str) or claimed != _digest(_canonical_json(unsigned).encode()):
        raise InstallationError("manifest_integrity_failed")


def _scan_artifact(root: Path) -> tuple[ManagedFile, ...]:
    return _scan_artifact_excluding(root, ())


def _scan_release_artifact(
    root: Path, descriptor: ReleaseDescriptor
) -> tuple[ManagedFile, ...]:
    files = _scan_artifact_excluding(root, ("release.json",))
    if files != descriptor.files:
        raise InstallationError("release_artifact_mismatch")
    return files


def _scan_artifact_excluding(
    root: Path, excluded: tuple[str, ...]
) -> tuple[ManagedFile, ...]:
    paths = sorted(root.rglob("*"), key=lambda item: item.as_posix())
    if len(paths) > MAX_ARTIFACT_FILES:
        raise InstallationError("artifact_too_many_entries")
    files: list[ManagedFile] = []
    total = 0
    for path in paths:
        if _is_link_like(path):
            raise InstallationError("artifact_link_forbidden")
        if path.is_dir():
            continue
        if not path.is_file():
            raise InstallationError("artifact_type_forbidden")
        data = _read_bounded(path, MAX_ARTIFACT_FILE_BYTES)
        total += len(data)
        if total > MAX_ARTIFACT_TOTAL_BYTES:
            raise InstallationError("artifact_too_large")
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        _safe_relative(relative)
        mode = 0o700 if path.stat().st_mode & stat.S_IXUSR else 0o600
        files.append(ManagedFile(relative, _digest(data), mode))
    if not files:
        raise InstallationError("artifact_empty")
    return tuple(files)


def _validate_release_descriptor(descriptor: ReleaseDescriptor) -> None:
    if (
        type(descriptor.schema_version) is not int
        or descriptor.schema_version != RELEASE_DESCRIPTOR_SCHEMA_VERSION
    ):
        raise InstallationError("unsupported_release_descriptor")
    if (
        type(descriptor.core_version) is not str
        or not _VERSION.fullmatch(descriptor.core_version)
        or type(descriptor.configuration_schema_minimum) is not int
        or type(descriptor.configuration_schema_maximum) is not int
        or descriptor.configuration_schema_minimum < 1
        or descriptor.configuration_schema_maximum
        < descriptor.configuration_schema_minimum
        or type(descriptor.migration) is not str
        or type(descriptor.release_notes_path) is not str
        or type(descriptor.artifact_sha256) is not str
        or not _DIGEST.fullmatch(descriptor.artifact_sha256)
    ):
        raise InstallationError("invalid_release_descriptor")
    try:
        notes = _safe_relative(descriptor.release_notes_path).as_posix()
    except InstallationError as error:
        raise InstallationError("invalid_release_descriptor") from error
    seen: set[str] = set()
    for item in descriptor.files:
        try:
            relative = _safe_relative(item.relative_path).as_posix()
        except InstallationError as error:
            raise InstallationError("invalid_release_descriptor") from error
        if (
            relative == "release.json"
            or relative in seen
            or type(item.sha256) is not str
            or not _DIGEST.fullmatch(item.sha256)
            or type(item.mode) is not int
            or item.mode not in (0o600, 0o700)
        ):
            raise InstallationError("invalid_release_descriptor")
        seen.add(relative)
    if notes not in seen:
        raise InstallationError("release_notes_missing")
    expected_artifact = _digest(
        _canonical_json([asdict(item) for item in descriptor.files]).encode()
    )
    if descriptor.artifact_sha256 != expected_artifact:
        raise InstallationError("release_artifact_integrity_failed")
    unsigned = asdict(descriptor)
    claimed = unsigned.pop("descriptor_sha256")
    if (
        type(claimed) is not str
        or not _DIGEST.fullmatch(claimed)
        or claimed != _digest(_canonical_json(unsigned).encode())
    ):
        raise InstallationError("release_descriptor_integrity_failed")
    if not (
        descriptor.configuration_schema_minimum
        <= SUPPORTED_CONFIGURATION_SCHEMA_VERSION
        <= descriptor.configuration_schema_maximum
    ):
        raise InstallationError("incompatible_release")
    if descriptor.migration != "none":
        raise InstallationError("unsupported_release_migration")


def _require_matching_file(path: Path, item: ManagedFile) -> None:
    if _is_link_like(path):
        raise InstallationError("managed_file_link_forbidden")
    try:
        if path.resolve(strict=True) != path:
            raise InstallationError("managed_file_link_forbidden")
        data = _read_bounded(path, MAX_ARTIFACT_FILE_BYTES)
        mode = stat.S_IMODE(path.stat().st_mode)
    except InstallationError as error:
        raise InstallationError("managed_file_missing") from error
    if _digest(data) != item.sha256 or mode != item.mode:
        raise InstallationError("managed_file_modified")


def _existing_root(value: str | os.PathLike[str], code: str) -> Path:
    path = _absolute_text_path(value, code)
    if _is_link_like(path):
        raise InstallationError("root_link_forbidden")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InstallationError(code) from error
    if not resolved.is_dir():
        raise InstallationError(code)
    return resolved


def _new_root(value: str | os.PathLike[str], code: str) -> Path:
    path = _absolute_text_path(value, code)
    if _is_link_like(path):
        raise InstallationError("root_link_forbidden")
    try:
        parent = path.parent.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InstallationError(code) from error
    target = parent / path.name
    if target.exists():
        raise InstallationError("target_exists")
    return target


def _absolute_text_path(value: str | os.PathLike[str], code: str) -> Path:
    try:
        text = os.fspath(value)
    except TypeError as error:
        raise InstallationError(code) from error
    if not isinstance(text, str) or not text or text.startswith("~"):
        raise InstallationError(code)
    path = Path(text)
    if not path.is_absolute() or path.name in ("", ".", ".."):
        raise InstallationError(code)
    return path


def _regular_file(value: str | os.PathLike[str], code: str) -> Path:
    path = _absolute_text_path(value, code)
    try:
        if _is_link_like(path) or not path.is_file():
            raise InstallationError(code)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InstallationError(code) from error
    return resolved


def _plan_file(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(value)
        if _is_link_like(path) or not path.is_file():
            raise InstallationError("plan_read_error")
        resolved = path.resolve(strict=True)
    except InstallationError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise InstallationError("plan_read_error") from error
    return resolved


def _read_bounded(path: Path, maximum: int) -> bytes:
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise InstallationError("file_changed")
        identity = _stat_identity(before)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or _stat_identity(opened) != identity:
                raise InstallationError("file_changed")
            data = stream.read(maximum + 1)
        after = path.stat()
        if _stat_identity(after) != identity:
            raise InstallationError("file_changed")
    except InstallationError:
        raise
    except OSError as error:
        raise InstallationError("read_error") from error
    if len(data) > maximum:
        raise InstallationError("file_too_large")
    return data


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _safe_relative(value: str) -> PurePosixPath:
    if type(value) is not str:
        raise InstallationError("invalid_manifest")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise InstallationError("invalid_manifest")
    return path


def _string_tuple(value: Any) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise InstallationError("invalid_manifest")
    return tuple(value)


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_link_like(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except FileNotFoundError:
        return False
    except OSError:
        return True
