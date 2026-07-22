"""Installation manifests and read-only install/removal planning."""

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
    remove_files.append(str(path))
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
        "removable_directories", "manifest_sha256",
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
    manifest = InstallationManifest(
        raw["schema_version"], raw["core_version"], raw["state"],
        raw["core_root"], raw["state_root"], raw["release_relative_path"],
        raw["artifact_sha256"], files, directories, raw["manifest_sha256"],
    )
    _validate_manifest(manifest)
    return manifest


def serialize(value: InstallPlan | RemovalPlan | InstallationManifest) -> str:
    return _canonical_json(asdict(value)) + "\n"


def _make_manifest(version: str, core: Path, state: Path, release: str,
                   artifact_digest: str, files: tuple[ManagedFile, ...]) -> InstallationManifest:
    directories = tuple(sorted({release, "releases"}, key=lambda value: (value.count("/"), value), reverse=True))
    unsigned = {
        "schema_version": MANIFEST_SCHEMA_VERSION, "core_version": version,
        "state": "active", "core_root": str(core), "state_root": str(state),
        "release_relative_path": release, "artifact_sha256": artifact_digest,
        "files": [asdict(item) for item in files],
        "removable_directories": list(directories),
    }
    return InstallationManifest(
        MANIFEST_SCHEMA_VERSION, version, "active", str(core), str(state),
        release, artifact_digest, files, directories,
        _digest(_canonical_json(unsigned).encode()),
    )


def _validate_manifest(manifest: InstallationManifest) -> None:
    if manifest.schema_version != 1 or manifest.state != "active":
        raise InstallationError("unsupported_manifest")
    if not _VERSION.fullmatch(manifest.core_version) or not _DIGEST.fullmatch(manifest.artifact_sha256):
        raise InstallationError("invalid_manifest")
    core = _absolute_text_path(manifest.core_root, "invalid_manifest")
    state = _absolute_text_path(manifest.state_root, "invalid_manifest")
    if core == state or _contains(core, state) or _contains(state, core):
        raise InstallationError("ownership_overlap")
    _safe_relative(manifest.release_relative_path)
    seen: set[str] = set()
    for item in manifest.files:
        _safe_relative(item.relative_path)
        if item.relative_path in seen or not _DIGEST.fullmatch(item.sha256):
            raise InstallationError("invalid_manifest")
        if type(item.mode) is not int or item.mode not in (0o600, 0o700):
            raise InstallationError("invalid_manifest")
        seen.add(item.relative_path)
    if len(set(manifest.removable_directories)) != len(manifest.removable_directories):
        raise InstallationError("invalid_manifest")
    release = PurePosixPath(manifest.release_relative_path)
    allowed_directories = {release, *release.parents}
    allowed_directories.discard(PurePosixPath("."))
    for directory in manifest.removable_directories:
        if _safe_relative(directory) not in allowed_directories:
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
        mode = 0o700 if path.stat().st_mode & stat.S_IXUSR else 0o600
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
    if resolved != path or not path.is_dir():
        raise InstallationError(code)
    return path


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
        if _is_link_like(path) or not path.is_file() or path.resolve(strict=True) != path:
            raise InstallationError(code)
    except (OSError, RuntimeError) as error:
        raise InstallationError(code) from error
    return path


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
