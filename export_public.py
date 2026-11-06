"""Copy the shareable part of this tracker into another folder, and check that nothing personal
goes with it. The public repository, BS-Personal-Finance, is made from these exports.

    uv run export_public.py ../BS-Personal-Finance   # export into a clone of the public repo
    uv run export_public.py --check                   # only check this copy; CI runs it on every push

What is shared is the engine: the code, the example files, the tests and the README, listed in
ENGINE below. Personal files never are: the data folder (which git ignores anyway), the spec,
the scrubbed samples and the accounts they came from. Only files git tracks are exported, and
only as they stand in the last commit, so an export is a commit and not whatever is on disk.

Before anything is written, every file that would be shared is checked for:

  * email addresses, apart from example.com ones and GitHub's noreply ones;
  * US phone numbers, apart from 555 ones;
  * your name and email as git knows them;
  * your own accounts' names and Fidelity labels, from data/accounts.toml and from the private
    tests/accounts.samples.toml, apart from ones the example files use too;
  * your salary and the typed amounts in data/budget.toml, from $1,000 up;
  * this Mac's tailnet address and names, from data/phone.json, once phone access is set up.

A hit stops it and names the file, the line and what was found. On real data, run it in your own
terminal: it reads the data folder.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent

# What a copy of the tracker is made of. Everything else stays private.
ENGINE = [
    ".claude/settings.json", ".github/*", ".gitignore", ".python-version", ".streamlit/config.toml",
    "CLAUDE.md", "LICENSE", "README.md", "pyproject.toml", "uv.lock",
    "accounts.example.toml", "budget.example.toml", "formats.example.toml", "rules.example.csv",
    "actions.py", "budget.py", "categorize.py", "config.py", "dashboard.py", "db.py", "export_public.py",
    "formats.py", "load.py", "ofx.py", "queries.py", "scrub.py",
    "install.sh", "launch.sh", "phone.py", "icon.py",
    "tests/*",
]
# Never shared, even where a pattern above would take it.
PRIVATE = ["tests/accounts.samples.toml", "tests/test_samples.py"]
# Files whose terms are generic by definition: a term that appears in them is never personal.
GENERIC = ["accounts.example.toml", "tests/accounts.toml"]
SAMPLE_ACCOUNTS = ROOT / "tests" / "accounts.samples.toml"
PHONE = config.DATA_DIR / "phone.json"

# A line that may name a person: the copyright holder.
ALLOWED_LINES = {"LICENSE": re.compile(r"^Copyright \(c\) ")}
NOT_SCANNED = {"uv.lock"}  # hashes and versions, written by uv

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Not addresses: the examples' own, GitHub's, Claude's co-author line, and the @2x.png that names a
# Retina image in the Desktop app's iconset (icon.py).
SAFE_EMAIL = re.compile(r"@example\.(com|org|net)$|^git@github\.com$|@users\.noreply\.github\.com$|"
                        r"^noreply@anthropic\.com$|@\dx\.png$", re.I)
PHONE_NUMBER = re.compile(r"(?<![\d.])\(?(\d{3})\)?[ .-](\d{3})[ .-]\d{4}(?![\d.])")


def is_engine(path: str) -> bool:
    """Whether a repo path is part of what's shared."""
    return path not in PRIVATE and any(fnmatch.fnmatch(path, pattern) for pattern in ENGINE)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def engine_files(untracked: bool = False) -> list[str]:
    """The shared files git tracks, plus untracked ones that aren't ignored when asked."""
    listed = git("ls-files").splitlines()
    if untracked:
        listed += git("ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({path for path in listed if is_engine(path) and (ROOT / path).is_file()})


def account_terms(path: Path) -> list[str]:
    """Account names and Fidelity labels in one accounts file."""
    if not path.exists():
        return []
    with path.open("rb") as file:
        raw = tomllib.load(file)
    return [str(entry[field]) for entry in raw.get("account", []) for field in ("name", "fidelity_label")
            if entry.get(field)]


def amount_patterns(value: float) -> list[str]:
    """How an amount of $1,000 or more might be written: 2150, 2,150, 2_150, with or without cents."""
    whole = int(value)
    forms = {f"{whole}", f"{whole:,}", f"{whole:_}"}
    if value != whole:
        forms |= {f"{value:.2f}", f"{value:,.2f}"}
    return sorted(forms)


def personal_terms() -> list[tuple[str, re.Pattern]]:
    """(what it is, pattern) for everything that would identify this copy's owner, read from
    their own files."""
    found: list[tuple[str, str]] = []
    for key in ("name", "email"):
        value = subprocess.run(["git", "config", f"user.{key}"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        if value:
            found.append((f"your git user.{key}", value))
    generic = {term.upper() for name in GENERIC for term in account_terms(ROOT / name)}
    for source in (config.ACCOUNTS_PATH, SAMPLE_ACCOUNTS):
        for term in account_terms(source):
            if term.upper() not in generic and len(term) >= 4:
                found.append((f"an account in {source.name}", term))
    if config.BUDGET_PATH.exists():
        with config.BUDGET_PATH.open("rb") as file:
            plan = tomllib.load(file)
        amounts = [plan.get("income", {}).get("salary", 0)]
        amounts += [line.get("monthly", 0) for line in plan.get("line", [])]
        for amount in amounts:
            if isinstance(amount, (int, float)) and amount >= 1_000:
                found += [("an amount in your budget", form) for form in amount_patterns(amount)]
    if PHONE.exists():
        access = json.loads(PHONE.read_text())
        host = str(access.get("host") or "")
        if host:
            mac, _, rest = host.partition(".")
            found += [("your Mac's tailnet address", host), ("your Mac's name on your tailnet", mac),
                      ("your tailnet's name", rest.partition(".")[0])]
        if access.get("login"):
            found.append(("your Tailscale login", str(access["login"])))
    unique = {(label, term) for label, term in found if len(term) >= 3}
    return [(label, term_pattern(term)) for label, term in sorted(unique)]


def term_pattern(term: str) -> re.Pattern:
    """A term as a whole word, without regard to case. Underscores and hyphens count as breaks
    between words, so a name inside a snake_case identifier is found too, and a space in the term
    matches a space, underscore or hyphen: "Rainy Day" finds test_from_rainy_day."""
    body = r"[ _-]".join(re.escape(part) for part in term.split(" "))
    return re.compile(rf"(?<![A-Za-z0-9$]){body}(?![A-Za-z0-9]|[,.]\d)", re.I)


def scan(texts: dict[str, str], terms: list[tuple[str, re.Pattern]]) -> list[str]:
    """Every line that carries personal data, as 'path:line: what was found'."""
    hits = []
    for path, text in texts.items():
        if path in NOT_SCANNED:
            continue
        allowed = ALLOWED_LINES.get(path)
        for number, line in enumerate(text.splitlines(), start=1):
            if allowed and allowed.search(line):
                continue
            for address in EMAIL.findall(line):
                if not SAFE_EMAIL.search(address):
                    hits.append(f"{path}:{number}: an email address")
            for match in PHONE_NUMBER.finditer(line):
                if match.group(2) != "555":
                    hits.append(f"{path}:{number}: a phone number")
            for label, pattern in terms:
                if pattern.search(line):
                    hits.append(f"{path}:{number}: {label}")
    return hits


def committed_texts(files: list[str]) -> dict[str, str]:
    """Each file as it stands in the last commit."""
    return {path: git("show", f"HEAD:{path}") for path in files}


def export(target: Path) -> int:
    """Check, then write the committed engine files into target, removing files the export no
    longer has. target's own .git is left alone."""
    if git("status", "--porcelain", "--untracked-files=no").strip():
        print("Commit or stash your changes first: an export is a commit, not what's on disk.", file=sys.stderr)
        return 1
    files = engine_files()
    texts = committed_texts(files)
    hits = scan(texts, personal_terms())
    if hits:
        print("Not exported: these lines carry personal data.", *hits, sep="\n  ", file=sys.stderr)
        return 1
    target.mkdir(parents=True, exist_ok=True)
    for existing in sorted(target.rglob("*"), reverse=True):
        relative = existing.relative_to(target).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if existing.is_file() and relative not in texts:
            existing.unlink()
        elif existing.is_dir() and not any(existing.iterdir()):
            existing.rmdir()
    for path in files:
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(subprocess.run(["git", "show", f"HEAD:{path}"], cwd=ROOT,
                                               capture_output=True, check=True).stdout)
        destination.chmod((ROOT / path).stat().st_mode & 0o777)  # keeps install.sh executable
    commit = git("rev-parse", "--short", "HEAD").strip()
    print(f"Exported {len(files)} files from {commit} to {target}. Read the diff there, then commit and push.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the shareable files, or check them for personal data.")
    parser.add_argument("target", nargs="?", type=Path, help="the folder to export into")
    parser.add_argument("--check", action="store_true", help="only check this copy, untracked files included")
    args = parser.parse_args(argv)
    if args.check:
        files = engine_files(untracked=True)
        hits = scan({path: (ROOT / path).read_text(encoding="utf-8", errors="replace") for path in files},
                    personal_terms())
        if hits:
            print("Personal data in files that would be shared:", *hits, sep="\n  ", file=sys.stderr)
            return 1
        print(f"{len(files)} files checked: nothing personal.")
        return 0
    if not args.target:
        parser.error("give a folder to export into, or --check")
    return export(args.target.resolve())


if __name__ == "__main__":
    sys.exit(main())
