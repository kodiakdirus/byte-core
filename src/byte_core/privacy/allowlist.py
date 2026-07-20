"""Narrow false-positive allowlisting for Core-public scanner inputs.

This bootstrap interface is internal and is not a stable public API.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from .models import (
    DataClass,
    Finding,
    ScanError,
    ScanPolicy,
    ScanResult,
    Severity,
    SourceOwnership,
)
from .rules import RULES_BY_ID
from .scanner import scan_text, validate_source_id

SUPPORTED_ALLOWLIST_SCHEMA_VERSIONS = frozenset({1})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_CLASSES = frozenset(
    {DataClass.SECRET, DataClass.PROHIBITED_SOURCE}
)


@dataclass(frozen=True)
class AllowlistEntry:
    """An exact, expiring approval for one public false positive."""

    rule_id: str
    source_id: str
    line: int
    source_sha256: str
    justification: str
    reviewed_on: date
    expires_on: date


@dataclass(frozen=True)
class AllowlistPolicy:
    """A versioned collection of public false-positive approvals."""

    schema_version: int
    entries: tuple[AllowlistEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))


@dataclass(frozen=True)
class AllowlistError:
    """A sanitized policy error with no source or finding content."""

    code: str
    entry_index: int | None = None


@dataclass(frozen=True)
class AllowlistReview:
    """Visible active and allowed findings after policy evaluation."""

    source_id: str
    active_findings: tuple[Finding, ...]
    allowed_findings: tuple[Finding, ...]
    errors: tuple[AllowlistError, ...]

    @property
    def passed(self) -> bool:
        """Return whether policy is valid and no active errors remain."""

        return not self.errors and not any(
            finding.severity is Severity.ERROR
            for finding in self.active_findings
        )


def scan_text_with_allowlist(
    source_id: str,
    text: str,
    *,
    source_ownership: SourceOwnership,
    allowlist: AllowlistPolicy,
    as_of: date,
    scan_policy: ScanPolicy | None = None,
) -> AllowlistReview:
    """Scan and evaluate one source without separating text from findings."""

    result = scan_text(source_id, text, policy=scan_policy)
    return _evaluate(
        result,
        text,
        source_ownership=source_ownership,
        allowlist=allowlist,
        as_of=as_of,
    )


def _evaluate(
    result: ScanResult,
    text: str,
    *,
    source_ownership: SourceOwnership,
    allowlist: AllowlistPolicy,
    as_of: date,
) -> AllowlistReview:
    errors: list[AllowlistError] = []
    valid_entries: list[tuple[int, AllowlistEntry]] = []

    if source_ownership is not SourceOwnership.CORE_PUBLIC:
        errors.append(AllowlistError("allowlist_scope_forbidden"))
    if (
        type(allowlist.schema_version) is not int
        or allowlist.schema_version
        not in SUPPORTED_ALLOWLIST_SCHEMA_VERSIONS
    ):
        errors.append(AllowlistError("unsupported_allowlist_schema"))
    if type(as_of) is not date:
        errors.append(AllowlistError("invalid_evaluation_date"))

    seen_scopes: set[tuple[str, str, int]] = set()
    for index, entry in enumerate(allowlist.entries):
        if not isinstance(entry, AllowlistEntry):
            errors.append(AllowlistError("invalid_entry", index))
            continue
        entry_errors = _validate_entry(entry, index, as_of)
        errors.extend(entry_errors)

        if (
            isinstance(entry.rule_id, str)
            and isinstance(entry.source_id, str)
            and type(entry.line) is int
        ):
            scope = (entry.rule_id, entry.source_id, entry.line)
            if scope in seen_scopes:
                errors.append(AllowlistError("duplicate_entry", index))
            else:
                seen_scopes.add(scope)

        if not entry_errors:
            valid_entries.append((index, entry))

    if errors:
        return _review(result, (), (), errors)

    source_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    findings_by_scope = {
        (finding.rule_id, finding.source_id, finding.line): finding
        for finding in result.findings
    }
    allowed_scopes: set[tuple[str, str, int]] = set()

    for index, entry in valid_entries:
        if entry.source_id != result.source_id:
            continue

        scope = (entry.rule_id, entry.source_id, entry.line)
        if entry.source_sha256 != source_digest:
            errors.append(AllowlistError("source_digest_mismatch", index))
            continue
        if scope not in findings_by_scope:
            errors.append(AllowlistError("unused_entry", index))
            continue
        allowed_scopes.add(scope)

    active = tuple(
        finding
        for finding in result.findings
        if (finding.rule_id, finding.source_id, finding.line)
        not in allowed_scopes
    )
    allowed = tuple(
        finding
        for finding in result.findings
        if (finding.rule_id, finding.source_id, finding.line)
        in allowed_scopes
    )
    return _review(result, active, allowed, errors)


def _validate_entry(
    entry: AllowlistEntry,
    index: int,
    as_of: date,
) -> list[AllowlistError]:
    errors: list[AllowlistError] = []
    rule = (
        RULES_BY_ID.get(entry.rule_id)
        if isinstance(entry.rule_id, str)
        else None
    )

    if not isinstance(entry.rule_id, str) or rule is None:
        errors.append(AllowlistError("unknown_rule", index))
    elif rule.data_class in _FORBIDDEN_CLASSES:
        errors.append(AllowlistError("forbidden_rule", index))

    try:
        validate_source_id(entry.source_id)
    except ScanError:
        errors.append(AllowlistError("invalid_source_id", index))

    if type(entry.line) is not int or entry.line <= 0:
        errors.append(AllowlistError("invalid_line", index))
    if (
        not isinstance(entry.source_sha256, str)
        or _SHA256.fullmatch(entry.source_sha256) is None
    ):
        errors.append(AllowlistError("invalid_source_digest", index))
    if not isinstance(entry.justification, str) or not entry.justification.strip():
        errors.append(AllowlistError("missing_justification", index))

    if type(entry.reviewed_on) is not date or type(entry.expires_on) is not date:
        errors.append(AllowlistError("invalid_entry_date", index))
        return errors
    if type(as_of) is not date:
        return errors
    if entry.reviewed_on > as_of:
        errors.append(AllowlistError("future_review_date", index))
    if entry.expires_on < entry.reviewed_on:
        errors.append(AllowlistError("invalid_expiry", index))
    elif entry.expires_on < as_of:
        errors.append(AllowlistError("expired_entry", index))

    return errors


def _review(
    result: ScanResult,
    active: tuple[Finding, ...],
    allowed: tuple[Finding, ...],
    errors: list[AllowlistError],
) -> AllowlistReview:
    if errors:
        active = result.findings
        allowed = ()
    return AllowlistReview(
        source_id=result.source_id,
        active_findings=tuple(active),
        allowed_findings=tuple(allowed),
        errors=tuple(
            sorted(
                set(errors),
                key=lambda error: (
                    error.code,
                    -1 if error.entry_index is None else error.entry_index,
                ),
            )
        ),
    )
