"""The statements of Finance mode, built as tables of lines from queries: a balance sheet as of a
period's end, an income statement for the period on a cash basis, and a cash flow by the indirect
method, reconciled to the balance sheet. See docs/spec.md, Finance mode.

A statement is a DataFrame of lines: label, level (section, line, subtotal, total), amount, prior
and change, where an amount that can't be known is None, shown as a dash. Every figure here equals
its Personal-mode counterpart: the balance sheet is Net worth's month-end balances, regrouped, and
the income statement's salary and expenses are This month's Income and Spending. Nothing in this
module touches Streamlit.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pandas as pd

import config
import queries

ASSET_LINES = [("Cash and cash equivalents", "cash"), ("Investments", "brokerage"),
               ("Retirement accounts", "retirement"), ("Health savings", "hsa")]
LIABILITY_LINES = [("Credit cards", "credit"), ("Loans", "loan")]
COLUMNS = ["label", "level", "amount", "prior", "change"]


def accounting(value: float | None) -> str:
    """Whole dollars the accounting way: "$1,234", "($1,234)", and a dash for a figure that can't
    be known."""
    if value is None or pd.isna(value):
        return "—"
    rounded = math.floor(abs(float(value)) + 0.5)  # halves away from zero, not Python's to-even
    return f"(${rounded:,.0f})" if value < 0 and rounded else f"${rounded:,.0f}"


def difference(amount: float | None, prior: float | None) -> float | None:
    """amount − prior, when both are known."""
    if amount is None or prior is None:
        return None
    return queries.cents(amount - prior)


def line(label: str, level: str, amount: float | None = None, prior: float | None = None) -> dict:
    return {"label": label, "level": level, "amount": amount, "prior": prior, "change": difference(amount, prior)}


def as_statement(lines: list[dict]) -> pd.DataFrame:
    """The lines as a DataFrame whose unknown figures stay None, never NaN."""
    frame = pd.DataFrame(lines, columns=COLUMNS)
    for column in ("amount", "prior", "change"):
        frame[column] = frame[column].astype(object).where(frame[column].notna(), None)
    return frame


def total_of(parts: list[float | None]) -> float | None:
    """The sum of the known parts; None when none is known. For a balance sheet total, where a
    kind with no accounts is unknown but the rest still adds up."""
    known = [part for part in parts if part is not None]
    return queries.cents(sum(known)) if known else None


def add(parts: list[float | None]) -> float | None:
    """The sum, or None when any part is unknown. For a cash flow subtotal, which can't be known
    without every one of its lines."""
    if any(part is None for part in parts):
        return None
    return queries.cents(sum(parts))


def kind_total(balances: dict[str, dict[str, dict]], month: str | None, kind: str) -> float | None:
    """The balances of a kind's accounts at a month end, summed; None when no account of the kind
    has one (or there is no such month end)."""
    if month is None:
        return None
    found = [balances[key][month]["balance"] for key, spec in config.ACCOUNTS.items()
             if spec["kind"] == kind and month in balances.get(key, {})]
    return queries.cents(sum(found)) if found else None


def balance_sheet(period: dict, today: date | None = None, db_path: Path | None = None) -> dict:
    """The balance sheet as of a period's end beside the previous period's end: {"lines": the
    statement, "accounts": {line label: a table of that line's accounts (name, amount, prior,
    as_of, prior_as_of)}, "end" and "prior_end": the two month ends}. Liabilities are shown as the
    amount owed; net worth is total assets less total liabilities."""
    months, balances = queries.month_end_balances(today, db_path)
    end, prior_end = period["end"], period["previous"]["end"]
    end = end if end in months else None
    prior_end = prior_end if prior_end in months else None

    lines, accounts = [line("Assets", "section")], {}
    assets, prior_assets = [], []
    for label, kind in ASSET_LINES:
        amount, prior = kind_total(balances, end, kind), kind_total(balances, prior_end, kind)
        assets.append(amount)
        prior_assets.append(prior)
        lines.append(line(label, "line", amount, prior))
        accounts[label] = line_accounts(balances, kind, end, prior_end)
    total_assets, prior_total_assets = total_of(assets), total_of(prior_assets)
    lines.append(line("Total assets", "subtotal", total_assets, prior_total_assets))

    lines.append(line("Liabilities", "section"))
    owed, prior_owed = [], []
    for label, kind in LIABILITY_LINES:
        amount, prior = kind_total(balances, end, kind), kind_total(balances, prior_end, kind)
        amount = None if amount is None else queries.cents(-amount)  # what is owed, as a positive figure
        prior = None if prior is None else queries.cents(-prior)
        owed.append(amount)
        prior_owed.append(prior)
        lines.append(line(label, "line", amount, prior))
        accounts[label] = line_accounts(balances, kind, end, prior_end, owed=True)
    total_owed, prior_total_owed = total_of(owed), total_of(prior_owed)
    lines.append(line("Total liabilities", "subtotal", total_owed, prior_total_owed))

    worth = None if total_assets is None and total_owed is None else queries.cents((total_assets or 0) - (total_owed or 0))
    prior_worth = None if prior_total_assets is None and prior_total_owed is None else \
        queries.cents((prior_total_assets or 0) - (prior_total_owed or 0))
    lines.append(line("Net worth", "total", worth, prior_worth))
    return {"lines": as_statement(lines), "accounts": {label: table for label, table in accounts.items() if not table.empty},
            "end": end, "prior_end": prior_end}


def line_accounts(balances: dict[str, dict[str, dict]], kind: str, end: str | None, prior_end: str | None,
                  owed: bool = False) -> pd.DataFrame:
    """One line's accounts: each with its balance at both month ends and how each was known."""
    rows = []
    for key, spec in config.ACCOUNTS.items():
        if spec["kind"] != kind:
            continue
        now = balances.get(key, {}).get(end) if end else None
        before = balances.get(key, {}).get(prior_end) if prior_end else None
        if now is None and before is None:
            continue
        sign = -1 if owed else 1
        rows.append({
            "name": spec["name"],
            "amount": None if now is None else queries.cents(sign * now["balance"]),
            "prior": None if before is None else queries.cents(sign * before["balance"]),
            "as_of": queries.as_of_label(kind, now["as_of"], now["exact"], end) if now else "—",
            "prior_as_of": queries.as_of_label(kind, before["as_of"], before["exact"], prior_end) if before else "—",
        })
    return pd.DataFrame(rows, columns=["name", "amount", "prior", "as_of", "prior_as_of"])


# ---------------------------------------------------------------- the income statement and cash flow

CAP_ORDER = ["401k", "roth", "hsa"]


def category_label(category: str) -> str:
    """ "going out" becomes "Going out", as the pie and the budget tables show it."""
    return category.capitalize()


def period_figures(months: list[str], data_months: set[str], db_path: Path | None = None) -> dict | None:
    """What the income statement and cash flow read from a period's transactions, or None when no
    month of the period has any: salary, employer contributions, investment income (and the part of
    it kept in investment accounts), expenses by category (net, positive, nonzero), the totals and
    net income, contributions by cap, the cash legs to investment and loan accounts, and the
    non-cash investing memo."""
    if not any(month in data_months for month in months):
        return None
    first, last = months[0], months[-1]
    totals = queries.category_totals(first, last, db_path)
    income = queries.investment_income(first, last, db_path)
    expenses = queries.spending_between(first, last, db_path)
    figures = {
        "salary": queries.cents(totals.get("income", 0.0)),
        "employer": queries.cents(totals.get("employer_match", 0.0)),
        "investment_income": queries.cents(income["amount"].sum()),
        "kept": queries.cents(income.loc[income["kind"].isin(config.INVESTMENT_KINDS), "amount"].sum()),
        "expenses": expenses[expenses != 0],
        "contributions": queries.contributions_between(first, last, db_path),
        "legs": queries.cash_legs(first, last, db_path),
        "non_cash": queries.non_cash_investing(first, last, db_path),
    }
    figures["total_income"] = queries.cents(figures["salary"] + figures["employer"] + figures["investment_income"])
    figures["total_expenses"] = queries.cents(figures["expenses"].sum())
    figures["net_income"] = queries.cents(figures["total_income"] - figures["total_expenses"])
    return figures


def figure(figures: dict | None, key: str) -> float | None:
    """One figure of a period, None when the period has no transactions."""
    return None if figures is None else figures[key]


def pair(now: dict | None, prior: dict | None, key: str) -> tuple[float | None, float | None]:
    """A figure for this period and the one before, for a line."""
    return figure(now, key), figure(prior, key)


def spent(figures: dict | None, category: str) -> float | None:
    """A period's net spending in one category: zero when the period has transactions but none in
    the category, None when it has no transactions at all."""
    return None if figures is None else queries.cents(figures["expenses"].get(category, 0.0))


def contributed(figures: dict | None, cap: str) -> float | None:
    """A period's contributions toward one cap, zero or None as spent is."""
    return None if figures is None else queries.cents(figures["contributions"].get(cap, 0.0))


def expense_lines(now: dict | None, prior: dict | None) -> list[dict]:
    """One line per spending category with spending in either period, largest first (by this
    period, then the one before), uncategorized last."""
    categories = set()
    for figures in (now, prior):
        if figures is not None:
            categories.update(figures["expenses"].index)
    ordered = sorted(categories, key=lambda category: (category == "uncategorized", -(spent(now, category) or 0.0),
                                                       -(spent(prior, category) or 0.0), category))
    return [line(category_label(category), "line", spent(now, category), spent(prior, category)) for category in ordered]


def caps_with_accounts() -> list[str]:
    """The caps an account counts toward, in CAP_ORDER."""
    present = {spec["cap_key"] for spec in config.ACCOUNTS.values() if spec.get("cap_key")}
    return [cap for cap in CAP_ORDER if cap in present]


def income_statement(period: dict, db_path: Path | None = None) -> dict:
    """The income statement for a period beside the one before it, on a cash basis: {"lines": the
    statement, "figures" and "prior": what each period's transactions gave (None without any)}.
    Take-home pay is income; expenses are net of refunds, as Spending is."""
    data_months = set(queries.months(db_path))
    now = period_figures(period["months"], data_months, db_path)
    prior = period_figures(period["previous"]["months"], data_months, db_path)

    lines = [line("Income", "section"),
             line("Salary and wages", "line", *pair(now, prior, "salary")),
             line("Employer retirement contributions", "line", *pair(now, prior, "employer")),
             line("Investment income", "line", *pair(now, prior, "investment_income")),
             line("Total income", "subtotal", *pair(now, prior, "total_income")),
             line("Expenses", "section"),
             *expense_lines(now, prior),
             line("Total expenses", "subtotal", *pair(now, prior, "total_expenses")),
             line("Net income", "total", *pair(now, prior, "net_income")),
             line("Memo: contributions to retirement and health accounts", "section")]
    for cap in caps_with_accounts():
        lines.append(line(queries.CAP_LABELS[cap], "line", contributed(now, cap), contributed(prior, cap)))
    return {"lines": as_statement(lines), "figures": now, "prior": prior}


def owed_on_cards(balances: dict[str, dict[str, dict]], month: str | None) -> float | None:
    """What the credit cards owed at a month end, as a positive figure: zero without a card
    account, None when no card has a balance for the month end."""
    if not any(spec["kind"] == "credit" for spec in config.ACCOUNTS.values()):
        return 0.0
    total = kind_total(balances, month, "credit")
    return None if total is None else queries.cents(-total)


def legs_to(figures: dict | None, kinds: set[str]) -> float | None:
    """The cash legs of a period's transfers whose partner is one of the kinds, summed."""
    if figures is None:
        return None
    legs = figures["legs"]
    return queries.cents(legs.loc[legs["partner_kind"].isin(kinds), "amount"].sum())


def flow_figures(figures: dict | None, balances: dict[str, dict[str, dict]], start: str | None, end: str | None) -> dict:
    """One period's column of the cash flow, by the indirect method, from its income statement
    figures and the balance sheets at its start (the previous period's end) and its end."""
    net_income = figure(figures, "net_income")
    kept = None if figures is None else queries.cents(-figures["kept"])
    employer = None if figures is None else queries.cents(-figures["employer"])
    cards = difference(owed_on_cards(balances, end), owed_on_cards(balances, start))
    operating = add([net_income, kept, employer, cards])
    investing = legs_to(figures, config.INVESTMENT_KINDS)
    financing = legs_to(figures, {"loan"})
    change = add([operating, investing, financing])
    cash_start, cash_end = kind_total(balances, start, "cash"), kind_total(balances, end, "cash")
    unexplained = difference(difference(cash_end, cash_start), change)
    return {"net_income": net_income, "kept": kept, "employer": employer, "cards": cards, "operating": operating,
            "investing": investing, "financing": financing, "change": change,
            "cash_start": cash_start, "cash_end": cash_end, "unexplained": unexplained,
            "non_cash": figure(figures, "non_cash")}


def cash_flow(period: dict, today: date | None = None, db_path: Path | None = None) -> dict:
    """The cash flow for a period beside the one before it, indirect method, reconciled to the
    balance sheet: {"lines": the statement, "figures" and "prior": each column's figures}. The last
    line, what the balance sheets' change in cash doesn't explain, is zero when every transaction is
    categorized and every transfer matched."""
    months, balances = queries.month_end_balances(today, db_path)
    data_months = set(queries.months(db_path))
    previous = period["previous"]
    before = queries.previous_period(period["kind"], previous)
    end, prior_end, before_end = (month if month in months else None for month in (period["end"], previous["end"], before["end"]))
    now = flow_figures(period_figures(period["months"], data_months, db_path), balances, prior_end, end)
    prior = flow_figures(period_figures(previous["months"], data_months, db_path), balances, before_end, prior_end)

    lines = [line("Operating activities", "section"),
             line("Net income", "line", *pair(now, prior, "net_income")),
             line("Investment income kept in investment accounts", "line", *pair(now, prior, "kept")),
             line("Employer contributions, received in retirement accounts", "line", *pair(now, prior, "employer")),
             line("Increase (decrease) in credit card balances", "line", *pair(now, prior, "cards")),
             line("Net cash from operating activities", "subtotal", *pair(now, prior, "operating")),
             line("Investing activities", "section"),
             line("Transfers to investment accounts", "line", *pair(now, prior, "investing")),
             line("Net cash from investing activities", "subtotal", *pair(now, prior, "investing")),
             line("Financing activities", "section"),
             line("Loan payments and proceeds", "line", *pair(now, prior, "financing")),
             line("Net cash from financing activities", "subtotal", *pair(now, prior, "financing")),
             line("Net change in cash", "total", *pair(now, prior, "change")),
             line("Cash at the start of the period", "line", *pair(now, prior, "cash_start")),
             line("Cash at the end of the period", "line", *pair(now, prior, "cash_end")),
             line("Not explained by the transactions", "total", *pair(now, prior, "unexplained")),
             line("Memo", "section"),
             line("Non-cash investing: payroll deferrals and employer money", "line", *pair(now, prior, "non_cash"))]
    return {"lines": as_statement(lines), "figures": now, "prior": prior, "end": end, "prior_end": prior_end}
