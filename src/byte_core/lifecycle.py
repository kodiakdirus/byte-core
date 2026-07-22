"""Plan, apply, and verify initial deployment creation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .canonical_documents import validate_canonical_documents

PLAN_SCHEMA_VERSION = 1
MAX_PLAN_BYTES = 256 * 1024
CONFIGURATION_CONTENT = "schema_version = 1\n"
TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "templates" / "canonical"


@dataclass(frozen=True)
class PlannedFile:
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class InitializationPlan:
    schema_version: int
    operation: str
    deployment_root: str
    files: tuple[PlannedFile, ...]
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    backout: tuple[str, ...]
    plan_id: str


@dataclass(frozen=True)
class LifecycleResult:
    code: str
    plan_id: str
    deployment_root: str


class LifecycleError(Exception):
    """A sanitized lifecycle failure with a stable error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def build_initialization_plan(
    deployment_root: str | os.PathLike[str],
) -> InitializationPlan:
    root = _resolve_new_root(deployment_root)
    contents = _initial_contents()
    files = tuple(
        PlannedFile(name, _digest(data))
        for name, data in sorted(contents.items())
    )
    unsigned = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "operation": "init",
        "deployment_root": str(root),
        "files": [asdict(item) for item in files],
        "preconditions": ["deployment_root_absent"],
        "postconditions": [
            "exact_file_set",
            "configuration_valid",
            "canonical_documents_valid",
        ],
        "backout": [
            "remove_unchanged_created_files",
            "remove_created_root_if_empty",
        ],
    }
    return InitializationPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        operation="init",
        deployment_root=str(root),
        files=files,
        preconditions=tuple(unsigned["preconditions"]),
        postconditions=tuple(unsigned["postconditions"]),
        backout=tuple(unsigned["backout"]),
        plan_id=_digest(_canonical_json(unsigned).encode("utf-8")),
    )


def serialize_plan(plan: InitializationPlan) -> str:
    return _canonical_json(asdict(plan)) + "\n"


def load_plan(path: str | os.PathLike[str]) -> InitializationPlan:
    try:
        plan_path = Path(path)
        if _is_link_like(plan_path):
            raise LifecycleError("plan_link_forbidden")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(plan_path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(MAX_PLAN_BYTES + 1)
    except LifecycleError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise LifecycleError("plan_read_error") from error
    if len(data) > MAX_PLAN_BYTES:
        raise LifecycleError("plan_too_large")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LifecycleError("invalid_plan") from error
    return _parse_plan(raw)


def apply_initialization(plan: InitializationPlan) -> LifecycleResult:
    _validate_plan(plan)
    root = Path(plan.deployment_root)
    contents = _initial_contents()
    created: list[Path] = []
    root_created = False
    try:
        root.mkdir(mode=0o700)
        root_created = True
        for item in plan.files:
            target = root / item.relative_path
            data = contents[item.relative_path]
            if _digest(data) != item.sha256:
                raise LifecycleError("template_changed")
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            created.append(target)
        verify_initialization(plan)
        return LifecycleResult("initialized", plan.plan_id, str(root))
    except FileExistsError as error:
        if not root_created:
            try:
                verify_initialization(plan)
                return LifecycleResult("already_initialized", plan.plan_id, str(root))
            except LifecycleError:
                raise LifecycleError("target_exists") from error
        cleanup_safe = _rollback(created, root, plan)
        raise LifecycleError(
            "apply_failed" if cleanup_safe else "recovery_required"
        ) from error
    except LifecycleError as error:
        if not root_created:
            raise
        cleanup_safe = _rollback(created, root, plan)
        if not cleanup_safe:
            raise LifecycleError("recovery_required") from error
        raise
    except Exception as error:
        cleanup_safe = _rollback(created, root, plan) if root_created else True
        raise LifecycleError(
            "apply_failed" if cleanup_safe else "recovery_required"
        ) from error


def verify_initialization(plan: InitializationPlan) -> LifecycleResult:
    _validate_plan(plan)
    root = Path(plan.deployment_root)
    try:
        if _is_link_like(root) or not root.is_dir():
            raise LifecycleError("verification_failed")
        expected = {item.relative_path: item.sha256 for item in plan.files}
        actual_names = {path.name for path in root.iterdir()}
        if actual_names != set(expected):
            raise LifecycleError("verification_failed")
        for name, digest in expected.items():
            path = root / name
            if _is_link_like(path) or not path.is_file():
                raise LifecycleError("verification_failed")
            if _digest(path.read_bytes()) != digest:
                raise LifecycleError("verification_failed")
        configuration = tomllib.loads(
            (root / "deployment.toml").read_text(encoding="utf-8")
        )
        if configuration != {"schema_version": 1}:
            raise LifecycleError("verification_failed")
        if not validate_canonical_documents(root).passed:
            raise LifecycleError("verification_failed")
    except LifecycleError:
        raise
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LifecycleError("verification_failed") from error
    return LifecycleResult("verified", plan.plan_id, str(root))


def remove_core_integration(
    deployment_root: str | os.PathLike[str],
) -> LifecycleResult:
    """Verify preservation when no installed Core integration exists yet."""

    root = _resolve_existing_root(deployment_root)
    try:
        configuration = tomllib.loads(
            (root / "deployment.toml").read_text(encoding="utf-8")
        )
        if configuration.get("schema_version") != 1:
            raise LifecycleError("verification_failed")
        if not validate_canonical_documents(root).passed:
            raise LifecycleError("verification_failed")
    except LifecycleError:
        raise
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LifecycleError("verification_failed") from error

    report_id = _digest(
        _canonical_json(
            {
                "operation": "remove",
                "deployment_root": str(root),
                "removed": [],
                "preserved": sorted(path.name for path in root.iterdir()),
            }
        ).encode("utf-8")
    )
    return LifecycleResult("core_integration_absent", report_id, str(root))


def _parse_plan(raw: Any) -> InitializationPlan:
    if type(raw) is not dict or set(raw) != {
        "schema_version", "operation", "deployment_root", "files",
        "preconditions", "postconditions", "backout", "plan_id"
    }:
        raise LifecycleError("invalid_plan")
    files_raw = raw["files"]
    if type(files_raw) is not list:
        raise LifecycleError("invalid_plan")
    try:
        files = tuple(
            PlannedFile(item["relative_path"], item["sha256"])
            for item in files_raw
            if type(item) is dict and set(item) == {"relative_path", "sha256"}
        )
    except (KeyError, TypeError) as error:
        raise LifecycleError("invalid_plan") from error
    if len(files) != len(files_raw):
        raise LifecycleError("invalid_plan")
    plan = InitializationPlan(
        raw["schema_version"], raw["operation"], raw["deployment_root"],
        files, _string_tuple(raw["preconditions"]),
        _string_tuple(raw["postconditions"]), _string_tuple(raw["backout"]),
        raw["plan_id"],
    )
    _validate_plan(plan)
    return plan


def _validate_plan(plan: InitializationPlan) -> None:
    if plan.schema_version != PLAN_SCHEMA_VERSION or plan.operation != "init":
        raise LifecycleError("unsupported_plan")
    if type(plan.deployment_root) is not str or type(plan.plan_id) is not str:
        raise LifecycleError("invalid_plan")
    expected = build_initialization_plan(plan.deployment_root)
    if plan.files != expected.files or plan.plan_id != expected.plan_id:
        raise LifecycleError("plan_integrity_failed")


def _resolve_new_root(value: str | os.PathLike[str]) -> Path:
    try:
        text = os.fspath(value)
        if not isinstance(text, str) or not text or text.startswith("~"):
            raise LifecycleError("invalid_target")
        candidate = Path(text)
        if not candidate.is_absolute() or candidate.name in ("", ".", ".."):
            raise LifecycleError("invalid_target")
        parent = candidate.parent.resolve(strict=True)
        if _is_link_like(candidate):
            raise LifecycleError("target_link_forbidden")
        return parent / candidate.name
    except LifecycleError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise LifecycleError("invalid_target") from error


def _resolve_existing_root(value: str | os.PathLike[str]) -> Path:
    root = _resolve_new_root(value)
    try:
        if _is_link_like(root):
            raise LifecycleError("target_link_forbidden")
        resolved = root.resolve(strict=True)
        if resolved != root or not root.is_dir():
            raise LifecycleError("invalid_target")
        return root
    except LifecycleError:
        raise
    except (OSError, RuntimeError) as error:
        raise LifecycleError("invalid_target") from error


def _initial_contents() -> dict[str, bytes]:
    contents = {"deployment.toml": CONFIGURATION_CONTENT.encode("utf-8")}
    try:
        for name in ("manifest.md", "runbook.md", "audit-log.md", "notebook.md"):
            contents[name] = (TEMPLATE_ROOT / name).read_bytes()
    except OSError as error:
        raise LifecycleError("template_read_error") from error
    return contents


def _rollback(created: list[Path], root: Path, plan: InitializationPlan) -> bool:
    expected = {item.relative_path: item.sha256 for item in plan.files}
    try:
        for path in reversed(created):
            if _is_link_like(path) or _digest(path.read_bytes()) != expected[path.name]:
                return False
            path.unlink()
        root.rmdir()
        return True
    except OSError:
        return False


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _string_tuple(value: Any) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise LifecycleError("invalid_plan")
    return tuple(value)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return stat.S_ISLNK(metadata.st_mode)
