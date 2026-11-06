"""installer/remove-v1.ps1, dry-run (-WhatIf) against a fake Users folder (#12)."""

import json
import shutil
import subprocess

import pytest
from conftest import TESTS_DIR

from caspar_controller import settings

SCRIPT = TESTS_DIR.parent / "installer" / "remove-v1.ps1"
pytestmark = pytest.mark.skipif(not shutil.which("powershell"), reason="needs Windows PowerShell")


def dry_run(tmp_path, config: dict | None, *files: str) -> subprocess.CompletedProcess:
    folder = tmp_path / "Users" / "operator" / settings.V1_INSTALL
    folder.mkdir(parents=True)
    for name in files:
        (folder / name).write_bytes(b"")
    if config is not None:
        (tmp_path / "config.json").write_text(json.dumps(config))
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-ConfigPath",
            str(tmp_path / "config.json"),
            "-UsersDir",
            str(tmp_path / "Users"),
            "-WhatIf",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    "config", [None, {"server_name": "New install"}], ids=["no config", "not migrated"]
)
def test_leaves_v1_alone_until_v2_imported_it(tmp_path, config):
    run = dry_run(tmp_path, config, "unins000.exe")
    assert run.returncode == 0 and "leaving v1.x alone" in run.stdout
    assert "Uninstall" not in run.stdout


def test_uninstalls_v1_once_migrated(tmp_path):
    run = dry_run(
        tmp_path, {"migrated_from": "1.x"}, "unins000.exe", "ElliottsCasparController.exe"
    )
    assert run.returncode == 0
    assert (
        'Performing the operation "Uninstall silently"' in run.stdout
        and "unins000.exe" in run.stdout
    )


def test_never_uninstalls_v2_from_v1s_folder(tmp_path):
    run = dry_run(tmp_path, {"migrated_from": "1.x"}, "unins000.exe", "CasparController.exe")
    assert run.returncode == 0 and "v2 is installed there" in run.stdout
    assert "Uninstall silently" not in run.stdout


def test_a_missing_uninstaller_fails(tmp_path):
    run = dry_run(tmp_path, {"migrated_from": "1.x"}, "ElliottsCasparController.exe")
    assert run.returncode == 1 and "remove it by hand" in run.stdout
