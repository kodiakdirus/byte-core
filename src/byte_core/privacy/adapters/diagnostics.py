"""Scanning for an already-constructed minimal diagnostic payload."""

from __future__ import annotations

from ..models import ScanError, ScanPolicy
from ..scanner import scan_text, validate_source_id
from .models import AdapterError, AdapterLimits, AdapterResult


def scan_diagnostic_payload(
    payload_text: str,
    *,
    logical_id: str = "diagnostic/report.json",
    policy: ScanPolicy | None = None,
    limits: AdapterLimits | None = None,
) -> AdapterResult:
    """Scan exact diagnostic text without collecting or serializing it."""

    active_limits = limits or AdapterLimits()
    try:
        validate_source_id(logical_id)
    except ScanError:
        return AdapterResult((), (AdapterError("invalid_diagnostic_id"),))
    if not logical_id.startswith("diagnostic/"):
        return AdapterResult((), (AdapterError("invalid_diagnostic_id"),))
    if not isinstance(payload_text, str) or not payload_text:
        return AdapterResult((), (AdapterError("invalid_diagnostic_payload"),))
    if "\0" in payload_text:
        return AdapterResult((), (AdapterError("binary_payload"),))
    try:
        encoded_size = len(payload_text.encode("utf-8"))
    except UnicodeEncodeError:
        return AdapterResult((), (AdapterError("invalid_utf8"),))
    if encoded_size > active_limits.max_file_bytes:
        return AdapterResult((), (AdapterError("payload_too_large"),))
    try:
        result = scan_text(logical_id, payload_text, policy=policy)
    except ScanError:
        return AdapterResult((), (AdapterError("scan_failed"),))
    return AdapterResult((result,), ())
