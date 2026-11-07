"""Tests for the Windows install that need no Windows: install.ps1 parses for Windows PowerShell 5.1
and PowerShell 7, says how it's used and stops on anything but Windows, and, with PowerShell's
own commands stood in for, registers bs-personal-finance under HKCU and makes the shortcut;
launch.pyw starts, finds and stops a page (with a stand-in for uv), waits for one still starting,
and stops one left idle; the page restarts under it; icon.py writes a Windows icon. The ones that
run install.ps1 need PowerShell 7 (pwsh), which GitHub's runners have. CI's windows job runs the
install itself on Windows, and these tests there too, so text files are read as UTF-8 by name:
Windows's Python reads them in the PC's code page otherwise."""

import ast
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

import icon

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.ps1"
LAUNCHER = ROOT / "launch.pyw"
ONE_LINE = ('powershell -ExecutionPolicy Bypass -c '
            '"irm https://raw.githubusercontent.com/BatteyS/BS-Personal-Finance/main/install.ps1 | iex"')
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(not PWSH, reason="needs PowerShell 7 (pwsh)")
COPY = r"C:\Users\pat\personal-finance"  # a made-up copy, for what the installer would make


def pwsh(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=120)


def quoted(text: str) -> str:
    """A PowerShell single-quoted string."""
    return "'" + text.replace("'", "''") + "'"


def installer_functions() -> str:
    """install.ps1 up to where it starts doing things: its settings and functions."""
    return INSTALLER.read_text(encoding="utf-8").split("\n# Run from inside a copy (-File install.ps1), that copy is used.\n")[0]


def run_functions(tmp_path: Path, test: str) -> list:
    """Run the installer's functions, then a test's PowerShell, which ends by printing JSON."""
    script = tmp_path / "test.ps1"
    script.write_text(installer_functions() + "\n" + test + "\n", encoding="utf-8")
    ran = pwsh("-File", str(script), env={"LOCALAPPDATA": r"C:\Users\pat\AppData\Local"})
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return json.loads(ran.stdout)


# ---------------------------------------------------------------- install.ps1

def test_the_one_line_in_the_readme_is_the_installers():
    assert ONE_LINE in (ROOT / "README.md").read_text(encoding="utf-8")
    assert ONE_LINE in INSTALLER.read_text(encoding="utf-8")


def test_the_windows_installer_is_ascii():
    """Windows PowerShell 5.1 reads a script file with no byte-order mark in the PC's code page."""
    INSTALLER.read_bytes().decode("ascii")


@needs_pwsh
def test_the_windows_installer_parses_without_powershell_7s_own_syntax():
    """No parse errors, and none of what only PowerShell 7 reads (&&, ||, ??, ?., a ? b : c): the
    one line runs it in Windows PowerShell 5.1."""
    ran = pwsh("-Command", "$t = $null; $e = $null; "
               f"[void][System.Management.Automation.Language.Parser]::ParseFile({quoted(str(INSTALLER))}, [ref]$t, [ref]$e); "
               "$seven = $t | Where-Object { $_.Kind -in 'AndAnd', 'OrOr', 'QuestionQuestion', 'QuestionQuestionEquals', "
               "'QuestionDot', 'QuestionLBracket', 'QuestionMark' }; "
               "@{ errors = @($e | ForEach-Object Message); seven = @($seven | ForEach-Object Text) } | ConvertTo-Json")
    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout) == {"errors": [], "seven": []}


@needs_pwsh
def test_the_windows_installer_explains_itself():
    shown = pwsh("-Command", f"Get-Help {quoted(str(INSTALLER))} -Full | Out-String -Width 300")
    assert shown.returncode == 0
    assert ONE_LINE in shown.stdout
    assert "-Dir" in shown.stdout and "-NoOpen" in shown.stdout


@needs_pwsh
def test_the_windows_installer_refuses_an_unknown_option():
    refused = pwsh("-File", str(INSTALLER), "-Fast")
    assert refused.returncode == 1 and "Fast" in refused.stdout + refused.stderr


@needs_pwsh
@pytest.mark.skipif(os.name == "nt", reason="on Windows this would really install")
@pytest.mark.parametrize("route", ["file", "one line"])
def test_the_windows_installer_is_for_windows_only(route):
    """From a copy (-File) and as the one line runs it (the script's text into iex): either way it
    stops before doing anything, says why, and exits 1."""
    if route == "file":
        stopped = pwsh("-File", str(INSTALLER), "-NoOpen")
    else:
        stopped = pwsh("-Command", f"Get-Content -Raw {quoted(str(INSTALLER))} | Invoke-Expression")
    assert stopped.returncode == 1
    assert "This installer is for Windows" in stopped.stdout
    assert "picks up where this stopped" in stopped.stdout


@needs_pwsh
def test_the_installer_registers_the_launch_link_for_this_user(tmp_path):
    """Register-Link with the registry's commands stood in for: it removes the old key, then writes
    HKCU\\Software\\Classes\\bs-personal-finance as a URL protocol whose open command is the shortcut's,
    making the command's key before setting values on its parents (New-Item -Force would empty them)."""
    calls = run_functions(tmp_path, f"""
$script:calls = New-Object System.Collections.ArrayList
function Test-Path {{ param([string]$LiteralPath, [string]$Path, $PathType) return $true }}
function Remove-Item {{ param([string]$LiteralPath, [switch]$Recurse, [switch]$Force)
    [void]$script:calls.Add(@('Remove-Item', $LiteralPath)) }}
function New-Item {{ param([string]$Path, $Value, [switch]$Force, $ItemType)
    [void]$script:calls.Add(@('New-Item', $Path, "$Value")) }}
function Set-ItemProperty {{ param([string]$LiteralPath, [string]$Name, $Value)
    [void]$script:calls.Add(@('Set-ItemProperty', $LiteralPath, $Name, "$Value")) }}
function New-ItemProperty {{ param([string]$LiteralPath, [string]$Name, $Value, $PropertyType, [switch]$Force)
    [void]$script:calls.Add(@('New-ItemProperty', $LiteralPath, $Name, "$Value", $PropertyType)) }}
Register-Link (Get-Launcher {quoted(COPY)})
ConvertTo-Json -InputObject $script:calls -Depth 3
""")
    key = r"HKCU:\Software\Classes\bs-personal-finance"
    command = rf'"{COPY}\.venv\Scripts\pythonw.exe" "{COPY}\launch.pyw"'
    assert calls[0] == ["Remove-Item", key]
    assert calls[1] == ["New-Item", key + r"\shell\open\command", command]
    assert ["Set-ItemProperty", key, "(default)", "URL:Personal Finance"] in calls
    assert ["New-ItemProperty", key, "URL Protocol", "", "String"] in calls
    assert ["New-ItemProperty", key + r"\shell\open", "FriendlyAppName", "Personal Finance", "String"] in calls
    assert ["New-Item", key + r"\DefaultIcon",
            r"C:\Users\pat\AppData\Local\Personal Finance\Personal Finance.ico,0"] in calls
    assert not any(call[0] == "New-Item" and call[1] in (key, key + r"\shell\open") for call in calls)


@needs_pwsh
def test_the_installer_makes_a_shortcut_that_runs_the_launcher_with_no_window(tmp_path):
    """New-Shortcut with WScript.Shell stood in for: the shortcut runs the copy's pythonw.exe on
    launch.pyw, in the copy, with the app's icon, and Test-OurShortcut knows it again."""
    saved = run_functions(tmp_path, f"""
$script:saved = New-Object System.Collections.ArrayList
function Test-Path {{ param([string]$LiteralPath) return $LiteralPath -like '*.ico' }}
function Remove-Item {{ }}
function New-Object {{
    param([string]$ComObject)
    $shell = [pscustomobject]@{{ }}
    $shell | Add-Member ScriptMethod CreateShortcut {{
        param($Path)
        $shortcut = [pscustomobject]@{{ Path = $Path; TargetPath = ''; Arguments = ''; WorkingDirectory = '';
            Description = ''; IconLocation = '' }}
        $shortcut | Add-Member ScriptMethod Save {{ [void]$script:saved.Add($this) }}
        $shortcut
    }}
    $shell
}}
New-Shortcut 'C:\\Users\\pat\\Desktop\\Personal Finance.lnk' (Get-Launcher {quoted(COPY)})
$ours = $script:saved[0]
function New-Object {{
    param([string]$ComObject)
    $shell = [pscustomobject]@{{ }}
    $shell | Add-Member ScriptMethod CreateShortcut {{ param($Path) $ours }}
    $shell
}}
[void]$script:saved.Add(@{{ ours = (Test-OurShortcut 'C:\\Users\\pat\\Desktop\\Personal Finance.lnk') }})
ConvertTo-Json -InputObject $script:saved -Depth 3
""")
    shortcut, known = saved
    assert shortcut["Path"] == r"C:\Users\pat\Desktop\Personal Finance.lnk"
    assert shortcut["TargetPath"] == rf"{COPY}\.venv\Scripts\pythonw.exe"
    assert shortcut["Arguments"] == rf'"{COPY}\launch.pyw"'
    assert shortcut["WorkingDirectory"] == COPY
    assert shortcut["IconLocation"] == r"C:\Users\pat\AppData\Local\Personal Finance\Personal Finance.ico,0"
    assert known == {"ours": True}


# ---------------------------------------------------------------- launch.pyw

def load_launcher(path: Path = LAUNCHER) -> types.ModuleType:
    """launch.pyw as a module. Off Windows, Python imports no .pyw by name, so it's loaded by path."""
    loader = importlib.machinery.SourceFileLoader("launch_pyw", str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("launch_pyw", loader))
    loader.exec_module(module)
    return module


def tcp_row_v4(state: int, local_port: int, remote_port: int) -> bytes:
    """A MIB_TCPROW_OWNER_PID: state, local address and port, remote address and port, pid."""
    return struct.pack("<II", state, 0x0100007F) + struct.pack(">H", local_port) + b"\0\0" \
        + struct.pack("<I", 0x0100007F) + struct.pack(">H", remote_port) + b"\0\0" + struct.pack("<I", 1234)


def tcp_row_v6(state: int, local_port: int, remote_port: int) -> bytes:
    """A MIB_TCP6ROW_OWNER_PID: local address, scope and port, remote address, scope and port, state, pid."""
    loopback = bytes(15) + b"\1"
    return (loopback + struct.pack("<I", 0) + struct.pack(">H", local_port) + b"\0\0"
            + loopback + struct.pack("<I", 0) + struct.pack(">H", remote_port) + b"\0\0"
            + struct.pack("<II", state, 1234))


def test_the_launcher_counts_the_pages_side_of_each_open_tab():
    """Windows's table holds both ends of a tab's connection to localhost: the page's (local port
    8501) counts, the browser's (remote port 8501) and a listening socket don't."""
    launcher = load_launcher()
    for (family, size, port_at, state_at), row in zip(launcher.TCP_TABLES, [tcp_row_v4, tcp_row_v6]):
        rows = [row(5, 8501, 50123), row(5, 50123, 8501), row(2, 8501, 0), row(5, 8501, 50124), row(5, 8502, 50125)]
        assert all(len(one) == size for one in rows)
        table = struct.pack("<I", len(rows)) + b"".join(rows)
        assert launcher.established(table, size, port_at, state_at, 8501) == 2


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.skipif(os.name == "nt", reason="the stand-in for uv is a script, which Windows won't run as uv.exe")
def test_the_launcher_starts_a_page_opens_it_again_and_stops_it(tmp_path):
    """The whole launcher, in a copy of its own, with a stand-in for uv that answers Streamlit's
    health check: the first start starts a page in the background, the second finds it, --stop
    stops it."""
    copy = tmp_path / "copy"
    copy.mkdir()
    shutil.copy(LAUNCHER, copy / "launch.pyw")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    uv = bin_ / "uv"
    uv.write_text(f"""#!{sys.executable}
import http.server, sys
args = sys.argv[1:]
if "streamlit" in args:
    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/_stcore/health" else 404)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *args):
            pass
    http.server.HTTPServer(("127.0.0.1", int(args[args.index("--server.port") + 1])), Health).serve_forever()
""")
    uv.chmod(0o755)
    port = free_port()
    env = {**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "LOCALAPPDATA": str(tmp_path / "local"),
           "PF_PORT": str(port), "PF_NO_BROWSER": "1", "PF_NO_UPDATE": "1"}
    pidfile = copy / ".page.pid"

    def launch(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(copy / "launch.pyw"), *args], env=env, capture_output=True,
                              text=True, timeout=60)

    try:
        first = launch()
        assert first.returncode == 0, first.stderr
        assert first.stdout.strip() == f"http://localhost:{port}/"
        pid, recorded_port = map(int, pidfile.read_text().split())
        assert recorded_port == port

        again = launch()
        assert again.stdout.strip() == f"http://localhost:{port}/"
        assert pidfile.read_text().split()[0] == str(pid)  # the same page, not a second one

        stopped = launch("--stop")
        assert stopped.stdout.strip() == f"Stopped the page on port {port}."
        assert not pidfile.exists()
        launcher = load_launcher(copy / "launch.pyw")
        for _ in range(50):
            if not launcher.answers(port, 0.2):
                break
            time.sleep(0.1)
        assert not launcher.answers(port, 0.2)
        assert launch("--stop").stdout.strip() == "This copy's page isn't running."
        assert "starting on port" in (tmp_path / "local" / "Personal Finance" / "Personal Finance.log").read_text(encoding="utf-8")
    finally:
        if pidfile.exists():
            os.kill(int(pidfile.read_text().split()[0]), 15)


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    """launch.pyw, its record and log in a temporary folder, with uv found and no waiting."""
    module = load_launcher()
    monkeypatch.setattr(module, "PIDFILE", tmp_path / ".page.pid")
    monkeypatch.setattr(module, "LOG", tmp_path / "logs" / "Personal Finance.log")
    monkeypatch.setattr(module.shutil, "which", lambda name, path=None: "uv")
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(module, "notify", lambda text: None)
    module.LOG.parent.mkdir(parents=True)
    return module


def test_a_second_click_waits_for_the_page_the_first_is_starting(launcher, monkeypatch):
    """A record from moments ago whose process runs but doesn't answer yet is a page still
    starting: it's waited for and opened, never started a second time."""
    launcher.record(os.getpid(), 8599)
    answers = iter([False, False, False, True])
    monkeypatch.setattr(launcher, "answers", lambda port, timeout=2: next(answers))
    opened = []
    monkeypatch.setattr(launcher, "open_page", opened.append)
    monkeypatch.setattr(launcher, "start_on", lambda *args: pytest.fail("started a second page"))
    assert launcher.launch([]) == 0 and opened == [8599]


def test_an_old_record_of_a_page_that_doesnt_answer_starts_a_new_one(launcher, monkeypatch):
    """An old record whose pid now belongs to something else (say, after a restart of Windows)."""
    launcher.record(os.getpid(), 8599)
    os.utime(launcher.PIDFILE, (time.time() - 3600, time.time() - 3600))
    monkeypatch.setattr(launcher, "answers", lambda port, timeout=2: False)
    monkeypatch.setattr(launcher, "listening", lambda port: port == launcher.FIRST_PORT)  # something else has 8501
    monkeypatch.setattr(launcher, "update_copy", lambda uv, env: None)
    started, opened = [], []
    monkeypatch.setattr(launcher, "start_on", lambda port, env: started.append(port))
    monkeypatch.setattr(launcher, "open_page", opened.append)
    assert launcher.launch([]) == 0
    assert started == opened == [launcher.FIRST_PORT + 1]
    assert not launcher.PIDFILE.exists()


class Page:
    """A stand-in for the page's process: it keeps running until stopped, or stops after `runs`
    minutes by itself."""
    pid = 4242

    def __init__(self, runs: int | None = None):
        self.minutes, self.runs, self.stopped = 0, runs, False

    def wait(self, timeout=None):
        if self.stopped or (self.runs is not None and self.minutes >= self.runs):
            return 0
        self.minutes += 1
        raise subprocess.TimeoutExpired("uv", timeout)


def test_the_watcher_stops_a_page_twenty_minutes_after_its_last_tab_closes(launcher, monkeypatch):
    page = Page()
    tabs = iter([1] * 5 + [0] * 30)
    monkeypatch.setattr(launcher, "spawn", lambda arguments, env, flags=0: page)
    monkeypatch.setattr(launcher, "draw_icon", lambda uv, env: None)
    monkeypatch.setattr(launcher, "connections", lambda port: next(tabs))
    monkeypatch.setattr(launcher, "stop", lambda pid: setattr(page, "stopped", True))
    launcher.serve(8501, "uv", {})
    assert page.stopped and page.minutes == 5 + 20
    assert not launcher.PIDFILE.exists()
    assert "no open tabs for 20 minutes: stopping" in launcher.LOG.read_text(encoding="utf-8")


def test_the_watcher_ends_with_its_page_and_leaves_a_newer_pages_record(launcher, monkeypatch):
    """After an update's restart the old page ends; a page started since keeps its record."""
    page = Page(runs=3)

    def newer_page_starts(uv, env):
        launcher.record(5151, 8501)

    monkeypatch.setattr(launcher, "spawn", lambda arguments, env, flags=0: page)
    monkeypatch.setattr(launcher, "draw_icon", newer_page_starts)
    monkeypatch.setattr(launcher, "connections", lambda port: 1)
    launcher.serve(8501, "uv", {})
    assert page.minutes == 3 and not page.stopped
    assert launcher.recorded() == (5151, 8501)


def test_the_launcher_says_what_to_do_without_uv(launcher, monkeypatch, capsys):
    monkeypatch.setattr(launcher.shutil, "which", lambda name, path=None: None)
    with pytest.raises(SystemExit) as stopped:
        launcher.launch([])
    assert stopped.value.code == 1
    assert "needs uv" in capsys.readouterr().err


def test_the_launcher_finds_uv_and_git_where_their_installers_put_them(launcher, monkeypatch, tmp_path):
    """A shortcut's PATH is from when Windows signed in, perhaps before the installer ran."""
    for folder in [".local/bin", "AppData/Local/Programs/MinGit/cmd"]:
        (tmp_path / folder).mkdir(parents=True)
    monkeypatch.setattr(launcher.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(launcher, "LOCAL", tmp_path / "AppData" / "Local")
    env = launcher.environment()
    assert env["PATH"].split(os.pathsep)[:2] == [str(tmp_path / ".local" / "bin"),
                                                 str(tmp_path / "AppData" / "Local" / "Programs" / "MinGit" / "cmd")]
    assert env["PYTHONUTF8"] == "1"


# ---------------------------------------------------------------- the rest

def test_icon_py_writes_a_windows_icon_with_each_size_as_a_png():
    data = icon.ico()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind, count) == (0, 1, len(icon.ICO_SIZES))
    for index, size in enumerate(icon.ICO_SIZES):
        width, height, _, _, planes, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        assert width == height == size % 256 and (planes, bits) == (1, 32)
        image = data[offset:offset + length]
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        assert struct.unpack_from(">II", image, 16) == (size, size)  # IHDR's width and height
    assert offset + length == len(data)


def dashboard_functions(*names: str, **namespace) -> dict:
    """Some of dashboard.py's functions, compiled from its source alone against stand-ins: importing
    it would run the page."""
    tree = ast.parse((ROOT / "dashboard.py").read_text(encoding="utf-8"))
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=body, type_ignores=[]), "dashboard.py", "exec"), namespace)
    return namespace


def test_the_page_restarts_under_the_windows_launcher(tmp_path):
    """On Windows (no os.getuid, no launchctl, no ps, no /bin/sh), the Update button's restart
    starts a fresh launch.pyw under pythonw.exe, two seconds on and opening no tab, then ends
    this page."""
    (tmp_path / ".page.pid").write_text("4242 8501\n")
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pythonw.exe").touch()
    started, timers = [], []
    fake_os = types.SimpleNamespace(name="nt", environ={"PATH": "x"}, getpid=lambda: 99, kill=lambda pid, sig: None)
    fake_subprocess = types.SimpleNamespace(
        DEVNULL=-3, DETACHED_PROCESS=8, CREATE_NEW_PROCESS_GROUP=512,
        run=lambda *args, **kwargs: pytest.fail(f"ran {args[0]}"),
        Popen=lambda args, **kwargs: started.append((args, kwargs)))
    fake_threading = types.SimpleNamespace(Timer=lambda delay, action: types.SimpleNamespace(
        start=lambda: timers.append(delay)))
    found = dashboard_functions("how_started", "restart_page", os=fake_os, subprocess=fake_subprocess,
                                threading=fake_threading, signal=types.SimpleNamespace(SIGTERM=15), ROOT=tmp_path,
                                Path=Path, sys=types.SimpleNamespace(executable=str(scripts / "python.exe")),
                                AGENT="io.github.batteys.bs-personal-finance.page")
    assert found["restart_page"]() == "launcher"
    [(args, kwargs)] = started
    assert args == [str(scripts / "pythonw.exe"), str(tmp_path / "launch.pyw"), "--after", "2"]
    assert kwargs["env"]["PF_NO_BROWSER"] == "1" and kwargs["creationflags"] == 8 | 512
    assert not (tmp_path / ".page.pid").exists()
    assert timers == [1.0]
