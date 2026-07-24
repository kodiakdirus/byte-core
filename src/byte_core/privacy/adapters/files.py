"""Explicit Core-public file input for the privacy scanner."""

from __future__ import annotations

import os
import stat
from itertools import islice
from pathlib import Path
from typing import Iterable

from ..models import ScanError, ScanPolicy, SourceOwnership
from ..scanner import scan_text, validate_source_id
from .models import AdapterError, AdapterLimits, AdapterResult

_TEXT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".ini",
        ".json",
        ".md",
        ".py",
        ".sh",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
        ".zsh",
    }
)
_TEXT_BASENAMES = frozenset(
    {
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        "byte",
        "LICENSE",
        "NOTICE",
    }
)


def scan_files(
    root: str | os.PathLike[str],
    relative_paths: Iterable[str],
    *,
    ownership: SourceOwnership,
    policy: ScanPolicy | None = None,
    limits: AdapterLimits | None = None,
) -> AdapterResult:
    """Scan only explicitly listed Core-public files below one root."""

    active_limits = limits or AdapterLimits()
    if ownership is not SourceOwnership.CORE_PUBLIC:
        return _result(errors=[AdapterError("ownership_forbidden")])

    try:
        root_path = Path(root)
        if _is_link_like(root_path):
            return _result(errors=[AdapterError("root_link_forbidden")])
        resolved_root = root_path.resolve(strict=True)
        if not resolved_root.is_dir():
            return _result(errors=[AdapterError("invalid_root")])
    except (OSError, RuntimeError, TypeError, ValueError):
        return _result(errors=[AdapterError("invalid_root")])

    if isinstance(relative_paths, (str, bytes)):
        return _result(errors=[AdapterError("invalid_paths")])
    try:
        selected = tuple(
            islice(iter(relative_paths), active_limits.max_files + 1)
        )
    except Exception:
        return _result(errors=[AdapterError("invalid_paths")])
    if not selected:
        return _result(errors=[AdapterError("no_inputs")])
    if len(selected) > active_limits.max_files:
        return _result(errors=[AdapterError("too_many_files")])

    errors: list[AdapterError] = []
    prepared: list[tuple[str, int, Path]] = []
    seen_logical: set[str] = set()
    seen_resolved: set[str] = set()

    for index, source_id in enumerate(selected):
        try:
            validate_source_id(source_id)
        except ScanError:
            errors.append(AdapterError("invalid_path", index))
            continue
        if len(source_id.split("/")) > active_limits.max_depth:
            errors.append(AdapterError("path_too_deep", index))
            continue
        if source_id in seen_logical:
            errors.append(AdapterError("duplicate_path", index))
            continue
        seen_logical.add(source_id)
        if not _is_supported_text_path(source_id):
            errors.append(AdapterError("unsupported_file_type", index))
            continue

        candidate = root_path.joinpath(*source_id.split("/"))
        path_error = _validate_candidate(candidate, root_path, resolved_root)
        if path_error is not None:
            errors.append(AdapterError(path_error, index))
            continue

        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            errors.append(AdapterError("path_not_found", index))
            continue
        resolved_key = os.path.normcase(str(resolved))
        if resolved_key in seen_resolved:
            errors.append(AdapterError("duplicate_path", index))
            continue
        seen_resolved.add(resolved_key)
        prepared.append((source_id, index, candidate))

    scans = []
    total_bytes = 0
    for source_id, index, candidate in sorted(prepared):
        read = _read_bounded(candidate, active_limits.max_file_bytes)
        if isinstance(read, str):
            errors.append(AdapterError(read, index))
            continue

        data = read
        if total_bytes + len(data) > active_limits.max_total_bytes:
            errors.append(AdapterError("aggregate_too_large", index))
            continue
        total_bytes += len(data)
        if b"\0" in data:
            errors.append(AdapterError("binary_file", index))
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(AdapterError("invalid_utf8", index))
            continue
        try:
            scans.append(scan_text(source_id, text, policy=policy))
        except ScanError:
            errors.append(AdapterError("scan_failed", index))

    return _result(scans=scans, errors=errors)


def _validate_candidate(
    candidate: Path,
    root_path: Path,
    resolved_root: Path,
) -> str | None:
    current = root_path
    try:
        relative_parts = candidate.relative_to(root_path).parts
    except ValueError:
        return "containment_escape"

    try:
        for part in relative_parts:
            current = current / part
            if _is_link_like(current):
                return "symlink_forbidden"
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
        metadata = candidate.stat()
    except FileNotFoundError:
        return "path_not_found"
    except (OSError, RuntimeError, ValueError):
        return "containment_escape"

    if not stat.S_ISREG(metadata.st_mode):
        return "not_regular_file"
    return None


def _is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True

    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _read_bounded(path: Path, maximum: int) -> bytes | str:
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            return "file_changed"
        identity = _stat_identity(before)
        flags = os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                return "file_changed"
            if _stat_identity(opened) != identity:
                return "file_changed"
            data = stream.read(maximum + 1)
        after = path.stat()
    except OSError:
        return "read_error"

    if _stat_identity(after) != identity:
        return "file_changed"
    if len(data) > maximum:
        return "file_too_large"
    return data


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _is_supported_text_path(source_id: str) -> bool:
    path = Path(source_id)
    return (
        path.name in _TEXT_BASENAMES
        or path.suffix.lower() in _TEXT_SUFFIXES
    )


def _result(
    *,
    scans=(),
    errors=(),
) -> AdapterResult:
    return AdapterResult(
        scans=tuple(sorted(scans, key=lambda scan: scan.source_id)),
        errors=tuple(sorted(set(errors))),
    )
