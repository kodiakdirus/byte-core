"""Sanitized data contracts for the Byte Core privacy scanner.

This bootstrap interface is internal and is not a stable public API.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class DataClass(str, Enum):
    """Privacy classes ordered by policy, not by enum value."""

    PUBLIC = "public"
    REVIEWED_DIAGNOSTIC = "reviewed_diagnostic"
    DEPLOYMENT_SENSITIVE = "deployment_sensitive"
    SECRET = "secret"
    PROHIBITED_SOURCE = "prohibited_source"


class Severity(str, Enum):
    """The action required for a scanner finding."""

    WARNING = "warning"
    ERROR = "error"


class ContentType(str, Enum):
    """The explicitly declared purpose of scanned text."""

    GENERAL = "general"
    PUBLIC_EXAMPLE = "public_example"


class SourceOwnership(str, Enum):
    """Ownership boundary for content entering privacy policy."""

    CORE_PUBLIC = "core_public"
    DEPLOYMENT_OWNED = "deployment_owned"
    DIAGNOSTIC = "diagnostic"


class ScanError(Exception):
    """A scanner failure whose message contains no input content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ScanPolicy:
    """Bounded controls for one in-memory scan."""

    content_type: ContentType = ContentType.GENERAL
    enabled_rule_ids: frozenset[str] | None = None
    max_characters: int = 1_000_000
    max_findings: int = 10_000

    def __post_init__(self) -> None:
        if not isinstance(self.content_type, ContentType):
            raise ValueError("content_type must be a ContentType")
        if self.max_characters <= 0 or self.max_findings <= 0:
            raise ValueError("scan limits must be positive")
        if self.enabled_rule_ids is not None:
            object.__setattr__(
                self,
                "enabled_rule_ids",
                frozenset(self.enabled_rule_ids),
            )


@dataclass(frozen=True)
class Finding:
    """A finding that cannot retain matched text or source context."""

    source_id: str
    line: int
    rule_id: str
    data_class: DataClass
    severity: Severity
    fingerprint: str
    description: str
    remediation: str

    def to_dict(self) -> Mapping[str, str | int]:
        """Return an immutable, explicitly allowlisted representation."""

        return MappingProxyType(
            {
                "source_id": self.source_id,
                "line": self.line,
                "rule_id": self.rule_id,
                "data_class": self.data_class.value,
                "severity": self.severity.value,
                "fingerprint": self.fingerprint,
                "description": self.description,
                "remediation": self.remediation,
            }
        )


@dataclass(frozen=True)
class ScanResult:
    """Deterministically ordered results for one logical source."""

    source_id: str
    findings: tuple[Finding, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(self.findings))

    @property
    def passed(self) -> bool:
        """Return whether the input has no error-severity findings."""

        return not any(
            finding.severity is Severity.ERROR
            for finding in self.findings
        )
