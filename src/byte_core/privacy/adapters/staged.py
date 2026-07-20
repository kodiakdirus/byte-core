"""Read-only scanning of content staged in a local Git index."""

from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path

from ..models import ScanError, ScanPolicy
from ..scanner import (
    MAX_SOURCE_ID_UTF8_BYTES,
    scan_text,
    validate_source_id,
)
from .files import _is_link_like, _is_supported_text_path
from .models import AdapterError, AdapterLimits, AdapterResult

_REGULAR_GIT_MODES = frozenset({"100644", "100755"})
_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_GIT_TIMEOUT_SECONDS = 10
_MAX_GIT_LIST_BYTES = 8_000_000


def scan_staged(
    repository_root: str | os.PathLike[str],
    *,
    policy: ScanPolicy | None = None,
    limits: AdapterLimits | None = None,
) -> AdapterResult:
    """Scan added, copied, modified, and renamed staged blobs."""

    active_limits = limits or AdapterLimits()
    root, root_error = _git_root(repository_root)
    if root_error is not None:
        return AdapterResult((), (AdapterError(root_error),))

    listed, error = _run_git_limited(
        root,
        (
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--diff-filter=ACMR",
            "--find-renames",
            "--find-copies-harder",
            "--no-ext-diff",
        ),
        max_stdout=min(
            _MAX_GIT_LIST_BYTES,
            active_limits.max_files * (MAX_SOURCE_ID_UTF8_BYTES + 1),
        ),
    )
    if error is not None:
        return AdapterResult((), (AdapterError(error),))
    if listed.returncode != 0:
        return AdapterResult((), (AdapterError("git_list_failed"),))

    raw_paths = tuple(part for part in listed.stdout.split(b"\0") if part)
    if len(raw_paths) > active_limits.max_files:
        return AdapterResult((), (AdapterError("too_many_files"),))

    errors: list[AdapterError] = []
    entries: list[tuple[str, int, str, int]] = []
    seen: set[str] = set()
    total_bytes = 0

    for index, raw_path in enumerate(raw_paths):
        try:
            source_id = raw_path.decode("utf-8")
            validate_source_id(source_id)
        except (UnicodeDecodeError, ScanError):
            errors.append(AdapterError("invalid_git_path", index))
            continue
        if len(source_id.split("/")) > active_limits.max_depth:
            errors.append(AdapterError("path_too_deep", index))
            continue
        if source_id in seen:
            errors.append(AdapterError("duplicate_path", index))
            continue
        seen.add(source_id)
        staged, staged_error = _staged_entry(root, source_id)
        if staged_error is not None:
            errors.append(AdapterError(staged_error, index))
            continue
        mode, object_id = staged
        if mode not in _REGULAR_GIT_MODES:
            errors.append(AdapterError("unsupported_git_mode", index))
            continue
        if not _is_supported_text_path(source_id):
            errors.append(AdapterError("unsupported_file_type", index))
            continue

        size, size_error = _blob_size(root, object_id)
        if size_error is not None:
            errors.append(AdapterError(size_error, index))
            continue
        if size > active_limits.max_file_bytes:
            errors.append(AdapterError("file_too_large", index))
            continue
        if total_bytes + size > active_limits.max_total_bytes:
            errors.append(AdapterError("aggregate_too_large", index))
            continue
        total_bytes += size
        entries.append((source_id, index, object_id, size))

    scans = []
    for source_id, index, object_id, expected_size in sorted(entries):
        blob, blob_error = _read_blob(root, object_id, expected_size)
        if blob_error is not None:
            errors.append(AdapterError(blob_error, index))
            continue
        if b"\0" in blob:
            errors.append(AdapterError("binary_file", index))
            continue
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(AdapterError("invalid_utf8", index))
            continue
        try:
            scans.append(scan_text(source_id, text, policy=policy))
        except ScanError:
            errors.append(AdapterError("scan_failed", index))

    return AdapterResult(
        tuple(sorted(scans, key=lambda scan: scan.source_id)),
        tuple(sorted(set(errors))),
    )


def _git_root(
    repository_root: str | os.PathLike[str],
) -> tuple[Path, str | None]:
    try:
        candidate = Path(repository_root)
        if _is_link_like(candidate):
            return candidate, "root_link_forbidden"
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir():
            return resolved, "invalid_root"
    except (OSError, RuntimeError, TypeError, ValueError):
        return Path(), "invalid_root"

    result, error = _run_git(resolved, ("rev-parse", "--show-toplevel"))
    if error is not None:
        return resolved, error
    if result.returncode != 0:
        return resolved, "not_git_repository"
    try:
        reported = Path(result.stdout.decode("utf-8").strip()).resolve(strict=True)
    except (UnicodeDecodeError, OSError, RuntimeError):
        return resolved, "git_root_invalid"
    if os.path.normcase(str(reported)) != os.path.normcase(str(resolved)):
        return resolved, "git_root_mismatch"
    return resolved, None


def _staged_entry(
    root: Path,
    source_id: str,
) -> tuple[tuple[str, str] | None, str | None]:
    result, error = _run_git(
        root,
        ("ls-files", "--stage", "-z", "--", source_id),
    )
    if error is not None:
        return None, error
    if result.returncode != 0:
        return None, "git_index_read_failed"
    records = tuple(part for part in result.stdout.split(b"\0") if part)
    if len(records) != 1:
        return None, "ambiguous_git_entry"
    try:
        metadata, returned_path = records[0].split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split(" ")
        decoded_path = returned_path.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None, "invalid_git_entry"
    if stage != "0" or decoded_path != source_id:
        return None, "ambiguous_git_entry"
    if _OBJECT_ID.fullmatch(object_id) is None:
        return None, "invalid_git_entry"
    return (mode, object_id), None


def _blob_size(root: Path, object_id: str) -> tuple[int, str | None]:
    result, error = _run_git(root, ("cat-file", "-s", object_id))
    if error is not None:
        return 0, error
    if result.returncode != 0:
        return 0, "git_blob_read_failed"
    try:
        size = int(result.stdout.decode("ascii").strip())
    except (ValueError, UnicodeDecodeError):
        return 0, "invalid_git_blob_size"
    if size < 0:
        return 0, "invalid_git_blob_size"
    return size, None


def _read_blob(
    root: Path,
    object_id: str,
    expected_size: int,
) -> tuple[bytes, str | None]:
    result, error = _run_git(root, ("cat-file", "blob", object_id))
    if error is not None:
        return b"", error
    if result.returncode != 0 or len(result.stdout) != expected_size:
        return b"", "git_blob_read_failed"
    return result.stdout, None


def _run_git(
    root: Path,
    arguments: tuple[str, ...],
) -> tuple[subprocess.CompletedProcess[bytes], str | None]:
    try:
        result = subprocess.run(
            _git_command(root, arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            env=_git_environment(),
        )
    except subprocess.TimeoutExpired:
        return _empty_process(), "git_timeout"
    except OSError:
        return _empty_process(), "git_unavailable"
    return result, None


def _run_git_limited(
    root: Path,
    arguments: tuple[str, ...],
    *,
    max_stdout: int,
) -> tuple[subprocess.CompletedProcess[bytes], str | None]:
    try:
        process = subprocess.Popen(
            _git_command(root, arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            env=_git_environment(),
        )
    except OSError:
        return _empty_process(), "git_unavailable"

    output = bytearray()
    state = {"exceeded": False, "read_failed": False}

    def read_stdout() -> None:
        stream = process.stdout
        if stream is None:
            state["read_failed"] = True
            return
        try:
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    return
                if len(output) + len(chunk) > max_stdout:
                    state["exceeded"] = True
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
                output.extend(chunk)
        except OSError:
            state["read_failed"] = True
            try:
                process.kill()
            except OSError:
                pass
        finally:
            stream.close()

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()
    try:
        return_code = process.wait(timeout=_GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join()
        return _empty_process(), "git_timeout"

    reader.join()
    if state["exceeded"]:
        return _empty_process(), "git_output_too_large"
    if state["read_failed"]:
        return _empty_process(), "git_read_failed"
    return (
        subprocess.CompletedProcess(
            process.args,
            return_code,
            bytes(output),
            b"",
        ),
        None,
    )


def _git_command(root: Path, arguments: tuple[str, ...]) -> tuple[str, ...]:
    return (
        "git",
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(root),
        *arguments,
    )


def _git_environment() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _empty_process() -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess((), 1, b"", b"")
