"""What changes once a year, where the data lives, which accounts exist and the categories.

The other modules read these values. Everything about one person's money (their accounts, the
transfers between them, their pay schedule) is read from accounts.toml in the data folder, or from
accounts.example.toml until that exists, and their own categories from categories.toml, so this
file holds nothing personal. See docs/spec.md, Config.
"""

import os
import re
import tomllib
from pathlib import Path

# Real data lives outside git: data/ in this repo on the laptop, /data in the container.
DATA_DIR = Path(os.environ.get("FINANCE_DATA_DIR") or Path(__file__).resolve().parent / "data")
DB_PATH = DATA_DIR / "finance.db"
RULES_PATH = DATA_DIR / "rules.csv"  # created from rules.example.csv when missing
EXPORTS_DIR = DATA_DIR / "exports"  # every uploaded file, kept
BUDGET_PATH = DATA_DIR / "budget.toml"  # copied from budget.example.toml and filled in by hand
ACCOUNTS_PATH = DATA_DIR / "accounts.toml"  # copied from accounts.example.toml and made your own
ACCOUNTS_EXAMPLE = Path(__file__).resolve().parent / "accounts.example.toml"
FORMATS_PATH = DATA_DIR / "formats.toml"  # layouts taught on the page, for institutions with no built-in parser
CATEGORIES_PATH = DATA_DIR / "categories.toml"  # categories of your own, added on the page

# IRS caps by year. Add next year's row when the IRS announces it
# (401(k) and IRA in October or November, HSA in May).
CAPS = {
    2026: {"401k": 24_500, "roth": 7_500, "hsa": 4_400},
    2027: {"401k": 25_500, "roth": 7_500, "hsa": 4_500},  # 401k and roth projected; hsa official
}

# Caps that count employer money. The 401(k) deferral cap doesn't; the HSA cap does.
CAPS_INCLUDE_EMPLOYER = {"hsa"}

# Institutions whose exports have a built-in parser (load.py). Any other short name, like "chase",
# is an institution whose files come in through a layout taught on the page or as OFX (formats.py).
BUILT_IN_INSTITUTIONS = {"fidelity", "bofa", "amex"}
INSTITUTION_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
KINDS = {"cash", "brokerage", "retirement", "hsa", "credit", "loan"}
INVESTMENT_KINDS = {"brokerage", "retirement", "hsa"}  # the market moves their balances
DEBT_KINDS = {"credit", "loan"}  # what is owed: their balances are negative
CAP_KEYS = {"401k", "roth", "hsa"}


class AccountsError(Exception):
    """accounts.toml can't be used. The message names the problem."""


def read_accounts(path: Path) -> dict:
    """Read and check an accounts file. Returns accounts (key to institution, kind, cap_key, name and
    fidelity_label), transfer_pairs, planned_caps and contributions_per_year, in the shapes the rest
    of the code reads. Raises AccountsError naming the first problem."""
    try:
        with path.open("rb") as file:
            raw = tomllib.load(file)
    except tomllib.TOMLDecodeError as error:
        raise AccountsError(f"{path.name} isn't valid TOML: {error}") from None
    accounts: dict[str, dict] = {}
    for number, entry in enumerate(raw.get("account", []), start=1):
        key = entry.get("key", "")
        where = f"{path.name}, account {number} ({key or 'no key'})"
        if not key or key in accounts:
            raise AccountsError(f"{where}: each [[account]] needs a key of its own")
        if not INSTITUTION_NAME.match(str(entry.get("institution", ""))):
            raise AccountsError(f"{where}: institution must be a short lowercase name, like fidelity, bofa, amex or chase")
        if entry.get("kind") not in KINDS:
            raise AccountsError(f"{where}: kind must be one of {', '.join(sorted(KINDS))}")
        if entry.get("cap") not in (None, *CAP_KEYS):
            raise AccountsError(f"{where}: cap must be one of {', '.join(sorted(CAP_KEYS))}, or left out")
        if entry["institution"] == "fidelity" and not entry.get("fidelity_label"):
            raise AccountsError(f"{where}: a Fidelity account needs a fidelity_label")
        accounts[key] = {
            "institution": entry["institution"], "kind": entry["kind"], "cap_key": entry.get("cap"),
            "name": entry.get("name", key),
            **({"fidelity_label": entry["fidelity_label"]} if entry.get("fidelity_label") else {}),
        }
    if not accounts:
        raise AccountsError(f"{path.name} lists no [[account]]")
    pairs = []
    for number, entry in enumerate(raw.get("transfer", []), start=1):
        pair, hints = entry.get("accounts", []), entry.get("hints", {})
        if len(pair) != 2 or pair[0] == pair[1] or any(key not in accounts for key in pair):
            raise AccountsError(f"{path.name}, transfer {number}: accounts must name two different accounts above")
        if any(key not in pair for key in hints):
            raise AccountsError(f"{path.name}, transfer {number}: a hint must belong to one of its two accounts")
        pairs.append({"accounts": tuple(pair), "hints": dict(hints)})
    planned = set(raw.get("planned", {}).get("caps", []))
    if not planned <= CAP_KEYS:
        raise AccountsError(f"{path.name}: [planned] caps must be among {', '.join(sorted(CAP_KEYS))}")
    per_year = raw.get("contributions_per_year", {})
    if set(per_year) != CAP_KEYS or any(not isinstance(count, int) or count < 1 for count in per_year.values()):
        raise AccountsError(f"{path.name}: [contributions_per_year] needs a whole number for each of "
                            f"{', '.join(sorted(CAP_KEYS))}")
    return {"accounts": accounts, "transfer_pairs": pairs, "planned_caps": planned, "contributions_per_year": per_year}


ACCOUNTS_SOURCE = ACCOUNTS_PATH if ACCOUNTS_PATH.exists() else ACCOUNTS_EXAMPLE
_settings = read_accounts(ACCOUNTS_SOURCE)
ACCOUNTS = _settings["accounts"]
# Pairs of your own accounts whose legs net out, matched top to bottom.
TRANSFER_PAIRS = _settings["transfer_pairs"]
# Caps with no export to measure yet: the bar assumes they're maxed, and they never count as behind.
PLANNED_CAPS = _settings["planned_caps"]
# Contributions per year for each cap, for the pace slack: 26 biweekly, 24 twice a month, 12 monthly.
CONTRIBUTIONS_PER_YEAR = _settings["contributions_per_year"]


def reload_accounts() -> None:
    """Read the accounts file again, after the page added an account to it. The settings above are
    updated in place, so every module that reads them sees the change without a restart."""
    global ACCOUNTS_SOURCE
    ACCOUNTS_SOURCE = ACCOUNTS_PATH if ACCOUNTS_PATH.exists() else ACCOUNTS_EXAMPLE
    settings = read_accounts(ACCOUNTS_SOURCE)
    ACCOUNTS.clear()
    ACCOUNTS.update(settings["accounts"])
    TRANSFER_PAIRS[:] = settings["transfer_pairs"]
    PLANNED_CAPS.clear()
    PLANNED_CAPS.update(settings["planned_caps"])
    CONTRIBUTIONS_PER_YEAR.clear()
    CONTRIBUTIONS_PER_YEAR.update(settings["contributions_per_year"])
TRANSFER_WINDOW_DAYS = 5

# Pass 1 of categorization: rows on investment accounts and every Fidelity account, matched against
# the description as whole words, plurals included ("FEE" matches FEES but not COFFEE or FEEDING);
# first match wins. "contribution" and "employer" only apply on accounts with a cap.
INVESTMENT_ACTIONS = [
    ("EMPLOYER", "employer"),
    ("MATCH", "employer"),
    ("CONTRIBUTION", "contribution"),  # also "Contributions"; no payroll deferral seen in a sample yet
    ("YOU BOUGHT", "internal"),
    ("YOU SOLD", "internal"),
    ("REINVESTMENT", "internal"),
    ("DIVIDEND", "internal"),
    ("CAPITAL GAIN", "internal"),
    ("INTEREST", "internal"),
    ("FEE", "internal"),
    ("CHANGE IN MARKET VALUE", "internal"),  # 401(k) rows, seen in the sample
    ("REVENUE CREDIT", "internal"),
]
# The pass-1 words that mean money received: a dividend, interest or a capital gain distribution.
# A positive `investment` row carrying one is investment income on the income statement.
INVESTMENT_INCOME = ["DIVIDEND", "CAPITAL GAIN", "INTEREST"]
PRIOR_YEAR_MARKER = "PRIOR YEAR"  # an IRA or HSA contribution for the previous tax year

# The categories every copy has. Spending ones count as spending; "income" counts as income; the
# rest come from passes 1 and 2 and count as neither.
BUILT_IN_SPENDING = [
    "housing", "groceries", "dining", "gas", "transit", "utilities",
    "subscriptions", "travel", "health", "shopping", "going out", "hobbies", "uncategorized",
]
OTHER_CATEGORIES = ["income", "retirement", "employer_match", "investment", "transfer"]
# Categories of your own, added on the page, are each spending or income.
CATEGORY_KINDS = ("spending", "income")
CATEGORY_NAME = re.compile(r"^[a-z][a-z0-9 &'/-]*$")
RESERVED_CATEGORIES = {"total"}  # Budget vs actual's last row


class CategoriesError(Exception):
    """categories.toml can't be used. The message names the problem."""


def read_categories(path: Path) -> dict[str, list[str]]:
    """Your own categories from a categories file, {"spending": [...], "income": [...]} in file
    order; both empty when there is no file. Names are lowercased with their spaces collapsed, as
    rules.csv's are. Raises CategoriesError naming the first problem."""
    if not path.exists():
        return {kind: [] for kind in CATEGORY_KINDS}
    try:
        with path.open("rb") as file:
            raw = tomllib.load(file)
    except tomllib.TOMLDecodeError as error:
        raise CategoriesError(f"{path.name} isn't valid TOML: {error}") from None
    taken = set(BUILT_IN_SPENDING) | set(OTHER_CATEGORIES) | RESERVED_CATEGORIES
    yours: dict[str, list[str]] = {}
    for kind in CATEGORY_KINDS:
        names = raw.get(kind, [])
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise CategoriesError(f"{path.name}: {kind} must be a list of names, like {kind} = [\"pets\"]")
        yours[kind] = []
        for name in (" ".join(name.split()).lower() for name in names):
            if not CATEGORY_NAME.match(name) or len(name) > 30:
                raise CategoriesError(f"{path.name}: {name!r} can't be a category name: up to 30 letters, digits, "
                                      "spaces and & ' / -, starting with a letter")
            if name in taken:
                raise CategoriesError(f"{path.name}: {name!r} is already a category")
            taken.add(name)
            yours[kind].append(name)
    return yours


def with_yours(yours: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """The spending and income categories with your own added: yours before uncategorized, which
    stays last, and after income."""
    return [*BUILT_IN_SPENDING[:-1], *yours["spending"], "uncategorized"], ["income", *yours["income"]]


YOUR_CATEGORIES = read_categories(CATEGORIES_PATH)
# What counts as spending (the ring, Budget vs actual, expenses) and what counts as income.
SPENDING_CATEGORIES, INCOME_CATEGORIES = with_yours(YOUR_CATEGORIES)


def all_categories() -> list[str]:
    """Every category a rule can name: spending, then income, then the rest."""
    return list(dict.fromkeys([*SPENDING_CATEGORIES, *INCOME_CATEGORIES, *OTHER_CATEGORIES]))


def reload_categories() -> None:
    """Read categories.toml again, after the page (or a hand edit) changed it. The lists above are
    updated in place, so every module and picker that reads them sees the change without a restart."""
    yours = read_categories(CATEGORIES_PATH)
    spending, income = with_yours(yours)
    YOUR_CATEGORIES.clear()
    YOUR_CATEGORIES.update(yours)
    SPENDING_CATEGORIES[:] = spending
    INCOME_CATEGORIES[:] = income
