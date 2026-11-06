"""Load bank CSV exports into the database.

    uv run load.py data/exports/        # a folder, or individual files

For each file: find the header row by its text, tell the institution from the columns it names,
parse the rows into the transactions shape, and insert the ones not already stored. One line per
file says how many rows were read, new and duplicate. See docs/spec.md: Bank exports, Row ids and
Data model.

The parsers were written against the layouts documented in the spec, before any samples existed.
The scrubbed samples are the truth: check each parser against them.
"""

from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import io
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import config
import db

DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")
# Amex's plain download has exactly these three columns; with extra details it also has one of
# the Amex-only columns. BofA's checking export always adds Running Bal., so the two can't be confused.
AMEX_PLAIN_COLUMNS = {"date", "description", "amount"}
AMEX_ONLY_COLUMNS = {"card member", "account #", "extended details", "appears on your statement as", "reference"}


class LoadError(Exception):
    """A file that can't be loaded as it is. The message says why, and nothing from it is inserted."""


def normalize(text: str) -> str:
    """Uppercase with whitespace collapsed: the form text is matched in."""
    return " ".join(text.upper().split())


def read_text(path: Path) -> str:
    """The file's text, whatever its encoding. A UTF-8 byte-order mark is dropped."""
    raw = path.read_bytes()
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig")
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def header_name(cell: str) -> str:
    """A header cell in lowercase, without a "($)" suffix: "Amount ($)" becomes "amount"."""
    name = " ".join(cell.lower().split())
    return name.removesuffix("($)").strip()


def detect_institution(names: set[str]) -> str | None:
    """Which institution a header row belongs to, judged by the columns it names."""
    if {"run date", "account", "action", "amount"} <= names:
        return "fidelity"
    if {"date", "description", "amount", "running bal."} <= names:
        return "bofa"
    if names == AMEX_PLAIN_COLUMNS or (AMEX_PLAIN_COLUMNS <= names and names & AMEX_ONLY_COLUMNS):
        return "amex"
    return None


def find_header(rows: list[list[str]], text: str) -> tuple[int, str]:
    """The header row's index and institution. Raises LoadError quoting the file's first three lines."""
    for index, row in enumerate(rows):
        institution = detect_institution({header_name(cell) for cell in row} - {""})
        if institution:
            return index, institution
    first_lines = "\n".join(f"    {line}" for line in text.splitlines()[:3]) or "    (the file is empty)"
    raise LoadError(f"no Fidelity, Bank of America or Amex header found; the file starts:\n{first_lines}")


def column_positions(header: list[str]) -> dict[str, int]:
    """Each column's position by header name (the first one, if a name repeats)."""
    positions: dict[str, int] = {}
    for index, cell in enumerate(header):
        positions.setdefault(header_name(cell), index)
    return positions


def cell(row: list[str], index: int) -> str:
    """The cell at index, or an empty string if the row is shorter."""
    return row[index] if index < len(row) else ""


def parse_date(text: str) -> str | None:
    """The ISO date for "10/15/2026", "10/15/26" or "2026-10-15"; None if it isn't a date."""
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), date_format).date().isoformat()
        except ValueError:
            continue
    return None


def parse_amount(text: str, row_number: int) -> float | None:
    """The amount in a cell like "-1,234.56", "$12.00" or "(45.10)"; None for an empty cell."""
    cleaned = text.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = float(cleaned)
    except ValueError:
        raise LoadError(f"row {row_number}: the amount {text.strip()!r} is not a number") from None
    return round(-value if negative else value, 2)


def data_rows(
    rows: list[list[str]], header_index: int, date_column: int, amount_column: int
) -> Iterator[tuple[int, list[str], float]]:
    """(row number, row, amount) for each row below the header that has a date and an amount.

    Rows without a date are layout: blank lines and disclaimers. Rows with a date but no amount
    aren't money moving: BofA's beginning balance, Fidelity's share-only transfers.
    """
    for number, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if parse_date(cell(row, date_column)) is None:
            continue
        amount = parse_amount(cell(row, amount_column), number)
        if amount is not None:
            yield number, row, amount


def transaction(account: str, date_text: str, amount: float, description: str) -> dict:
    """One row in the transactions shape; the id comes from row_ids, the category from Phase 2."""
    date = parse_date(date_text)
    assert date is not None, "data_rows only yields rows with a date"
    return {"account": account, "date": date, "month": date[:7], "amount": amount, "description": description.strip()}


def parse_bofa(rows: list[list[str]], header_index: int) -> list[dict]:
    """Bank of America checking. Amounts are already signed the app's way."""
    columns = column_positions(rows[header_index])
    return [
        transaction("bofa_checking", cell(row, columns["date"]), amount, cell(row, columns["description"]))
        for _, row, amount in data_rows(rows, header_index, columns["date"], columns["amount"])
    ]


def parse_amex(rows: list[list[str]], header_index: int) -> list[dict]:
    """Amex. The file has charges positive and payments negative, so every sign is flipped."""
    columns = column_positions(rows[header_index])
    parsed = []
    for _, row, amount in data_rows(rows, header_index, columns["date"], columns["amount"]):
        flipped = -amount if amount else 0.0  # flipping 0.0 would store -0.0
        parsed.append(transaction("amex", cell(row, columns["date"]), flipped, cell(row, columns["description"])))
    return parsed


def fidelity_account(label: str) -> str:
    """The ACCOUNTS key whose fidelity_label appears in a Fidelity Account cell.

    When several labels appear, the longest wins: CASH MANAGEMENT beats INDIVIDUAL for
    "Cash Management (Individual)". No match, or a tie, raises LoadError.
    """
    text = normalize(label)
    matches = sorted(
        (
            (len(normalize(spec["fidelity_label"])), key)
            for key, spec in config.ACCOUNTS.items()
            if spec.get("fidelity_label") and normalize(spec["fidelity_label"]) in text
        ),
        reverse=True,
    )
    if not matches:
        raise LoadError(f"the Fidelity account {label.strip()!r} matches no fidelity_label in accounts.toml")
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        raise LoadError(f"the Fidelity account {label.strip()!r} matches two fidelity_labels in accounts.toml equally well")
    return matches[0][1]


def parse_fidelity(rows: list[list[str]], header_index: int) -> list[dict]:
    """Fidelity. One file holds several accounts, told apart by the Account column. A row's text
    is its Action, followed by its Description when that says more than "No Description"."""
    columns = column_positions(rows[header_index])
    parsed = []
    for number, row, amount in data_rows(rows, header_index, columns["run date"], columns["amount"]):
        try:
            account = fidelity_account(cell(row, columns["account"]))
        except LoadError as error:
            raise LoadError(f"row {number}: {error}") from None
        description = cell(row, columns["action"]).strip()
        extra = cell(row, columns["description"]).strip() if "description" in columns else ""
        if extra and normalize(extra) != "NO DESCRIPTION":
            description = f"{description} {extra}"
        parsed.append(transaction(account, cell(row, columns["run date"]), amount, description))
    return parsed


PARSERS = {"fidelity": parse_fidelity, "bofa": parse_bofa, "amex": parse_amex}


def row_ids(transactions: list[dict]) -> list[str]:
    """Each row's id: the SHA-256 of account|date|amount|description|occurrence, where occurrence
    counts identical rows within the file, so two identical charges on one day are both kept."""
    seen: Counter[tuple[str, str, str, str]] = Counter()
    ids = []
    for row in transactions:
        key = (row["account"], row["date"], f"{row['amount']:.2f}", row["description"])
        seen[key] += 1
        ids.append(hashlib.sha256("|".join([*key, str(seen[key])]).encode()).hexdigest())
    return ids


def insert_new(connection: sqlite3.Connection, transactions: list[dict], ids: list[str], imported_at: str) -> int:
    """Insert the rows whose ids aren't stored yet; return how many were new."""
    before = connection.total_changes
    connection.executemany(
        "INSERT OR IGNORE INTO transactions (id, account, date, amount, description, category, month, imported_at)"
        " VALUES (?, ?, ?, ?, ?, 'uncategorized', ?, ?)",  # categorize.py sorts it out; until then it shows
        [
            (row_id, row["account"], row["date"], row["amount"], row["description"], row["month"], imported_at)
            for row_id, row in zip(ids, transactions)
        ],
    )
    return connection.total_changes - before


def load_file(path: Path, connection: sqlite3.Connection, imported_at: str) -> dict:
    """Load one export and record it in imports; return that imports row.

    Raises LoadError, in which case nothing from the file is inserted.
    """
    try:
        text = read_text(path)
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except (OSError, csv.Error) as error:
        raise LoadError(f"can't read it as CSV: {error}") from None
    header_index, institution = find_header(rows, text)
    transactions = PARSERS[institution](rows, header_index)
    ids = row_ids(transactions)
    with connection:  # one database transaction per file: all of it or none of it
        new = insert_new(connection, transactions, ids, imported_at)
        record = {
            "file": path.name,
            "institution": institution,
            "rows_read": len(transactions),
            "rows_new": new,
            "rows_duplicate": len(transactions) - new,
            "imported_at": imported_at,
        }
        connection.execute(
            "INSERT INTO imports (file, institution, rows_read, rows_new, rows_duplicate, imported_at)"
            " VALUES (:file, :institution, :rows_read, :rows_new, :rows_duplicate, :imported_at)",
            record,
        )
    return record


def load_files(paths: list[Path], db_path: Path | None = None) -> list[dict]:
    """Load each export as one import run, into config.DB_PATH unless db_path is given.

    Returns one result per file: its imports row, or {"file": name, "error": reason} for a file
    that was rejected.
    """
    imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
    connection = db.connect(db_path)
    results: list[dict] = []
    try:
        for path in paths:
            try:
                results.append(load_file(path, connection, imported_at))
            except LoadError as error:
                results.append({"file": path.name, "error": str(error)})
    finally:
        connection.close()
    return results


def csv_files(paths: list[Path]) -> list[Path]:
    """The files named, plus the CSV files inside any folders named, in a stable order."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(child for child in path.iterdir() if child.suffix.lower() == ".csv"))
        else:
            files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    """Load the exports named on the command line; return 1 if any file was rejected."""
    parser = argparse.ArgumentParser(description="Load bank CSV exports into finance.db.")
    parser.add_argument("paths", nargs="+", type=Path, help="export files, or folders of them")
    args = parser.parse_args(argv)
    files = csv_files(args.paths)
    if not files:
        print("no CSV files found", file=sys.stderr)
        return 1
    results = load_files(files)
    for result in results:
        if "error" in result:
            print(f"{result['file']}: rejected: {result['error']}", file=sys.stderr)
        else:
            print(
                f"{result['file']}: {result['institution']}, {result['rows_read']} read, "
                f"{result['rows_new']} new, {result['rows_duplicate']} duplicate"
            )
    return 1 if any("error" in result for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
