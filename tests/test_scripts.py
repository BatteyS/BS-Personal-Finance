"""Tests for install.sh and launch.sh that need no Mac: they parse, say how they're used, and the
installer stops on anything but macOS. On a Mac, the full install is tried by hand."""

import platform
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("script", ["install.sh", "launch.sh"])
def test_the_scripts_parse(script):
    assert subprocess.run(["sh", "-n", str(ROOT / script)]).returncode == 0


def test_the_installer_explains_itself():
    shown = subprocess.run(["sh", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True)
    assert shown.returncode == 0
    assert "raw.githubusercontent.com/BatteyS/BS-Personal-Finance/main/install.sh" in shown.stdout
    assert "--dir FOLDER" in shown.stdout


def test_the_installer_refuses_an_unknown_option():
    refused = subprocess.run(["sh", str(ROOT / "install.sh"), "--fast"], capture_output=True, text=True)
    assert refused.returncode == 1 and "isn't an option" in refused.stderr


@pytest.mark.skipif(platform.system() == "Darwin", reason="on a Mac this would really install")
def test_the_installer_is_for_macos_only(tmp_path):
    stopped = subprocess.run(["sh", str(ROOT / "install.sh"), "--dir", str(tmp_path / "copy"), "--no-open"],
                             capture_output=True, text=True)
    assert stopped.returncode == 1 and "This installer is for macOS" in stopped.stderr
