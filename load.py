"""Load bank CSV exports into the database.

    uv run load.py data/exports/        # a folder, or individual files

For each file: find the header row by its text, tell the institution from the columns it names,
parse the rows into the transactions shape, and insert the ones not already stored. One line per
file says how many rows were read, new and duplicate. Fidelity, Bank of America and Amex have
built-in parsers; any other institution's CSV comes in through a layout taught on the page, and
OFX/QFX files through the account they name (formats.py, ofx.py). See docs/spec.md: Bank exports,
Row ids and Data model.

Some files also carry balances, kept as readings in the balances table (see docs/spec.md, Net
worth): Bank of America's running balance gives one reading per day in the file, and a Fidelity
positions download gives every Fidelity account's value on the day it was downloaded, and no
transactions at all. A positions download, and an OFX/QFX investment statement that lists its
positions, also say what each account holds: those holdings go in the holdings table, and the
account's reading is their sum, so the two always agree.

The parsers were written against the layouts documented in the spec, before any samples existed.
The scrubbed samples are the truth: check each parser against them.
"""

from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import io
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import config
import db
import formats
import ofx

DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d %b %Y",
                "%b-%d-%Y")
POSITIONS = "fidelity positions"  # the institution of a Fidelity positions download
POSITIONS_DATE = re.compile(r"Positions_([A-Za-z]{3}-\d{2}-\d{4})")  # the day, in the download's name
DOWNLOADED = re.compile(r"Date downloaded\s+(\S+)", re.IGNORECASE)  # or in its footer
# Amex's plain download has exactly these three columns; with extra details it also has one of
# the Amex-only columns. BofA's checking export always adds Running Bal., so the two can't be confused.
AMEX_PLAIN_COLUMNS = {"date", "description", "amount"}
AMEX_ONLY_COLUMNS = {"card member", "account #", "extended details", "appears on your statement as", "reference"}


class LoadError(Exception):
    """A file that can't be loaded as it is. The message says why, and nothing from it is inserted."""


class UnknownLayout(LoadError):
    """A file the tracker hasn't been taught: a CSV whose header matches no layout ("csv"), or an
    OFX file whose account matches none of yours ("ofx", with the account number's last four
    digits). The page offers to teach it."""

    def __init__(self, message: str, kind: str, account_ends: str = ""):
        super().__init__(message)
        self.kind = kind
        self.account_ends = account_ends


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
    if {"account name", "current value"} <= names:
        return POSITIONS
    if {"date", "description", "amount", "running bal."} <= names:
        return "bofa"
    if names == AMEX_PLAIN_COLUMNS or (AMEX_PLAIN_COLUMNS <= names and names & AMEX_ONLY_COLUMNS):
        return "amex"
    return None


def find_header(rows: list[list[str]], text: str, layouts: list[dict] | None = None) -> tuple[int, str, dict | None]:
    """The header row's index, its institution, and the taught layout it matches (None for a
    built-in one). Raises UnknownLayout quoting the file's first three lines."""
    for index, row in enumerate(rows):
        institution = detect_institution({header_name(cell) for cell in row} - {""})
        if institution:
            return index, institution, None
        layout = formats.match_layout(row, layouts or [])
        if layout:
            return index, layout["name"], layout
    first_lines = "\n".join(f"    {line}" for line in text.splitlines()[:3]) or "    (the file is empty)"
    raise UnknownLayout(f"its layout isn't one this tracker knows yet; the file starts:\n{first_lines}", "csv")


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


def parse_number(text: str) -> float | None:
    """A cell like "12.345", "$1,234.5678" or "(3.00)" at full precision, for shares and prices;
    None for an empty cell or one that isn't a number ("--", "n/a"), since such a cell is a figure
    the file doesn't know rather than a mistake."""
    cleaned = text.strip().replace("$", "").replace(",", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    try:
        value = float(cleaned[1:-1] if negative else cleaned)
    except ValueError:
        return None
    return -value if negative else value


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


def bofa_readings(rows: list[list[str]], header_index: int) -> list[dict]:
    """Bank of America's running balance as one reading per day in the file: the balance after the
    day's last posted row. The export lists oldest first, so that is the last of the day's rows; a
    file that runs the other way is read the other way. The beginning-balance row counts too."""
    columns = column_positions(rows[header_index])
    dated = []
    for number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        day, balance_text = parse_date(cell(row, columns["date"])), cell(row, columns["running bal."]).strip()
        if day is None or not balance_text:
            continue
        dated.append((day, parse_amount(balance_text, number)))
    if not dated:
        return []
    newest_first = dated[0][0] > dated[-1][0]
    by_day: dict[str, float] = {}
    for day, balance in (reversed(dated) if newest_first else dated):
        by_day[day] = balance  # in posting order, the day's last balance wins
    return [{"account": "bofa_checking", "as_of": day, "balance": balance + 0.0} for day, balance in by_day.items()]


def parse_amex(rows: list[list[str]], header_index: int) -> list[dict]:
    """Amex. The file has charges positive and payments negative, so every sign is flipped."""
    columns = column_positions(rows[header_index])
    parsed = []
    for _, row, amount in data_rows(rows, header_index, columns["date"], columns["amount"]):
        flipped = -amount if amount else 0.0  # flipping 0.0 would store -0.0
        parsed.append(transaction("amex", cell(row, columns["date"]), flipped, cell(row, columns["description"])))
    return parsed


def match_fidelity_label(label: str) -> str | None:
    """The ACCOUNTS key whose fidelity_label appears in a Fidelity Account cell, or None when none does.

    When several labels appear, the longest wins: CASH MANAGEMENT beats INDIVIDUAL for
    "Cash Management (Individual)". A tie raises LoadError.
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
        return None
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        raise LoadError(f"the Fidelity account {label.strip()!r} matches two fidelity_labels in accounts.toml equally well")
    return matches[0][1]


def fidelity_account(label: str) -> str:
    """The ACCOUNTS key for a Fidelity Account cell; no match raises LoadError."""
    account = match_fidelity_label(label)
    if account is None:
        raise LoadError(f"the Fidelity account {label.strip()!r} matches no fidelity_label in accounts.toml")
    return account


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


def parse_positions(rows: list[list[str]], header_index: int) -> tuple[list[dict], int, list[str]]:
    """A Fidelity positions download: one holding per row with an Account name and a Current
    value, the core cash position and any pending activity included. Returns the holdings
    ({account, symbol, description, quantity, price, value, cost_basis}; the day is added by the
    caller), how many rows were read, and the Account names that match none of your
    fidelity_labels, which are skipped rather than guessed.

    Only Account name and Current value are needed; the other columns are read where the file has
    them, and a cell that isn't a number ("--") is a figure the file doesn't know. The asterisks
    Fidelity puts after the core position's symbol (SPAXX**) are dropped."""
    columns = column_positions(rows[header_index])

    def text(row: list[str], name: str) -> str:
        return cell(row, columns[name]).strip() if name in columns else ""

    holdings: list[dict] = []
    skipped: list[str] = []
    read = 0
    for number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        label, value_text = text(row, "account name"), text(row, "current value")
        if not label or not value_text:
            continue  # blank lines and the disclaimer block
        value = parse_amount(value_text, number)
        read += 1
        account = match_fidelity_label(label)
        if account is None:
            if label not in skipped:
                skipped.append(label)
            continue
        cost_basis = parse_number(text(row, "cost basis total"))
        holdings.append({
            "account": account, "symbol": text(row, "symbol").rstrip("*"), "description": text(row, "description"),
            "quantity": parse_number(text(row, "quantity")), "price": parse_number(text(row, "last price")),
            "value": value, "cost_basis": None if cost_basis is None else round(cost_basis, 2) + 0.0,
        })
    return holdings, read, skipped


def holding_readings(holdings: list[dict]) -> list[dict]:
    """One reading per account and day: the sum of its holdings' value, so a reading that comes
    with holdings always equals them."""
    totals: dict[tuple[str, str], float] = {}
    for holding in holdings:
        key = (holding["account"], holding["as_of"])
        totals[key] = totals.get(key, 0.0) + holding["value"]
    return [{"account": account, "as_of": as_of, "balance": round(total, 2) + 0.0}
            for (account, as_of), total in totals.items()]


def write_holdings(connection: sqlite3.Connection, holdings: list[dict], source: str, recorded_at: str) -> None:
    """Store holdings ({account, as_of, symbol, ...}) in the order given. Each account and day's
    holdings replace any stored for it: a positions download is the whole of what the account
    held that day."""
    days = list(dict.fromkeys((holding["account"], holding["as_of"]) for holding in holdings))
    for account, as_of in days:
        connection.execute("DELETE FROM holdings WHERE account = ? AND as_of = ?", (account, as_of))
        own = [holding for holding in holdings if (holding["account"], holding["as_of"]) == (account, as_of)]
        connection.executemany(
            "INSERT INTO holdings (account, as_of, line, symbol, description, quantity, price, value, cost_basis,"
            " source, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(account, as_of, line, holding["symbol"], holding["description"], holding["quantity"], holding["price"],
              holding["value"], holding["cost_basis"], source, recorded_at)
             for line, holding in enumerate(own, start=1)],
        )


def positions_date(file_name: str, rows: list[list[str]], header_index: int) -> str | None:
    """The day a positions file's balances were true: the date in its name
    (Portfolio_Positions_Nov-07-2026.csv, under any upload stamp), else the "Date downloaded" line
    in its footer; None when neither is there."""
    found = POSITIONS_DATE.search(file_name)
    if found:
        return parse_date(found.group(1))
    for row in rows[header_index + 1:]:
        found = DOWNLOADED.search(" ".join(row))
        if found:
            return parse_date(found.group(1))
    return None


def write_readings(connection: sqlite3.Connection, readings: list[dict], source: str, recorded_at: str) -> tuple[int, int]:
    """Store readings ({account, as_of, balance}), each replacing an earlier reading for the same
    account and day. Returns how many were written (new or changed) and how many were already
    there unchanged."""
    written = unchanged = 0
    for reading in readings:
        stored = connection.execute("SELECT balance FROM balances WHERE account = ? AND as_of = ?",
                                    (reading["account"], reading["as_of"])).fetchone()
        if stored and round(stored[0], 2) == round(reading["balance"], 2):
            unchanged += 1
            continue
        connection.execute(
            "INSERT OR REPLACE INTO balances (account, as_of, balance, source, recorded_at) VALUES (?, ?, ?, ?, ?)",
            (reading["account"], reading["as_of"], reading["balance"], source, recorded_at),
        )
        written += 1
    return written, unchanged


def parse_layout(rows: list[list[str]], header_index: int, layout: dict) -> list[dict]:
    """A CSV in a taught layout. One amount column is flipped when spending shows as positive;
    debit and credit columns become credit minus debit."""
    columns = {formats.header_key(name): index for index, name in reversed(list(enumerate(rows[header_index])))}
    parsed = []
    for number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        date_text = cell(row, columns[layout["date"]])
        if parse_date(date_text) is None:
            continue
        if layout["amount"]:
            amount = parse_amount(cell(row, columns[layout["amount"]]), number)
            if amount is not None and layout["spending_is"] == "positive":
                amount = -amount
        else:
            debit = parse_amount(cell(row, columns[layout["debit"]]), number)
            credit = parse_amount(cell(row, columns[layout["credit"]]), number)
            amount = None if debit is None and credit is None else round((credit or 0) - abs(debit or 0), 2)
        if amount is None:
            continue
        description = " ".join(cell(row, columns[name]).strip() for name in layout["description"]).strip()
        parsed.append(transaction(layout["account"], date_text, amount + 0.0, description))
    return parsed


def parse_ofx(text: str, matches: list[dict], today: str) -> tuple[list[dict], list[dict]]:
    """An OFX/QFX file: each statement's transactions, on the account its number ends like, and
    the holdings of each investment statement that lists its positions, as of its DTASOF (or
    `today`, an ISO date, without one)."""
    found = ofx.statements(text)
    if not found:
        raise LoadError("it looks like OFX but holds no statement")
    parsed, holdings = [], []
    for statement in found:
        account = formats.ofx_account(statement["account_number"], matches)
        if account is None:
            ends = "".join(c for c in statement["account_number"] if c.isdigit())[-4:]
            raise UnknownLayout(f"its {statement['kind']} account ending {ends or '(no number)'} isn't one of yours yet",
                                "ofx", ends)
        parsed += [{"account": account, "date": row["date"], "month": row["date"][:7], "amount": row["amount"],
                    "description": row["description"]} for row in statement["transactions"]]
        holdings += [{**holding, "account": account, "as_of": statement["as_of"] or today}
                     for holding in statement["positions"] or []]
    return parsed, holdings


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


def load_file(path: Path, connection: sqlite3.Connection, imported_at: str, known: dict | None = None) -> dict:
    """Load one export and record it in imports; return that imports row. `known` is what
    formats.read_formats returns; it's read here when not given.

    Raises LoadError, in which case nothing from the file is inserted.
    """
    known = known if known is not None else formats.read_formats()
    try:
        text = read_text(path)
    except OSError as error:
        raise LoadError(f"can't read it: {error}") from None
    readings: list[dict] = []
    holdings: list[dict] = []
    extra: dict = {}  # what the page says about a file's holdings, beyond its imports row
    if ofx.is_ofx(text):
        institution = "ofx"
        transactions, holdings = parse_ofx(text, known["ofx"], imported_at[:10])
        readings = holding_readings(holdings)
        if readings:
            extra = {"as_of": max(reading["as_of"] for reading in readings), "accounts": len(readings)}
    else:
        try:
            rows = list(csv.reader(io.StringIO(text, newline="")))
        except csv.Error as error:
            raise LoadError(f"can't read it as CSV: {error}") from None
        header_index, institution, layout = find_header(rows, text, known["csv"])
        if layout:
            transactions = parse_layout(rows, header_index, layout)
        elif institution == POSITIONS:
            transactions = []
            holdings, read, skipped = parse_positions(rows, header_index)
            as_of = positions_date(path.name, rows, header_index) or imported_at[:10]
            for holding in holdings:
                holding["as_of"] = as_of
            readings = holding_readings(holdings)
            extra = {"as_of": as_of, "accounts": len(readings), "skipped": skipped}
        else:
            transactions = PARSERS[institution](rows, header_index)
            if institution == "bofa":
                readings = bofa_readings(rows, header_index)
    ids = row_ids(transactions)
    with connection:  # one database transaction per file: all of it or none of it
        new = insert_new(connection, transactions, ids, imported_at)
        written, unchanged = write_readings(connection, readings, institution.replace(" ", "_"), imported_at)
        write_holdings(connection, holdings, institution.replace(" ", "_"), imported_at)
        record = {
            "file": path.name,
            "institution": institution,
            "rows_read": read if institution == POSITIONS else len(transactions),
            "rows_new": written if institution == POSITIONS else new,
            "rows_duplicate": unchanged if institution == POSITIONS else len(transactions) - new,
            "imported_at": imported_at,
        }
        connection.execute(
            "INSERT INTO imports (file, institution, rows_read, rows_new, rows_duplicate, imported_at)"
            " VALUES (:file, :institution, :rows_read, :rows_new, :rows_duplicate, :imported_at)",
            record,
        )
    return {**record, **extra}


def load_files(paths: list[Path], db_path: Path | None = None) -> list[dict]:
    """Load each export as one import run, into config.DB_PATH unless db_path is given.

    Returns one result per file: its imports row, or {"file": name, "error": reason} for a file
    that was rejected.
    """
    imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        known = formats.read_formats()
    except formats.FormatsError as error:
        return [{"file": path.name, "error": f"formats.toml: {error}"} for path in paths]
    connection = db.connect(db_path)
    results: list[dict] = []
    try:
        for path in paths:
            try:
                results.append(load_file(path, connection, imported_at, known))
            except UnknownLayout as error:
                results.append({"file": path.name, "error": str(error), "unknown": error.kind,
                                "account_ends": error.account_ends, "path": str(path)})
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
