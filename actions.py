"""What the page can change: saving uploads, importing them, adding a rule and re-running the rules,
adding, renaming and removing a category of your own, teaching a file, adding an account,
recording a balance, and editing the budget, its tax assumptions and the pay schedule.

dashboard.py calls these; they write files and the database, and compute nothing for display.
"""

from __future__ import annotations

import csv
import io
import tomllib
from collections import Counter
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import re

import tomlkit

import budget
import categorize
import config
import db
import formats
import load
from load import normalize

# The kinds an account added on the page can be, with how the form words each.
KINDS = {"credit": "a credit card", "cash": "checking or savings", "brokerage": "a brokerage account",
         "retirement": "a retirement account (a 401(k), an IRA)", "hsa": "a health savings account", "loan": "a loan"}
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
# What a first budget.toml starts with when the page writes it, in place of the example's first
# lines; the example's explanation of the file follows.
FIRST_BUDGET = """# Your budget, started on the page from budget.example.toml, so any amount you haven't changed yet
# is the example's made-up one. Edit it on the page (the Edit switches on the Budget and
# Assumptions & taxes tabs), here by hand, or with `uv run budget.py --interview`.
#
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
    """Recategorize every row with the current categories.toml and rules.csv, both read again so a
    hand edit counts; return the count per category. Raises config.CategoriesError or
    categorize.RulesError naming the problem, and then changes nothing."""
    config.reload_categories()
    rules = categorize.read_rules(categorize.ensure_rules_file())
    with closing(db.connect()) as connection:
        return categorize.categorize(connection, rules)


def import_files(paths: list[Path]) -> list[dict]:
    """Load saved exports, then recategorize everything; return load's result for each file.

    If rules.csv or categories.toml can't be read, the files stay loaded (their new rows show as
    uncategorized) and the error is added as one more result.
    """
    results = load.load_files(paths)
    try:
        rerun_rules()
    except (categorize.RulesError, config.CategoriesError) as error:
        name = config.CATEGORIES_PATH.name if isinstance(error, config.CategoriesError) else "rules.csv"
        results.append({"file": name, "error": f"{error}. The files were loaded; fix it and click Re-run rules."})
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


# ---------------------------------------------------------------- categories of your own

CATEGORIES_HEADER = """# Categories of your own, added on the page. You can edit this by hand too, then click Re-run rules.
# A spending category counts as spending (the ring, Budget vs actual, the income statement's
# expenses); an income category counts as income. The built-in categories are in config.py.
"""


def category_name(name: str) -> str:
    """A category name as rules.csv and budget.toml spell it: lowercase, single spaces."""
    return " ".join(name.split()).lower()


def kind_of_yours(name: str, yours: dict[str, list[str]]) -> str:
    """Whether one of your categories is spending or income. Raises CategoriesError for a name
    that isn't yours: the built-in categories are the code's, and stay."""
    for kind in config.CATEGORY_KINDS:
        if name in yours[kind]:
            return kind
    raise config.CategoriesError(f"{name!r} isn't one of your categories; the built-in ones can't be changed")


def save_categories(yours: dict[str, list[str]]) -> None:
    """Write your categories to categories.toml through a trial file that must read cleanly, then
    reload them in place, so every picker on the page offers them at once."""
    text = CATEGORIES_HEADER + "".join(f"{kind} = {budget.toml_value(yours[kind])}\n" for kind in config.CATEGORY_KINDS)
    replace_checked(config.CATEGORIES_PATH, text, config.read_categories)
    config.reload_categories()


def add_category(name: str, kind: str) -> str:
    """Add a spending or income category of your own; returns its name as stored. A name that is
    taken, or that a rule couldn't spell, raises CategoriesError and changes nothing."""
    if kind not in config.CATEGORY_KINDS:
        raise config.CategoriesError(f"a category counts as {' or '.join(config.CATEGORY_KINDS)}, not {kind!r}")
    if not category_name(name):
        raise config.CategoriesError("type a name for the new category")
    yours = config.read_categories(config.CATEGORIES_PATH)  # the file as it stands, hand edits included
    yours[kind].append(category_name(name))
    save_categories(yours)
    return category_name(name)


def rules_naming(name: str, rules_path: Path | None = None) -> int:
    """How many rules in rules.csv sort rows into a category."""
    path = rules_path or config.RULES_PATH
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8-sig") as file:
        return sum(category_name(row.get("category") or "") == name for row in csv.DictReader(file))


def budget_lines_naming(name: str) -> list[str]:
    """The budget lines whose own `category` is this one. A budget.toml that can't be read is named
    instead, since what it uses can't be told."""
    if not config.BUDGET_PATH.exists():
        return []
    try:
        with config.BUDGET_PATH.open("rb") as file:
            lines = tomllib.load(file).get("line", [])
    except tomllib.TOMLDecodeError:
        return [f"in {config.BUDGET_PATH.name}, which can't be read right now"]
    return [str(line.get("item", "?")) for line in lines if category_name(str(line.get("category", ""))) == name]


def category_uses(name: str) -> str:
    """What uses a category, in words ("2 rules, the budget line Vet and 14 transactions"), or ""
    when nothing does. Only a category nothing uses is renamed or removed, so no rule, budget line
    or row is ever left pointing at a name that is gone."""
    uses = []
    rules = rules_naming(name)
    if rules:
        uses.append(f"{rules} rule{'s' * (rules != 1)}")
    lines = budget_lines_naming(name)
    if lines:
        uses.append(f"the budget line{'s' * (len(lines) != 1)} {', '.join(lines)}")
    with closing(db.connect()) as connection:
        rows = connection.execute("SELECT COUNT(*) FROM transactions WHERE category = ?", (name,)).fetchone()[0]
    if rows:
        uses.append(f"{rows} transaction{'s' * (rows != 1)}")
    return ", ".join(uses[:-1]) + " and " + uses[-1] if len(uses) > 1 else "".join(uses)


def refuse_if_used(name: str, change: str) -> None:
    """Raise CategoriesError when something uses the category, saying what."""
    uses = category_uses(name)
    if uses:
        raise config.CategoriesError(f"{name} is used by {uses}, so it can't be {change}. Move those to another "
                                     "category first (rules.csv and budget.toml), then click Re-run rules.")


def rename_category(name: str, new_name: str) -> str:
    """Rename one of your categories that nothing uses yet (a typo just made, say); returns the
    new name as stored."""
    yours = config.read_categories(config.CATEGORIES_PATH)
    kind = kind_of_yours(name, yours)
    if not category_name(new_name):
        raise config.CategoriesError("type the category's new name")
    refuse_if_used(name, "renamed")
    yours[kind][yours[kind].index(name)] = category_name(new_name)
    save_categories(yours)
    return category_name(new_name)


def remove_category(name: str) -> None:
    """Remove one of your categories that nothing uses."""
    yours = config.read_categories(config.CATEGORIES_PATH)
    kind = kind_of_yours(name, yours)
    refuse_if_used(name, "removed")
    yours[kind].remove(name)
    save_categories(yours)


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


def your_accounts() -> dict[str, dict]:
    """The accounts that are the user's own: none while the page runs on the example accounts,
    whose Fidelity, BofA and Amex accounts belong to nobody."""
    return {} if config.ACCOUNTS_SOURCE == config.ACCOUNTS_EXAMPLE else dict(config.ACCOUNTS)


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
    bad addition never replaces a good file. The trial is removed either way, and an error names
    the real file, not the trial."""
    path.parent.mkdir(parents=True, exist_ok=True)
    trial = path.with_name(path.name + ".new")
    trial.write_text(text, encoding="utf-8")
    try:
        check(trial)
    except Exception as error:
        trial.unlink()
        raise type(error)(str(error).replace(trial.name, path.name)) from None
    trial.replace(path)


def add_account(name: str, kind: str, institution: str, paid_from: str | None = None, payment_hint: str = "") -> str:
    """Add an account to accounts.toml, starting it from the example when there is none yet, and
    for a card, the transfer pair with the account that pays it. Returns the new key; the accounts
    are reloaded in place."""
    if kind not in KINDS:
        raise config.AccountsError(f"kind must be one of {', '.join(KINDS)}")
    if paid_from and paid_from not in your_accounts():
        raise config.AccountsError(f"{paid_from} isn't one of your accounts, so it can't pay this card. "
                                   "Choose another, or None of these and add it later.")
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


def record_balance(account: str, as_of: date, amount: float) -> None:
    """Store a balance typed on the page: what the account holds or, for a card or loan, what is
    owed, which is stored negative. A reading for the same account and day is replaced, and so
    are the holdings that came with it: a typed balance has none."""
    if account not in config.ACCOUNTS:
        raise config.AccountsError(f"{account} isn't one of your accounts")
    balance = -abs(amount) if config.ACCOUNTS[account]["kind"] in config.DEBT_KINDS else float(amount)
    with closing(db.connect()) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO balances (account, as_of, balance, source, recorded_at) VALUES (?, ?, ?, 'page', ?)",
            (account, as_of.isoformat(), round(balance, 2) + 0.0, datetime.now().astimezone().isoformat(timespec="seconds")),
        )
        connection.execute("DELETE FROM holdings WHERE account = ? AND as_of = ?", (account, as_of.isoformat()))


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



def budget_text() -> str:
    """budget.toml's text, or for a first save, the example's under a header that makes it yours."""
    if config.BUDGET_PATH.exists():
        return config.BUDGET_PATH.read_text(encoding="utf-8")
    _, _, explanation = budget.BUDGET_EXAMPLE.read_text(encoding="utf-8").partition("\n#\n")
    return FIRST_BUDGET + explanation


def save_budget_inputs(changes: dict[str, dict]) -> bool:
    """Write changed inputs ({section: {key: value}}, from budget.changed_inputs) into budget.toml,
    keeping its comments and layout; a first save starts the file from the example. A budget that
    wouldn't load isn't written. Returns whether anything was."""
    if not changes:
        return False
    replace_checked(config.BUDGET_PATH, budget.with_inputs(budget_text(), changes), budget.load)
    return True


def save_budget_lines(lines: list[tuple[int | None, dict]], read: list[dict]) -> bool:
    """Write the line editor's lines (from budget.edited_lines) into budget.toml, keeping its comments
    and layout. `read` is the lines the editor showed: if the file's lines have changed since, by
    hand or from another tab, nothing is written. Returns whether anything was."""
    if [origin for origin, _ in lines] == list(range(len(read))) and [line for _, line in lines] == read:
        return False
    current = budget.load(config.BUDGET_PATH if config.BUDGET_PATH.exists() else budget.BUDGET_EXAMPLE)
    if current["line"] != read:
        raise budget.BudgetError("budget.toml's lines changed after the page showed them, so nothing was saved. "
                                 "Look them over and make the change again.")
    replace_checked(config.BUDGET_PATH, budget.with_lines(budget_text(), lines), budget.load)
    return True


def save_pay_schedule(per_year: dict[str, int], planned: list[str]) -> bool:
    """Write the pay schedule (contributions a year toward each cap) and the planned caps into
    accounts.toml, keeping its comments and layout, and reload the accounts in place, so the
    verdict's pace slack uses them on the next run. Returns whether anything changed."""
    if per_year == config.CONTRIBUTIONS_PER_YEAR and set(planned) == config.PLANNED_CAPS:
        return False
    if not your_accounts():
        raise config.AccountsError("The pay schedule is kept with your accounts, and there are none yet: "
                                   "import a bank export and teach it to the page first.")
    document = tomlkit.parse(config.ACCOUNTS_PATH.read_text(encoding="utf-8"))
    if "contributions_per_year" not in document:
        document["contributions_per_year"] = tomlkit.table()
    for cap, count in per_year.items():
        budget.set_toml_value(document["contributions_per_year"], cap, count)
    if planned or "planned" in document:
        if "planned" not in document:
            document["planned"] = tomlkit.table()
        budget.set_toml_value(document["planned"], "caps", sorted(planned))
    replace_checked(config.ACCOUNTS_PATH, tomlkit.dumps(document), config.read_accounts)
    config.reload_accounts()
    return True
