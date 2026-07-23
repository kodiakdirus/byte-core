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

from .lifecycle import (
    LifecycleError,
    apply_initialization,
    build_initialization_plan,
    load_plan,
    remove_core_integration,
    serialize_plan,
    verify_initialization,
)
from .installation import (
    InstallationError,
    apply_installation,
    apply_update,
    build_install_plan,
    build_removal_plan,
    build_update_plan,
    load_install_plan,
    load_release_descriptor,
    load_release_notes,
    load_update_plan,
    serialize as serialize_installation_plan,
    verify_installation,
    verify_update,
)
from .shell_integration import (
    ShellIntegrationError,
    apply_shell_plan,
    build_shell_install_plan,
    build_shell_removal_plan,
    load_shell_plan,
    serialize_shell_plan,
    verify_shell_plan,
)


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


@dataclass(frozen=True)
class UpdateCheckResult:
    command: str
    status: str
    from_version: str
    to_version: str
    migration: str
    release_notes_path: str
    descriptor_sha256: str
    plan_id: str
    action_count: int


SUPPORTED_HOSTS = frozenset(
    {
        ("linux", "x86_64", "ubuntu/24.04"),
        ("macos", "arm64", "macos/15"),
        ("macos", "arm64", "macos/26"),
    }
)


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
    init = commands.add_parser("init", help="initialize a new deployment")
    init.add_argument("--deployment-root", required=True)

    plan = commands.add_parser("plan", help="build a read-only lifecycle plan")
    plan_operations = plan.add_subparsers(dest="operation", required=True)
    plan_init = plan_operations.add_parser("init")
    plan_init.add_argument("--deployment-root", required=True)
    plan_install = plan_operations.add_parser("install")
    plan_install.add_argument("--artifact-root", required=True)
    plan_install.add_argument("--core-root", required=True)
    plan_install.add_argument("--state-root", required=True)
    plan_install.add_argument("--core-version", required=True)
    plan_remove = plan_operations.add_parser("remove")
    plan_remove.add_argument("--manifest", required=True)
    plan_remove.add_argument("--preserve-root", action="append", default=[])
    plan_update = plan_operations.add_parser("update")
    plan_update.add_argument("--manifest", required=True)
    plan_update.add_argument("--artifact-root", required=True)

    for name in ("apply", "verify"):
        command = commands.add_parser(name, help=f"{name} an exact plan")
        command.add_argument("--plan", required=True)
        command.add_argument("--format", choices=("text", "json"), default="text")
    remove = commands.add_parser(
        "remove", help="remove Core integration while preserving deployment data"
    )
    remove.add_argument("--deployment-root", required=True)
    remove.add_argument("--format", choices=("text", "json"), default="text")
    update = commands.add_parser(
        "update", help="inspect, plan, or apply a local Core update"
    )
    update_mode = update.add_mutually_exclusive_group(required=True)
    update_mode.add_argument("--check", action="store_true")
    update_mode.add_argument("--plan", action="store_true")
    update_mode.add_argument("--apply", metavar="PLAN_FILE")
    update.add_argument("--manifest")
    update.add_argument("--artifact-root")
    update.add_argument("--format", choices=("text", "json"), default="text")
    shell = commands.add_parser(
        "shell", help="plan and manage optional Bash or Zsh integration"
    )
    shell_operations = shell.add_subparsers(dest="shell_operation", required=True)
    shell_plan = shell_operations.add_parser("plan")
    shell_plan.add_argument("--home-root", required=True)
    shell_plan.add_argument("--shell", dest="shell_name", choices=("bash", "zsh"),
                            required=True)
    shell_plan.add_argument("--shell-script", required=True)
    shell_plan.add_argument("--syntax-highlighting")
    shell_plan_remove = shell_operations.add_parser("plan-remove")
    shell_plan_remove.add_argument("--home-root", required=True)
    shell_plan_remove.add_argument(
        "--shell", dest="shell_name", choices=("bash", "zsh"), required=True
    )
    for name in ("apply", "verify", "remove"):
        shell_action = shell_operations.add_parser(name)
        shell_action.add_argument("--plan", required=True)
        shell_action.add_argument(
            "--format", choices=("text", "json"), default="text"
        )
    commands.add_parser("doctor", help="reserved; not implemented")
    return parser


def collect_check_report() -> CheckReport:
    system = platform.system()
    return build_check_report(
        system=system,
        machine=platform.machine(),
        posix=os.name == "posix",
        python_version=(sys.version_info.major, sys.version_info.minor),
        git_version=_git_version(),
        host_release=_host_release(system),
    )


def build_check_report(
    *,
    system: str,
    machine: str,
    posix: bool,
    python_version: tuple[int, int],
    git_version: str | None,
    host_release: str,
) -> CheckReport:
    python_supported = (3, 11) <= python_version <= (3, 14)
    python_value = f"{python_version[0]}.{python_version[1]}"
    normalized_system = _normalize_system(system)
    normalized_machine = _normalize_machine(machine)
    platform_supported = normalized_system != "unsupported" and posix
    architecture_identified = normalized_machine != "unknown"
    host_supported = (
        platform_supported
        and architecture_identified
        and (normalized_system, normalized_machine, host_release) in SUPPORTED_HOSTS
    )
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
            normalized_machine,
        ),
        CheckResult(
            "host",
            "pass" if host_supported else "fail",
            f"{normalized_system}/{normalized_machine}/{host_release}",
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


def _normalize_system(value: str) -> str:
    return {"Darwin": "macos", "Linux": "linux"}.get(value, "unsupported")


def _normalize_machine(value: str) -> str:
    machine = _safe_identifier(value)
    return {
        "aarch64": "arm64",
        "amd64": "x86_64",
        "x64": "x86_64",
    }.get(machine, machine)


def _host_release(system: str) -> str:
    if system == "Darwin":
        version = _safe_identifier(platform.mac_ver()[0].partition(".")[0])
        return "unknown" if version == "unknown" else f"macos/{version}"
    if system == "Linux":
        try:
            release = platform.freedesktop_os_release()
        except OSError:
            return "unknown"
        identifier = _safe_identifier(release.get("ID", ""))
        version = _safe_identifier(release.get("VERSION_ID", ""))
        if identifier == "unknown" or version == "unknown":
            return "unknown"
        return f"{identifier}/{version}"
    return "unknown"


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    input_stream = stdin or sys.stdin
    parser = build_parser()
    active_plan = None

    try:
        arguments = parser.parse_args(argv)
        if arguments.command == "plan":
            if arguments.operation == "init":
                active_plan = build_initialization_plan(arguments.deployment_root)
                output.write(serialize_plan(active_plan))
            elif arguments.operation == "install":
                installation_plan = build_install_plan(
                    arguments.artifact_root, arguments.core_root,
                    arguments.state_root, arguments.core_version,
                )
                output.write(serialize_installation_plan(installation_plan))
            elif arguments.operation == "remove":
                removal_plan = build_removal_plan(
                    arguments.manifest,
                    preserve_roots=tuple(arguments.preserve_root),
                )
                output.write(serialize_installation_plan(removal_plan))
            else:
                update_plan = build_update_plan(
                    arguments.manifest,
                    arguments.artifact_root,
                )
                output.write(serialize_installation_plan(update_plan))
            return ExitStatus.SUCCESS
        if arguments.command == "apply":
            readiness = collect_check_report()
            if not readiness.supported:
                output.write(_format_text(readiness))
                return ExitStatus.UNSUPPORTED
            try:
                active_plan = load_plan(arguments.plan)
                result = apply_initialization(active_plan)
            except LifecycleError as error:
                if error.code not in {"invalid_plan", "unsupported_plan"}:
                    raise
                try:
                    active_plan = load_install_plan(arguments.plan)
                    result = apply_installation(active_plan)
                except InstallationError as install_error:
                    if install_error.code not in {"invalid_plan", "unsupported_plan"}:
                        raise
                    active_plan = load_update_plan(arguments.plan)
                    result = apply_update(active_plan)
            output.write(_format_lifecycle_result(result, arguments.format))
            return ExitStatus.SUCCESS
        if arguments.command == "verify":
            try:
                active_plan = load_plan(arguments.plan)
                result = verify_initialization(active_plan)
            except LifecycleError as error:
                if error.code not in {"invalid_plan", "unsupported_plan"}:
                    raise
                try:
                    active_plan = load_install_plan(arguments.plan)
                    result = verify_installation(active_plan)
                except InstallationError as install_error:
                    if install_error.code not in {"invalid_plan", "unsupported_plan"}:
                        raise
                    active_plan = load_update_plan(arguments.plan)
                    result = verify_update(active_plan)
            output.write(_format_lifecycle_result(result, arguments.format))
            return ExitStatus.SUCCESS
        if arguments.command == "init":
            readiness = collect_check_report()
            output.write(_format_text(readiness))
            if not readiness.supported:
                return ExitStatus.UNSUPPORTED
            active_plan = build_initialization_plan(arguments.deployment_root)
            output.write(_format_plan_preview(active_plan))
            output.write("Type the full plan ID to apply: ")
            output.flush()
            if input_stream.readline().strip() != active_plan.plan_id:
                errors.write("byte: initialization cancelled\n")
                return ExitStatus.REFUSED
            result = apply_initialization(active_plan)
            output.write(_format_lifecycle_result(result, "text"))
            return ExitStatus.SUCCESS
        if arguments.command == "remove":
            readiness = collect_check_report()
            if not readiness.supported:
                output.write(_format_text(readiness))
                return ExitStatus.UNSUPPORTED
            result = remove_core_integration(arguments.deployment_root)
            output.write(_format_lifecycle_result(result, arguments.format))
            return ExitStatus.SUCCESS
        if arguments.command == "update":
            if arguments.apply is None:
                if arguments.manifest is None or arguments.artifact_root is None:
                    parser.error(
                        "--check and --plan require --manifest and --artifact-root"
                    )
                active_plan = build_update_plan(
                    arguments.manifest, arguments.artifact_root
                )
                descriptor = load_release_descriptor(
                    os.path.join(active_plan.artifact_root, "release.json")
                )
                if arguments.plan:
                    output.write(serialize_installation_plan(active_plan))
                    return ExitStatus.SUCCESS
                result = _make_update_check_result(active_plan, descriptor)
                output.write(
                    _format_update_check(result, arguments.format)
                )
                return ExitStatus.SUCCESS
            if arguments.manifest is not None or arguments.artifact_root is not None:
                parser.error("--apply accepts only an exact plan file")
            if arguments.format != "text":
                parser.error("--apply uses an interactive text preview")
            readiness = collect_check_report()
            if not readiness.supported:
                output.write(_format_text(readiness))
                return ExitStatus.UNSUPPORTED
            active_plan = load_update_plan(arguments.apply)
            candidate = build_update_plan(
                active_plan.manifest_path, active_plan.artifact_root
            )
            if candidate != active_plan:
                raise InstallationError("plan_stale")
            descriptor, release_notes = load_release_notes(
                active_plan.artifact_root,
                active_plan.release_descriptor_sha256,
            )
            output.write(
                _format_update_preview(active_plan, descriptor, release_notes)
            )
            output.write("Type the full plan ID to apply: ")
            output.flush()
            if input_stream.readline().strip() != active_plan.plan_id:
                errors.write("byte: update cancelled\n")
                return ExitStatus.REFUSED
            result = apply_update(active_plan)
            verify_update(active_plan)
            output.write(_format_lifecycle_result(result, arguments.format))
            return ExitStatus.SUCCESS
        if arguments.command == "shell":
            if arguments.shell_operation == "plan":
                active_plan = build_shell_install_plan(
                    arguments.home_root,
                    arguments.shell_name,
                    arguments.shell_script,
                    arguments.syntax_highlighting,
                )
                output.write(serialize_shell_plan(active_plan))
                return ExitStatus.SUCCESS
            if arguments.shell_operation == "plan-remove":
                active_plan = build_shell_removal_plan(
                    arguments.home_root, arguments.shell_name
                )
                output.write(serialize_shell_plan(active_plan))
                return ExitStatus.SUCCESS
            active_plan = load_shell_plan(arguments.plan)
            if arguments.shell_operation == "verify":
                result = verify_shell_plan(active_plan)
            else:
                readiness = collect_check_report()
                if not readiness.supported:
                    output.write(_format_text(readiness))
                    return ExitStatus.UNSUPPORTED
                result = apply_shell_plan(active_plan)
            output.write(_format_lifecycle_result(result, arguments.format))
            return ExitStatus.SUCCESS
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
    except InstallationError as error:
        errors.write(f"byte: {error.code}\n")
        if error.code == "recovery_required":
            if active_plan is not None:
                errors.write(
                    "byte: preserve the Core and state roots; inspect the "
                    f"operation journal for plan {active_plan.plan_id}\n"
                )
            return ExitStatus.RECOVERY_REQUIRED
        if error.code == "verification_failed":
            return ExitStatus.VERIFICATION_FAILED
        if error.code in {
            "target_exists", "root_link_forbidden", "artifact_link_forbidden",
            "managed_file_modified", "managed_file_missing",
            "managed_path_link_forbidden", "managed_paths_changed",
            "active_mismatch", "plan_stale",
        }:
            return ExitStatus.REFUSED
        if error.code == "apply_failed":
            return ExitStatus.INTERNAL_ERROR
        return ExitStatus.INVALID_INPUT
    except LifecycleError as error:
        errors.write(f"byte: {error.code}\n")
        if error.code == "recovery_required":
            if active_plan is not None:
                errors.write(
                    "byte: preserve the deployment root and compare only the "
                    f"targets in plan {active_plan.plan_id}:\n"
                )
                errors.write(f"  root: {active_plan.deployment_root}\n")
                for item in active_plan.files:
                    errors.write(f"  target: {item.relative_path}\n")
            return ExitStatus.RECOVERY_REQUIRED
        if error.code == "verification_failed":
            return ExitStatus.VERIFICATION_FAILED
        if error.code in {"target_exists", "target_link_forbidden"}:
            return ExitStatus.REFUSED
        if error.code == "apply_failed":
            return ExitStatus.INTERNAL_ERROR
        return ExitStatus.INVALID_INPUT
    except ShellIntegrationError as error:
        errors.write(f"byte: {error.code}\n")
        if error.code == "recovery_required":
            if active_plan is not None:
                errors.write(
                    "byte: preserve the shell profile and exact backup for "
                    f"plan {active_plan.plan_id}\n"
                )
            return ExitStatus.RECOVERY_REQUIRED
        if error.code == "verification_failed":
            return ExitStatus.VERIFICATION_FAILED
        if error.code in {
            "profile_changed", "managed_block_conflict",
            "malformed_managed_block", "managed_block_mismatch",
            "backup_exists", "backup_directory_invalid",
        }:
            return ExitStatus.REFUSED
        if error.code == "apply_failed":
            return ExitStatus.INTERNAL_ERROR
        return ExitStatus.INVALID_INPUT
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


def _format_plan_preview(plan) -> str:
    lines = [
        "Byte initialization plan",
        f"Deployment root: {plan.deployment_root}",
        f"Plan ID: {plan.plan_id}",
        "Create:",
    ]
    lines.extend(f"  - {item.relative_path}" for item in plan.files)
    lines.append("Backout: remove only unchanged files created by this invocation")
    return "\n".join(lines) + "\n"


def _format_lifecycle_result(result, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(asdict(result), sort_keys=True) + "\n"
    return f"Result: {result.code}\nPlan ID: {result.plan_id}\n"


def _make_update_check_result(plan, descriptor) -> UpdateCheckResult:
    return UpdateCheckResult(
        command="update-check",
        status="eligible",
        from_version=plan.from_version,
        to_version=plan.to_version,
        migration=descriptor.migration,
        release_notes_path=descriptor.release_notes_path,
        descriptor_sha256=descriptor.descriptor_sha256,
        plan_id=plan.plan_id,
        action_count=len(plan.actions),
    )


def _format_update_check(
    result: UpdateCheckResult, output_format: str
) -> str:
    if output_format == "json":
        return json.dumps(asdict(result), sort_keys=True) + "\n"
    return "\n".join(
        (
            "Byte update check",
            f"Status: {result.status}",
            f"Current version: {result.from_version}",
            f"Next version: {result.to_version}",
            f"Migration: {result.migration}",
            f"Release notes: {result.release_notes_path}",
            f"Managed file actions: {result.action_count}",
            f"Plan ID: {result.plan_id}",
        )
    ) + "\n"


def _format_update_preview(plan, descriptor, release_notes: str) -> str:
    lines = [
        "Byte update plan",
        f"Current version: {plan.from_version}",
        f"Next version: {plan.to_version}",
        f"Migration: {descriptor.migration}",
        f"Release notes ({descriptor.release_notes_path}):",
        *(f"  {line}" for line in release_notes.splitlines()),
        f"Plan ID: {plan.plan_id}",
        "Create:",
    ]
    lines.extend(f"  - {action.target}" for action in plan.actions)
    lines.append(
        f"Preserve backout release: {plan.backout_release_relative_path}"
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
