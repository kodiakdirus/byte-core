"""CI entry point for scanning one explicit release artifact directory."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from byte_core.privacy.adapters import scan_artifact_directory  # noqa: E402


def main(arguments: list[str] | None = None) -> int:
    """Return success only when the complete bounded artifact scan passes."""

    active_arguments = sys.argv[1:] if arguments is None else arguments
    if len(active_arguments) != 1:
        print("usage: check_release_privacy.py ARTIFACT_DIRECTORY", file=sys.stderr)
        return 2

    result = scan_artifact_directory(active_arguments[0])
    for error in result.errors:
        location = (
            ""
            if error.input_index is None
            else f" input={error.input_index}"
        )
        print(f"privacy adapter error: {error.code}{location}")

    finding_count = 0
    for scan in result.scans:
        for finding in scan.findings:
            finding_count += 1
            print(
                f"{finding.source_id}:{finding.line}: "
                f"{finding.rule_id}: {finding.description}"
            )

    print(
        "privacy release scan: "
        f"files={len(result.scans)} findings={finding_count} "
        f"adapter_errors={len(result.errors)}"
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
