"""What the page can change: saving uploads, importing them, adding a rule and re-running the rules.

dashboard.py calls these; they write files and the database, and compute nothing for display.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path

import re

import budget
import categorize
import config
import db
import formats
import load
from load import normalize

KINDS = {"credit": "a credit card", "cash": "checking or savings", "brokerage": "a brokerage account"}
# What a first accounts.toml starts with when the page writes it: the pay schedule and no planned
# caps, but none of the example's accounts, which aren't the user's.
FIRST_ACCOUNTS = """# Your accounts, started by the page when you taught it your first file. Add more there, or here
# by hand: accounts.example.toml explains every field, including the caps for a 401(k), Roth IRA
# or HSA and the transfer pairs between your accounts.

# Contributions a year toward each cap: 26 if you're paid every two weeks, 24 twice a month, 12 monthly.
[contributions_per_year]
401k = 26
hsa = 26
roth = 12

[planned]
caps = []
"""


def save_upload(name: str, data: bytes, now: datetime | None = None, exports_dir: Path | None = None) -> Path:
    """Keep an uploaded export as EXPORTS_DIR/YYYYMMDD-HHMMSS_<name>, so a bank's reused download
    name never overwrites last month's file. Only the file name from the browser is used."""
    folder = exports_dir or config.EXPORTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = Path(name).name or "export.csv"
    target = folder / f"{stamp}_{base}"
    copy = 2
    while target.exists():
        target = folder / f"{stamp}_{copy}_{base}"
        copy += 1
    target.write_bytes(data)
    return target


def rerun_rules() -> Counter[str]:
    """Recategorize every row with the current rules.csv; return the count per category."""
    rules = categorize.read_rules(categorize.ensure_rules_file())
    with closing(db.connect()) as connection:
        return categorize.categorize(connection, rules)


def import_files(paths: list[Path]) -> list[dict]:
    """Load saved exports, then recategorize everything; return load's result for each file.

    If rules.csv can't be read, the files stay loaded (their new rows show as uncategorized) and
    the rules error is added as one more result.
    """
    results = load.load_files(paths)
    try:
        rerun_rules()
    except categorize.RulesError as error:
        results.append({"file": "rules.csv", "error": f"{error}. The files were loaded; fix it and click Re-run rules."})
    return results


def add_rule(pattern: str, category: str, match: str = "contains", rules_path: Path | None = None,
             examples: list[str] | None = None) -> None:
    """Append one rule to rules.csv. The whole file is checked first, so a bad rule raises
    categorize.RulesError and the file is left as it was. With examples (the descriptions the rule is
    meant for), a rule that matches none of them is refused the same way."""
    path = categorize.ensure_rules_file(rules_path)
    line = io.StringIO()
    csv.writer(line, lineterminator="\n").writerow([pattern.strip(), category, match])
    current = path.read_text(encoding="utf-8-sig")
    if current and not current.endswith("\n"):
        current += "\n"
    trial = path.with_name(path.name + ".new")
    trial.write_text(current + line.getvalue(), encoding="utf-8")
    try:
        new_rule = categorize.read_rules(trial)[-1]
    except categorize.RulesError as error:
        trial.unlink()
        raise categorize.RulesError(str(error).replace(trial.name, path.name)) from None
    if examples and not any(new_rule["regex"].search(normalize(text)) for text in examples):
        trial.unlink()
        raise categorize.RulesError(f"{pattern.strip()!r} doesn't match any of this merchant's rows, so nothing would change")
    trial.replace(path)


# ---------------------------------------------------------------- teaching a new file

def plain_name(file_name: str) -> list[str]:
    """The words of an uploaded file's name, without the upload stamp, digits or punctuation, for
    the teaching form's first guesses: "20270201-093000_Chase1234_Activity.CSV" gives
    ["Chase", "Activity"]. Digits go, since a bank may put part of an account number there."""
    stem = re.sub(r"^\d{8}-\d{6}_(\d+_)?", "", Path(file_name).stem)
    return re.findall(r"[A-Za-z]+", stem)


def cell_amount(text: str) -> float | None:
    """A cell as a number, or None when it's empty or isn't one."""
    try:
        return load.parse_amount(text, 0)
    except load.LoadError:
        return None


def is_date(text: str) -> bool:
    return load.parse_date(text) is not None


def read_unknown(path: Path) -> dict:
    """What the teaching form shows for a file no layout matches: for a CSV, its header row, its
    first rows and first guesses at each column; for OFX, nothing more than its kind."""
    text = load.read_text(path)
    if load.ofx.is_ofx(text):
        return {"kind": "ofx"}
    rows = list(csv.reader(io.StringIO(text, newline="")))
    index = formats.guess_header(rows, is_date)
    if index is None:
        return {"kind": "csv", "header": None}
    header, below = rows[index], [row for row in rows[index + 1:] if any(cell.strip() for cell in row)]
    return {"kind": "csv", "header": header, "rows": below[:8],
            "suggest": formats.suggest(header, below[:50], is_date, cell_amount)}


def layout_from(fields: dict, header: list[str], account: str) -> dict:
    """A layout in the shape the loader reads, from the teaching form's choices."""
    one_column = fields["mode"] == "amount"
    return {
        "name": fields["name"].strip(), "headers": [formats.header_key(cell) for cell in header if cell.strip()],
        "account": account, "date": fields["date"],
        "amount": fields["amount"] if one_column else None,
        "spending_is": fields["spending_is"] if one_column else None,
        "debit": None if one_column else fields["debit"], "credit": None if one_column else fields["credit"],
        "description": list(fields["description"]),
    }


def preview_layout(path: Path, fields: dict, header: list[str], limit: int = 6) -> list[dict]:
    """The first transactions a layout would store from the file, before anything is saved.
    Raises load.LoadError when a chosen column doesn't hold amounts."""
    rows = list(csv.reader(io.StringIO(load.read_text(path), newline="")))
    index = next(i for i, row in enumerate(rows) if row == header)
    return load.parse_layout(rows, index, layout_from(fields, header, "(preview)"))[:limit]


def account_key(name: str) -> str:
    """A key for a new account, from its name and not one already taken: "Chase Freedom" becomes
    chase_freedom, or chase_freedom_2."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "account"
    key, number = base, 2
    while key in config.ACCOUNTS:
        key, number = f"{base}_{number}", number + 1
    return key


def replace_checked(path: Path, text: str, check) -> None:
    """Write text to path through a trial file that `check` must read without an error first, so a
    bad addition never replaces a good file. The trial is removed either way."""
    path.parent.mkdir(parents=True, exist_ok=True)
    trial = path.with_name(path.name + ".new")
    trial.write_text(text, encoding="utf-8")
    try:
        check(trial)
    except Exception:
        trial.unlink()
        raise
    trial.replace(path)


def add_account(name: str, kind: str, institution: str, paid_from: str | None = None, payment_hint: str = "") -> str:
    """Add an account to accounts.toml, starting it from the example when there is none yet, and
    for a card, the transfer pair with the account that pays it. Returns the new key; the accounts
    are reloaded in place."""
    if kind not in KINDS:
        raise config.AccountsError(f"kind must be one of {', '.join(KINDS)}")
    key = account_key(name)
    if config.ACCOUNTS_PATH.exists():
        text = config.ACCOUNTS_PATH.read_text(encoding="utf-8").rstrip("\n") + "\n\n[[account]]\n"
    else:
        text = FIRST_ACCOUNTS + "\n[[account]]\n"
    text += "".join(f"{field} = {budget.toml_value(value)}\n" for field, value in
                    (("key", key), ("name", name.strip()), ("institution", institution.strip().lower()), ("kind", kind)))
    if paid_from:
        text += f"\n[[transfer]]\naccounts = {budget.toml_value([paid_from, key])}\n"
        if payment_hint.strip():
            text += f"hints = {{ {budget.toml_value(paid_from)} = {budget.toml_value(payment_hint.strip().upper())} }}\n"
    replace_checked(config.ACCOUNTS_PATH, text, config.read_accounts)
    config.reload_accounts()
    return key


def add_to_formats(block: str) -> None:
    """Append one [[csv]] or [[ofx]] block to formats.toml, checked against the accounts first."""
    current = config.FORMATS_PATH.read_text(encoding="utf-8").rstrip("\n") + "\n\n" if config.FORMATS_PATH.exists() else ""
    replace_checked(config.FORMATS_PATH, current + block, formats.read_formats)


def teach_csv(path: Path, fields: dict, header: list[str], account: str) -> list[dict]:
    """Save a CSV layout for the file, then import it; returns load's result."""
    layout = layout_from(fields, header, account)
    lines = ["[[csv]]"] + [f"{key} = {budget.toml_value(value)}" for key, value in layout.items() if value]
    add_to_formats("\n".join(lines) + "\n")
    return import_files([path])


def teach_ofx(path: Path, account_ends: str, account: str) -> list[dict]:
    """Match an OFX account to one of yours by its last four digits, then import the file."""
    add_to_formats(f"[[ofx]]\naccount_ends = {budget.toml_value(account_ends)}\naccount = {budget.toml_value(account)}\n")
    return import_files([path])

