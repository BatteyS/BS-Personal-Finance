"""Personal Finance on your phone: the page, reached over Tailscale, as an icon on the home screen.

    uv run phone.py enable    # set it up (sh install.sh --phone runs this)
    uv run phone.py status    # say whether it works, and why not
    uv run phone.py disable   # back to the Mac alone

Three parts, all on the Mac:

  * A LaunchAgent keeps the page running on port 8501 while you're logged in, restarting it if
    it stops, so the phone always finds it. Without phone access the page stops when idle; with
    it, the page stays up.
  * `tailscale serve` gives the Mac an HTTPS address on your tailnet, which only your own
    devices can open, and forwards it to the page, which still listens on the Mac alone.
    Tailscale names the account asking in a Tailscale-User-Login header. The page keeps the
    address on itself, whatever port it runs on: whoever started it (the Desktop app, the
    LaunchAgent, Flightdeck or a terminal), a running page points the address at its own port
    when it forwards to one nothing listens on (follow, below).
  * data/phone.json records the Mac's address and the Tailscale login set up here. The page
    answers a forwarded request only for that login (refusal, below).

It needs the Tailscale app on the Mac and the phone, signed in to the same account, and a copy
outside Documents, Desktop, Downloads and iCloud Drive, which macOS keeps background jobs out of.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import config
import icon

ROOT = Path(__file__).resolve().parent
PORT = 8501
LABEL = "io.github.batteys.bs-personal-finance.page"
PHONE = config.DATA_DIR / "phone.json"
LOGIN_VARIABLE = "FINANCE_TAILSCALE_LOGIN"  # a server's stand-in for phone.json: the login the page answers
LOG = Path.home() / "Library" / "Logs" / "Personal Finance.log"
TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
PROTECTED = ["Documents", "Desktop", "Downloads", "Library/Mobile Documents"]
FOLLOW_EVERY = 60  # seconds between a running page's checks of where its address forwards
LOOPBACK = re.compile(r"http://(?:127\.0\.0\.1|localhost|\[::1\]):(\d+)/?")  # a proxy to a port on this Mac


def say(text: str = "") -> None:
    print(text, flush=True)


# ---------------------------------------------------------------- the page's guard

def read_access(path: Path | None = None) -> dict | None:
    """What phone.json records; on a server with no phone.json, the login FINANCE_TAILSCALE_LOGIN
    names (docker-compose.yml sets it from .env); None when there is neither, so phone access
    isn't set up."""
    path = path or PHONE
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    login = os.environ.get(LOGIN_VARIABLE, "").strip()
    return {"login": login} if login else None


def refusal(headers, access: dict | None) -> str | None:
    """Why the page won't answer this request, or None when it will. A request on the Mac itself
    carries no Tailscale-User-Login; one Tailscale forwarded carries the account asking, which must
    be the one phone access was set up for."""
    login = (headers or {}).get("Tailscale-User-Login")
    if not login:
        return None
    if not access:
        return "Phone access isn't set up on this Mac, so the page answers the Mac alone."
    if login.lower() != str(access.get("login", "")).lower():
        return "This page answers only the Tailscale account it was set up for."
    return None


# ---------------------------------------------------------------- the LaunchAgent

def plist_path(label: str = LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def agent_plist(root: Path = ROOT, port: int = PORT, label: str = LABEL) -> bytes:
    """The LaunchAgent: run the page on `port` at login, and again whenever it stops."""
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": ["/bin/sh", str(root / "launch.sh"), "--serve-always", str(port)],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    })


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def agent_loaded(label: str = LABEL) -> bool:
    return launchctl("print", f"gui/{os.getuid()}/{label}").returncode == 0


def page_answers(port: int = PORT, timeout: float = 2) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=timeout) as answer:
            return answer.status == 200
    except OSError:
        return False


def install_agent(root: Path = ROOT, port: int = PORT) -> bool:
    """Write and load the LaunchAgent, then wait up to two minutes for the page to answer."""
    remove_agent()
    stop_launched_page(root)
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(agent_plist(root, port))
    result = launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
    if result.returncode != 0:
        say(f"launchd didn't take the agent: {result.stderr.strip()}")
        return False
    for _ in range(240):
        if page_answers(port):
            return True
        time.sleep(0.5)
    say(f"The page hasn't answered on port {port} after two minutes. What it printed is in {LOG}.")
    return False


def remove_agent() -> None:
    if agent_loaded():
        launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    plist_path().unlink(missing_ok=True)


def stop_launched_page(root: Path = ROOT) -> None:
    """Stop a page the Desktop app started, so the agent can have port 8501."""
    subprocess.run(["/bin/sh", str(root / "launch.sh"), "--stop"], capture_output=True)


# ---------------------------------------------------------------- Tailscale

def find_tailscale() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    return TAILSCALE_APP if os.path.isfile(TAILSCALE_APP) else None


def parse_status(data: dict) -> dict:
    """What `tailscale status --json` says matters here: running, this Mac's name, the account."""
    me = data.get("Self") or {}
    user = (data.get("User") or {}).get(str(me.get("UserID")), {})
    host = (me.get("DNSName") or "").rstrip(".").lower() or None
    return {"running": data.get("BackendState") == "Running", "host": host, "login": user.get("LoginName")}


def tailscale_status(cli: str | None) -> dict:
    out = {"found": bool(cli), "running": False, "host": None, "login": None}
    if not cli:
        return out
    try:
        result = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, timeout=15)
        return {**out, **parse_status(json.loads(result.stdout or "{}"))}
    except (OSError, subprocess.SubprocessError, ValueError):
        return out


def serve(cli: str, port: int = PORT) -> bool:
    """Point this Mac's tailnet address at the page. The first time, Tailscale may print a link to
    turn HTTPS on for the tailnet, and waits until it is."""
    say("The first time, Tailscale may print a login.tailscale.com link to turn on HTTPS for your tailnet:")
    say("open it (hold Command and double-click it) and click Enable. This waits up to 10 minutes.")
    say(f"$ tailscale serve --bg http://127.0.0.1:{port}")
    try:
        return subprocess.run([cli, "serve", "--bg", f"http://127.0.0.1:{port}"], timeout=600).returncode == 0
    except (OSError, subprocess.SubprocessError) as error:
        say(f"tailscale serve didn't run: {error}")
        return False


def serve_config(cli: str) -> dict | None:
    """What Tailscale serves (`tailscale serve status --json`), or None when it can't say."""
    try:
        result = subprocess.run([cli, "serve", "status", "--json"], capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout or "{}") if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def root_handler(config: dict, host: str) -> dict:
    """What Tailscale does with the root of this Mac's address, `https://<host>/`: HTTPS on 443,
    path /. Every other path and port, such as the job search tool's on 8443, isn't the page's."""
    web = next((site for name, site in (config.get("Web") or {}).items() if name.lower() == f"{host}:443"), {})
    return (web.get("Handlers") or {}).get("/") or {}


def forwarded_port(config: dict, host: str) -> int | None:
    """The port on this Mac that Tailscale forwards the address's root to, or None when it forwards
    it to none (nothing served, a folder, a path, another machine)."""
    found = LOOPBACK.fullmatch(root_handler(config, host).get("Proxy") or "")
    return int(found.group(1)) if found else None


def funnel_on(config: dict, host: str) -> bool:
    """Whether Funnel puts the address on the internet. The page never touches it then: a request
    from the internet carries no Tailscale-User-Login, which the guard reads as the Mac's own."""
    return any(on and name.lower() == f"{host}:443" for name, on in (config.get("AllowFunnel") or {}).items())


def listening(port: int) -> bool:
    """Whether anything on this Mac takes a connection on `port`: a page, or any other program.
    Only a refused connection says nothing is there."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        return True
    except ConnectionRefusedError:
        return False
    except OSError:
        return True


def point(cli: str, port: int) -> bool:
    """Forward the address's root to `port`, on the tailnet alone, leaving every other path and
    port Tailscale serves as it was."""
    try:
        command = [cli, "serve", "--bg", "--https=443", "--set-path=/", f"http://127.0.0.1:{port}"]
        return subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def unserve(cli: str) -> bool:
    try:
        return subprocess.run([cli, "serve", "--https=443", "off"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------- the address follows the page
# The page may run on any port: 8501 under the LaunchAgent, the first free one from 8501 under the
# Desktop app, whichever Flightdeck picks under Flightdeck. So each running page checks, as its
# first visitor arrives and every minute after, where the address forwards, and takes it over
# when nothing is there. It never takes it from a program that answers, so of two pages running
# at once the one the address has keeps it, and when that one stops, the other takes it within a
# minute. Tailscale always forwards straight to the page's own port, never through a proxy in
# front of it, which might not pass on Tailscale-User-Login, which the guard reads.

def dead_port(config: dict, host: str, port: int) -> int | None:
    """The port the address forwards to, when the page on `port` should take it over: nothing
    listens there. None to leave the address be: it forwards to this page already, to a program
    that answers (another copy of the page, or anything else), or to no port on this Mac, or
    Funnel is on."""
    if funnel_on(config, host):
        return None
    current = forwarded_port(config, host)
    if current is None or current == port or listening(current):
        return None
    return current


def follow(port: int) -> str | None:
    """One check by the page running on `port`: with phone access set up on this Mac, when its
    address forwards to a port nothing listens on, point it at this page. Says what it did, or
    None when it left the address be. A server has no phone.json and no host: its Tailscale
    sidecar serves its own config (docker-compose.yml)."""
    host = (read_access() or {}).get("host")
    cli = find_tailscale() if host else None
    config = serve_config(cli) if cli else None
    old = dead_port(config, host, port) if config is not None else None
    if old is None:
        return None
    if not point(cli, port):
        return (f"The phone's address forwards to port {old}, where nothing answers, and Tailscale "
                f"wouldn't move it to this page's port, {port}.")
    return f"The phone's address forwarded to port {old}, where nothing answers: now to this page's, {port}."


def keep_following(port: int, every: float = FOLLOW_EVERY) -> bool:
    """Run follow now and every `every` seconds after, in a thread of the page's own that ends with
    it, saying in the page's log what it changed. One per page: Streamlit may load this module
    afresh, so a second call finds the first thread by its name. Returns whether it started one."""
    name = f"phone-address-{port}"
    if any(thread.name == name for thread in threading.enumerate()):
        return False

    def watch() -> None:
        last = None
        while True:
            try:
                said = follow(port)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                said = f"Checking the phone's address failed: {error}"
            if said and said != last:  # once, not every minute
                say(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {said}")
            last = said
            time.sleep(every)

    threading.Thread(target=watch, name=name, daemon=True).start()
    return True


# Before it had an address of its own, the job search tool answered on this Mac's, and a phone that
# opened it there kept its service worker, which answers / and the icons from that tool's saved
# copy, so the phone shows neither this page nor its icons. A browser checks a worker's script,
# /sw.js, for a new version as it opens a page the worker answers: this one takes the old one's
# place, deletes that copy, removes itself, and reloads the page, which then comes from the Mac.
# Personal Finance registers no worker of its own, so a phone that never had one never runs it.
SERVICE_WORKER = """\
// Personal Finance has no service worker. This one replaces the job search tool's, which a phone
// kept from when that tool answered on this address: it deletes that tool's saved copy, removes
// itself, and reloads the page, which then comes from the Mac (phone.py).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil((async () => {
  for (const name of await caches.keys()) {
    if (name.startsWith("bs-job-search-")) await caches.delete(name);
  }
  await self.registration.unregister();
  for (const client of await self.clients.matchAll({ type: "window" })) client.navigate(client.url);
})()));
"""


def install_service_worker(static: Path | None = None) -> bool:
    """Write SERVICE_WORKER as sw.js in Streamlit's own static folder (or `static`), which Streamlit
    serves at /sw.js, unless it's there already. Returns whether it wrote it. A Streamlit that
    can't be written to (a container's) raises OSError; no phone there ever had the old worker."""
    static = static or icon.streamlit_static()
    if static is None or not static.is_dir():
        return False
    file = static / "sw.js"
    if file.exists() and file.read_text(encoding="utf-8") == SERVICE_WORKER:
        return False
    file.write_text(SERVICE_WORKER, encoding="utf-8")
    return True


# ---------------------------------------------------------------- what's wrong

def protected_folder(root: Path = ROOT, home: Path | None = None) -> str | None:
    """The folder macOS keeps background jobs out of, if the copy is in one."""
    home = home or Path.home()
    for name in PROTECTED:
        if root == home / name or home / name in root.parents:
            return name
    return None


def diagnose(ts: dict, access: dict | None, loaded: bool, forwarded: int | bool | None, answers: bool) -> str | None:
    """Why phone access doesn't work, in a sentence with what to do, or None when it does.
    `forwarded` is the port Tailscale forwards the address to (False: none on this Mac; None: it
    can't say), and `answers` whether the page answers there (on PORT when it can't say)."""
    fix = "Set it up again with: sh install.sh --phone"
    if not access:
        return "Phone access isn't set up. To set it up: sh install.sh --phone"
    if not ts["found"]:
        return "Tailscale isn't on this Mac. Install it from the Mac App Store, sign in, then: sh install.sh --phone"
    if not ts["running"]:
        return "Tailscale is off on this Mac. Turn it on from its menu-bar icon."
    if ts["host"] != access.get("host"):
        return f"This Mac's tailnet name changed (it was {access.get('host')}). {fix}"
    if forwarded is False:
        return f"Tailscale isn't forwarding this Mac's address to the page. {fix}"
    port = PORT if forwarded is None else forwarded
    if answers:
        return None  # whatever keeps it running: the LaunchAgent, Flightdeck, or an open Desktop app
    if loaded and port == PORT:
        return f"The page isn't answering on port {PORT}. What it printed is in {LOG}."
    return (f"Tailscale forwards this Mac's address to port {port}, where the page isn't answering. Open "
            f"Personal Finance on this Mac: a running page takes the address over within a minute when "
            f"nothing answers there. Or, to have launchd keep a page running for it: sh install.sh --phone")


# ---------------------------------------------------------------- commands

def enable() -> int:
    if sys.platform != "darwin":
        say("Phone access is set up on a Mac for now.")
        return 1
    folder = protected_folder()
    if folder:
        say(f"This copy is in {folder}, which macOS keeps background jobs out of, so the page couldn't run "
            f"for the phone. Move the folder to your home folder (like ~/personal-finance), run "
            f"sh install.sh there, then set phone access up again.")
        return 1
    cli = find_tailscale()
    ts = tailscale_status(cli)
    if not ts["found"]:
        say("Phone access needs the free Tailscale app on this Mac and on your phone, signed in to the same")
        say("account. Install it on the Mac from the Mac App Store (or tailscale.com/download), sign in,")
        say("then run this again.")
        return 1
    if not (ts["running"] and ts["host"] and ts["login"]):
        say("Tailscale is installed but not signed in and on. Open it from the menu bar, sign in, then run this again.")
        return 1
    say(f"Starting the page under launchd, on port {PORT}, so it's there whenever the phone asks...")
    if not install_agent():
        return 1
    if not serve(cli):
        say("\ntailscale serve didn't finish. If it printed a link to turn on HTTPS, open it, click Enable,")
        say("and run this again.")
        return 1
    PHONE.parent.mkdir(parents=True, exist_ok=True)
    PHONE.write_text(json.dumps({"host": ts["host"], "login": ts["login"], "port": PORT}, indent=2) + "\n")
    say(f"\nPhone access is on. Your Mac's address, for your devices only:\n\n    https://{ts['host']}\n")
    say("On the phone: sign in to the Tailscale app with the same account, open that address in Safari")
    say("(Chrome on Android), then Share, Add to Home Screen. The icon opens the page like an app.")
    say("The Mac has to be awake: keep it plugged in, and in System Settings, Battery, Options, turn on")
    say("Prevent automatic sleeping on power adapter when the display is off.")
    return 0


def disable() -> int:
    cli = find_tailscale()
    if cli:
        unserve(cli)
    remove_agent()
    PHONE.unlink(missing_ok=True)
    say("Phone access is off: the page answers this Mac alone again, and stops when idle.")
    return 0


def status() -> int:
    cli = find_tailscale()
    ts = tailscale_status(cli)
    access = read_access()
    config = serve_config(cli) if cli else None
    forwarded = None if config is None or not access else (forwarded_port(config, access.get("host", "")) or False)
    why = diagnose(ts, access, agent_loaded(), forwarded, page_answers(forwarded or PORT))
    if why:
        say(why)
        return 1
    say(f"Phone access works: https://{access['host']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    commands = {"enable": enable, "disable": disable, "status": status}
    if len(argv) != 1 or argv[0] not in commands:
        say(__doc__.split("\n\n")[1])
        return 2
    return commands[argv[0]]()


if __name__ == "__main__":
    sys.exit(main())
