"""The monthly budget: read budget.toml and work out every line, the way a budget spreadsheet
does. See docs/spec.md, Budget.

    uv run budget.py              # check budget.toml and print the summary by class
    uv run budget.py --interview  # answer questions in the terminal; writes budget.toml

The page edits the same file (the Edit switches on the Budget and Assumptions & taxes tabs), through
with_inputs and with_lines, which keep its comments and layout.

Every dollar of gross pay is assigned to a line. Most lines are typed amounts; taxes, payroll
savings, the HDHP premium and the commuter benefit are calculated from the inputs, and the surplus
line takes whatever is left, which goes to the taxable brokerage. Amounts are positive costs,
except the commuter benefit, which offsets the transit pass it covers.

budget.toml holds real figures, so it lives in the data folder, outside git. The repo ships
budget.example.toml with made-up amounts. On real data, run this in your own terminal.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tomllib
from pathlib import Path

import pandas as pd
import tomlkit

import config

BUDGET_EXAMPLE = Path(__file__).resolve().parent / "budget.example.toml"
CLASSES = ["Necessary", "Discretionary", "Surplus"]
# Lines whose amount is calculated: their `from` names one of these.
CALCULATED = [
    "federal_income_tax", "social_security", "medicare", "illinois_income_tax",
    "retirement_401k", "hsa", "roth_ira", "hdhp_premium", "commuter_benefit", "surplus",
]
# The spending category each budget line is compared with on the Budget vs actual tab, by line item
# name. A line's own `category` overrides this ("" leaves the line out of the comparison). Lines with
# neither, like taxes and payroll savings, aren't spending and aren't compared.
DEFAULT_CATEGORIES = {
    "Out-of-pocket medical / pharmacy": "health",
    "Rent": "housing",
    "Renter's insurance": "housing",
    "Electric": "utilities",
    "Gas / heat": "utilities",
    "Internet": "utilities",
    "Phone": "utilities",
    "Groceries": "groceries",
    "Dining out, takeout & coffee": "dining",
    "CTA 30-day pass": "transit",
    "Rideshare / Divvy (non-commute)": "transit",
    "Employer commuter benefit": "transit",  # nets the pass down to what you pay
    "Household & personal care": "shopping",
    "Clothing": "shopping",
    "Gym / fitness": "health",
    "Entertainment & social": "going out",
    "Subscriptions & streaming": "subscriptions",
    "Hobbies": "hobbies",
    "Gifts & donations": "shopping",
    "Travel": "travel",
    "Miscellaneous buffer": "uncategorized",
}
INPUTS = {
    "income": ["salary", "retirement_401k", "roth_ira", "hdhp_premium_monthly", "commuter_benefit_cap_monthly"],
    "hsa": ["limit", "employer"],
    "employer_match": ["rate", "cap_share_of_salary"],
    "tax": ["federal_standard_deduction", "social_security_rate", "social_security_wage_base", "medicare_rate",
            "illinois_rate", "illinois_exemption", "federal_brackets"],
}


class BudgetError(Exception):
    """budget.toml is missing something or has a line that can't be used. The message says what."""


def budget_path() -> Path:
    """The live budget file in the data folder (it may not exist yet)."""
    return config.BUDGET_PATH


def load(path: Path) -> dict:
    """Read and check a budget file. Raises BudgetError naming the first problem."""
    try:
        with path.open("rb") as file:
            budget = tomllib.load(file)
    except tomllib.TOMLDecodeError as error:
        raise BudgetError(f"{path.name} isn't valid TOML: {error}") from None
    for section, keys in INPUTS.items():
        for key in keys:
            if key not in budget.get(section, {}):
                raise BudgetError(f"{path.name}: [{section}] needs {key}")
    for section, key, *_ in QUESTIONS:
        value = budget[section][key]
        if not is_number(value) or value < 0:
            raise BudgetError(f"{path.name}: [{section}] {key} must be a number, zero or more")
        if (section, key) in FRACTIONS and value > 1:
            raise BudgetError(f"{path.name}: [{section}] {key} is a fraction, like 0.062 for 6.2%")
    if budget["hsa"]["employer"] > budget["hsa"]["limit"]:
        raise BudgetError(f"{path.name}: [hsa] employer can't be more than the limit, which covers both")
    brackets = budget["tax"]["federal_brackets"]
    if (not brackets or any(not isinstance(bracket, list) or len(bracket) != 2 or not all(map(is_number, bracket))
                            for bracket in brackets) or brackets[0][0] != 0):
        raise BudgetError(f"{path.name}: federal_brackets is a list of [over, rate] pairs starting at [0, rate]")
    if any(later[0] <= earlier[0] for earlier, later in zip(brackets, brackets[1:])):
        raise BudgetError(f"{path.name}: federal_brackets go from the lowest up, each over a larger amount than the last")
    if any(not 0 <= rate <= 1 for _, rate in brackets):
        raise BudgetError(f"{path.name}: each federal bracket's rate is a fraction, like 0.22 for 22%")
    lines = budget.get("line", [])
    items = [line.get("item", "") for line in lines]
    for number, line in enumerate(lines, start=1):
        where = f"{path.name}, line item {number} ({line.get('item') or 'no name'})"
        if not line.get("group") or not line.get("item"):
            raise BudgetError(f"{where}: each [[line]] needs a group and an item")
        if line.get("class") not in CLASSES:
            raise BudgetError(f"{where}: class must be one of {', '.join(CLASSES)}")
        if ("monthly" in line) == ("from" in line):
            raise BudgetError(f"{where}: give either monthly (a typed amount) or from (a calculated one), not both")
        if "monthly" in line and not is_number(line["monthly"]):
            raise BudgetError(f"{where}: monthly must be a number")
        if "from" in line and line["from"] not in CALCULATED:
            raise BudgetError(f"{where}: from must be one of {', '.join(CALCULATED)}")
        if line.get("from") == "commuter_benefit" and line.get("covers") not in items:
            raise BudgetError(f"{where}: covers must name the line item the benefit pays for")
        if line.get("category") not in (None, "", *config.SPENDING_CATEGORIES):
            raise BudgetError(f"{where}: category must be one of {', '.join(config.SPENDING_CATEGORIES)}, or \"\"")
    if [line.get("from") for line in lines].count("surplus") != 1:
        raise BudgetError(f"{path.name}: exactly one line must take the surplus (from = \"surplus\")")
    return budget


def is_number(value) -> bool:
    """Whether a value read from TOML is a number (TOML's true and false aren't)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def line_category(line: dict) -> str | None:
    """The spending category a budget line is compared with, or None when it isn't compared."""
    if "category" in line:
        return line["category"] or None
    return DEFAULT_CATEGORIES.get(line["item"])


def federal_income_tax(taxable: float, brackets: list[list[float]]) -> float:
    """Progressive federal tax: each bracket's rate applies to the income above its threshold."""
    tax, previous_rate = 0.0, 0.0
    for over, rate in brackets:
        if taxable > over:
            tax += (taxable - over) * (rate - previous_rate)
        previous_rate = rate
    return tax


def tax_calculation(budget: dict) -> dict[str, float]:
    """The Assumptions tab's annual calculation: taxable wages, each tax, take-home pay, and the
    employer's 401(k) match and HSA money."""
    income, hsa, tax, match = budget["income"], budget["hsa"], budget["tax"], budget["employer_match"]
    salary = income["salary"]
    hsa_payroll = hsa["limit"] - hsa["employer"]
    premium = income["hdhp_premium_monthly"] * 12
    taxable_wages = salary - income["retirement_401k"] - hsa_payroll - premium
    federal_taxable = max(0.0, taxable_wages - tax["federal_standard_deduction"])
    federal = federal_income_tax(federal_taxable, tax["federal_brackets"])
    fica_wages = salary - hsa_payroll - premium  # the 401(k) doesn't reduce FICA wages; payroll HSA and premium do
    social_security = min(fica_wages, tax["social_security_wage_base"]) * tax["social_security_rate"]
    medicare = fica_wages * tax["medicare_rate"]
    illinois_taxable = max(0.0, taxable_wages - tax["illinois_exemption"])
    illinois = illinois_taxable * tax["illinois_rate"]
    total = federal + social_security + medicare + illinois
    employer_match = min(income["retirement_401k"], salary * match["cap_share_of_salary"]) * match["rate"]
    return {
        "gross_salary": salary,
        "retirement_401k": income["retirement_401k"],
        "hsa_payroll": hsa_payroll,
        "hdhp_premium": premium,
        "taxable_wages": taxable_wages,
        "federal_taxable_income": federal_taxable,
        "federal_income_tax": federal,
        "fica_wages": fica_wages,
        "social_security": social_security,
        "medicare": medicare,
        "illinois_taxable_income": illinois_taxable,
        "illinois_income_tax": illinois,
        "total_taxes": total,
        "effective_tax_rate": total / salary if salary else 0.0,
        "take_home": taxable_wages - total,
        "employer_match": employer_match,
        "employer_hsa": hsa["employer"],
    }


def line_items(budget: dict) -> pd.DataFrame:
    """Every budget line with its monthly and annual amount and share of gross pay, in file order."""
    calc = tax_calculation(budget)
    gross = budget["income"]["salary"] / 12
    monthly_from = {
        "federal_income_tax": calc["federal_income_tax"] / 12,
        "social_security": calc["social_security"] / 12,
        "medicare": calc["medicare"] / 12,
        "illinois_income_tax": calc["illinois_income_tax"] / 12,
        "retirement_401k": calc["retirement_401k"] / 12,
        "hsa": calc["hsa_payroll"] / 12,
        "roth_ira": budget["income"]["roth_ira"] / 12,
        "hdhp_premium": budget["income"]["hdhp_premium_monthly"],
    }
    typed = {line["item"]: float(line["monthly"]) for line in budget["line"] if "monthly" in line}
    rows = []
    for line in budget["line"]:
        source = line.get("from")
        if source == "commuter_benefit":
            monthly = -min(typed.get(line["covers"], monthly_from.get(line["covers"], 0.0)),
                           budget["income"]["commuter_benefit_cap_monthly"])
        elif source == "surplus":
            monthly = None  # filled in below, once every other line is known
        elif source:
            monthly = monthly_from[source]
        else:
            monthly = float(line["monthly"])
        rows.append({"group": line["group"], "item": line["item"], "class": line["class"], "monthly": monthly,
                     "calculated": bool(source), "category": line_category(line), "note": line.get("note", "")})
    frame = pd.DataFrame(rows)
    surplus = frame["monthly"].isna()
    frame.loc[surplus, "monthly"] = gross - frame.loc[~surplus, "monthly"].sum()
    # Kept to full precision, as the spreadsheet does, so annual figures are exactly 12 x monthly and
    # the lines add up to gross; the page rounds to cents only when it shows them.
    frame["monthly"] = frame["monthly"].astype(float)
    frame["annual"] = frame["monthly"] * 12
    frame["share"] = frame["monthly"] / gross if gross else 0.0
    return frame


def with_totals(rows: list[tuple[str, float]], gross: float) -> pd.DataFrame:
    """Label, monthly, annual and share of gross for summary rows."""
    return pd.DataFrame(
        [{"label": label, "monthly": value, "annual": value * 12, "share": value / gross if gross else 0.0}
         for label, value in rows]
    )


def summary_by_class(budget: dict, lines: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The spreadsheet's top summary: gross split by class, then take-home pay and saving."""
    calc = tax_calculation(budget)
    gross = budget["income"]["salary"] / 12
    by_class = lines.groupby("class")["monthly"].sum()
    surplus = by_class.get("Surplus", 0.0)
    split = with_totals([
        ("Gross income", gross),
        ("Necessary (taxes, savings, essentials)", by_class.get("Necessary", 0.0)),
        ("Discretionary", by_class.get("Discretionary", 0.0)),
        ("Surplus → taxable brokerage", surplus),
        ("Total allocated (equals gross)", lines["monthly"].sum()),
    ], gross)
    saving = lines.loc[lines["group"].isin(["Pre-tax savings", "After-tax savings"]), "monthly"].sum() + surplus
    employer = (calc["employer_match"] + calc["employer_hsa"]) / 12
    commuter = -lines.loc[lines["item"].isin(commuter_items(budget)), "monthly"].sum()
    pay = with_totals([
        ("Paycheck take-home (after taxes and pre-tax deductions)", calc["take_home"] / 12),
        ("Your saving (401(k), HSA, Roth IRA and surplus)", saving),
        ("Employer contributions (401(k) match and HSA)", employer),
        ("Total saving, with employer contributions", saving + employer),
        ("Employer commuter benefit (tax-free, on top of gross)", commuter),
    ], gross)
    return split, pay


def commuter_items(budget: dict) -> list[str]:
    """The line items that are the employer commuter benefit."""
    return [line["item"] for line in budget["line"] if line.get("from") == "commuter_benefit"]


def budget_by_category(budget: dict) -> pd.DataFrame:
    """Monthly budget per spending category, with the budget lines that make it up, largest first."""
    lines = line_items(budget)
    compared = lines[lines["category"].notna()]
    frame = (compared.groupby("category", sort=False)
             .agg(monthly=("monthly", "sum"), lines=("item", lambda items: ", ".join(items)))
             .reset_index())
    return frame.sort_values("monthly", ascending=False, kind="stable").reset_index(drop=True)


def uncompared_lines(budget: dict) -> list[str]:
    """Typed lines (the ones you spend on) that no category is compared with."""
    return [line["item"] for line in budget["line"] if "monthly" in line and not line_category(line)]


def summary_by_group(lines: pd.DataFrame, gross: float) -> pd.DataFrame:
    """Monthly, annual and share of gross for each group, in the order the groups first appear."""
    groups = lines.groupby("group", sort=False)["monthly"].sum()
    rows = list(groups.items()) + [("Total", lines["monthly"].sum())]
    return with_totals(rows, gross)


# The interview's questions about inputs: (section, key, question, kind). Percent answers are typed
# as percents ("6.2") and stored as fractions (0.062).
QUESTIONS = [
    ("income", "salary", "Annual salary, gross", "dollars"),
    ("income", "retirement_401k", "401(k) contribution, annual (pre-tax)", "dollars"),
    ("income", "roth_ira", "Roth IRA contribution, annual", "dollars"),
    ("income", "hdhp_premium_monthly", "HDHP premium, monthly (pre-tax)", "dollars"),
    ("income", "commuter_benefit_cap_monthly", "Employer commuter benefit cap, monthly", "dollars"),
    ("hsa", "limit", "HSA annual limit (self-only)", "dollars"),
    ("hsa", "employer", "Employer HSA contribution, annual", "dollars"),
    ("employer_match", "rate", "401(k) match: employer adds this % of what you put in", "percent"),
    ("employer_match", "cap_share_of_salary", "401(k) match applies to contributions up to this % of salary", "percent"),
    ("tax", "federal_standard_deduction", "Federal standard deduction (single)", "dollars"),
    ("tax", "social_security_rate", "Social Security rate", "percent"),
    ("tax", "social_security_wage_base", "Social Security wage base", "dollars"),
    ("tax", "medicare_rate", "Medicare rate", "percent"),
    ("tax", "illinois_rate", "Illinois income tax rate", "percent"),
    ("tax", "illinois_exemption", "Illinois personal exemption", "dollars"),
]
# The inputs that can't be more than the whole: a tax rate, or a share of salary. (The match rate
# can: some employers add more than a dollar for each dollar.)
FRACTIONS = {("employer_match", "cap_share_of_salary"), ("tax", "social_security_rate"), ("tax", "medicare_rate"),
             ("tax", "illinois_rate")}
LINE_KEYS = ["group", "item", "class", "monthly", "from", "covers", "category", "note"]


def shown(value: float, kind: str) -> str:
    """A default as the interview offers it: "24,500" or "6.2"."""
    if kind == "percent":
        return f"{value * 100:g}"
    return f"{value:,.2f}".removesuffix(".00")


def parse(answer: str, kind: str) -> float:
    """A typed amount: "$24,500", "24500", "6.2%" or "6.2". Raises ValueError if it isn't a number."""
    number = float(answer.replace("$", "").replace(",", "").replace("%", "").replace("_", "").strip())
    if number < 0:
        raise ValueError("negative")
    value = number / 100 if kind == "percent" else number
    return int(value) if kind == "dollars" and value == int(value) else value


def ask(question: str, default: float, kind: str, prompt=input, say=print) -> float:
    """Ask for one amount until the answer is a number; Enter keeps the default."""
    unit = " %" if kind == "percent" else " $"
    while True:
        answer = prompt(f"{question}{unit} [{shown(default, kind)}]: ").strip()
        if not answer:
            return default
        try:
            return parse(answer, kind)
        except ValueError:
            say("  Type a number, like 1,850 or 6.2, or press Enter to keep the value in brackets.")


def ask_brackets(brackets: list[list[float]], prompt=input, say=print) -> list[list[float]]:
    """Keep the federal brackets, or type new ones from the lowest up."""
    say("Federal brackets (single):")
    for over, rate in brackets:
        say(f"  over ${over:,.0f}: {rate:.0%}")
    if prompt("Keep these brackets? [Y/n]: ").strip().lower() not in ("n", "no"):
        return brackets
    typed: list[list[float]] = []
    say("Type each bracket from the lowest up; the first starts at $0. Press Enter on 'over' when done.")
    while True:
        over = 0 if not typed else None
        if over is None:
            answer = prompt("  Taxable income over $: ").strip()
            if not answer:
                return typed
            try:
                over = parse(answer, "dollars")
            except ValueError:
                say("  Type a number.")
                continue
        rate = ask(f"  Rate for income over ${over:,.0f}", typed[-1][1] if typed else 0.10, "percent", prompt, say)
        typed.append([over, rate])


def ask_category(item: str, prompt=input, say=print) -> str:
    """Which spending category a line is compared with on Budget vs actual; Enter leaves it out."""
    names = ", ".join(config.SPENDING_CATEGORIES)
    while True:
        answer = prompt(f"  Spending category for {item} ({names}; Enter to leave it out): ").strip().lower()
        if answer in ("", *config.SPENDING_CATEGORIES):
            return answer
        say("  That isn't one of the categories listed.")


def interview(start: dict, prompt=input, say=print) -> dict:
    """Walk through every input and every typed line of a budget, defaulting to its current values,
    and return the updated budget. Calculated lines are left as they are; new lines can be added."""
    plan = copy.deepcopy(start)
    say("Press Enter to keep the value in brackets. Amounts can include $ and commas.\n")
    section_titles = {"income": "Income and savings", "hsa": "HSA", "employer_match": "Employer 401(k) match",
                      "tax": "Tax parameters (check them against your spreadsheet's Assumptions tab)"}
    current = None
    for section, key, question, kind in QUESTIONS:
        if section != current:
            say(f"\n== {section_titles[section]}")
            current = section
        plan[section][key] = ask(question, plan[section][key], kind, prompt, say)
    plan["tax"]["federal_brackets"] = ask_brackets(plan["tax"]["federal_brackets"], prompt, say)

    say("\n== Monthly line items (taxes, payroll savings, the premium and the commuter benefit are calculated)")
    group = None
    for line in plan["line"]:
        if "monthly" not in line:
            continue
        if line["group"] != group:
            say(f"-- {line['group']}")
            group = line["group"]
        placeholder = " (a placeholder in your spreadsheet)" if "Placeholder" in line.get("note", "") else ""
        line["monthly"] = ask(f"{line['item']}, monthly{placeholder}", line["monthly"], "dollars", prompt, say)
        if not line_category(line) and "category" not in line:
            line["category"] = ask_category(line["item"], prompt, say)

    say("\n== Anything else? Add lines the list above doesn't have; press Enter on the name when done.")
    surplus_at = next(index for index, line in enumerate(plan["line"]) if line.get("from") == "surplus")
    while True:
        item = prompt("New line item name: ").strip()
        if not item:
            break
        group = prompt("  Group (for example Lifestyle): ").strip() or "Lifestyle"
        kind = prompt("  Necessary or Discretionary? [N/d]: ").strip().lower()
        monthly = ask("  Monthly", 0, "dollars", prompt, say)
        plan["line"].insert(surplus_at, {"group": group, "item": item,
                                         "class": "Discretionary" if kind.startswith("d") else "Necessary",
                                         "monthly": monthly, "category": ask_category(item, prompt, say)})
        surplus_at += 1
    return plan


def toml_value(value) -> str:
    """One value in TOML: a number, a string or a list of them."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:_}" if isinstance(value, int) else repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)  # JSON string escapes are valid TOML


def dumps(plan: dict) -> str:
    """A budget as budget.toml text, in the example's layout."""
    out = ["# The budget, written by `uv run budget.py --interview`. Edit by hand or run the interview again.", ""]
    for section in INPUTS:
        out.append(f"[{section}]")
        for key, value in plan[section].items():
            if key == "federal_brackets":
                out.append("federal_brackets = [")
                out += [f"    {toml_value(bracket)}," for bracket in value]
                out.append("]")
            else:
                out.append(f"{key} = {toml_value(value)}")
        out.append("")
    for line in plan["line"]:
        out.append("[[line]]")
        out += [f"{key} = {toml_value(line[key])}" for key in LINE_KEYS if key in line]
        out.append("")
    return "\n".join(out)


def save(plan: dict, path: Path) -> None:
    """Write the budget to path, checking it first; a budget that doesn't load isn't written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    trial = path.with_name(path.name + ".new")
    trial.write_text(dumps(plan), encoding="utf-8")
    try:
        load(trial)
    except BudgetError:
        trial.unlink()
        raise
    trial.replace(path)


# ---------------------------------------------------------------- editing on the page

def missing(value) -> bool:
    """Whether a cell of the page's editors was left empty."""
    return value is None or bool(pd.isna(value))


def cell_text(value) -> str:
    """A text cell from the page's editors, trimmed; an empty one is ""."""
    return "" if missing(value) else str(value).strip()


def from_percent(value: float) -> float:
    """A percent as the page takes it (6.2) as the fraction the file keeps (0.062), without the
    float noise that dividing leaves (0.062000000000000006)."""
    return round(value / 100, 10)


def plain_number(value: float) -> int | float:
    """A whole amount as an int, so the file says 1_800 rather than 1800.0."""
    return int(value) if float(value).is_integer() else float(value)


def typed_inputs(answers: dict[tuple[str, str], float], brackets: list[tuple]) -> dict[str, dict]:
    """The Assumptions editor's answers, in its units (dollars, and percents typed as percents), as
    the inputs the file keeps: each section's values, and the brackets as [over, rate] pairs from the
    lowest up. Bracket rows left empty are dropped; one with a single number raises BudgetError."""
    kinds = {(section, key): kind for section, key, _, kind in QUESTIONS}
    inputs: dict[str, dict] = {section: {} for section in INPUTS}
    for (section, key), value in answers.items():
        inputs[section][key] = from_percent(value) if kinds[section, key] == "percent" else plain_number(value)
    rows = [(over, rate) for over, rate in brackets if not (missing(over) and missing(rate))]
    if any(missing(over) or missing(rate) for over, rate in rows):
        raise BudgetError("Each federal bracket needs both numbers: the taxable income it starts over, and its rate")
    inputs["tax"]["federal_brackets"] = sorted([plain_number(over), from_percent(rate)] for over, rate in rows)
    return inputs


def changed_inputs(plan: dict, inputs: dict[str, dict]) -> dict[str, dict]:
    """The inputs whose values differ from the plan's, by section, leaving out sections with none."""
    return {section: changed for section, values in inputs.items()
            if (changed := {key: value for key, value in values.items() if plan[section].get(key) != value})}


def line_rows(budget: dict) -> list[dict]:
    """Every line as the page's line editor shows it: group, item and class; the monthly amount of a
    typed line, None for a calculated one, which says so; the category it's compared with; its note."""
    return [{"group": line["group"], "item": line["item"], "class": line["class"], "monthly": line.get("monthly"),
             "calculated": "calculated" if "from" in line else "", "category": line_category(line),
             "note": line.get("note", "")} for line in budget["line"]]


def edited_lines(lines: list[dict], rows: list[dict]) -> list[tuple[int | None, dict]]:
    """The line editor's rows as lines for with_lines, each with the position it was edited from.
    A row's `at` is the position of the line it shows; a row without one is new and goes just
    before the surplus line, as the interview puts new lines, unless it was left blank. A category
    is written only when it isn't the item's default or the line already had one, and a renamed
    line stays covered by the commuter benefit that covered it. Raises BudgetError naming the first
    row that can't be used."""
    kept: list[tuple[int, dict]] = []
    new: list[tuple[None, dict]] = []
    for number, row in enumerate(rows, start=1):
        at = row.get("at")
        original = lines[at] if at is not None else {}
        group, item = cell_text(row.get("group")), cell_text(row.get("item"))
        if at is None and not any([group, item, cell_text(row.get("note")), cell_text(row.get("category")),
                                   not missing(row.get("monthly"))]):
            continue  # a row added and left blank
        if not group or not item:
            raise BudgetError(f"{item or f'Row {number}'}: each line needs a group and a line item")
        if row.get("class") not in CLASSES:
            raise BudgetError(f"{item}: class must be one of {', '.join(CLASSES)}")
        line = {**original, "group": group, "item": item, "class": row["class"]}
        monthly = row.get("monthly")
        if "from" in original:
            if not missing(monthly):
                raise BudgetError(f"{item} is calculated, so it takes no monthly amount: its inputs are on Assumptions & taxes")
        elif missing(monthly):
            raise BudgetError(f"{item}: type its monthly amount")
        elif monthly < 0:
            raise BudgetError(f"{item}: the monthly amount is zero or more")
        else:
            line["monthly"] = plain_number(monthly)
        note = cell_text(row.get("note"))
        if note:
            line["note"] = note
        else:
            line.pop("note", None)
        category = cell_text(row.get("category")) or None
        if "category" in original or category != DEFAULT_CATEGORIES.get(item):
            line["category"] = category or ""
        if at is None:
            new.append((None, line))
        else:
            kept.append((at, line))
    renamed = {lines[at]["item"]: line["item"] for at, line in kept if lines[at]["item"] != line["item"]}
    for _, line in kept:
        if line.get("covers") in renamed:
            line["covers"] = renamed[line["covers"]]
    surplus_at = next(index for index, line in enumerate(lines) if line.get("from") == "surplus")
    if surplus_at not in [at for at, _ in kept]:
        raise BudgetError("The surplus line can't be removed: it takes whatever gross pay the other lines leave")
    edited: list[tuple[int | None, dict]] = []
    for at, line in kept:
        if at == surplus_at:
            edited += new
        edited.append((at, line))
    return edited


def set_toml_value(table, key: str, value) -> None:
    """Set key in a tomlkit table to value, written as toml_value writes it, unless it already holds
    that value, so what didn't change stays exactly as it was written. A comment after the old
    value stays after the new one, at the same column."""
    old = table.get(key)
    if old is not None and old == value:
        return
    new = tomlkit.value(toml_value(value))
    if isinstance(old, tomlkit.items.Item) and old.trivia.comment:
        new.trivia.comment = old.trivia.comment
        width = len(old.as_string()) + len(old.trivia.comment_ws) - len(new.as_string())
        new.trivia.comment_ws = " " * max(1, width)
    table[key] = new


def set_brackets(table, brackets: list[list[float]]) -> None:
    """The federal brackets, changed pair by pair in the list already there, so it keeps its layout
    and comments, one pair to a line."""
    old = table.get("federal_brackets")
    if old == brackets:
        return
    if not isinstance(old, tomlkit.items.Array):
        old = tomlkit.array()
        old.multiline(True)
        table["federal_brackets"] = old
        old = table["federal_brackets"]
    for index, bracket in enumerate(brackets):
        if index >= len(old):
            old.append(tomlkit.value(toml_value(bracket)))
        elif old[index] != bracket:
            old[index] = tomlkit.value(toml_value(bracket))
    while len(old) > len(brackets):
        del old[len(brackets)]


def with_inputs(text: str, inputs: dict[str, dict]) -> str:
    """budget.toml's text with new values for some of its inputs ({section: {key: value}}), keeping
    its comments, its order and the way it writes everything else."""
    document = tomlkit.parse(text)
    for section, values in inputs.items():
        if section not in document:
            document[section] = tomlkit.table()
        for key, value in values.items():
            if key == "federal_brackets":
                set_brackets(document[section], value)
            else:
                set_toml_value(document[section], key, value)
    return tomlkit.dumps(document)


def with_lines(text: str, lines: list[tuple[int | None, dict]]) -> str:
    """budget.toml's text with these lines in place of its [[line]] tables. Each line comes with the
    position of the table it was edited from, or None when it's new; positions only go up, since
    the editor can't reorder lines. A table keeps its comments, keys this code doesn't know, and
    whatever didn't change; a table no line came from is removed."""
    document = tomlkit.parse(text)
    tables = document["line"]
    origins = [origin for origin, _ in lines if origin is not None]
    if origins != sorted(set(origins)) or any(origin >= len(tables) for origin in origins):
        raise ValueError("lines keep their order, and each comes from a table in the file")
    for index in reversed(range(len(tables))):
        if index not in origins:
            del tables[index]
    for position, (origin, line) in enumerate(lines):
        table = tomlkit.table() if origin is None else tables[position]
        for key in LINE_KEYS:
            if key in line:
                set_toml_value(table, key, line[key])
            elif key in table:
                del table[key]
        if origin is None and position < len(tables):
            table.add(tomlkit.nl())  # the blank line before the next [[line]]
            tables.insert(position, table)
        elif origin is None:
            if len(tables):
                tables[-1].add(tomlkit.nl())
            tables.append(table)
    return tomlkit.dumps(document).rstrip("\n") + "\n"


def print_summary(plan: dict) -> None:
    """The summary by class, as the top of the spreadsheet's Budget tab shows it."""
    split, _ = summary_by_class(plan, line_items(plan))
    for row in split.itertuples():
        print(f"{row.label:<45} {row.monthly:>12,.2f} a month  {row.share:>6.1%}")


def main(argv: list[str] | None = None) -> int:
    """Check the live budget file and print the summary by class, or run the interview."""
    parser = argparse.ArgumentParser(description="Check the budget, or fill it in by answering questions.")
    parser.add_argument("--interview", action="store_true", help="ask for every amount and write budget.toml")
    args = parser.parse_args(argv)
    path = budget_path()
    try:
        if args.interview:
            start = load(path) if path.exists() else load(BUDGET_EXAMPLE)
            print(f"Defaults come from {'your ' + path.name if path.exists() else 'the made-up example'}.")
            plan = interview(start)
            print()
            print_summary(plan)
            if input(f"\nSave to {path}? [Y/n]: ").strip().lower() in ("n", "no"):
                print("Not saved.")
                return 1
            save(plan, path)
            print(f"Saved {path}. A page already open shows it on its next click or refresh.")
            return 0
        if not path.exists():
            print(f"No budget yet: run `uv run budget.py --interview`, or copy budget.example.toml to {path}.",
                  file=sys.stderr)
            return 1
        print_summary(load(path))
        return 0
    except BudgetError as error:
        print(error, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped; nothing saved.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
