"""Sanitized result and limit models shared by privacy adapters."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import ScanResult

_HARD_MAX_FILES = 4_096
_HARD_MAX_FILE_BYTES = 16_000_000
_HARD_MAX_TOTAL_BYTES = 64_000_000
_HARD_MAX_DEPTH = 100


@dataclass(frozen=True)
class AdapterLimits:
    """Configurable bounds that cannot exceed hard safety ceilings."""

    max_files: int = 256
    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 16_000_000
    max_depth: int = 20

    def __post_init__(self) -> None:
        values = (
            self.max_files,
            self.max_file_bytes,
            self.max_total_bytes,
            self.max_depth,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("adapter limits must be positive integers")
        if self.max_files > _HARD_MAX_FILES:
            raise ValueError("max_files exceeds hard limit")
        if self.max_file_bytes > _HARD_MAX_FILE_BYTES:
            raise ValueError("max_file_bytes exceeds hard limit")
        if self.max_total_bytes > _HARD_MAX_TOTAL_BYTES:
            raise ValueError("max_total_bytes exceeds hard limit")
        if self.max_depth > _HARD_MAX_DEPTH:
            raise ValueError("max_depth exceeds hard limit")


@dataclass(frozen=True, order=True)
class AdapterError:
    """A sanitized adapter error identified only by input ordinal."""

    code: str
    input_index: int | None = None


@dataclass(frozen=True)
class AdapterResult:
    """Deterministically ordered scans and sanitized adapter failures."""

    scans: tuple[ScanResult, ...]
    errors: tuple[AdapterError, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scans", tuple(self.scans))
        object.__setattr__(self, "errors", tuple(self.errors))

    @property
    def passed(self) -> bool:
        """Return whether every selected input was safely scanned."""

        return not self.errors and all(scan.passed for scan in self.scans)
