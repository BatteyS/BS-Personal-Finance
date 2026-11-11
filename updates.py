"""Which version this copy is, whether a newer one is out, and bringing it up to date.

    uv run updates.py           # say the version, and whether there's a newer one
    uv run updates.py update    # bring this copy up to date

A copy is a clone of the public repository, BS-Personal-Finance, whose releases are commits named
"BS-Personal-Finance-v1.20" and so on, numbered after the change they were published for. An
update is a fast-forward of main to the repository's: it never merges, rebases or writes over a
change of yours, and stops, changing nothing, when there is one. Personal Finance on the Desktop
runs the update each time it starts the page (launch.sh). The page's sidebar names its version
beside the monogram, and shows an Update button when a newer one is out. A copy with no git history,
the container, reads its version from the VERSION file an export writes. The private repository
the releases are exported from has none in its history, so it goes by the pull requests merged
into it: "#42 · d037abf".
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELEASE = re.compile(r"^BS-Personal-Finance-(v\d+(?:\.\d+)*)$")
MERGED = re.compile(r"^Merge pull request #(\d+)\b")  # GitHub's subject for a merged pull request
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


def merged_of(subjects: list[str]) -> str | None:
    """The newest pull request merged among commit subjects (newest first), "#42", or None."""
    for subject in subjects:
        found = MERGED.match(subject.strip())
        if found:
            return f"#{found.group(1)}"
    return None


def describe(root: Path = ROOT) -> dict:
    """Which version this copy is, and the commit it stands on:
    {"version": "v1.2", "commit": "5fea0e9", "date": "2026-11-08", "edited": False}.

    The version is the latest release in its history, "v1.2", or "v1.2 +3" with commits of its own
    since. A copy with no release in its history, the private repository the releases are exported
    from, goes by the latest pull request merged into it and adds the commit, "#42 · d037abf", since
    one pull request spans many commits there; a history with neither is the commit alone. Either
    way " · edited" follows when its tracked files differ from that commit, as a copy that update()
    would refuse to touch does. Outside a git checkout (the container) the version is what VERSION
    says, or "", and there is no commit."""
    outside = {"version": version_file(root), "commit": "", "date": "", "edited": False}
    try:
        log = git("log", "--format=%h%x00%cs%x00%s", "-200", root=root)
        status = git("status", "--porcelain", "--untracked-files=no", root=root)
    except (OSError, subprocess.SubprocessError):
        return outside
    if log.returncode != 0 or not log.stdout.strip():
        return outside
    commits = [line.split("\x00", 2) for line in log.stdout.strip().split("\n")]  # a subject is one line
    commit, day = commits[0][0], commits[0][1]
    subjects = [subject for _, _, subject in commits]
    release, since = release_of(subjects)
    if release:
        shown = release + (f" +{since}" if since else "")
    else:
        merged = merged_of(subjects)
        shown = f"{merged} · {commit}" if merged else commit
    edited = status.returncode == 0 and bool(status.stdout.strip())
    return {"version": shown + (" · edited" if edited else ""), "commit": commit, "date": day, "edited": edited}


def version(root: Path = ROOT) -> str:
    """This copy's version as the page shows it (describe): "v1.2", "v1.2 +3", "#42 · d037abf",
    with " · edited" when its files differ from its commit; outside a git checkout, what VERSION
    says, or ""."""
    return describe(root)["version"]


def check(root: Path = ROOT) -> dict | None:
    """Whether the repository has commits this copy lacks: {"behind": n, "latest": "v1.3" or None},
    the latest being the private repository's latest pull request, "#43", where it has no release.
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
    return {"behind": int(behind or 0), "latest": release_of(subjects)[0] or merged_of(subjects)}


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
