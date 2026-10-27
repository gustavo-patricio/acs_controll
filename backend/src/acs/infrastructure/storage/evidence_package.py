"""Private Linux packages; the manifest is published last as a commit marker."""

import os
from pathlib import Path
from stat import S_IMODE, S_ISREG

from acs.cwmp.compatibility.evidence_package import (
    MAX_MANIFEST,
    manifest_bytes,
    verify_package,
)
from acs.cwmp.soap.inform import MAX_BODY
from acs.domain.operations.evidence import (
    EvidenceManifest,
    EvidencePackage,
    InvalidEvidence,
)


def read_bounded(
    path: str | Path, limit: int, *, directory: int | None = None, private: bool = False
) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not S_ISREG(info.st_mode) or info.st_size > limit:
            raise InvalidEvidence
        if private and (S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid()):
            raise InvalidEvidence
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise InvalidEvidence
    return payload


def _directory(path: Path) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
        os.close(fd)
        raise InvalidEvidence
    return fd


def _write(directory: int, name: str, payload: bytes) -> None:
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory,
    )
    with os.fdopen(fd, "wb") as stream:
        if S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise InvalidEvidence
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def publish_package(output: Path, package: EvidencePackage) -> None:
    manifest = manifest_bytes(package.manifest)
    verify_package(package.xml, manifest)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = _directory(output.parent)
    try:
        # Atomic reservation: existing directories, files and symlinks all fail.
        os.mkdir(output.name, mode=0o700, dir_fd=parent)
        directory = None
        try:
            directory = _directory(output)
            _write(directory, "inform.xml", package.xml)
            _write(directory, ".manifest.tmp", manifest)
            os.link(
                ".manifest.tmp",
                "manifest.json",
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            os.unlink(".manifest.tmp", dir_fd=directory)
            os.fsync(directory)
            os.fsync(parent)
        except Exception:
            if directory is not None:
                for name in ("manifest.json", ".manifest.tmp", "inform.xml"):
                    try:
                        os.unlink(name, dir_fd=directory)
                    except FileNotFoundError:
                        pass
            os.rmdir(output.name, dir_fd=parent)
            raise
        finally:
            if directory is not None:
                os.close(directory)
    finally:
        os.close(parent)


def load_package(path: Path) -> EvidenceManifest:
    directory = _directory(path)
    try:
        if set(os.listdir(directory)) != {"inform.xml", "manifest.json"}:
            raise InvalidEvidence
        manifest = read_bounded(
            "manifest.json", MAX_MANIFEST, directory=directory, private=True
        )
        xml = read_bounded("inform.xml", MAX_BODY, directory=directory, private=True)
        return verify_package(xml, manifest)
    finally:
        os.close(directory)
