"""Initial high-confidence rules for the Byte Core privacy scanner."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Pattern

from .models import ContentType, DataClass, ScanPolicy, Severity


@dataclass(frozen=True)
class RuleMatch:
    """A transient match that must never escape the scanner engine."""

    start: int


Matcher = Callable[[str, ScanPolicy], Iterable[RuleMatch]]


@dataclass(frozen=True)
class Rule:
    """Metadata and matching behavior for a stable scanner rule."""

    rule_id: str
    data_class: DataClass
    severity: Severity
    description: str
    remediation: str
    matcher: Matcher


def _regex_matcher(pattern: Pattern[str]) -> Matcher:
    def match(text: str, policy: ScanPolicy) -> Iterable[RuleMatch]:
        del policy
        for candidate in pattern.finditer(text):
            yield RuleMatch(candidate.start())

    return match


def _ip_matcher(version: int) -> Matcher:
    candidate_pattern = (
        re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
        if version == 4
        else re.compile(
            r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}"
            r"[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])"
        )
    )

    def match(text: str, policy: ScanPolicy) -> Iterable[RuleMatch]:
        del policy
        for candidate in candidate_pattern.finditer(text):
            value = candidate.group(0)
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue

            if address.version != version or _is_documentation_address(address):
                continue
            yield RuleMatch(candidate.start())

    return match


def _is_documentation_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    networks = (
        ipaddress.ip_network("192.0.2.0/24"),
        ipaddress.ip_network("198.51.100.0/24"),
        ipaddress.ip_network("203.0.113.0/24"),
        ipaddress.ip_network("2001:db8::/32"),
    )
    return any(address in network for network in networks)


_RESERVED_DOMAINS = (
    "example.com",
    "example.net",
    "example.org",
)


def _domain_matcher(text: str, policy: ScanPolicy) -> Iterable[RuleMatch]:
    if policy.content_type is not ContentType.PUBLIC_EXAMPLE:
        return

    pattern = re.compile(
        r"(?<![A-Za-z0-9_-])"
        r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
        r"[A-Za-z]{2,63}"
        r"(?![A-Za-z0-9_-])"
    )
    for candidate in pattern.finditer(text):
        value = candidate.group(0).lower().rstrip(".")
        if value.endswith(".test") or any(
            value == domain or value.endswith(f".{domain}")
            for domain in _RESERVED_DOMAINS
        ):
            continue
        yield RuleMatch(candidate.start())


RULES = (
    Rule(
        "PRIV-SECRET-001",
        DataClass.SECRET,
        Severity.ERROR,
        "Possible private-key material detected.",
        "Remove the key material and revoke it if it was real.",
        _regex_matcher(
            re.compile(
                r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
            )
        ),
    ),
    Rule(
        "PRIV-SECRET-002",
        DataClass.SECRET,
        Severity.ERROR,
        "Possible authorization credential detected.",
        "Remove the credential-bearing authorization value.",
        _regex_matcher(
            re.compile(
                r"(?im)^\s*(?:authorization|proxy-authorization)\s*:\s*"
                r"(?:basic|bearer)\s+[A-Za-z0-9._~+/=-]{8,}"
            )
        ),
    ),
    Rule(
        "PRIV-SECRET-003",
        DataClass.SECRET,
        Severity.ERROR,
        "Possible credential-bearing URL detected.",
        "Remove credentials from the URL and use an opaque reference.",
        _regex_matcher(
            re.compile(
                r"(?i)\b[a-z][a-z0-9+.-]*://"
                r"[^\s/@:]+:[^\s/@]+@[^\s/]+"
            )
        ),
    ),
    Rule(
        "PRIV-SECRET-004",
        DataClass.SECRET,
        Severity.ERROR,
        "Possible sensitive assignment detected.",
        "Remove the value and use an opaque credential reference.",
        _regex_matcher(
            re.compile(
                r"(?i)(?<![A-Za-z0-9_])['\"]?"
                r"(?:password|passwd|secret|token|api[_-]?key)"
                r"['\"]?[ \t]*(?::|=)[ \t]*"
                r"(?:['\"][^'\"\r\n]{4,}['\"]|[^\s,;#]{4,})"
            )
        ),
    ),
    Rule(
        "PRIV-SECRET-005",
        DataClass.SECRET,
        Severity.ERROR,
        "Possible service token detected.",
        "Remove the token and revoke it if it was real.",
        _regex_matcher(
            re.compile(
                r"(?<![A-Za-z0-9])(?:"
                r"AKIA[0-9A-Z]{16}|"
                r"gh[pousr]_[A-Za-z0-9]{36,255}"
                r")(?![A-Za-z0-9])"
            )
        ),
    ),
    Rule(
        "PRIV-IDENTITY-001",
        DataClass.DEPLOYMENT_SENSITIVE,
        Severity.ERROR,
        "Non-documentation IPv4 address detected.",
        "Replace it with an address from a documentation network.",
        _ip_matcher(4),
    ),
    Rule(
        "PRIV-IDENTITY-002",
        DataClass.DEPLOYMENT_SENSITIVE,
        Severity.ERROR,
        "Non-documentation IPv6 address detected.",
        "Replace it with an address from 2001:db8::/32.",
        _ip_matcher(6),
    ),
    Rule(
        "PRIV-IDENTITY-003",
        DataClass.DEPLOYMENT_SENSITIVE,
        Severity.ERROR,
        "Windows user-profile path detected.",
        "Replace it with a generic logical or documentation path.",
        _regex_matcher(
            re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:)?\\Users\\[^\\\s]+")
        ),
    ),
    Rule(
        "PRIV-IDENTITY-004",
        DataClass.DEPLOYMENT_SENSITIVE,
        Severity.ERROR,
        "Unix user-home path detected.",
        "Replace it with a generic logical or documentation path.",
        _regex_matcher(
            re.compile(r"(?<![A-Za-z0-9])/(?:home|Users)/[^/\s]+")
        ),
    ),
    Rule(
        "PRIV-IDENTITY-005",
        DataClass.DEPLOYMENT_SENSITIVE,
        Severity.ERROR,
        "Connection URL containing user information detected.",
        "Remove the identity and use a generic fictional endpoint.",
        _regex_matcher(
            re.compile(
                r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+@[^\s/]+"
            )
        ),
    ),
    Rule(
        "PRIV-IDENTITY-006",
        DataClass.DEPLOYMENT_SENSITIVE,
        Severity.ERROR,
        "Non-reserved domain detected in a public example.",
        "Replace it with a reserved documentation domain.",
        _domain_matcher,
    ),
)

RULES_BY_ID = {rule.rule_id: rule for rule in RULES}
