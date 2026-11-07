"""Opens the page on Windows, as launch.sh does on a Mac: starts it in the background if it is not
already running, waits until it answers, then opens it in your browser. Personal Finance in the
Start menu and on the Desktop runs this, and so does the link bs-personal-finance://launch
(install.ps1 makes them). From a terminal, in the copy's folder:

    .venv\\Scripts\\python.exe launch.pyw           # start the page if need be, then open it
    .venv\\Scripts\\python.exe launch.pyw --stop    # stop the page this copy started

The shortcuts run it with pythonw.exe, the Python that opens no window, and everything it starts
runs without one too, so no console window ever shows. Streamlit can't tell when its last tab
closes, so the launcher watches the page's open connections (each tab holds one) and stops the
page once it has had none for twenty minutes. It takes the first free port from 8501 up, and a
page this copy already started is opened rather than started twice, even while it's still
starting. What it prints goes to %LOCALAPPDATA%\\Personal Finance\\Personal Finance.log. When it
can't start, it exits 1 and says why in a message box (on stderr, from a terminal).

It needs only the standard library: the page runs through `uv run`, which brings the packages up
to date first, as on the Mac. --after SECONDS waits that long first; the page's restart after an
update uses it. Off Windows the few parts that are Windows's own fall back to what a Mac or Linux
has, which is how the tests run it.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent
WINDOWS = os.name == "nt"
LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
LOG = LOCAL / "Personal Finance" / "Personal Finance.log"
PIDFILE = ROOT / ".page.pid"  # "<pid> <port>" of the page this copy started; gitignored
IDLE_MINUTES = int(os.environ.get("PF_IDLE_MINUTES") or 20)
FIRST_PORT = int(os.environ.get("PF_PORT") or 8501)
LAST_PORT = FIRST_PORT + 9
# Windows's flags for starting a program: with a console no one sees, which the programs it starts
# share; with no console at all; on its own, so it outlives this launcher. 0 elsewhere.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NO_CONSOLE = getattr(subprocess, "DETACHED_PROCESS", 0)
OWN_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
READYING = "Getting the page ready. The first start, or one after an update, can take a minute."


def fail(message: str) -> NoReturn:
    """Say why the page can't open, and exit 1: on stderr from a terminal, in a message box from
    the Start menu (pythonw.exe has no stderr)."""
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    elif WINDOWS:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Personal Finance", 0x30)  # MB_ICONWARNING
    sys.exit(1)


def log(line: str) -> None:
    with LOG.open("a", encoding="utf-8") as file:
        file.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def trim_log() -> None:
    """Keep the log from growing forever: past 1 MB, keep its last 200 KB. A page running holds
    the log open, which Windows won't let anything replace: then it waits for another start."""
    try:
        if LOG.stat().st_size > 1_000_000:
            kept = LOG.read_bytes()[-200_000:]
            LOG.with_suffix(".tmp").write_bytes(kept)
            os.replace(LOG.with_suffix(".tmp"), LOG)
    except OSError:
        pass


def tail(lines: int) -> str:
    try:
        return "\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def environment() -> dict[str, str]:
    """What the launcher's programs run with. PATH gains the places uv and git install themselves,
    since a shortcut's PATH is the one Windows had at sign-in, maybe from before the installer ran;
    Python runs in UTF-8 mode, as it does on a Mac (launch.sh sets LANG), so files read the same."""
    home = Path.home()
    folders = [home / ".local" / "bin", home / ".cargo" / "bin",
               LOCAL / "Programs" / "Git" / "cmd", LOCAL / "Programs" / "MinGit" / "cmd"]
    if os.environ.get("ProgramFiles"):
        folders.append(Path(os.environ["ProgramFiles"]) / "Git" / "cmd")
    path = os.pathsep.join([*(str(folder) for folder in folders if folder.is_dir()), os.environ.get("PATH", "")])
    return {**os.environ, "PATH": path, "PYTHONUTF8": "1"}


def run(arguments: list[str], env: dict[str, str]) -> None:
    """Run a program to the end, what it prints going to the log; a failure is only logged."""
    try:
        with LOG.open("a", encoding="utf-8") as output:
            subprocess.run(arguments, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=output,
                           stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    except OSError as error:
        log(f"{arguments[0]} didn't run: {error}")


def spawn(arguments: list[str], env: dict[str, str], flags: int = NO_CONSOLE | OWN_GROUP) -> subprocess.Popen:
    """Start a program that outlives this launcher, with no window, printing into the log."""
    with LOG.open("a", encoding="utf-8") as output:
        options = {"cwd": ROOT, "env": env, "stdin": subprocess.DEVNULL, "stdout": output, "stderr": subprocess.STDOUT}
        if WINDOWS:
            return subprocess.Popen(arguments, creationflags=flags, **options)
        return subprocess.Popen(arguments, start_new_session=True, **options)


def pythonw() -> str:
    """The Python that opens no window, beside this one in the copy's .venv\\Scripts, or this one."""
    beside = Path(sys.executable).with_name("pythonw.exe")
    return str(beside) if WINDOWS and beside.exists() else sys.executable


def notify(text: str) -> None:
    """A notification by the clock, from a PowerShell with no window; nothing if it can't."""
    if not WINDOWS:
        return
    text = text.replace("'", "''")
    script = ("Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
              "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
              f"$n.Visible = $true; $n.ShowBalloonTip(10000, 'Personal Finance', '{text}', 'Info'); "
              "Start-Sleep -Seconds 12; $n.Dispose()")
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    except OSError:
        pass


# ---------------------------------------------------------------- the page and its port

def listening(port: int) -> bool:
    """Whether anything accepts connections on the port, at 127.0.0.1 or ::1 (localhost is both)."""
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except TimeoutError:
            return True  # something is there, only slow
        except OSError:
            continue
    return False


def answers(port: int, timeout: float = 2) -> bool:
    """Whether a page answers on the port: Streamlit's health check says so. Never through a proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/_stcore/health", timeout=timeout) as answer:
            return answer.status == 200
    except OSError:
        return False


def recorded() -> tuple[int, int] | None:
    """The pid and port in .page.pid, or None without a record."""
    try:
        pid, port = PIDFILE.read_text().split()[:2]
        return int(pid), int(port)
    except (OSError, ValueError):
        return None


def record(pid: int, port: int) -> None:
    PIDFILE.write_text(f"{pid} {port}\n")


def starting() -> bool:
    """Whether the record is from moments ago: a page another click started, still getting ready."""
    try:
        return time.time() - PIDFILE.stat().st_mtime < 300
    except OSError:
        return False


def alive(pid: int) -> bool:
    """Whether a process is running. On Windows os.kill would end it, so the process is asked."""
    if not WINDOWS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def stop(pid: int) -> None:
    """Stop the page and what it started: `uv run`, and the Python under it."""
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


# ---------------------------------------------------------------- open tabs

ESTABLISHED = 5  # MIB_TCP_STATE_ESTAB
TCP_TABLE_OWNER_PID_CONNECTIONS = 4
# For IPv4 and IPv6: the address family, the size of a row of GetExtendedTcpTable's table, and
# where in a row the local port (two bytes, network order) and the state sit.
TCP_TABLES = [(2, 24, 8, 0), (23, 56, 20, 48)]  # AF_INET, MIB_TCPROW_OWNER_PID; AF_INET6, MIB_TCP6ROW_OWNER_PID


def established(table: bytes, row_size: int, port_at: int, state_at: int, port: int) -> int:
    """How many rows of a TCP table are established connections whose local port is `port`: the
    page's side of each open tab's connection."""
    (rows,) = struct.unpack_from("<I", table, 0)
    found = 0
    for row in range(rows):
        start = 4 + row * row_size
        (state,) = struct.unpack_from("<I", table, start + state_at)
        (local_port,) = struct.unpack_from(">H", table, start + port_at)
        found += state == ESTABLISHED and local_port == port
    return found


def connections(port: int) -> int:
    """How many connections the page on this port has open: one per open tab, from Windows's own
    table of connections (what netstat shows, without netstat's translated words). Off Windows it
    says one, so a test's page is never stopped as idle."""
    if not WINDOWS:
        return 1
    import ctypes
    from ctypes import wintypes

    get_table = ctypes.WinDLL("iphlpapi").GetExtendedTcpTable
    get_table.restype = wintypes.DWORD
    total = 0
    for family, row_size, port_at, state_at in TCP_TABLES:
        size = wintypes.DWORD(0)
        table = None
        for _ in range(5):  # the table can grow between asking its size and reading it
            result = get_table(table, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_CONNECTIONS, 0)
            if result != 122:  # ERROR_INSUFFICIENT_BUFFER
                break
            table = ctypes.create_string_buffer(size.value)
        if result == 0 and table is not None:
            total += established(table.raw, row_size, port_at, state_at, port)
    return total


# ---------------------------------------------------------------- what the launcher does

def draw_icon(uv: str, env: dict[str, str]) -> None:
    """The phone's home-screen icon, served from Streamlit's static folder (gitignored), drawn again
    whenever icon.py is newer, as launch.sh does."""
    icon = ROOT / "static" / "apple-touch-icon.png"
    try:
        if icon.stat().st_mtime > (ROOT / "icon.py").stat().st_mtime:
            return
    except OSError:
        pass
    run([uv, "run", "--quiet", "icon.py", str(icon)], env)


def update_copy(uv: str, env: dict[str, str]) -> None:
    """A page starts on the newest version: bring this copy up to date first (updates.py: a
    fast-forward of main, never over a change of yours). PF_NO_UPDATE=1 skips it."""
    if os.environ.get("PF_NO_UPDATE"):
        return
    log("bringing the copy up to date")
    run([uv, "run", "--quiet", "updates.py", "update"], env)


def serve(port: int, uv: str, env: dict[str, str]) -> None:
    """--serve PORT, what start_on starts in the background: run the page and watch it. Once it has
    had no connections for IDLE_MINUTES, stop it. The watcher ends when its page does."""
    # uv and the Python it starts share one console no one sees. With none at all, Windows would
    # open a window for that Python.
    page = spawn([uv, "run", "streamlit", "run", "dashboard.py", "--server.port", str(port),
                  "--server.address", "localhost", "--server.headless", "true"], env, NO_WINDOW | OWN_GROUP)
    record(page.pid, port)
    draw_icon(uv, env)
    idle = 0
    while True:
        try:
            page.wait(timeout=60)
            break  # the page stopped: a restart, --stop, or a crash
        except subprocess.TimeoutExpired:
            pass
        if not PIDFILE.exists():
            record(page.pid, port)
        idle = 0 if connections(port) > 0 else idle + 1
        if idle >= IDLE_MINUTES:
            log(f"no open tabs for {IDLE_MINUTES} minutes: stopping")
            stop(page.pid)
            page.wait()
            break
    # Remove the record only while it's still this page's: a page started since keeps its own.
    if (recorded() or (None, None))[0] == page.pid:
        PIDFILE.unlink(missing_ok=True)


def wait_for(port: int) -> None:
    """Wait until the page answers on the port, saying so if it takes a while, or fail."""
    for waited in range(1, 601):  # half seconds: five minutes
        if answers(port, 1):
            return
        time.sleep(0.5)
        if waited == 8:
            notify(READYING)
        if waited >= 10 and not PIDFILE.exists():
            fail(f"The page didn't start. The last of what it printed:\n\n{tail(12)}\n\nAll of it is in {LOG}")
    fail(f"The page hasn't answered after five minutes. What it printed is in {LOG}")


def start_on(port: int, env: dict[str, str]) -> None:
    """Start the page on the port in the background, and wait until it answers."""
    log(f"starting on port {port}")
    spawn([pythonw(), str(ROOT / "launch.pyw"), "--serve", str(port)], env)
    wait_for(port)


def open_page(port: int) -> None:
    url = f"http://localhost:{port}/"
    if os.environ.get("PF_NO_BROWSER"):  # for tests, and the page's restart, whose tab is open
        print(url, flush=True)
        return
    try:
        if WINDOWS:
            os.startfile(url)
        elif not webbrowser.open(url):
            raise OSError
    except OSError:
        fail(f"The page is running, but no browser opened. Open {url} in yours.")


def stop_page() -> None:
    """--stop: stop the page this copy started."""
    found = recorded()
    if found and alive(found[0]):
        stop(found[0])
        print(f"Stopped the page on port {found[1]}.")
    else:
        print("This copy's page isn't running.")
    PIDFILE.unlink(missing_ok=True)


def launch(argv: list[str]) -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    trim_log()
    args = list(argv)
    if args[:1] == ["--after"] and len(args) >= 2:
        time.sleep(float(args[1]))
        args = args[2:]
    env = environment()
    uv = shutil.which("uv", path=env["PATH"])
    if not uv:
        fail("Personal Finance needs uv, which is not installed. Open PowerShell and run:\n\n"
             f'powershell -ExecutionPolicy Bypass -File "{ROOT / "install.ps1"}"\n\nIt installs what is missing.')
    if args[:1] == ["--serve"] and len(args) == 2:
        serve(int(args[1]), uv, env)
        return 0
    if args == ["--stop"]:
        stop_page()
        return 0
    if args:
        print(__doc__.split("\n\n")[1])
        return 2

    found = recorded()
    if found and alive(found[0]):
        port = found[1]
        if answers(port):
            open_page(port)
            return 0
        if starting():
            wait_for(port)
            open_page(port)
            return 0
    PIDFILE.unlink(missing_ok=True)
    update_copy(uv, env)
    for port in range(FIRST_PORT, LAST_PORT + 1):
        if not listening(port):
            start_on(port, env)
            open_page(port)
            return 0
    fail(f"Ports {FIRST_PORT} to {LAST_PORT} are all taken by other programs, so the page has nowhere to run. "
         "Quit one of them, then open Personal Finance again.")


def main(argv: list[str] | None = None) -> int:
    """The launcher, with anything it didn't foresee logged and said, since under pythonw.exe a
    traceback would go nowhere."""
    try:
        return launch(sys.argv[1:] if argv is None else argv)
    except Exception as error:  # noqa: BLE001 - the last word, under a Python with no console
        try:
            log("".join(traceback.format_exception(error)))
        except OSError:
            pass
        fail(f"Personal Finance ran into a problem: {error}\n\nWhat it printed is in {LOG}")


if __name__ == "__main__":
    sys.exit(main())
