"""Create one deterministic archive from a verified Byte Core artifact."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import sys
import tarfile
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from byte_core.installation import (  # noqa: E402
    InstallationError,
    load_release_descriptor,
)
from byte_core.privacy.adapters import scan_artifact_directory  # noqa: E402


class PackageError(Exception):
    pass


def package(
    artifact_root: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
) -> tuple[Path, str]:
    artifact = _existing_root(artifact_root)
    output = _new_output(output_path)
    try:
        descriptor = load_release_descriptor(artifact / "release.json")
    except InstallationError as error:
        raise PackageError(f"artifact_{error.code}") from error
    privacy = scan_artifact_directory(artifact)
    if not privacy.passed:
        raise PackageError("artifact_privacy_failed")
    expected = {
        *(item.relative_path for item in descriptor.files),
        "release.json",
    }
    actual = {
        path.relative_to(artifact).as_posix()
        for path in artifact.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise PackageError("artifact_paths_changed")

    prefix = PurePosixPath(f"byte-core-{descriptor.core_version}")
    try:
        with output.open("xb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, mtime=0
            ) as compressed:
                with tarfile.open(
                    mode="w", fileobj=compressed, format=tarfile.PAX_FORMAT
                ) as archive:
                    _add_directory(archive, prefix)
                    directories: set[PurePosixPath] = set()
                    for relative in expected:
                        parent = PurePosixPath(relative).parent
                        while parent != PurePosixPath("."):
                            directories.add(parent)
                            parent = parent.parent
                    for directory in sorted(
                        directories,
                        key=lambda value: (len(value.parts), value.as_posix()),
                    ):
                        _add_directory(archive, prefix / directory)
                    for relative in sorted(expected):
                        _add_file(
                            archive,
                            artifact / relative,
                            prefix / PurePosixPath(relative),
                        )
    except Exception:
        try:
            output.unlink()
        except FileNotFoundError:
            pass
        raise
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return output, digest


def _existing_root(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(value)
        if path.is_symlink():
            raise PackageError("invalid_artifact_root")
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise PackageError("invalid_artifact_root") from error
    if not resolved.is_dir():
        raise PackageError("invalid_artifact_root")
    return resolved


def _new_output(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(value)
        if (
            not path.is_absolute()
            or path.name in {"", ".", ".."}
            or path.suffixes[-2:] != [".tar", ".gz"]
        ):
            raise PackageError("invalid_output_path")
        parent = path.parent.resolve(strict=True)
        output = parent / path.name
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise PackageError("invalid_output_path") from error
    if output.exists() or output.is_symlink():
        raise PackageError("output_exists")
    return output


def _add_directory(archive: tarfile.TarFile, path: PurePosixPath) -> None:
    info = tarfile.TarInfo(path.as_posix())
    info.type = tarfile.DIRTYPE
    info.mode = 0o700
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    archive.addfile(info)


def _add_file(
    archive: tarfile.TarFile,
    source: Path,
    path: PurePosixPath,
) -> None:
    data = source.read_bytes()
    info = tarfile.TarInfo(path.as_posix())
    info.size = len(data)
    info.mode = source.stat().st_mode & 0o777
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    archive.addfile(info, io.BytesIO(data))


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--output", required=True)
    active = parser.parse_args(arguments)
    try:
        output, digest = package(active.artifact, active.output)
    except PackageError as error:
        parser.error(str(error))
    print(f"{digest}  {output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
