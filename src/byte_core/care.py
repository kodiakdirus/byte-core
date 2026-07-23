"""Local-first, allowlisted Byte Care diagnostic reports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from .privacy.adapters import scan_diagnostic_payload

CARE_SCHEMA_VERSION = 1
BYTE_CORE_BOOTSTRAP_VERSION = "0.0.0"
MAX_REPORT_BYTES = 64 * 1024
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}")
_COMPONENTS = frozenset({
    "configuration", "documents", "installation", "lifecycle",
    "privacy", "shell", "update",
})
_PHASES = frozenset({"check", "plan", "apply", "verify", "backout"})
_PLATFORMS = frozenset({"linux", "macos", "unsupported"})
_ARCHITECTURES = frozenset({"arm64", "x86_64", "unknown"})
_MODES = frozenset({"off", "local-only", "ask-before-reporting"})


class CareError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DiagnosticReport:
    schema_version: int
    byte_core_version: str
    component: str
    phase: str
    error_code: str
    exit_code: int
    platform: str
    architecture: str
    python_version: str
    configuration_schema_version: int | None
    fingerprint: str


@dataclass(frozen=True)
class CareResult:
    code: str
    mode: str
    fingerprint: str
    stored: bool


def build_diagnostic_report(
    *,
    component: str,
    phase: str,
    error_code: str,
    exit_code: int,
    platform: str,
    architecture: str,
    python_version: str,
    configuration_schema_version: int | None,
) -> DiagnosticReport:
    unsigned = {
        "schema_version": CARE_SCHEMA_VERSION,
        "byte_core_version": BYTE_CORE_BOOTSTRAP_VERSION,
        "component": component,
        "phase": phase,
        "error_code": error_code,
        "exit_code": exit_code,
        "platform": platform,
        "architecture": architecture,
        "python_version": python_version,
        "configuration_schema_version": configuration_schema_version,
    }
    _validate_unsigned(unsigned)
    fingerprint = _fingerprint(unsigned)
    return DiagnosticReport(**unsigned, fingerprint=fingerprint)


def serialize_diagnostic_report(report: DiagnosticReport) -> str:
    _validate_report(report)
    return json.dumps(asdict(report), sort_keys=True, separators=(",", ":")) + "\n"


def load_diagnostic_report(
    path: str | os.PathLike[str],
) -> DiagnosticReport:
    try:
        report_path = Path(path)
        if (
            not report_path.is_absolute()
            or report_path.is_symlink()
            or not report_path.is_file()
        ):
            raise CareError("report_read_failed")
        raw = json.loads(_read_bounded(report_path).decode("utf-8"))
    except CareError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CareError("invalid_report") from error
    keys = {
        "schema_version", "byte_core_version", "component", "phase",
        "error_code", "exit_code", "platform", "architecture",
        "python_version", "configuration_schema_version", "fingerprint",
    }
    if type(raw) is not dict or set(raw) != keys:
        raise CareError("invalid_report")
    try:
        report = DiagnosticReport(**raw)
    except TypeError as error:
        raise CareError("invalid_report") from error
    _validate_report(report)
    return report


def prepare_local_report(
    report: DiagnosticReport,
    *,
    mode: str,
    report_root: str | os.PathLike[str] | None,
    approved: bool = False,
) -> CareResult:
    _validate_report(report)
    if mode not in _MODES:
        if mode == "automatic-sanitized":
            raise CareError("automatic_reporting_unsupported")
        raise CareError("invalid_reporting_mode")
    if mode == "off":
        if report_root is not None or approved:
            raise CareError("invalid_reporting_request")
        return CareResult("reporting_off", mode, report.fingerprint, False)
    if report_root is None:
        raise CareError("report_root_required")
    payload = serialize_diagnostic_report(report)
    scan = scan_diagnostic_payload(
        payload, logical_id=f"diagnostic/{report.fingerprint}.json"
    )
    if not scan.passed:
        raise CareError("privacy_scan_failed")
    if mode == "ask-before-reporting" and not approved:
        return CareResult("approval_required", mode, report.fingerprint, False)

    root = _report_root(report_root)
    target = root / f"{report.fingerprint}.json"
    data = payload.encode("utf-8")
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise CareError("report_target_invalid")
        if stat.S_IMODE(target.stat().st_mode) != 0o600:
            raise CareError("report_target_invalid")
        if _read_bounded(target) != data:
            raise CareError("report_collision")
        return CareResult("report_already_local", mode, report.fingerprint, True)
    _write_exclusive(target, data)
    if _read_bounded(target) != data:
        raise CareError("report_verification_failed")
    return CareResult("report_saved_local", mode, report.fingerprint, True)


def _validate_report(report: DiagnosticReport) -> None:
    if type(report) is not DiagnosticReport:
        raise CareError("invalid_report")
    raw = asdict(report)
    fingerprint = raw.pop("fingerprint")
    _validate_unsigned(raw)
    if (
        type(fingerprint) is not str
        or fingerprint != _fingerprint(raw)
    ):
        raise CareError("report_integrity_failed")


def _validate_unsigned(raw: dict) -> None:
    if (
        raw["schema_version"] != CARE_SCHEMA_VERSION
        or raw["byte_core_version"] != BYTE_CORE_BOOTSTRAP_VERSION
        or type(raw["component"]) is not str
        or raw["component"] not in _COMPONENTS
        or type(raw["phase"]) is not str
        or raw["phase"] not in _PHASES
        or type(raw["error_code"]) is not str
        or _IDENTIFIER.fullmatch(raw["error_code"]) is None
        or type(raw["exit_code"]) is not int
        or raw["exit_code"] < 1
        or raw["exit_code"] > 255
        or type(raw["platform"]) is not str
        or raw["platform"] not in _PLATFORMS
        or type(raw["architecture"]) is not str
        or raw["architecture"] not in _ARCHITECTURES
        or type(raw["python_version"]) is not str
        or _VERSION.fullmatch(raw["python_version"]) is None
        or (
            raw["configuration_schema_version"] is not None
            and (
                type(raw["configuration_schema_version"]) is not int
                or raw["configuration_schema_version"] < 1
            )
        )
    ):
        raise CareError("invalid_report")


def _fingerprint(unsigned: dict) -> str:
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(
        b"byte-core/care-report/v1\0" + payload.encode("utf-8")
    ).hexdigest()
    return digest[:24]


def _report_root(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(value)
        if not path.is_absolute() or path.is_symlink():
            raise CareError("invalid_report_root")
        if path.exists():
            resolved = path.resolve(strict=True)
            if (
                not resolved.is_dir()
                or stat.S_IMODE(resolved.stat().st_mode) & 0o077
            ):
                raise CareError("invalid_report_root")
        else:
            parent = path.parent.resolve(strict=True)
            resolved = parent / path.name
            resolved.mkdir(mode=0o700)
    except CareError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise CareError("invalid_report_root") from error
    return resolved


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        try:
            path.unlink()
        except OSError:
            pass
        raise CareError("report_write_failed") from error
    path.chmod(0o600)


def _read_bounded(path: Path) -> bytes:
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise CareError("report_too_large")
        data = path.read_bytes()
    except OSError as error:
        raise CareError("report_read_failed") from error
    if len(data) > MAX_REPORT_BYTES:
        raise CareError("report_too_large")
    return data
