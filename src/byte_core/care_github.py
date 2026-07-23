"""Reviewed GitHub issue transport for Byte Care reports."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

from .care import DiagnosticReport, serialize_diagnostic_report

OFFICIAL_REPOSITORY = "kodiakdirus/byte-core"
REPORT_LABEL = "byte-care"
MINIMUM_RETRY_SECONDS = 3600
MAX_TRANSPORT_BYTES = 128 * 1024
_FINGERPRINT = re.compile(r"[0-9a-f]{24}")


class CareTransportError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GithubAction:
    repository: str
    action: str
    issue_number: int | None
    title: str
    label: str
    fingerprint: str
    markdown: str
    report: DiagnosticReport


@dataclass(frozen=True)
class GithubTransportResult:
    code: str
    action: str
    fingerprint: str
    issue_number: int | None


Runner = Callable[[Sequence[str], str | None], subprocess.CompletedProcess[str]]


def plan_github_action(
    report: DiagnosticReport,
    *,
    repository: str,
    runner: Runner | None = None,
) -> GithubAction:
    if repository != OFFICIAL_REPOSITORY:
        raise CareTransportError("repository_not_official")
    _validate_report_shape(report)
    active_runner = runner or _run
    _require_authentication(active_runner)
    completed = active_runner(
        (
            "gh", "issue", "list", "--repo", repository,
            "--state", "open", "--limit", "100",
            "--json", "number,body",
        ),
        None,
    )
    if completed.returncode != 0:
        raise CareTransportError("github_search_failed")
    try:
        issues = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise CareTransportError("github_response_invalid") from error
    if type(issues) is not list:
        raise CareTransportError("github_response_invalid")
    marker = f"<!-- byte-care:{report.fingerprint} -->"
    matches = []
    for item in issues:
        if (
            type(item) is not dict
            or set(item) != {"number", "body"}
            or type(item["number"]) is not int
            or type(item["body"]) is not str
        ):
            raise CareTransportError("github_response_invalid")
        if marker in item["body"]:
            matches.append(item["number"])
    if len(matches) > 1:
        raise CareTransportError("duplicate_issue_ambiguous")
    markdown = render_github_markdown(report)
    return GithubAction(
        repository=repository,
        action="comment" if matches else "create",
        issue_number=matches[0] if matches else None,
        title=(
            f"[Byte Care] {report.component} {report.error_code} "
            f"({report.fingerprint})"
        ),
        label=REPORT_LABEL,
        fingerprint=report.fingerprint,
        markdown=markdown,
        report=report,
    )


def submit_github_action(
    action: GithubAction,
    *,
    approval_fingerprint: str,
    transport_root: str | os.PathLike[str],
    runner: Runner | None = None,
    now: int | None = None,
) -> GithubTransportResult:
    _validate_action(action)
    if approval_fingerprint != action.fingerprint:
        raise CareTransportError("submission_not_approved")
    root = _transport_root(transport_root)
    active_now = int(time.time()) if now is None else now
    if type(active_now) is not int or active_now < 0:
        raise CareTransportError("invalid_submission_time")
    state_path = root / f"{action.fingerprint}.state.json"
    if state_path.exists():
        state = _load_state(state_path)
        if active_now - state["submitted_at"] < MINIMUM_RETRY_SECONDS:
            raise CareTransportError("submission_rate_limited")
        if action.action == "create":
            raise CareTransportError("action_stale")

    active_runner = runner or _run
    markdown_path = root / f"{action.fingerprint}.{active_now}.md"
    _write_exclusive(markdown_path, action.markdown.encode("utf-8"))
    _require_authentication(active_runner)
    if action.action == "create":
        arguments = (
            "gh", "issue", "create", "--repo", action.repository,
            "--title", action.title, "--label", action.label,
            "--body-file", "-",
        )
    else:
        arguments = (
            "gh", "issue", "comment", str(action.issue_number),
            "--repo", action.repository, "--body-file", "-",
        )
    completed = active_runner(arguments, action.markdown)
    if completed.returncode != 0:
        raise CareTransportError("github_submission_failed")
    issue_number = (
        action.issue_number
        if action.issue_number is not None
        else _issue_number(completed.stdout)
    )
    state = {
        "schema_version": 1,
        "fingerprint": action.fingerprint,
        "action": action.action,
        "issue_number": issue_number,
        "submitted_at": active_now,
        "markdown_file": markdown_path.name,
    }
    _replace_file(
        state_path,
        (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    return GithubTransportResult(
        "submitted", action.action, action.fingerprint, issue_number
    )


def render_github_markdown(report: DiagnosticReport) -> str:
    _validate_report_shape(report)
    values = asdict(report)
    lines = [
        f"<!-- byte-care:{report.fingerprint} -->",
        "# Byte Care report",
        "",
        "This report was constructed locally from Byte Core's minimal "
        "diagnostic schema and explicitly reviewed before submission.",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]
    for key in (
        "byte_core_version", "component", "phase", "error_code", "exit_code",
        "platform", "architecture", "python_version",
        "configuration_schema_version", "fingerprint",
    ):
        value = values[key]
        lines.append(f"| `{key}` | `{value if value is not None else 'unknown'}` |")
    lines.extend(
        (
            "",
            "No logs, environment variables, configuration values, inventory, "
            "credentials, paths, prompts, transcripts, or command output are included.",
            "",
        )
    )
    return "\n".join(lines)


def _require_authentication(runner: Runner) -> None:
    completed = runner(("gh", "auth", "status", "--hostname", "github.com"), None)
    if completed.returncode != 0:
        raise CareTransportError("github_authentication_required")


def _validate_report_shape(report: DiagnosticReport) -> None:
    try:
        serialize_diagnostic_report(report)
    except Exception as error:
        raise CareTransportError("invalid_report") from error
    if not _FINGERPRINT.fullmatch(report.fingerprint):
        raise CareTransportError("invalid_report")


def _validate_action(action: GithubAction) -> None:
    if type(action) is not GithubAction:
        raise CareTransportError("invalid_action")
    _validate_report_shape(action.report)
    expected_title = (
        f"[Byte Care] {action.report.component} "
        f"{action.report.error_code} ({action.report.fingerprint})"
    )
    if (
        action.repository != OFFICIAL_REPOSITORY
        or action.action not in ("create", "comment")
        or action.label != REPORT_LABEL
        or not _FINGERPRINT.fullmatch(action.fingerprint)
        or action.fingerprint != action.report.fingerprint
        or action.title != expected_title
        or action.markdown != render_github_markdown(action.report)
        or action.markdown.count(
            f"<!-- byte-care:{action.fingerprint} -->"
        ) != 1
        or (action.action == "create" and action.issue_number is not None)
        or (
            action.action == "comment"
            and (
                type(action.issue_number) is not int
                or action.issue_number < 1
            )
        )
    ):
        raise CareTransportError("invalid_action")


def _transport_root(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(value)
        if not path.is_absolute() or path.is_symlink():
            raise CareTransportError("invalid_transport_root")
        if path.exists():
            root = path.resolve(strict=True)
            if not root.is_dir() or root.stat().st_mode & 0o077:
                raise CareTransportError("invalid_transport_root")
        else:
            root = path.parent.resolve(strict=True) / path.name
            root.mkdir(mode=0o700)
    except CareTransportError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise CareTransportError("invalid_transport_root") from error
    return root


def _load_state(path: Path) -> dict:
    try:
        raw = json.loads(_read_bounded(path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CareTransportError("transport_state_invalid") from error
    keys = {
        "schema_version", "fingerprint", "action", "issue_number",
        "submitted_at", "markdown_file",
    }
    if (
        type(raw) is not dict
        or set(raw) != keys
        or raw["schema_version"] != 1
        or type(raw["submitted_at"]) is not int
    ):
        raise CareTransportError("transport_state_invalid")
    return raw


def _issue_number(output: str) -> int:
    match = re.search(r"/issues/([1-9][0-9]*)\s*$", output)
    if match is None:
        raise CareTransportError("github_response_invalid")
    return int(match.group(1))


def _run(
    arguments: Sequence[str], input_text: str | None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(arguments),
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CareTransportError("github_cli_failed") from error


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        try:
            path.unlink()
        except OSError:
            pass
        raise CareTransportError("transport_write_failed") from error
    path.chmod(0o600)


def _replace_file(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    _write_exclusive(temporary, data)
    try:
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_bounded(path: Path) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise CareTransportError("transport_state_invalid")
        data = path.read_bytes()
    except OSError as error:
        raise CareTransportError("transport_read_failed") from error
    if len(data) > MAX_TRANSPORT_BYTES:
        raise CareTransportError("transport_too_large")
    return data
