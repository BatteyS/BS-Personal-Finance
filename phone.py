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
    Tailscale names the account asking in a Tailscale-User-Login header.
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
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent
PORT = 8501
LABEL = "io.github.batteys.bs-personal-finance.page"
PHONE = config.DATA_DIR / "phone.json"
LOG = Path.home() / "Library" / "Logs" / "Personal Finance.log"
TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
PROTECTED = ["Documents", "Desktop", "Downloads", "Library/Mobile Documents"]


def say(text: str = "") -> None:
    print(text, flush=True)


# ---------------------------------------------------------------- the page's guard

def read_access(path: Path | None = None) -> dict | None:
    """What phone.json records, or None when phone access isn't set up."""
    path = path or PHONE
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


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


def served(cli: str, port: int = PORT) -> bool | None:
    """Whether Tailscale forwards this Mac's address to the page, or None when it can't say."""
    try:
        result = subprocess.run([cli, "serve", "status", "--json"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return f"127.0.0.1:{port}" in (result.stdout or "") if result.returncode == 0 else None


def unserve(cli: str) -> bool:
    try:
        return subprocess.run([cli, "serve", "--https=443", "off"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------- what's wrong

def protected_folder(root: Path = ROOT, home: Path | None = None) -> str | None:
    """The folder macOS keeps background jobs out of, if the copy is in one."""
    home = home or Path.home()
    for name in PROTECTED:
        if root == home / name or home / name in root.parents:
            return name
    return None


def diagnose(ts: dict, access: dict | None, loaded: bool, answers: bool, forwarded: bool | None) -> str | None:
    """Why phone access doesn't work, in a sentence with what to do, or None when it does."""
    fix = "Set it up again with: sh install.sh --phone"
    if not access:
        return "Phone access isn't set up. To set it up: sh install.sh --phone"
    if not ts["found"]:
        return "Tailscale isn't on this Mac. Install it from the Mac App Store, sign in, then: sh install.sh --phone"
    if not ts["running"]:
        return "Tailscale is off on this Mac. Turn it on from its menu-bar icon."
    if ts["host"] != access.get("host"):
        return f"This Mac's tailnet name changed (it was {access.get('host')}). {fix}"
    if not loaded:
        return f"The page's LaunchAgent isn't loaded, so nothing keeps the page running. {fix}"
    if not answers:
        return f"The page isn't answering on port {PORT}. What it printed is in {LOG}."
    if forwarded is False:
        return f"Tailscale isn't forwarding this Mac's address to the page. {fix}"
    return None


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
    why = diagnose(ts, access, agent_loaded(), page_answers(), served(cli) if cli else None)
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
