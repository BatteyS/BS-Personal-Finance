"""Tests for phone.py and icon.py that need no tailnet: the page's guard, reading Tailscale's
status, the LaunchAgent, the folder check, the diagnosis, and the icon. All names are made up."""

import plistlib
import struct
from pathlib import Path

import pytest

import icon
import phone

ACCESS = {"host": "mymac.example-tailnet.ts.net", "login": "pat@example.com", "port": 8501}
HEALTHY = {"found": True, "running": True, "host": "mymac.example-tailnet.ts.net", "login": "pat@example.com"}


@pytest.mark.parametrize(
    ("headers", "access", "refused"),
    [
        ({}, None, False),  # on the Mac itself, with or without phone access
        ({}, ACCESS, False),
        ({"Tailscale-User-Login": "pat@example.com"}, ACCESS, False),
        ({"Tailscale-User-Login": "PAT@example.com"}, ACCESS, False),  # logins compare without case
        ({"Tailscale-User-Login": "sam@example.com"}, ACCESS, True),  # someone else on the tailnet
        ({"Tailscale-User-Login": "pat@example.com"}, None, True),  # forwarded, but never set up
    ],
)
def test_the_page_answers_the_mac_and_the_account_set_up_for_it(headers, access, refused):
    assert (phone.refusal(headers, access) is not None) == refused


def test_access_is_read_from_phone_json(tmp_path):
    path = tmp_path / "phone.json"
    assert phone.read_access(path) is None
    path.write_text('{"host": "mymac.example-tailnet.ts.net", "login": "pat@example.com"}')
    assert phone.read_access(path)["login"] == "pat@example.com"
    path.write_text("not json")
    assert phone.read_access(path) is None


def test_a_server_names_the_login_in_the_environment(tmp_path, monkeypatch):
    missing = tmp_path / "phone.json"
    monkeypatch.setenv(phone.LOGIN_VARIABLE, "pat@example.com")
    assert phone.read_access(missing) == {"login": "pat@example.com"}
    assert phone.refusal({"Tailscale-User-Login": "Pat@example.com"}, phone.read_access(missing)) is None
    assert phone.refusal({"Tailscale-User-Login": "sam@example.com"}, phone.read_access(missing))
    monkeypatch.setenv(phone.LOGIN_VARIABLE, "   ")
    assert phone.read_access(missing) is None


def test_tailscale_status_names_this_mac_and_its_account():
    data = {
        "BackendState": "Running",
        "Self": {"DNSName": "MyMac.example-tailnet.ts.net.", "UserID": 7},
        "User": {"7": {"LoginName": "pat@example.com"}},
    }
    assert phone.parse_status(data) == {"running": True, "host": "mymac.example-tailnet.ts.net",
                                        "login": "pat@example.com"}
    assert phone.parse_status({"BackendState": "Stopped"})["running"] is False


def test_the_launch_agent_keeps_the_page_running(tmp_path):
    agent = plistlib.loads(phone.agent_plist(tmp_path, 8501))
    assert agent["Label"] == phone.LABEL
    assert agent["ProgramArguments"] == ["/bin/sh", str(tmp_path / "launch.sh"), "--serve-always", "8501"]
    assert agent["RunAtLoad"] and agent["KeepAlive"]


def test_folders_macos_keeps_background_jobs_out_of(tmp_path):
    home = tmp_path
    assert phone.protected_folder(home / "Documents" / "finance", home) == "Documents"
    assert phone.protected_folder(home / "Library" / "Mobile Documents" / "x", home) == "Library/Mobile Documents"
    assert phone.protected_folder(home / "personal-finance", home) is None


@pytest.mark.parametrize(
    ("ts", "access", "loaded", "answers", "forwarded", "says"),
    [
        (HEALTHY, None, False, False, None, "isn't set up"),
        ({**HEALTHY, "found": False}, ACCESS, True, True, True, "Tailscale isn't on this Mac"),
        ({**HEALTHY, "running": False}, ACCESS, True, True, True, "Tailscale is off"),
        ({**HEALTHY, "host": "othermac.example-tailnet.ts.net"}, ACCESS, True, True, True, "name changed"),
        (HEALTHY, ACCESS, False, True, True, "LaunchAgent isn't loaded"),
        (HEALTHY, ACCESS, True, False, True, "isn't answering"),
        (HEALTHY, ACCESS, True, True, False, "isn't forwarding"),
    ],
)
def test_the_diagnosis_says_what_is_wrong(ts, access, loaded, answers, forwarded, says):
    assert says in phone.diagnose(ts, access, loaded, answers, forwarded)


def test_a_working_setup_has_no_diagnosis():
    assert phone.diagnose(HEALTHY, ACCESS, True, True, True) is None
    assert phone.diagnose(HEALTHY, ACCESS, True, True, None) is None  # serve status unknown isn't a fault


def test_an_unknown_command_prints_the_usage(capsys):
    assert phone.main(["reboot"]) == 2
    assert "uv run phone.py enable" in capsys.readouterr().out


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_the_icon_is_a_png_of_the_size_asked_for():
    assert png_size(icon.draw(64)) == (64, 64)


def test_the_icon_is_a_ring_on_a_rounded_square():
    size = 180
    assert icon.pixel(1, 1, size)[3] == 0  # the rounded corner is see-through
    assert icon.pixel(size / 2, size / 2, size)[:3] == icon.BACKGROUND  # the ring's hole
    assert icon.pixel(size / 2, size * 0.8, size)[:3] == icon.RING  # the ring, at six o'clock
    assert icon.pixel(size * 0.79, size / 2, size)[:3] == icon.SLICE  # the light slice, at three


def test_the_tab_and_home_screen_icons_have_no_clear_pixel():
    """Safari puts white behind a tab icon's clear pixels, so the rounded square showed in a white
    box there: the tab's and the home screen's icons are the square filled to the edge."""
    import zlib

    size = 64
    assert icon.pixel(0.5, 0.5, size, square=True) == (*icon.BACKGROUND, 255)  # a corner, filled
    data = icon.draw(size, square=True)
    rows = zlib.decompress(data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8])
    stride = 1 + size * 4  # a filter byte, then RGBA
    assert {rows[row * stride + 1 + column * 4 + 3] for row in range(size) for column in range(size)} == {255}


def test_the_iconset_has_every_size_macos_wants(tmp_path):
    icon.write_iconset(tmp_path / "icon.iconset")
    names = sorted(path.name for path in (tmp_path / "icon.iconset").iterdir())
    assert "icon_16x16.png" in names and "icon_512x512@2x.png" in names and len(names) == 12
    assert png_size((tmp_path / "icon.iconset" / "icon_512x512@2x.png").read_bytes()) == (1024, 1024)
