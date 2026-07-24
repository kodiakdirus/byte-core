"""Validate a Byte Core v0.1 candidate and its manual-evidence ledger."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from byte_core.installation import (  # noqa: E402
    InstallationError,
    load_release_descriptor,
)
from byte_core.privacy.adapters import scan_artifact_directory  # noqa: E402

EXPECTED_TARGETS = frozenset(
    {
        ("ubuntu-24.04", "x86_64"),
        ("macos-15", "arm64"),
        ("macos-26", "arm64"),
    }
)
COMMIT = re.compile(r"[0-9a-f]{40}")


class GateError(Exception):
    pass


def check(
    artifact_root: str | Path,
    evidence_path: str | Path,
    *,
    require_complete: bool,
) -> tuple[str, ...]:
    artifact = Path(artifact_root)
    try:
        descriptor = load_release_descriptor(artifact / "release.json")
    except InstallationError as error:
        raise GateError(f"artifact_{error.code}") from error
    if descriptor.core_version != "0.1.0":
        raise GateError("unexpected_release_version")
    privacy = scan_artifact_directory(artifact)
    if not privacy.passed:
        raise GateError("artifact_privacy_failed")

    evidence_file = Path(evidence_path)
    try:
        raw = json.loads(evidence_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GateError("manual_evidence_read_failed") from error
    if (
        type(raw) is not dict
        or set(raw) != {"schema_version", "release", "targets"}
        or raw["schema_version"] != 1
        or raw["release"] != "0.1.0"
        or type(raw["targets"]) is not list
    ):
        raise GateError("invalid_manual_evidence")

    statuses: list[str] = []
    seen: set[tuple[str, str]] = set()
    for target in raw["targets"]:
        if type(target) is not dict or set(target) != {
            "operating_system", "architecture", "status", "evidence"
        }:
            raise GateError("invalid_manual_evidence")
        identity = (target["operating_system"], target["architecture"])
        if identity not in EXPECTED_TARGETS or identity in seen:
            raise GateError("invalid_manual_evidence")
        seen.add(identity)
        status = target["status"]
        if status == "pending" and target["evidence"] is None:
            statuses.append(f"{identity[0]}/{identity[1]}: pending")
            continue
        if status != "passed" or type(target["evidence"]) is not dict:
            raise GateError("invalid_manual_evidence")
        _validate_record(target["evidence"], evidence_file.parent)
        statuses.append(f"{identity[0]}/{identity[1]}: passed")
    if seen != EXPECTED_TARGETS:
        raise GateError("manual_evidence_targets_incomplete")
    if require_complete and any(item.endswith(": pending") for item in statuses):
        raise GateError("manual_evidence_pending")
    return tuple(sorted(statuses))


def _validate_record(value: dict[str, object], root: Path) -> None:
    if set(value) != {"completed_on", "commit_sha", "record"}:
        raise GateError("invalid_manual_evidence")
    if (
        type(value["completed_on"]) is not str
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value["completed_on"])
        or type(value["commit_sha"]) is not str
        or COMMIT.fullmatch(value["commit_sha"]) is None
        or type(value["record"]) is not str
    ):
        raise GateError("invalid_manual_evidence")
    record = Path(value["record"])
    if record.is_absolute() or ".." in record.parts:
        raise GateError("invalid_manual_evidence")
    path = root / record
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise GateError("manual_evidence_record_missing") from error
    for heading in (
        "## Installation", "## Verification", "## Backout",
        "## Preservation", "## Offline",
    ):
        if heading not in text:
            raise GateError("manual_evidence_record_incomplete")


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--require-complete", action="store_true")
    active = parser.parse_args(arguments)
    try:
        statuses = check(
            active.artifact, active.evidence,
            require_complete=active.require_complete,
        )
    except GateError as error:
        print(f"v0.1 release gate: blocked: {error}")
        return 1
    print("v0.1 release gate: automated candidate passed")
    for status in statuses:
        print(f"manual evidence: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
