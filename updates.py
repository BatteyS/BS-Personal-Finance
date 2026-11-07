"""Which version this copy is, whether a newer one is out, and bringing it up to date.

    uv run updates.py           # say the version, and whether there's a newer one
    uv run updates.py update    # bring this copy up to date

A copy is a clone of the public repository, BS-Personal-Finance, whose releases are commits named
"BS-Personal-Finance-v1.20" and so on, numbered after the change they were published for. An
update is a fast-forward of main to the repository's: it never merges, rebases or writes over a
change of yours, and stops, changing nothing, when there is one. Personal Finance on the Desktop
runs the update each time it starts the page (launch.sh), and the page's sidebar shows the version
and, when a newer one is out, an Update button. A copy with no git history, the container, reads
its version from the VERSION file an export writes.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELEASE = re.compile(r"^BS-Personal-Finance-(v\d+(?:\.\d+)*)$")
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never stop to ask for a sign-in


def git(*args: str, root: Path = ROOT, timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout, env=ENV)


def release_of(subjects: list[str]) -> tuple[str | None, int]:
    """The newest release among commit subjects (newest first), and how many commits came after it."""
    for count, subject in enumerate(subjects):
        found = RELEASE.match(subject.strip())
        if found:
            return found.group(1), count
    return None, len(subjects)


def version_file(root: Path = ROOT) -> str:
    """The release a VERSION file names, "v1.20", or "" without one: what an export writes for a
    copy that has no git history to read, such as the container."""
    try:
        found = RELEASE.match((root / "VERSION").read_text().strip())
    except OSError:
        return ""
    return found.group(1) if found else ""


def version(root: Path = ROOT) -> str:
    """This copy's version: "v1.2", "v1.2 +3" with commits of its own since, or the commit itself
    in a copy with no release in its history; outside a git checkout, what VERSION says, or ""."""
    try:
        log = git("log", "--format=%s", "-200", root=root)
    except (OSError, subprocess.SubprocessError):
        return version_file(root)
    if log.returncode != 0:
        return version_file(root)
    release, since = release_of(log.stdout.splitlines())
    if release:
        return release + (f" +{since}" if since else "")
    commit = git("rev-parse", "--short", "HEAD", root=root).stdout.strip()
    return commit


def check(root: Path = ROOT) -> dict | None:
    """Whether the repository has commits this copy lacks: {"behind": n, "latest": "v1.3" or None}.
    None when it can't tell: not a git checkout, not on main, no origin, or offline."""
    try:
        if git("rev-parse", "--abbrev-ref", "HEAD", root=root).stdout.strip() != "main":
            return None
        if git("fetch", "--quiet", "origin", "main", root=root, timeout=30).returncode != 0:
            return None
        behind = git("rev-list", "--count", "HEAD..origin/main", root=root).stdout.strip()
        subjects = git("log", "--format=%s", "-200", "origin/main", root=root).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    return {"behind": int(behind or 0), "latest": release_of(subjects)[0]}


def update(root: Path = ROOT) -> tuple[bool, str]:
    """Fast-forward main to the repository's. Returns whether it changed anything, and what to say."""
    try:
        if git("rev-parse", "--abbrev-ref", "HEAD", root=root).stdout.strip() != "main":
            return False, "This copy isn't on main, so it wasn't updated."
        if git("status", "--porcelain", "--untracked-files=no", root=root).stdout.strip():
            return False, "This copy has changes of yours to its files, so it wasn't updated. Commit or undo them first."
        before = version(root)
        pulled = git("pull", "--ff-only", "--quiet", "origin", "main", root=root, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"The update didn't run: {error}"
    if pulled.returncode != 0:
        return False, f"The update didn't go through: {(pulled.stderr or pulled.stdout).strip()}"
    after = version(root)
    if after == before:
        return False, f"Already up to date ({after})."
    return True, f"Updated from {before} to {after}. Restart the page to use it: close it, then open it again."


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["update"]:
        changed, message = update()
        print(message)
        return 0 if changed or message.startswith("Already") else 1
    print(f"This copy: {version() or 'not a git checkout'}")
    found = check()
    if found and found["behind"]:
        print(f"A newer version is out ({found['latest'] or 'new commits'}): uv run updates.py update")
    elif found:
        print("It's up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
