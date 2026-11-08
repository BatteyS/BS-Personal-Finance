"""Tests for install.sh and launch.sh. Most need no Mac: they parse, say how they're used, and the
installer stops on anything but macOS. The ones that build an app with osacompile run only on a
Mac, and the full install is tried by hand."""

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


APP_ID = "io.github.batteys.bs-personal-finance"
on_a_mac = pytest.mark.skipif(platform.system() != "Darwin", reason="osacompile builds the app only on a Mac")


def old_app(folder: Path, script: str = "on run\nend run\n", bundle_id: str = APP_ID) -> Path:
    """Personal Finance as install.sh made it before v1.25: an applet with the installer's bundle id
    and no CFBundleURLTypes, sealed, with Finder's info on it, as an app that has sat on the Desktop
    has."""
    source = folder / "main.applescript"
    source.write_text(script)
    app = folder / "Personal Finance.app"
    subprocess.run(["osacompile", "-o", str(app), str(source)], check=True)
    subprocess.run(["plutil", "-replace", "CFBundleIdentifier", "-string", bundle_id,
                    str(app / "Contents/Info.plist")], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)
    # The Finder flags an app on the Desktop gets (all zeros would be no Finder info at all), which
    # codesign won't seal: "resource fork, Finder information, or similar detritus not allowed".
    subprocess.run(["xattr", "-wx", "com.apple.FinderInfo", "00" * 8 + "20" + "00" * 23, str(app)], check=True)
    return app


def url_schemes(app: Path) -> list[str]:
    shown = subprocess.run(["plutil", "-extract", "CFBundleURLTypes", "json", "-o", "-",
                            str(app / "Contents/Info.plist")], capture_output=True, text=True)
    return [scheme for url_type in json.loads(shown.stdout or "[]") for scheme in url_type["CFBundleURLSchemes"]]


def install_link(home: Path, app: Path, path_first: Path | None = None) -> subprocess.CompletedProcess:
    """sh install.sh --link APP, with its log in a home folder of the test's own."""
    (home / "Library/Logs").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home)}
    if path_first:
        env["PATH"] = f"{path_first}:{env['PATH']}"
    return subprocess.run(["sh", str(ROOT / "install.sh"), "--link", str(app)], capture_output=True, text=True, env=env)


@on_a_mac
def test_install_link_gives_an_app_made_before_the_link_the_link_and_seals_it(tmp_path):
    app = old_app(tmp_path)
    assert "com.apple.FinderInfo" in subprocess.run(["xattr", str(app)], capture_output=True, text=True).stdout
    try:
        linked = install_link(tmp_path / "home", app)
        assert linked.returncode == 0, linked.stdout + linked.stderr
        assert url_schemes(app) == ["bs-personal-finance"]
        sealed = subprocess.run(["codesign", "--verify", "--strict", str(app)], capture_output=True, text=True)
        assert sealed.returncode == 0, sealed.stderr
        again = install_link(tmp_path / "home", app)
        assert again.returncode == 0 and url_schemes(app) == ["bs-personal-finance"]
    finally:
        # --link registered this app with LaunchServices; the one on the Desktop answers the link.
        subprocess.run([LSREGISTER, "-u", str(app)], capture_output=True)


@on_a_mac
def test_install_link_leaves_the_app_as_it_was_when_it_cant_seal_it(tmp_path):
    """Info.plist goes back byte for byte, so the old seal still matches and the app still opens."""
    app = old_app(tmp_path)
    plist = (app / "Contents/Info.plist").read_bytes()
    stand_ins = tmp_path / "bin"
    stand_ins.mkdir()
    (stand_ins / "codesign").write_text("#!/bin/sh\nexit 1\n")
    (stand_ins / "codesign").chmod(0o755)
    failed = install_link(tmp_path / "home", app, path_first=stand_ins)
    assert failed.returncode == 1 and "Couldn't give" in failed.stderr
    assert (app / "Contents/Info.plist").read_bytes() == plist
    assert subprocess.run(["/usr/bin/codesign", "--verify", str(app)], capture_output=True).returncode == 0


@on_a_mac
def test_install_link_leaves_an_app_it_didnt_make_alone(tmp_path):
    app = old_app(tmp_path, bundle_id="com.example.someone-else")
    plist = (app / "Contents/Info.plist").read_bytes()
    refused = install_link(tmp_path / "home", app)
    assert refused.returncode == 1 and "isn't a Personal Finance this installer made" in refused.stderr
    assert (app / "Contents/Info.plist").read_bytes() == plist


@on_a_mac
def test_the_launcher_asks_for_the_link_for_the_app_that_ran_it_once_that_app_has_quit(tmp_path):
    """launch.sh's link_app on its own, run by a real applet as Personal Finance on the Desktop runs
    the launcher, with a stand-in install.sh that writes down what it was asked and whether the
    app was still running. Run from a terminal, or by an app that has the link, it asks nothing."""
    function = re.search(r"^link_app\(\) \{$.*?^\}$", (ROOT / "launch.sh").read_text(), re.M | re.S).group(0)
    copy = tmp_path / "copy"
    copy.mkdir()
    asked = copy / "asked"
    (copy / "install.sh").write_text(
        f'pgrep -f "$3/Contents/MacOS/applet" >/dev/null && echo still running >>{shlex.quote(str(asked))}\n'
        f'echo "$@" >>{shlex.quote(str(asked))}\n')
    launcher = copy / "launch.sh"
    launcher.write_text(f"ROOT={shlex.quote(str(copy))}\nLOG={shlex.quote(str(copy / 'launch.log'))}\n{function}\nlink_app\n")
    app = old_app(tmp_path, f'on run\n\tdo shell script "/bin/sh " & quoted form of "{launcher}"\nend run\n',
                  bundle_id=APP_ID + ".test")

    def asked_after(seconds: float) -> str:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not asked.exists():
            time.sleep(0.2)
        return asked.read_text() if asked.exists() else ""

    try:
        subprocess.run(["sh", str(launcher)], check=True)  # from a terminal: no app ran it
        assert asked_after(3) == ""
        subprocess.run(["open", "-W", str(app)], check=True, timeout=30)
        assert asked_after(10) == f"--link {app}\n"
        asked.unlink()
        subprocess.run(["plutil", "-insert", "CFBundleURLTypes", "-json",
                        '[{"CFBundleURLSchemes":["bs-personal-finance"]}]', str(app / "Contents/Info.plist")], check=True)
        subprocess.run(["xattr", "-d", "com.apple.FinderInfo", str(app)], check=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)
        subprocess.run(["open", "-W", str(app)], check=True, timeout=30)
        assert asked_after(3) == ""
    finally:
        subprocess.run([LSREGISTER, "-u", str(app)], capture_output=True)


def test_the_launcher_brings_the_copy_up_to_date_before_starting_a_page():
    launcher = (ROOT / "launch.sh").read_text()
    assert 'run --quiet updates.py update' in launcher and "PF_NO_UPDATE" in launcher
    assert launcher.count("\n  update_copy\n") == 1  # under phone access's LaunchAgent
    assert "rm -f \"$PIDFILE\"\nupdate_copy\n" in launcher  # before a fresh page, never for one already up
