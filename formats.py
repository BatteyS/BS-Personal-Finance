"""Layouts for exports with no built-in parser: CSV files from any institution, taught once on the
page, and which of your accounts an OFX/QFX file's account number belongs to.

They live in data/formats.toml (formats.example.toml shows the shape):

    [[csv]]
    name = "Chase card"                         # shown in the import summary
    headers = ["transaction date", "post date", "description", "category", "type", "amount", "memo"]
    account = "chase_card"                      # a key in accounts.toml
    date = "transaction date"
    amount = "amount"                           # one signed column...
    spending_is = "negative"                    # ...where spending shows as negative (or "positive")
    # debit = "debit"                           # or two columns: money out, and money in
    # credit = "credit"
    description = ["description"]               # joined with a space

    [[ofx]]
    account_ends = "1234"                       # the last four digits of the file's account number
    account = "chase_checking"

A CSV file is recognized by its header row: the lowercased names of its columns must be exactly a
layout's headers. Only the last four digits of an account number are kept, never the whole number.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import config

SPENDING = {"negative", "positive"}


class FormatsError(Exception):
    """formats.toml can't be used. The message names the problem."""


def read_formats(path: Path | None = None) -> dict[str, list[dict]]:
    """The CSV layouts and OFX matches in formats.toml, checked against the accounts; empty lists
    when the file doesn't exist. Raises FormatsError naming the first problem."""
    path = path or config.FORMATS_PATH
    if not path.exists():
        return {"csv": [], "ofx": []}
    try:
        with path.open("rb") as file:
            raw = tomllib.load(file)
    except tomllib.TOMLDecodeError as error:
        raise FormatsError(f"{path.name} isn't valid TOML: {error}") from None
    layouts = [check_layout(entry, f"{path.name}, csv layout {number}")
               for number, entry in enumerate(raw.get("csv", []), start=1)]
    matches = []
    for number, entry in enumerate(raw.get("ofx", []), start=1):
        where = f"{path.name}, ofx account {number}"
        ends = str(entry.get("account_ends", ""))
        if not (ends.isdigit() and len(ends) == 4):
            raise FormatsError(f"{where}: account_ends must be the last four digits of the account number")
        if entry.get("account") not in config.ACCOUNTS:
            raise FormatsError(f"{where}: account must be a key in accounts.toml")
        matches.append({"account_ends": ends, "account": entry["account"]})
    return {"csv": layouts, "ofx": matches}


def check_layout(entry: dict, where: str) -> dict:
    """One CSV layout, checked and with its header names lowercased."""
    headers = [header_key(name) for name in entry.get("headers", [])]
    if not entry.get("name") or not headers:
        raise FormatsError(f"{where}: a layout needs a name and its headers")
    if entry.get("account") not in config.ACCOUNTS:
        raise FormatsError(f"{where} ({entry['name']}): account must be a key in accounts.toml")
    columns = {"date": entry.get("date"), "amount": entry.get("amount"), "debit": entry.get("debit"),
               "credit": entry.get("credit")}
    descriptions = entry.get("description") or []
    if isinstance(descriptions, str):
        descriptions = [descriptions]
    named = [value for value in [*columns.values(), *descriptions] if value]
    missing = [name for name in named if header_key(name) not in headers]
    if missing:
        raise FormatsError(f"{where} ({entry['name']}): {', '.join(missing)} isn't one of its headers")
    if not columns["date"] or not descriptions:
        raise FormatsError(f"{where} ({entry['name']}): name the date column and at least one description column")
    if columns["amount"]:
        if columns["debit"] or columns["credit"]:
            raise FormatsError(f"{where} ({entry['name']}): give one amount column, or debit and credit, not both")
        if entry.get("spending_is") not in SPENDING:
            raise FormatsError(f"{where} ({entry['name']}): spending_is must be negative or positive")
    elif not (columns["debit"] and columns["credit"]):
        raise FormatsError(f"{where} ({entry['name']}): name an amount column, or a debit and a credit column")
    return {
        "name": entry["name"], "headers": headers, "account": entry["account"],
        "date": header_key(columns["date"]),
        "amount": header_key(columns["amount"]) if columns["amount"] else None,
        "spending_is": entry.get("spending_is"),
        "debit": header_key(columns["debit"]) if columns["debit"] else None,
        "credit": header_key(columns["credit"]) if columns["credit"] else None,
        "description": [header_key(name) for name in descriptions],
    }


def header_key(cell: str) -> str:
    """A header cell as layouts name it: lowercase, whitespace collapsed, no "($)" suffix."""
    return " ".join(str(cell).lower().split()).removesuffix("($)").strip()


def match_layout(names: list[str], layouts: list[dict]) -> dict | None:
    """The layout whose headers are exactly this row's column names, if any."""
    row = sorted(name for name in (header_key(cell) for cell in names) if name)
    for layout in layouts:
        if sorted(layout["headers"]) == row:
            return layout
    return None


def ofx_account(account_number: str, matches: list[dict]) -> str | None:
    """Which of your accounts an OFX account number belongs to, by its last four digits."""
    digits = "".join(character for character in account_number if character.isdigit())
    for match in matches:
        if digits.endswith(match["account_ends"]):
            return match["account"]
    return None


# ---------------------------------------------------------------- teaching a new layout

DATE_NAME = ("date", "posted", "posting", "transaction")
DESCRIPTION_NAME = ("description", "payee", "name", "merchant", "details", "memo")


def guess_header(rows: list[list[str]], is_date) -> int | None:
    """The header row of a CSV no layout matches: the first row with two or more named cells whose
    next row has a date in it. `is_date` tells a date cell (load.parse_date, passed in)."""
    for index, row in enumerate(rows[:-1]):
        if sum(1 for value in row if value.strip()) >= 2 and any(is_date(value) for value in rows[index + 1]):
            return index
    return None


def suggest(header: list[str], rows: list[list[str]], is_date, as_amount) -> dict:
    """First guesses for the teaching form, from the header and the rows under it: the date column,
    the amount column or debit and credit columns, which way spending shows, and the description
    columns. `as_amount` reads a cell as a number or None."""
    names = [header_key(cell) for cell in header]
    columns = [name for name in names if name]

    def first(test) -> str | None:
        return next((name for name in columns if test(name)), None)

    date = first(lambda name: any(word in name for word in DATE_NAME)) or next(
        (names[i] for i in range(len(names)) if rows and is_date(rows[0][i] if i < len(rows[0]) else "")), None)
    debit = first(lambda name: name.startswith(("debit", "withdrawal")))
    credit = first(lambda name: name.startswith(("credit", "deposit")))
    amount = first(lambda name: name.startswith("amount"))
    if amount is None and not (debit and credit):
        amount = next((name for i, name in enumerate(names) if name and name != date and rows and
                       all(as_amount(row[i] if i < len(row) else "") is not None for row in rows[:5])), None)
    signs = []
    if amount in names:
        position = names.index(amount)
        signs = [as_amount(row[position]) for row in rows if position < len(row)]
        signs = [value for value in signs if value]
    negatives = sum(1 for value in signs if value < 0)
    descriptions = [name for name in columns if any(word in name for word in DESCRIPTION_NAME)][:1] or \
        [name for name in columns if name not in (date, amount, debit, credit)][:1]
    return {
        "date": date,
        "mode": "amount" if amount else "debit and credit",
        "amount": amount, "debit": debit, "credit": credit,
        # Most rows are spending: if most amounts are negative, spending shows as negative.
        "spending_is": "negative" if negatives * 2 >= len(signs) else "positive",
        "description": descriptions,
    }
