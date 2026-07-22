"""Read-only validation for canonical deployment documents."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

SUPPORTED_DOCUMENT_SCHEMA_VERSIONS = frozenset({1})
ROLE_FILENAMES = MappingProxyType(
    {
        "manifest": "manifest.md",
        "runbook": "runbook.md",
        "audit-log": "audit-log.md",
        "notebook": "notebook.md",
    }
)

_EXPECTED_TITLES = MappingProxyType(
    {
        "manifest": "Manifest",
        "runbook": "Runbook",
        "audit-log": "Audit Log",
        "notebook": "Notebook",
    }
)
_MARKER = re.compile(
    r"<!-- byte-core-document: schema=([1-9][0-9]*) "
    r"role=([a-z][a-z-]*) -->"
)
_LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^)\n]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_MAX_DOCUMENT_BYTES = 256 * 1024
_MAX_MARKDOWN_FILES = 64


@dataclass(frozen=True)
class DocumentError:
    """A sanitized canonical-document validation failure."""

    code: str
    source: str | None = None
    reference: str | None = None


@dataclass(frozen=True)
class CanonicalDocument:
    """Validated structural metadata for one canonical document."""

    role: str
    source: str
    schema_version: int


@dataclass(frozen=True)
class DocumentValidationResult:
    """Immutable result of a read-only document-set validation."""

    documents: tuple[CanonicalDocument, ...]
    errors: tuple[DocumentError, ...]

    @property
    def passed(self) -> bool:
        return not self.errors


def validate_canonical_documents(
    root: str | os.PathLike[str],
) -> DocumentValidationResult:
    """Validate one canonical document root without modifying it."""

    try:
        root_path = Path(root)
        if _is_link_like(root_path):
            return _result(errors=[DocumentError("root_link_forbidden")])
        resolved_root = root_path.resolve(strict=True)
        if not resolved_root.is_dir():
            return _result(errors=[DocumentError("invalid_root")])
    except (OSError, RuntimeError, TypeError, ValueError):
        return _result(errors=[DocumentError("invalid_root")])

    try:
        markdown_paths = sorted(root_path.glob("*.md"), key=lambda item: item.name)
    except OSError:
        return _result(errors=[DocumentError("read_error")])
    if len(markdown_paths) > _MAX_MARKDOWN_FILES:
        return _result(errors=[DocumentError("too_many_documents")])

    errors: list[DocumentError] = []
    parsed: dict[str, tuple[CanonicalDocument, str]] = {}
    role_sources: dict[str, str] = {}

    for path in markdown_paths:
        source = path.name
        text = _read_document(path, resolved_root)
        if isinstance(text, DocumentError):
            errors.append(DocumentError(text.code, source))
            continue

        first_line = text.splitlines()[0] if text.splitlines() else ""
        marker = _MARKER.fullmatch(first_line)
        if marker is None:
            if first_line.startswith("<!-- byte-core-document:"):
                errors.append(DocumentError("invalid_marker", source))
            continue

        schema_version = int(marker.group(1))
        role = marker.group(2)
        if role not in ROLE_FILENAMES:
            errors.append(DocumentError("unsupported_role", source))
            continue
        if role in role_sources:
            errors.append(DocumentError("duplicate_role", source, role))
            continue

        role_sources[role] = source
        document = CanonicalDocument(role, source, schema_version)
        parsed[role] = (document, text)

    for role, expected_source in ROLE_FILENAMES.items():
        item = parsed.get(role)
        if item is None:
            errors.append(DocumentError("missing_role", expected_source, role))
            continue
        document, text = item
        if document.source != expected_source:
            errors.append(
                DocumentError("unexpected_filename", document.source, expected_source)
            )
        if document.schema_version not in SUPPORTED_DOCUMENT_SCHEMA_VERSIONS:
            errors.append(DocumentError("unsupported_schema", document.source))
        expected_heading = f"# {_EXPECTED_TITLES[role]}"
        if expected_heading not in text.splitlines()[1:]:
            errors.append(DocumentError("missing_title", document.source))

    schema_versions = {item[0].schema_version for item in parsed.values()}
    if len(schema_versions) > 1:
        errors.append(DocumentError("schema_mismatch"))

    for document, text in parsed.values():
        errors.extend(_validate_links(document.source, text, parsed))

    documents = tuple(
        parsed[role][0] for role in ROLE_FILENAMES if role in parsed
    )
    return _result(documents=documents, errors=errors)


def _read_document(
    path: Path,
    resolved_root: Path,
) -> str | DocumentError:
    try:
        if _is_link_like(path):
            return DocumentError("link_forbidden")
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            return DocumentError("not_regular_file")
        if before.st_size > _MAX_DOCUMENT_BYTES:
            return DocumentError("document_too_large")

        identity = _stat_identity(before)
        flags = os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                return DocumentError("file_changed")
            if _stat_identity(opened) != identity:
                return DocumentError("file_changed")
            data = stream.read(_MAX_DOCUMENT_BYTES + 1)
        after = path.stat()

        if _stat_identity(after) != identity:
            return DocumentError("file_changed")
        if len(data) > _MAX_DOCUMENT_BYTES:
            return DocumentError("document_too_large")
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return DocumentError("invalid_utf8")
    except (OSError, RuntimeError, ValueError):
        return DocumentError("read_error")


def _validate_links(
    source: str,
    text: str,
    parsed: dict[str, tuple[CanonicalDocument, str]],
) -> list[DocumentError]:
    errors: list[DocumentError] = []
    targets = {item[0].source: item[1] for item in parsed.values()}

    for raw_target in _LINK.findall(text):
        target = raw_target.strip()
        split = urlsplit(target)
        if split.scheme or split.netloc or target.startswith(("/", "~")):
            errors.append(DocumentError("external_link_forbidden", source))
            continue
        decoded_path = unquote(split.path)
        logical = PurePosixPath(decoded_path) if decoded_path else PurePosixPath(source)
        if logical.is_absolute() or ".." in logical.parts or len(logical.parts) != 1:
            errors.append(DocumentError("link_escape", source))
            continue
        target_source = logical.name
        target_text = targets.get(target_source)
        if target_text is None:
            errors.append(DocumentError("broken_link", source, target_source))
            continue
        if split.fragment and _slug(split.fragment) not in _heading_slugs(target_text):
            errors.append(DocumentError("broken_fragment", source, target_source))

    return errors


def _heading_slugs(text: str) -> set[str]:
    return {_slug(heading) for heading in _HEADING.findall(text)}


def _slug(value: str) -> str:
    lowered = unquote(value).strip().lower()
    lowered = re.sub(r"[^\w\- ]", "", lowered)
    return re.sub(r"[\s-]+", "-", lowered).strip("-")


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


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _result(
    *,
    documents: list[CanonicalDocument] | tuple[CanonicalDocument, ...] = (),
    errors: list[DocumentError] | tuple[DocumentError, ...] = (),
) -> DocumentValidationResult:
    return DocumentValidationResult(tuple(documents), tuple(errors))
