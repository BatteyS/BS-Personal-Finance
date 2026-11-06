"""Scrub real bank exports into samples that are safe to share with Claude Code.

Run it yourself, in your own terminal, on files straight from the bank:

    uv run scrub.py ~/Downloads/stmt.csv ~/Downloads/activity.csv --redact "Jane Example"

Each input becomes samples/<name>.scrubbed.csv with the same layout (preamble lines, header,
data rows, footer lines), so the loader's parsers see the real shape. In the data rows:

- amounts are rounded to whole dollars and keep their format; a non-zero amount never rounds
  to zero, and both legs of a transfer round to the same value, so they still match;
- balance columns become zero;
- account-number columns have every digit replaced;
- Card Member names become REDACTED;
- dates, prices, merchant text, Fidelity actions and account labels are kept.

Everywhere else too: bare numbers outside the data rows (BofA's summary block) become zero;
the names in BofA's INDN: field, Zelle payments and wire beneficiaries, and every --redact
phrase, become REDACTED; and any run of five or more
digits, or four digits that are not a year, becomes a stand-in of the same length. Within one
run the same digits always get the same stand-in, so distinct accounts and references stay
distinct.

A file is refused, and nothing is written for it, when no header row or no data rows can be
found. The script prints counts, never values. Read every output before you share it.
"""

from __future__ import annotations

import argparse
import codecs
import csv
import io
import re
import sys
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

REDACTED = "REDACTED"
DEFAULT_OUT = Path(__file__).resolve().parent / "samples"

# A cell holding one number and nothing else: "-1,234.56", "$12.00", "(45.10)", " 625".
NUMBER = re.compile(r"^(\s*[-+($]*)(\d[\d,]*)(\.\d+)?(\)?\s*)$")
DATE = re.compile(r"^\s*(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\s*$")
DIGITS = re.compile(r"\d+")
YEAR = re.compile(r"(19|20)\d\d")
# Names inside bank descriptions. Each pattern's first group is kept and the name after it
# becomes REDACTED:
#   BofA ACH:  "... INDN:JANE EXAMPLE CO ID:123 WEB"
#   Zelle:     "Zelle payment to JANE EXAMPLE for rent; Conf# abc", "Zelle Recurring payment from ..."
#   Wires:     "... BNF:1/ACME ID:123 ...", "... /ACC/Jane Example", "... ORIG:JANE EXAMPLE ID:..."
NEXT_FIELD = r"(?=\s+[A-Z]+(?: [A-Z]+)?:|$)"
NAME_FIELDS = [
    re.compile(r"(INDN:)\s*.*?" + NEXT_FIELD, re.IGNORECASE),
    re.compile(r"(Zelle(?: Recurring)? payment (?:to|from) ).+?(?=\s+for\b|\s+Conf#|;|$)", re.IGNORECASE),
    re.compile(r"((?:BNF|ORIG):\s*).*?(?=\s+ID:|$)", re.IGNORECASE),  # the name runs up to " ID:"
    re.compile(r"(/ACC/).*?(?=\s+[A-Z]+:|$)", re.IGNORECASE),
]

REPORT_ORDER = ["data rows", "amounts rounded", "numbers zeroed", "numbers masked", "names redacted"]


def normalize_header(name: str) -> str:
    """Lowercase a header cell and collapse its whitespace."""
    return " ".join(name.lower().split())


def find_header(rows: list[list[str]]) -> int | None:
    """Index of the first row naming a date column and a different money column (an amount, or a
    debit or credit), or None."""
    for index, row in enumerate(rows):
        names = [normalize_header(cell) for cell in row]
        dates = [i for i, name in enumerate(names) if "date" in name]
        amounts = [i for i, name in enumerate(names) if re.match(r"(amount|debit|credit|withdrawal|deposit)", name)]
        if any(d != a for d in dates for a in amounts):
            return index
    return None


def column_role(header: str) -> str:
    """How a data column is scrubbed, decided from its header text."""
    name = normalize_header(header)
    if "date" in name or re.search(r"\b(price|rate)\b", name):
        return "keep"
    if re.search(r"\bbal", name):
        return "zero"
    if re.search(r"\baccount (number|no\b|#)|\bacct\b", name):
        return "account number"
    if "card member" in name:
        return "person"
    if re.search(r"\b(amount|quantity|commission|fees?|interest|debits?|credits?|withdrawals?|deposits?)\b", name):
        return "round"
    return "text"


def rebuild_number(match: re.Match[str], value: Decimal, keep_sign: bool) -> str:
    """Write `value` in the matched cell's format: its symbols, sign, separators and decimals."""
    prefix, whole, fraction, suffix = match.group(1), match.group(2), match.group(3) or "", match.group(4)
    if not keep_sign:
        prefix = prefix.replace("-", "").replace("+", "").replace("(", "")
        suffix = suffix.replace(")", "")
    places = len(fraction) - 1 if fraction else 0
    spec = f",.{places}f" if "," in whole else f".{places}f"
    return prefix + format(value, spec) + suffix


def round_amount(cell: str, step: int, counts: Counter[str]) -> str:
    """Round a money cell to the nearest `step` dollars. A non-zero amount never becomes zero."""
    match = NUMBER.match(cell)
    if not match:
        return cell
    value = Decimal(match.group(2).replace(",", "") + (match.group(3) or ""))
    rounded = (value / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step
    if rounded == 0 and value != 0:
        rounded = Decimal(step)
    counts["amounts rounded"] += 1
    return rebuild_number(match, rounded, keep_sign=True)


def zero_number(cell: str, counts: Counter[str]) -> str:
    """Replace a numeric cell with zero in the same format; leave any other cell alone."""
    match = NUMBER.match(cell)
    if not match:
        return cell
    counts["numbers zeroed"] += 1
    return rebuild_number(match, Decimal(0), keep_sign=False)


def stand_in(digits: str, stand_ins: dict[str, str]) -> str:
    """A same-length replacement for a run of digits, the same for the whole run of the script."""
    if digits not in stand_ins:
        stand_ins[digits] = str(len(stand_ins) + 1).zfill(len(digits))
    return stand_ins[digits]


def mask_text(text: str, redact: list[str], stand_ins: dict[str, str], counts: Counter[str]) -> str:
    """Redact names, then replace digit runs that could identify someone. Years are kept."""
    redacted = text
    for pattern in NAME_FIELDS:
        redacted = pattern.sub(r"\g<1>" + REDACTED, redacted)
    for phrase in redact:
        redacted = re.sub(re.escape(phrase), REDACTED, redacted, flags=re.IGNORECASE)
    if redacted != text:
        counts["names redacted"] += 1

    def replace(match: re.Match[str]) -> str:
        digits = match.group(0)
        if len(digits) < 4 or (len(digits) == 4 and YEAR.fullmatch(digits)):
            return digits
        counts["numbers masked"] += 1
        return stand_in(digits, stand_ins)

    return DIGITS.sub(replace, redacted)


def mask_account_number(cell: str, stand_ins: dict[str, str], counts: Counter[str]) -> str:
    """Replace every digit in an account-number cell, consistently across rows and files."""
    if not DIGITS.search(cell):
        return cell
    counts["numbers masked"] += 1
    return DIGITS.sub(lambda match: stand_in(match.group(0), stand_ins), cell)


def scrub_data_cell(
    cell: str, role: str, redact: list[str], step: int, stand_ins: dict[str, str], counts: Counter[str]
) -> str:
    """Scrub one cell of a data row according to its column's role."""
    if role == "keep" or not cell.strip():
        return cell
    if role == "person":
        counts["names redacted"] += 1
        return REDACTED
    if role == "account number":
        return mask_account_number(cell, stand_ins, counts)
    if NUMBER.match(cell):
        if role == "zero":
            return zero_number(cell, counts)
        if role == "round" or "." in cell:  # a decimal in an unknown column is treated as money
            return round_amount(cell, step, counts)
    return mask_text(cell, redact, stand_ins, counts)


def scrub_loose_cell(cell: str, redact: list[str], stand_ins: dict[str, str], counts: Counter[str]) -> str:
    """Scrub a cell outside the data rows: a bare number becomes zero, text is masked."""
    if NUMBER.match(cell):
        return zero_number(cell, counts)
    return mask_text(cell, redact, stand_ins, counts)


def is_data_row(row: list[str], header: list[str], date_column: int) -> bool:
    """A row below the header is data when it is as wide as the header or its date cell parses."""
    if len(row) == len(header):
        return True
    return len(row) > date_column and bool(DATE.match(row[date_column]))


def scrub_text(text: str, redact: list[str], step: int, stand_ins: dict[str, str]) -> tuple[str, Counter[str]]:
    """Scrub one export's text. Returns the scrubbed text and counts of what changed."""
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header_index = find_header(rows)
    if header_index is None:
        raise ValueError("no header row found (looked for a row naming a date column and an amount column)")
    header = rows[header_index]
    roles = [column_role(name) for name in header]
    date_column = next(i for i, name in enumerate(header) if "date" in normalize_header(name))  # first date column
    phrases = sorted({phrase.strip() for phrase in redact if phrase.strip()}, key=len, reverse=True)
    counts: Counter[str] = Counter()

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n" if "\r\n" in text else "\n")
    for index, row in enumerate(rows):
        if index == header_index:
            writer.writerow(row)
        elif index > header_index and is_data_row(row, header, date_column):
            counts["data rows"] += 1
            writer.writerow([
                scrub_data_cell(cell, roles[i] if i < len(roles) else "text", phrases, step, stand_ins, counts)
                for i, cell in enumerate(row)
            ])
        else:
            writer.writerow([scrub_loose_cell(cell, phrases, stand_ins, counts) for cell in row])
    if counts["data rows"] == 0:
        raise ValueError("found a header but no data rows (are the dates in an unusual format?)")
    return out.getvalue(), counts


def read_export(path: Path) -> tuple[str, str]:
    """The file's text, and the encoding to write it back in (a UTF-8 byte-order mark is kept)."""
    raw = path.read_bytes()
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def scrub_file(
    source: Path, out_dir: Path, redact: list[str], step: int, stand_ins: dict[str, str], force: bool = False
) -> tuple[Path, Counter[str]]:
    """Scrub `source` into out_dir/<stem>.scrubbed.csv. Never overwrites the input."""
    target = out_dir / f"{source.stem}.scrubbed.csv"
    if target.resolve() == source.resolve():
        raise ValueError("the output would overwrite the input")
    if target.exists() and not force:
        raise ValueError(f"{target.name} already exists in {out_dir}; pass --force to replace it")
    text, encoding = read_export(source)
    scrubbed, counts = scrub_text(text, redact, step, stand_ins)
    out_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(scrubbed, encoding=encoding, newline="")
    return target, counts


def main(argv: list[str] | None = None) -> int:
    """Scrub each file named on the command line; return 1 if any was refused."""
    parser = argparse.ArgumentParser(description="Scrub real bank exports into samples that are safe to share.")
    parser.add_argument("files", nargs="+", type=Path, help="CSV exports straight from the bank")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default: samples/ in this repo)")
    parser.add_argument(
        "--redact", action="append", default=[], metavar="TEXT",
        help="text to replace with REDACTED wherever it appears, such as your name; repeatable",
    )
    parser.add_argument("--step", type=int, default=1, help="round amounts to this many dollars (default: 1)")
    parser.add_argument("--force", action="store_true", help="replace outputs that already exist")
    args = parser.parse_args(argv)
    if args.step < 1:
        parser.error("--step must be at least 1")

    stand_ins: dict[str, str] = {}
    failed = False
    for source in args.files:
        try:
            target, counts = scrub_file(source, args.out, args.redact, args.step, stand_ins, args.force)
        except (OSError, ValueError, csv.Error) as error:
            print(f"{source.name}: not scrubbed: {error}", file=sys.stderr)
            failed = True
            continue
        summary = ", ".join(f"{counts[key]} {key}" for key in REPORT_ORDER)
        print(f"{source.name} -> {target}: {summary}")
    if not failed:
        print("Read each output before sharing it: names, account numbers and balances should be gone.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
