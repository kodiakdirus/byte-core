"""Bounded traversal and scanning of a release artifact directory."""

from __future__ import annotations

import os
from pathlib import Path

from ..models import ScanPolicy, SourceOwnership
from .files import _is_link_like, scan_files
from .models import AdapterError, AdapterLimits, AdapterResult


def scan_artifact_directory(
    artifact_root: str | os.PathLike[str],
    *,
    policy: ScanPolicy | None = None,
    limits: AdapterLimits | None = None,
) -> AdapterResult:
    """Scan all bounded regular text files in one explicit directory."""

    active_limits = limits or AdapterLimits()
    try:
        root = Path(artifact_root)
        if _is_link_like(root):
            return AdapterResult((), (AdapterError("root_link_forbidden"),))
        resolved_root = root.resolve(strict=True)
        if not resolved_root.is_dir():
            return AdapterResult((), (AdapterError("invalid_root"),))
    except (OSError, RuntimeError, TypeError, ValueError):
        return AdapterResult((), (AdapterError("invalid_root"),))

    paths: list[str] = []
    errors: list[AdapterError] = []
    ordinal = 0
    stopped = False
    max_entries = active_limits.max_files * 4

    def visit(directory: Path, parts: tuple[str, ...]) -> None:
        nonlocal ordinal, stopped
        if stopped:
            return
        try:
            entries = []
            remaining = max_entries - ordinal
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if len(entries) >= remaining:
                        errors.append(AdapterError("too_many_entries"))
                        stopped = True
                        return
                    entries.append(entry)
            entries.sort(key=lambda item: item.name)
        except OSError:
            errors.append(AdapterError("directory_read_failed", ordinal))
            ordinal += 1
            return

        for entry in entries:
            if stopped:
                return
            current_index = ordinal
            ordinal += 1
            entry_path = Path(entry.path)
            relative_parts = parts + (entry.name,)
            if len(relative_parts) > active_limits.max_depth:
                errors.append(AdapterError("path_too_deep", current_index))
                continue
            if _is_link_like(entry_path):
                errors.append(AdapterError("symlink_forbidden", current_index))
                continue
            try:
                resolved = entry_path.resolve(strict=True)
                resolved.relative_to(resolved_root)
            except (OSError, RuntimeError, ValueError):
                errors.append(AdapterError("containment_escape", current_index))
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    visit(entry_path, relative_parts)
                elif entry.is_file(follow_symlinks=False):
                    paths.append("/".join(relative_parts))
                    if len(paths) > active_limits.max_files:
                        errors.append(AdapterError("too_many_files"))
                        stopped = True
                        return
                else:
                    errors.append(AdapterError("not_regular_file", current_index))
            except OSError:
                errors.append(AdapterError("entry_read_failed", current_index))

    visit(root, ())
    if any(
        error.code in {"too_many_entries", "too_many_files"}
        for error in errors
    ):
        return AdapterResult((), tuple(sorted(set(errors))))

    if not paths and errors:
        return AdapterResult((), tuple(sorted(set(errors))))

    scanned = scan_files(
        root,
        tuple(paths),
        ownership=SourceOwnership.CORE_PUBLIC,
        policy=policy,
        limits=active_limits,
    )
    return AdapterResult(
        scanned.scans,
        tuple(sorted(set((*errors, *scanned.errors)))),
    )
