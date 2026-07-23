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
MAX_ARTIFACT_FILES = 512
MAX_ARTIFACT_FILE_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_INSTALL_PLAN_BYTES = 1024 * 1024
ACTIVE_SCHEMA_VERSION = 1
JOURNAL_SCHEMA_VERSION = 1
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
            (str(Path(manifest.core_root) / item)
             for item in manifest.removable_directories),
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
    core_version: str,
) -> UpdatePlan:
    path = _regular_file(manifest_path, "manifest_read_error")
    current = load_installation_manifest(path)
    expected_manifest_path = Path(current.state_root) / "installation.json"
    if path != expected_manifest_path:
        raise InstallationError("manifest_path_mismatch")
    active = _load_active_release(Path(current.state_root) / "active.json")
    _verify_active_manifest(active, current)
    _verify_release(current)

    if not _VERSION.fullmatch(core_version):
        raise InstallationError("invalid_core_version")
    if _version_tuple(core_version) <= _version_tuple(current.core_version):
        raise InstallationError("update_version_not_newer")
    artifact = _existing_root(artifact_root, "invalid_artifact_root")
    files = _scan_artifact(artifact)
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
        "current_manifest_sha256": current.manifest_sha256,
        "actions": [asdict(item) for item in actions],
        "next_manifest": asdict(next_manifest),
        "activation": asdict(activation),
        "backout_release_relative_path": current.release_relative_path,
        "backout_manifest_sha256": current.manifest_sha256,
        "preconditions": [
            "current_installation_verified",
            "new_release_absent",
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


def serialize(
    value: (
        InstallPlan
        | RemovalPlan
        | UpdatePlan
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
    unsigned = {
        "schema_version": ACTIVE_SCHEMA_VERSION,
        "core_version": plan.manifest.core_version,
        "release_relative_path": plan.manifest.release_relative_path,
        "manifest_sha256": plan.manifest.manifest_sha256,
        "activation_plan_id": plan.plan_id,
    }
    return ActiveRelease(
        ACTIVE_SCHEMA_VERSION, plan.manifest.core_version,
        plan.manifest.release_relative_path, plan.manifest.manifest_sha256,
        plan.plan_id, _digest(_canonical_json(unsigned).encode()),
    )


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
    manifest = load_installation_manifest(manifest_path)
    if manifest != plan.manifest:
        raise InstallationError("manifest_mismatch")
    active = _load_active_release(Path(plan.state_root) / "active.json")
    _verify_active_manifest(active, manifest)
    if active != _make_active_release(plan):
        raise InstallationError("active_mismatch")
    _verify_release(manifest)


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
        _safe_relative(relative)
        mode = 0o700 if path.stat().st_mode & stat.S_IXUSR else 0o600
        files.append(ManagedFile(relative, _digest(data), mode))
    if not files:
        raise InstallationError("artifact_empty")
    return tuple(files)


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
