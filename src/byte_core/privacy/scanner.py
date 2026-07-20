"""Pure in-memory scanning for privacy-sensitive text."""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_right

from .models import Finding, ScanError, ScanPolicy, ScanResult
from .rules import RULES, RULES_BY_ID

_SOURCE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_SOURCE_ID_CHARACTERS = 4_096
MAX_SOURCE_ID_UTF8_BYTES = 16_384


def scan_text(
    source_id: str,
    text: str,
    *,
    policy: ScanPolicy | None = None,
) -> ScanResult:
    """Scan one bounded text value without retaining matched content."""

    active_policy = policy or ScanPolicy()
    validate_source_id(source_id)
    if not isinstance(text, str):
        raise ScanError("invalid_text")
    if len(text) > active_policy.max_characters:
        raise ScanError("input_too_large")

    rules = _enabled_rules(active_policy)
    findings: dict[tuple[str, int, str], Finding] = {}
    newline_offsets = [
        offset for offset, character in enumerate(text) if character == "\n"
    ]

    for rule in rules:
        for match in rule.matcher(text, active_policy):
            line = bisect_right(newline_offsets, match.start) + 1
            fingerprint = _fingerprint(rule.rule_id, source_id, line)
            key = (rule.rule_id, line, fingerprint)
            if (
                key not in findings
                and len(findings) >= active_policy.max_findings
            ):
                raise ScanError("too_many_findings")
            findings[key] = Finding(
                source_id=source_id,
                line=line,
                rule_id=rule.rule_id,
                data_class=rule.data_class,
                severity=rule.severity,
                fingerprint=fingerprint,
                description=rule.description,
                remediation=rule.remediation,
            )

    ordered = tuple(
        sorted(
            findings.values(),
            key=lambda item: (
                item.source_id,
                item.line,
                item.rule_id,
                item.fingerprint,
            ),
        )
    )
    return ScanResult(source_id=source_id, findings=ordered)


def validate_source_id(source_id: str) -> None:
    """Validate one sanitized logical source identifier."""

    if not isinstance(source_id, str) or not source_id:
        raise ScanError("invalid_source_id")
    if len(source_id) > MAX_SOURCE_ID_CHARACTERS:
        raise ScanError("invalid_source_id")
    try:
        encoded_source_id = source_id.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ScanError("invalid_source_id") from error
    if len(encoded_source_id) > MAX_SOURCE_ID_UTF8_BYTES:
        raise ScanError("invalid_source_id")
    if "\\" in source_id or source_id.startswith("/"):
        raise ScanError("invalid_source_id")

    segments = source_id.split("/")
    if any(
        segment in {"", ".", ".."}
        or _SOURCE_SEGMENT.fullmatch(segment) is None
        for segment in segments
    ):
        raise ScanError("invalid_source_id")


def _enabled_rules(policy: ScanPolicy):
    if policy.enabled_rule_ids is None:
        return RULES

    unknown = policy.enabled_rule_ids.difference(RULES_BY_ID)
    if unknown:
        raise ScanError("unknown_rule")
    return tuple(
        rule for rule in RULES if rule.rule_id in policy.enabled_rule_ids
    )


def _fingerprint(rule_id: str, source_id: str, line: int) -> str:
    digest = hashlib.sha256()
    digest.update(b"byte-core/privacy-finding/v1\0")
    digest.update(rule_id.encode("ascii"))
    digest.update(b"\0")
    digest.update(source_id.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(line).encode("ascii"))
    return digest.hexdigest()[:16]
