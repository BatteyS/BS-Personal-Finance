"""Tests for install.sh and launch.sh that need no Mac: they parse, say how they're used, and the
installer stops on anything but macOS. On a Mac, the full install is tried by hand."""

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"


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


def test_the_page_stops_after_twenty_idle_minutes_unless_told_otherwise():
    assert "IDLE_MINUTES=${PF_IDLE_MINUTES:-20}" in (ROOT / "launch.sh").read_text()


def test_the_launcher_draws_the_phone_icon_again_once_icon_py_is_newer(tmp_path):
    """launch.sh's draw_icon on its own, with a stand-in for uv that counts drawings: it draws when
    there's no icon, leaves a current one alone, and draws again after an update changes icon.py."""
    function = re.search(r"^draw_icon\(\) \{$.*?^\}$", (ROOT / "launch.sh").read_text(), re.M | re.S).group(0)
    uv = tmp_path / "uv"
    uv.write_text("#!/bin/sh\necho drawn >>drawings\nmkdir -p static && touch static/apple-touch-icon.png\n")
    uv.chmod(0o755)
    icon = tmp_path / "icon.py"
    icon.touch()
    os.utime(icon, (1_700_000_000, 1_700_000_000))  # checked out long ago

    def launch() -> None:
        subprocess.run(["sh", "-c", f"UV={shlex.quote(str(uv))}\n{function}\ndraw_icon"], cwd=tmp_path, check=True)

    launch()  # no icon yet
    launch()  # the icon is newer than icon.py
    os.utime(icon, (time.time() + 60, time.time() + 60))  # an update brought a new icon.py
    launch()
    assert (tmp_path / "drawings").read_text().split() == ["drawn", "drawn"]


def test_the_installer_tells_the_desktop_app_to_answer_the_launch_link():
    """The source alone, so it runs anywhere: the applet handles open location and gets the scheme."""
    installer = (ROOT / "install.sh").read_text()
    assert "on open location" in installer
    assert 'URL_SCHEME="bs-personal-finance"' in installer
    assert "plutil -insert CFBundleURLTypes" in installer


@pytest.mark.skipif(platform.system() != "Darwin", reason="osacompile builds the app only on a Mac")
def test_the_desktop_app_answers_the_launch_link(tmp_path):
    """Builds the app with the installer's own make_app (its functions, then one call) and looks at
    what came out: the bs-personal-finance scheme in Info.plist, and an open location handler in
    the compiled script, so bs-personal-finance://launch opens the page like a double-click."""
    functions = (ROOT / "install.sh").read_text().split('step "Personal Finance"')[0]
    app = tmp_path / "Personal Finance.app"
    log = tmp_path / "install.log"
    script = functions + "\n".join([
        "",
        f"DIR={shlex.quote(str(ROOT))}",
        f"UV={shlex.quote(shutil.which('uv') or 'false')}",
        f"LOG={shlex.quote(str(log))}",
        f"make_app {shlex.quote(str(app))}",
        "",
    ])
    try:
        built = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        assert built.returncode == 0, log.read_text() if log.exists() else built.stderr
        url_types = subprocess.run(
            ["plutil", "-extract", "CFBundleURLTypes", "json", "-o", "-", str(app / "Contents/Info.plist")],
            capture_output=True, text=True, check=True).stdout
        assert json.loads(url_types) == [
            {"CFBundleURLName": "io.github.batteys.bs-personal-finance", "CFBundleURLSchemes": ["bs-personal-finance"]}]
        source = subprocess.run(["osadecompile", str(app / "Contents/Resources/Scripts/main.scpt")],
                                capture_output=True, text=True, check=True).stdout
        assert "on open location" in source
    finally:
        # make_app registered this app with LaunchServices; the one on the Desktop answers the link.
        subprocess.run([LSREGISTER, "-u", str(app)], capture_output=True)


def test_the_launcher_brings_the_copy_up_to_date_before_starting_a_page():
    launcher = (ROOT / "launch.sh").read_text()
    assert 'run --quiet updates.py update' in launcher and "PF_NO_UPDATE" in launcher
    assert launcher.count("\n  update_copy\n") == 1  # under phone access's LaunchAgent
    assert "rm -f \"$PIDFILE\"\nupdate_copy\n" in launcher  # before a fresh page, never for one already up
