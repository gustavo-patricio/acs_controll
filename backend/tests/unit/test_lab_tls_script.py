"""Exercise local certificate issuance in disposable Linux directories."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not shutil.which("openssl") or not shutil.which("bash"),
    reason="The WSL provisioning script requires Bash and OpenSSL",
)


def prepare_script(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    script = tmp_path / "workspace/scripts/create_lab_tls.sh"
    script.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).parents[2] / "scripts/create_lab_tls.sh", script)
    private = tmp_path / "private"
    return script, private, {**os.environ, "ACS_LAB_TLS_DIR": str(private)}


def run_script(
    script: Path, ip: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), ip],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def verify(private: Path, ip: str) -> int:
    return subprocess.run(
        [
            "openssl",
            "verify",
            "-purpose",
            "sslserver",
            "-verify_ip",
            ip,
            "-CAfile",
            str(private / "ca.crt"),
            str(private / "server.crt"),
        ],
        capture_output=True,
        timeout=10,
        check=False,
    ).returncode


def test_issue_and_renew_preserve_ca_and_previous_certificate(tmp_path: Path) -> None:
    script, private, env = prepare_script(tmp_path)
    result = run_script(script, "192.168.18.7", env)
    assert result.returncode == 0, result.stderr
    original_ca = (private / "ca.crt").read_bytes()
    original_server = (private / "server.crt").read_bytes()
    assert verify(private, "192.168.18.7") == 0
    assert verify(private, "127.0.0.1") == 0
    assert verify(private, "192.168.18.8") != 0
    assert private.stat().st_mode & 0o777 == 0o700
    for name in ("ca.key", "server.key"):
        assert (private / name).stat().st_mode & 0o777 == 0o600
    exported = script.parents[1] / ".tls"
    assert (exported / "lan-ca.crt").read_bytes() == original_ca
    assert (exported / "mirrored").resolve() == private
    renewed = run_script(script, "192.168.18.8", env)
    assert renewed.returncode == 0, renewed.stderr
    assert (private / "ca.crt").read_bytes() == original_ca
    assert verify(private, "192.168.18.8") == 0
    assert verify(private, "192.168.18.7") != 0
    backups = list(private.glob("backup.*/server.crt"))
    assert len(backups) == 1 and backups[0].read_bytes() == original_server


@pytest.mark.parametrize(
    "ip", ["192.168.18.999", "192.168.018.7", "localhost", "127.0.0.1;invalid"]
)
def test_invalid_address_does_not_create_keys(tmp_path: Path, ip: str) -> None:
    script, private, env = prepare_script(tmp_path)
    assert run_script(script, ip, env).returncode != 0
    assert not private.exists()


def test_existing_project_directory_is_not_overwritten(tmp_path: Path) -> None:
    script, private, env = prepare_script(tmp_path)
    existing = script.parents[1] / ".tls/mirrored"
    existing.mkdir(parents=True)
    assert run_script(script, "192.168.18.7", env).returncode != 0
    assert existing.is_dir() and not existing.is_symlink()
    assert not (private / "ca.key").exists()
