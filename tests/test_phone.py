"""Tests for phone.py and icon.py that need no tailnet: the page's guard, reading Tailscale's
status, the LaunchAgent, the address following the page, the job search tool's old worker, the
folder check, the diagnosis, and the icon. All names are made up, and nothing runs Tailscale."""

import plistlib
import socket
import struct
import subprocess
import threading
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


# ---------------------------------------------------------------- the address follows the page

HOST = ACCESS["host"]


def served(proxy: str | None = "http://127.0.0.1:8501", funnel: bool = False, **more_handlers) -> dict:
    """What `tailscale serve status --json` prints, with the root forwarded to `proxy`."""
    handlers = {"/": {"Proxy": proxy}} if proxy else {}
    config = {"TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
              "Web": {f"{HOST}:443": {"Handlers": {**handlers, **more_handlers}},
                      f"{HOST}:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8770"}}}}}
    if funnel:
        config["AllowFunnel"] = {f"{HOST}:443": True}
    return config


@pytest.mark.parametrize(
    ("config", "port"),
    [
        (served("http://127.0.0.1:5103"), 5103),
        (served("http://localhost:8501/"), 8501),
        (served(None, **{"/jobs": {"Proxy": "http://127.0.0.1:8770"}}), None),  # a path isn't the root
        (served("http://192.0.2.7:8501"), None),  # another machine
        (served("http://127.0.0.1:8501/finance"), None),  # a path on the page's side
        ({"Web": {f"{HOST.upper()}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:5103"}}}}}, 5103),
        ({}, None),  # nothing served
    ],
)
def test_the_address_root_is_read_from_the_serve_config(config, port):
    assert phone.forwarded_port(config, HOST) == port


def test_only_a_port_nothing_listens_on_is_taken_over(monkeypatch):
    live = {8770, 9000}
    monkeypatch.setattr(phone, "listening", lambda port: port in live)
    assert phone.dead_port(served("http://127.0.0.1:8501"), HOST, 5103) == 8501  # the old agent's port, gone
    assert phone.dead_port(served("http://127.0.0.1:5103"), HOST, 5103) is None  # this page's already
    assert phone.dead_port(served("http://127.0.0.1:9000"), HOST, 5103) is None  # a program that answers
    assert phone.dead_port(served(None), HOST, 5103) is None  # nothing served at the root: set up again
    assert phone.dead_port(served("http://127.0.0.1:8501", funnel=True), HOST, 5103) is None  # on the internet


@pytest.fixture
def tailnet(monkeypatch):
    """A Mac with phone access set up, its address forwarded to the old agent's port 8501, which
    nothing listens on, and a record of what was asked of Tailscale."""
    state = {"access": dict(ACCESS), "config": served("http://127.0.0.1:8501"), "live": set(), "pointed": []}
    monkeypatch.setattr(phone, "read_access", lambda path=None: state["access"])
    monkeypatch.setattr(phone, "find_tailscale", lambda: "/usr/local/bin/tailscale")
    monkeypatch.setattr(phone, "serve_config", lambda cli: state["config"])
    monkeypatch.setattr(phone, "listening", lambda port: port in state["live"])
    monkeypatch.setattr(phone, "point", lambda cli, port: state["pointed"].append(port) or True)
    return state


def test_a_page_takes_over_an_address_that_forwards_to_nothing(tailnet):
    said = phone.follow(5103)
    assert tailnet["pointed"] == [5103]
    assert "port 8501" in said and "5103" in said


@pytest.mark.parametrize(
    "change",
    [
        lambda state: state.update(access=None),  # phone access isn't set up
        lambda state: state.update(access={"login": "pat@example.com"}),  # a server: no host
        lambda state: state.update(config=None),  # Tailscale can't say what it serves
        lambda state: state["live"].add(8501),  # another page, or anything, answers there
        lambda state: state.update(config=served("http://127.0.0.1:5103")),  # this page has it
        lambda state: state.update(config=served("http://127.0.0.1:8501", funnel=True)),
    ],
)
def test_a_page_leaves_the_address_be_otherwise(tailnet, change):
    change(tailnet)
    assert phone.follow(5103) is None
    assert tailnet["pointed"] == []


def test_pointing_moves_the_root_alone_on_the_tailnet(monkeypatch):
    commands = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: commands.append(command) or
                        subprocess.CompletedProcess(command, 0))
    assert phone.point("tailscale", 5103)
    assert commands == [["tailscale", "serve", "--bg", "--https=443", "--set-path=/", "http://127.0.0.1:5103"]]


def test_listening_tells_a_port_in_use_from_a_free_one():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert phone.listening(port)
    assert not phone.listening(port)


def test_a_page_keeps_one_watch_on_its_address(monkeypatch):
    checked = threading.Event()
    monkeypatch.setattr(phone, "follow", lambda port: checked.set())
    assert phone.keep_following(48123, every=3600)
    assert checked.wait(5)
    assert not phone.keep_following(48123, every=3600)  # Streamlit loading phone.py afresh


def test_the_job_search_tools_old_worker_is_replaced_by_one_that_removes_itself(tmp_path):
    assert phone.install_service_worker(tmp_path)
    worker = (tmp_path / "sw.js").read_text()
    assert "skipWaiting" in worker and "registration.unregister()" in worker
    assert 'startsWith("bs-job-search-")' in worker and "caches.delete" in worker
    assert '"fetch"' not in worker  # it answers nothing: every request goes to the Mac
    assert not phone.install_service_worker(tmp_path)  # there already
    assert not phone.install_service_worker(tmp_path / "missing")


# ---------------------------------------------------------------- the diagnosis

@pytest.mark.parametrize(
    ("ts", "access", "loaded", "forwarded", "answers", "says"),
    [
        (HEALTHY, None, False, None, False, "isn't set up"),
        ({**HEALTHY, "found": False}, ACCESS, True, 8501, True, "Tailscale isn't on this Mac"),
        ({**HEALTHY, "running": False}, ACCESS, True, 8501, True, "Tailscale is off"),
        ({**HEALTHY, "host": "othermac.example-tailnet.ts.net"}, ACCESS, True, 8501, True, "name changed"),
        (HEALTHY, ACCESS, True, False, True, "isn't forwarding"),
        (HEALTHY, ACCESS, True, 8501, False, "isn't answering on port 8501"),  # the agent's page is down
        (HEALTHY, ACCESS, True, None, False, "isn't answering on port 8501"),
        (HEALTHY, ACCESS, False, 5103, False, "Open Personal Finance on this Mac"),  # whatever ran it stopped
        (HEALTHY, ACCESS, True, 5103, False, "to port 5103"),
    ],
)
def test_the_diagnosis_says_what_is_wrong(ts, access, loaded, forwarded, answers, says):
    assert says in phone.diagnose(ts, access, loaded, forwarded, answers)


def test_a_working_setup_has_no_diagnosis():
    assert phone.diagnose(HEALTHY, ACCESS, True, 8501, True) is None
    assert phone.diagnose(HEALTHY, ACCESS, True, None, True) is None  # serve status unknown isn't a fault
    assert phone.diagnose(HEALTHY, ACCESS, False, 5103, True) is None  # Flightdeck runs it, no LaunchAgent


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
    box there: the tab's and the home screen's icons are the square filled to the edge, with no
    alpha channel at all, like seanbattey.com's and Job Search's home-screen icons, which Safari
    shows as they are. The Desktop app's rounded square keeps its clear corners."""
    import io

    from PIL import Image

    assert icon.pixel(0.5, 0.5, 64, square=True)[3] == 255  # a corner, filled
    # Lighter at the top, as Job Search's icon is: Safari drew a white outline around the flat
    # square in its tab bar, and none around this.
    top, bottom = icon.pixel(32, 0.5, 64, square=True), icon.pixel(32, 63.5, 64, square=True)
    assert top[:3] != bottom[:3] and sum(top[:3]) > sum(bottom[:3])
    assert all(abs(a - b) <= 1 for a, b in zip(top[:3], icon.TOP))
    assert all(abs(a - b) <= 1 for a, b in zip(bottom[:3], icon.BACKGROUND))
    assert icon.pixel(0.5, 0.5, 64)[3] == 0  # the Desktop app's rounded square keeps its clear corner
    for size in (icon.FAVICON_SIZE, icon.TOUCH_SIZE):
        image = Image.open(io.BytesIO(icon.draw(size, square=True)))
        assert image.mode == "RGB" and image.size == (size, size)
        assert image.getpixel((0, 0)) == icon.TOP  # the top corner, filled with the lighter brown
        assert image.getpixel((0, size - 1)) == icon.BACKGROUND  # the bottom one, the site's brown
    rounded = Image.open(io.BytesIO(icon.draw(64)))
    assert rounded.mode == "RGBA" and rounded.getpixel((0, 0))[3] == 0


def test_the_iconset_has_every_size_macos_wants(tmp_path):
    icon.write_iconset(tmp_path / "icon.iconset")
    names = sorted(path.name for path in (tmp_path / "icon.iconset").iterdir())
    assert "icon_16x16.png" in names and "icon_512x512@2x.png" in names and len(names) == 12
    assert png_size((tmp_path / "icon.iconset" / "icon_512x512@2x.png").read_bytes()) == (1024, 1024)
