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
