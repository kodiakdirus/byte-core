"""Byte Core lifecycle command entry point."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Sequence, TextIO


class ExitStatus(IntEnum):
    SUCCESS = 0
    USAGE = 2
    UNSUPPORTED = 3
    INVALID_INPUT = 4
    REFUSED = 5
    VERIFICATION_FAILED = 6
    RECOVERY_REQUIRED = 7
    INTERNAL_ERROR = 70


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    value: str


@dataclass(frozen=True)
class CheckReport:
    command: str
    supported: bool
    checks: tuple[CheckResult, ...]


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(ExitStatus.USAGE, f"byte: usage error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="byte",
        description="Safely inspect and manage a self-managed Byte deployment.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser(
        "check",
        help="inspect local prerequisites without making changes",
        description="Inspect local prerequisites without making changes.",
    )
    check.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="select human-readable text or deterministic JSON output",
    )
    for name in ("init", "plan", "apply", "verify", "update", "remove", "doctor"):
        commands.add_parser(name, help="reserved; not implemented")
    return parser


def collect_check_report() -> CheckReport:
    python_supported = sys.version_info >= (3, 11)
    python_value = f"{sys.version_info.major}.{sys.version_info.minor}"

    system = platform.system()
    normalized_system = {
        "Darwin": "macos",
        "Linux": "linux",
    }.get(system, "unsupported")
    platform_supported = normalized_system != "unsupported" and os.name == "posix"

    machine = _safe_identifier(platform.machine())
    architecture_identified = machine != "unknown"

    git_version = _git_version()
    git_supported = git_version is not None

    checks = (
        CheckResult(
            "python",
            "pass" if python_supported else "fail",
            python_value,
        ),
        CheckResult(
            "platform",
            "pass" if platform_supported else "fail",
            normalized_system,
        ),
        CheckResult(
            "architecture",
            "pass" if architecture_identified else "fail",
            machine,
        ),
        CheckResult(
            "git",
            "pass" if git_supported else "fail",
            git_version or "unavailable",
        ),
    )
    return CheckReport(
        command="check",
        supported=all(check.status == "pass" for check in checks),
        checks=checks,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    parser = build_parser()

    try:
        arguments = parser.parse_args(argv)
        if arguments.command != "check":
            errors.write("byte: command is not implemented\n")
            return ExitStatus.UNSUPPORTED

        report = collect_check_report()
        if arguments.format == "json":
            output.write(json.dumps(asdict(report), sort_keys=True) + "\n")
        else:
            output.write(_format_text(report))
        return (
            ExitStatus.SUCCESS
            if report.supported
            else ExitStatus.UNSUPPORTED
        )
    except SystemExit:
        raise
    except Exception:
        errors.write("byte: internal error\n")
        return ExitStatus.INTERNAL_ERROR


def _git_version() -> str | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    match = re.fullmatch(
        r"git version ([0-9]+(?:\.[0-9]+){1,3})(?:\s+[^\r\n]+)?\s*",
        completed.stdout,
    )
    return match.group(1) if match else None


def _safe_identifier(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or re.fullmatch(r"[a-z0-9_.-]{1,64}", normalized) is None:
        return "unknown"
    return normalized


def _format_text(report: CheckReport) -> str:
    lines = ["Byte environment check"]
    for check in report.checks:
        lines.append(f"[{check.status.upper()}] {check.name}: {check.value}")
    summary = "supported" if report.supported else "unsupported"
    lines.append(f"Result: {summary}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
