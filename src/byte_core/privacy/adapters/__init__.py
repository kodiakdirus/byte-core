"""Bounded input adapters for the internal privacy scanner."""

from .artifacts import scan_artifact_directory
from .diagnostics import scan_diagnostic_payload
from .files import scan_files
from .models import AdapterError, AdapterLimits, AdapterResult
from .staged import scan_staged

__all__ = [
    "AdapterError",
    "AdapterLimits",
    "AdapterResult",
    "scan_artifact_directory",
    "scan_diagnostic_payload",
    "scan_files",
    "scan_staged",
]
