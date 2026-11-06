"""Recompute every transaction's category and transfer_id, in three passes.

    uv run categorize.py                              # recategorize every row
    uv run categorize.py --report                     # then list uncategorized merchants
    uv run categorize.py --report --month 2026-10     # just one month's

Pass 1 sorts rows on investment accounts by their Fidelity action. Pass 2 pairs transfers between
your own accounts. Pass 3 applies rules.csv to whatever is left. Nothing is edited by hand, so a
new rule recategorizes past months too. See docs/spec.md: Categorization and Rules.

The report prints real merchant totals: on real data, run it in your own terminal.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import config
import db
from load import normalize

RULES_EXAMPLE = Path(__file__).resolve().parent / "rules.example.csv"
CATEGORIES = set(config.SPENDING_CATEGORIES) | set(config.OTHER_CATEGORIES)
INVESTMENT_KINDS = {"brokerage", "retirement", "hsa"}
# A transfer between two of these is a rollover, never a contribution.
ROLLOVER_KINDS = {"retirement", "hsa"}


class RulesError(Exception):
    """rules.csv has a line that can't be used. The message names the line."""


def read_rules(path: Path) -> list[dict]:
    """The rules in file order. Raises RulesError on a line with an empty pattern, an unknown
    category or match type, or a regex that doesn't compile."""
    rules = []
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not {"pattern", "category", "match"} <= set(reader.fieldnames or []):
            raise RulesError(f"{path.name}: the first line must name the columns pattern, category, match")
        for line, row in enumerate(reader, start=2):
            pattern = (row["pattern"] or "").strip()
            category = (row["category"] or "").strip().lower()
            match = (row["match"] or "contains").strip().lower()
            where = f"{path.name} line {line}"
            if not pattern:
                raise RulesError(f"{where}: the pattern is empty")
            if category not in CATEGORIES:
                raise RulesError(f"{where}: {category!r} isn't a category (the lists are in config.py)")
            if match == "contains":
                regex = re.compile(re.escape(normalize(pattern)))
            elif match == "regex":
                try:
                    regex = re.compile(pattern, re.IGNORECASE)
                except re.error as error:
                    raise RulesError(f"{where}: the regex doesn't compile ({error})") from None
            else:
                raise RulesError(f"{where}: match must be contains or regex, not {match!r}")
            rules.append({"line": line, "pattern": pattern, "category": category, "regex": regex})
    return rules


def ensure_rules_file(path: Path | None = None) -> Path:
    """The live rules file, created from rules.example.csv the first time."""
    path = path or config.RULES_PATH
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(RULES_EXAMPLE, path)
    return path


def merchant_key(description: str) -> str:
    """The description without digits or the punctuation around them, so one merchant's rows group
    together: "COSTCO WHSE #0123 CHICAGO IL" becomes "COSTCO WHSE CHICAGO IL"."""
    words = (re.sub(r"\d+", "", word).strip("#*-.,:;/'\"()") for word in normalize(description).split())
    return " ".join(word for word in words if word)


def suggested_pattern(descriptions: list[str]) -> str:
    """A contains pattern that every one of a merchant's descriptions matches: their shared leading
    words, stopping at the first later word with a digit in it (a store or phone number).
    "COSTCO WHSE #0123 CHICAGO IL" becomes "COSTCO WHSE"; "7-ELEVEN 00036 CHICAGO IL" becomes "7-ELEVEN".
    """
    shared: list[str] = []
    for words in zip(*(normalize(description).split() for description in descriptions)):
        if len(set(words)) > 1 or (shared and re.search(r"\d", words[0])):
            break
        shared.append(words[0])
    while shared and not re.search(r"[A-Z0-9]", shared[-1]):
        shared.pop()  # a trailing "-" or "#" says nothing
    if shared:
        return " ".join(shared)
    # No leading word in common. Each merchant-key word is a piece of every description, so the
    # longest one still matches them all.
    return max(merchant_key(descriptions[0]).split() or [normalize(descriptions[0])], key=len)


def takes_pass_1(row: dict) -> bool:
    """Whether a row goes through pass 1: rows on investment accounts, and every Fidelity row,
    since Fidelity's cash accounts also sweep into a money market that pays dividends."""
    account = config.ACCOUNTS.get(row["account"], {})
    return account.get("kind") in INVESTMENT_KINDS or account.get("institution") == "fidelity"


def investment_category(description: str, cap_key: str | None) -> str | None:
    """Pass 1 for one row: retirement, employer_match, investment, or None to leave it to passes 2
    and 3. Patterns match whole words, plurals included: FEE matches FEES but not COFFEE or FEEDING."""
    text = normalize(description)
    for pattern, kind in config.INVESTMENT_ACTIONS:
        if not re.search(r"\b" + re.escape(normalize(pattern)) + r"S?\b", text):
            continue
        if kind == "internal":
            return "investment"
        if cap_key is None:
            continue  # contribution words mean nothing on an account without a cap
        if kind == "contribution" or cap_key in config.CAPS_INCLUDE_EMPLOYER:
            return "retirement"
        return "employer_match"
    return None


def days_apart(first: dict, second: dict) -> int:
    """Calendar days between two rows' dates, in either order."""
    return abs((date.fromisoformat(first["date"]) - date.fromisoformat(second["date"])).days)


def kind_of(row: dict) -> str:
    """The kind of the account a row is on: cash, brokerage, retirement, hsa or credit."""
    return config.ACCOUNTS.get(row["account"], {}).get("kind", "")


def link(first: dict, second: dict, categories: dict[str, str], transfer_ids: dict[str, str]) -> None:
    """Mark two rows as the legs of one transfer. A retirement leg stays retirement when its partner
    isn't a retirement or HSA account (a contribution paid from cash); otherwise both are transfers."""
    for leg, partner in ((first, second), (second, first)):
        transfer_ids[leg["id"]] = partner["id"]
    for leg, partner in ((first, second), (second, first)):
        keep = categories.get(leg["id"]) == "retirement" and kind_of(partner) not in ROLLOVER_KINDS
        categories[leg["id"]] = "retirement" if keep else "transfer"


def pair_transfers(rows: list[dict], categories: dict[str, str], transfer_ids: dict[str, str]) -> None:
    """Pass 2: pair each leg leaving one of your accounts with the leg arriving at the other.

    Same amount to the cent, at most TRANSFER_WINDOW_DAYS apart in either direction, hints present.
    One-to-one: leaving legs in date order, each taking the closest unused arriving leg.
    """
    for pair in config.TRANSFER_PAIRS:
        accounts = set(pair["accounts"])
        hints = {account: normalize(hint) for account, hint in pair["hints"].items()}
        candidates = [
            row for row in rows
            if row["account"] in accounts
            and row["id"] not in transfer_ids
            and categories.get(row["id"]) in (None, "retirement")
            and hints.get(row["account"], "") in normalize(row["description"])
        ]
        leaving = sorted((row for row in candidates if row["amount"] < 0), key=lambda row: (row["date"], row["id"]))
        arriving = [row for row in candidates if row["amount"] > 0]
        for out in leaving:
            options = [
                row for row in arriving
                if row["id"] not in transfer_ids
                and row["account"] != out["account"]
                and round(row["amount"], 2) == round(-out["amount"], 2)
                and days_apart(row, out) <= config.TRANSFER_WINDOW_DAYS
            ]
            if options:
                partner = min(options, key=lambda row: (days_apart(row, out), row["date"], row["id"]))
                link(out, partner, categories, transfer_ids)


def rule_category(description: str, rules: list[dict]) -> str:
    """Pass 3: the first rule matching the description, or uncategorized."""
    text = normalize(description)
    for rule in rules:
        if rule["regex"].search(text):
            return rule["category"]
    return "uncategorized"


def categorize(connection: sqlite3.Connection, rules: list[dict]) -> Counter[str]:
    """Recompute category and transfer_id for every row; return how many rows got each category."""
    rows = [
        dict(zip(("id", "account", "date", "amount", "description"), values))
        for values in connection.execute("SELECT id, account, date, amount, description FROM transactions ORDER BY date, id")
    ]
    categories: dict[str, str] = {}
    transfer_ids: dict[str, str] = {}
    for row in rows:
        if takes_pass_1(row):
            category = investment_category(row["description"], config.ACCOUNTS[row["account"]]["cap_key"])
            if category:
                categories[row["id"]] = category
    pair_transfers(rows, categories, transfer_ids)
    for row in rows:
        if row["id"] not in categories:
            categories[row["id"]] = rule_category(row["description"], rules)
    with connection:
        connection.executemany(
            "UPDATE transactions SET category = ?, transfer_id = ? WHERE id = ?",
            [(categories[row["id"]], transfer_ids.get(row["id"]), row["id"]) for row in rows],
        )
    return Counter(categories.values())


def uncategorized_report(connection: sqlite3.Connection, month: str | None = None) -> list[dict]:
    """Uncategorized rows grouped by merchant key, largest absolute total first. Each group has its
    merchant key, count, total, a suggested rule pattern, and the descriptions it covers."""
    query = "SELECT description, amount FROM transactions WHERE category = 'uncategorized'"
    parameters: tuple = ()
    if month:
        query += " AND month = ?"
        parameters = (month,)
    groups: dict[str, dict] = {}
    for description, amount in connection.execute(query, parameters):
        key = merchant_key(description)
        group = groups.setdefault(key, {"merchant": key, "count": 0, "total": 0.0, "descriptions": []})
        group["count"] += 1
        group["total"] += amount
        group["descriptions"].append(description)
    for group in groups.values():
        group["total"] = round(group["total"], 2) + 0.0
        group["pattern"] = suggested_pattern(group["descriptions"])
    return sorted(groups.values(), key=lambda group: (-abs(group["total"]), group["merchant"]))


def main(argv: list[str] | None = None) -> int:
    """Recategorize everything; with --report, list the uncategorized merchants."""
    parser = argparse.ArgumentParser(description="Recompute every transaction's category.")
    parser.add_argument("--report", action="store_true", help="list uncategorized merchants with counts and totals")
    parser.add_argument("--month", help="limit the report to one month, as YYYY-MM")
    args = parser.parse_args(argv)
    try:
        rules = read_rules(ensure_rules_file())
    except RulesError as error:
        print(error, file=sys.stderr)
        return 1
    connection = db.connect()
    try:
        counts = categorize(connection, rules)
        summary = ", ".join(f"{count} {name}" for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        print(f"{sum(counts.values())} rows: {summary or 'none'}")
        if args.report:
            for group in uncategorized_report(connection, args.month):
                print(f"{group['total']:>12,.2f}  {group['count']:>4}  {group['merchant']}  (rule: {group['pattern']})")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
