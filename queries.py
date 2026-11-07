"""Every number on the page, computed from the transactions table, and for the Net worth tab from
the balances table too. See docs/spec.md, Measures and Net worth.

Each function opens its own connection (Streamlit serves sessions on separate threads) and returns
a number, a dict or a DataFrame. Nothing else in the app computes money. Sums are rounded to cents
before they are compared or shown.
"""

from __future__ import annotations

import calendar
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd

import budget
import categorize
import config
import db
from load import normalize

CAP_LABELS = {"401k": "401(k)", "roth": "Roth IRA", "hsa": "HSA"}
# A reading this many days from a month's last day stands for that month end: the monthly routine
# downloads positions on the first weekend, a few days after the month ends.
READING_WINDOW_DAYS = 7
# Money into an investment account that isn't the market's doing: transfers in (or out), payroll
# contributions and employer money. Buys, sells, dividends and fees are the account's own activity.
MONEY_IN_CATEGORIES = {"retirement", "employer_match"}

# Brokerage legs whose partner is a cash or credit account: new money into brokerage, or money taken
# back out. A move from one investment account to another (between the two brokerages, or from a
# brokerage into the Roth) is neutral, so it never counts.
INFLOW_SQL = """
SELECT leg.month, leg.amount
FROM transactions AS leg
JOIN accounts AS account ON account.name = leg.account
JOIN transactions AS partner ON partner.id = leg.transfer_id
JOIN accounts AS partner_account ON partner_account.name = partner.account
WHERE account.kind = 'brokerage' AND partner_account.kind NOT IN ('brokerage', 'retirement', 'hsa')
"""


class MissingCapsError(Exception):
    """config.CAPS has no row for the selected year."""


def read(sql: str, parameters: tuple = (), db_path: Path | None = None) -> pd.DataFrame:
    """Run one query on a fresh connection."""
    with closing(db.connect(db_path)) as connection:
        return pd.read_sql_query(sql, connection, params=parameters)


def cents(value: float) -> float:
    """A sum rounded to cents, never -0.0."""
    return round(float(value), 2) + 0.0


def money(value: float) -> str:
    """Whole dollars: "$1,850", "-$500"."""
    return f"-${-value:,.0f}" if value < 0 else f"${value:,.0f}"


def signed_money(value: float) -> str:
    """Whole dollars with a plus on gains: "+$1,850", "$0", "-$500"."""
    return f"+{money(value)}" if value > 0 else money(value)


def month_label(month: str) -> str:
    """ "2026-10" becomes "October 2026"."""
    year, number = month.split("-")
    return f"{calendar.month_name[int(number)]} {year}"


def months(db_path: Path | None = None) -> list[str]:
    """Every month with data, oldest first."""
    return read("SELECT DISTINCT month FROM transactions ORDER BY month", db_path=db_path)["month"].tolist()


def default_month(today: date | None = None, db_path: Path | None = None) -> str | None:
    """The latest month with data before the current calendar month, since overlapping exports leave
    the newest month partial; the latest month with data if there is none before; None if empty."""
    available = months(db_path)
    if not available:
        return None
    current = (today or date.today()).strftime("%Y-%m")
    earlier = [month for month in available if month < current]
    return earlier[-1] if earlier else available[-1]


def contributions(cap_key: str, month: str, db_path: Path | None = None) -> float:
    """Measured contributions toward one cap for the month's tax year, through the end of the month.

    A row's tax year is its calendar year, minus one when it is marked as a prior-year contribution.
    """
    rows = read(
        "SELECT t.date, t.amount, t.description FROM transactions AS t JOIN accounts AS a ON a.name = t.account"
        " WHERE t.category = 'retirement' AND a.cap_key = ? AND t.month <= ?",
        (cap_key, month),
        db_path,
    )
    if rows.empty:
        return 0.0
    marker = normalize(config.PRIOR_YEAR_MARKER)
    prior_year = rows["description"].map(lambda text: marker in normalize(text)).astype(int)
    tax_year = rows["date"].str[:4].astype(int) - prior_year
    return cents(rows.loc[tax_year == int(month[:4]), "amount"].sum())


def cap_status(month: str, db_path: Path | None = None) -> pd.DataFrame:
    """One row per cap: contributions so far, the cap, the pace marker, whether it is behind, whether
    it is only planned, how far short of pace it is, and how far over the cap it is. Raises
    MissingCapsError for a year without caps.

    Behind means more than one contribution short of a straight-line pace, so two-paycheck months
    and deposits that post a few days late don't count; a planned cap is never behind. Only caps
    you have an account for, or plan, are listed: someone with no HSA is never behind on one.
    """
    year, number = int(month[:4]), int(month[5:7])
    if year not in config.CAPS:
        raise MissingCapsError(f"config.py has no caps for {year}; add a {year} row to CAPS to see the bars.")
    tracked = {spec["cap_key"] for spec in config.ACCOUNTS.values() if spec.get("cap_key")} | config.PLANNED_CAPS
    rows = []
    for cap_key, cap in config.CAPS[year].items():
        if cap_key not in tracked:
            continue
        pace = cents(cap * number / 12)
        planned = cap_key in config.PLANNED_CAPS
        so_far = pace if planned else contributions(cap_key, month, db_path)
        slack_line = cents(pace - cap / config.CONTRIBUTIONS_PER_YEAR[cap_key])
        behind = not planned and so_far < slack_line
        rows.append({
            "cap_key": cap_key,
            "label": CAP_LABELS.get(cap_key, cap_key),
            "contributions": so_far,
            "cap": cap,
            "pace": pace,
            "behind": behind,
            "planned": planned,
            "short": cents(pace - so_far) if behind else 0.0,
            "over": cents(so_far - cap) if so_far > cap else 0.0,  # an excess contribution to withdraw
        })
    return pd.DataFrame(rows, columns=["cap_key", "label", "contributions", "cap", "pace", "behind", "planned",
                                       "short", "over"])


def brokerage_inflow_by_month(db_path: Path | None = None) -> pd.Series:
    """Net money into brokerage from your other accounts, by month (withdrawals count negative)."""
    rows = read(INFLOW_SQL, db_path=db_path)
    return rows.groupby("month")["amount"].sum().round(2)


def brokerage_inflow(month: str, db_path: Path | None = None) -> float:
    """Net money into brokerage in one month."""
    return cents(brokerage_inflow_by_month(db_path).get(month, 0.0))


# verdict, brokerage_year_to_date and trend take the inflow series and the income and spending
# table as keyword arguments, so the page can compute each once per render and share it.


def verdict(month: str, db_path: Path | None = None, *, inflow_by_month: pd.Series | None = None,
            totals: pd.DataFrame | None = None) -> dict:
    """The banner: green when brokerage inflow is above zero, no cap is behind pace and the month
    isn't short; red otherwise. Without a brokerage account, brokerage inflow is left out of both
    the color and the text, which says what was left after spending instead.

    "Short" is how much the month's spending beat its income, and is left off when spending didn't.
    The text also names any cap behind pace or over the cap. The cap rows come back too, under "caps".
    """
    if inflow_by_month is None:
        inflow_by_month = brokerage_inflow_by_month(db_path)
    if totals is None:
        totals = income_and_spending(db_path)
    inflow = cents(inflow_by_month.get(month, 0.0))
    caps = cap_status(month, db_path)
    income, spending = (totals.loc[month].tolist() if month in totals.index else (0.0, 0.0))
    short = max(cents(spending - income), 0.0)
    has_brokerage = any(spec["kind"] == "brokerage" for spec in config.ACCOUNTS.values())
    if has_brokerage:
        text = f"{month_label(month)}: {signed_money(inflow)} to brokerage"
        if short > 0:
            text += f", {money(short)} short"
    elif short > 0:
        text = f"{month_label(month)}: {money(short)} short"
    else:
        text = f"{month_label(month)}: {signed_money(cents(income - spending))} left after spending"
    behind = caps.loc[caps["behind"], "label"].tolist()
    if behind:
        text += f" · {' and '.join(behind)} behind pace"
    for row in caps[caps["over"] > 0].itertuples():
        text += f" · {row.label} over the cap by {money(row.over)}"
    green = bool((inflow > 0 or not has_brokerage) and not caps["behind"].any() and short == 0)
    return {"green": green, "inflow": inflow, "income": income, "spending": spending, "short": short, "text": text,
            "caps": caps}


def brokerage_year_to_date(month: str, db_path: Path | None = None, *,
                           inflow_by_month: pd.Series | None = None) -> tuple[float, pd.DataFrame]:
    """Brokerage inflow from January through the month: the total, and a row per month."""
    by_month = brokerage_inflow_by_month(db_path) if inflow_by_month is None else inflow_by_month
    year, number = month[:4], int(month[5:7])
    so_far = [f"{year}-{n:02d}" for n in range(1, number + 1)]
    frame = pd.DataFrame({"month": so_far, "inflow": [cents(by_month.get(m, 0.0)) for m in so_far]})
    return cents(frame["inflow"].sum()), frame


def spending_by_category(month: str, db_path: Path | None = None) -> pd.DataFrame:
    """Category, amount spent and share of spending for one month, largest first.

    Amounts are net, so a refund reduces the category it belongs to. Shares are of what the
    categories that spent money spent; a category that nets to a refund has no share (NaN).
    """
    placeholders = ",".join("?" for _ in config.SPENDING_CATEGORIES)
    rows = read(
        f"SELECT category, -SUM(amount) AS amount FROM transactions"
        f" WHERE month = ? AND category IN ({placeholders}) GROUP BY category",
        (month, *config.SPENDING_CATEGORIES),
        db_path,
    )
    rows["amount"] = rows["amount"].round(2) + 0.0
    rows = rows[rows["amount"] != 0].sort_values(["amount", "category"], ascending=[False, True]).reset_index(drop=True)
    spent = rows["amount"].where(rows["amount"] > 0)
    rows["share"] = spent / spent.sum() if spent.sum() > 0 else float("nan")
    return rows


def spending_between(first: str, last: str, db_path: Path | None = None) -> pd.Series:
    """Net spending per spending category over the months first through last, as positive amounts."""
    placeholders = ",".join("?" for _ in config.SPENDING_CATEGORIES)
    rows = read(
        f"SELECT category, -SUM(amount) AS spent FROM transactions"
        f" WHERE month BETWEEN ? AND ? AND category IN ({placeholders}) GROUP BY category",
        (first, last, *config.SPENDING_CATEGORIES),
        db_path,
    )
    return rows.set_index("category")["spent"].round(2) + 0.0


def year_start(month: str, db_path: Path | None = None) -> str:
    """The first month with data in the month's year, so a year that started before the first export
    isn't counted as months of spending nothing."""
    same_year = [candidate for candidate in months(db_path) if candidate[:4] == month[:4] and candidate <= month]
    return same_year[0] if same_year else month


def budget_vs_actual(plan: dict, month: str, db_path: Path | None = None) -> tuple[pd.DataFrame, str]:
    """Budget against spending per category: for the month, and year to date from the year's first
    month with data. Differences are budget minus spent, so positive is under budget. Returns the
    table, with a total row last, and the year-to-date start month."""
    first = year_start(month, db_path)
    counted = int(month[5:7]) - int(first[5:7]) + 1
    planned = budget.budget_by_category(plan).set_index("category")
    spent, spent_ytd = spending_between(month, month, db_path), spending_between(first, month, db_path)
    extra = [name for name in config.SPENDING_CATEGORIES
             if name not in planned.index and (spent.get(name, 0) or spent_ytd.get(name, 0))]
    rows = []
    for name in [*planned.index, *extra]:
        monthly = planned["monthly"].get(name, 0.0)
        rows.append({
            "category": name, "lines": planned["lines"].get(name, "(no budget line)"),
            "budget": monthly, "spent": spent.get(name, 0.0),
            "ytd_budget": monthly * counted, "ytd_spent": spent_ytd.get(name, 0.0),
        })
    frame = pd.DataFrame(rows, columns=["category", "lines", "budget", "spent", "ytd_budget", "ytd_spent"])
    totals = frame[["budget", "spent", "ytd_budget", "ytd_spent"]].sum()
    frame.loc[len(frame)] = {"category": "Total", "lines": "", **totals}
    for column in ("budget", "spent", "ytd_budget", "ytd_spent"):
        frame[column] = frame[column].astype(float).round(2) + 0.0
    frame["difference"] = (frame["budget"] - frame["spent"]).round(2) + 0.0
    frame["ytd_difference"] = (frame["ytd_budget"] - frame["ytd_spent"]).round(2) + 0.0
    return frame, first


def line_items(month: str, category: str, limit: int = 10, db_path: Path | None = None) -> pd.DataFrame:
    """The month's biggest rows in one category, largest spend first: date, description, account and
    amount spent (a refund shows as negative)."""
    rows = read(
        "SELECT date, description, account, -amount AS spent FROM transactions"
        " WHERE month = ? AND category = ? ORDER BY spent DESC, date, id LIMIT ?",
        (month, category, limit),
        db_path,
    )
    rows["date"] = pd.to_datetime(rows["date"]).dt.date
    rows["account"] = rows["account"].map(lambda key: config.ACCOUNTS.get(key, {}).get("name", key))
    rows["amount"] = rows.pop("spent").round(2) + 0.0
    return rows


def uncategorized(month: str, db_path: Path | None = None) -> pd.DataFrame:
    """The month's uncategorized rows by merchant key, largest total first: merchant, count, total,
    a suggested rule pattern, and the descriptions in the group."""
    with closing(db.connect(db_path)) as connection:
        groups = categorize.uncategorized_report(connection, month)
    return pd.DataFrame(groups, columns=["merchant", "count", "total", "pattern", "descriptions"])


def income_and_spending(db_path: Path | None = None) -> pd.DataFrame:
    """Income and total spending (net, all spending categories) for every month, indexed by month."""
    placeholders = ",".join("?" for _ in config.SPENDING_CATEGORIES)
    frame = read(
        "SELECT month, SUM(CASE WHEN category = 'income' THEN amount ELSE 0 END) AS income,"
        f" -SUM(CASE WHEN category IN ({placeholders}) THEN amount ELSE 0 END) AS spending"
        " FROM transactions GROUP BY month",
        tuple(config.SPENDING_CATEGORIES),
        db_path,
    )
    return frame.set_index("month").round(2) + 0.0


def trend(end_month: str, count: int = 12, db_path: Path | None = None, *,
          inflow_by_month: pd.Series | None = None, totals: pd.DataFrame | None = None) -> pd.DataFrame:
    """Income, spending and brokerage inflow for each of the `count` months ending at end_month."""
    end = pd.Period(end_month, freq="M")
    frame = pd.DataFrame({"month": [str(end - offset) for offset in range(count - 1, -1, -1)]})
    frame = frame.join(income_and_spending(db_path) if totals is None else totals, on="month")
    frame["brokerage"] = frame["month"].map(brokerage_inflow_by_month(db_path) if inflow_by_month is None else inflow_by_month)
    return frame.fillna(0.0).round(2)


# ---------------------------------------------------------------- net worth


def month_end(month: str) -> date:
    """The last day of "2026-10"."""
    year, number = int(month[:4]), int(month[5:7])
    return date(year, number, calendar.monthrange(year, number)[1])


def served_month(as_of: date) -> str:
    """The month whose end a reading stands for: the nearest month end within the window, else the
    reading's own month. A reading on 2 November stands for October."""
    own = as_of.strftime("%Y-%m")
    previous = (as_of.replace(day=1) - pd.Timedelta(days=1)).strftime("%Y-%m")
    for month in (previous, own):
        if abs((as_of - month_end(month)).days) <= READING_WINDOW_DAYS:
            return month
    return own


def readings(db_path: Path | None = None) -> pd.DataFrame:
    """Every reading: account, as_of (a date), balance and source, oldest first within each account."""
    frame = read("SELECT account, as_of, balance, source FROM balances ORDER BY account, as_of", db_path=db_path)
    frame["as_of"] = pd.to_datetime(frame["as_of"]).dt.date
    return frame


def balance_rows(db_path: Path | None = None) -> pd.DataFrame:
    """What the balances need from every transaction: account, date, month, amount, category and
    whether it is a transfer leg."""
    frame = read("SELECT account, date, month, amount, category, transfer_id IS NOT NULL AS transfer FROM transactions",
                 db_path=db_path)
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame


def month_end_balance(kind: str, account_readings: pd.DataFrame, account_rows: pd.DataFrame, month: str,
                      reach_from: date | None = None) -> dict | None:
    """One account's balance at a month end, from its readings (oldest first) and its transactions:
    {"balance", "as_of" (the reading's date), "exact", "estimated"}, or None when it can't be known.

    For a cash, credit or loan account: the reading nearest the month end, adjusted by the
    transactions between the two dates (added when the reading comes first, taken off when it
    comes after), which is exact since the exports carry every transaction. For an investment
    account: the reading within READING_WINDOW_DAYS of the month end (exact), else the latest
    earlier one, carried forward (not exact), since the market has moved since.

    A month end more than READING_WINDOW_DAYS before the first reading is reached only on or after
    reach_from, the earliest transaction from the account's institution: before it, the exports
    don't cover the gap. There an investment account's balance is an estimate: the first reading
    less the money put in between, as if the market stood still.
    """
    if account_readings.empty:
        return None
    end = month_end(month)
    first = account_readings.iloc[0]
    if (first["as_of"] - end).days > READING_WINDOW_DAYS:
        if reach_from is None or end < reach_from:
            return None
        if kind in config.INVESTMENT_KINDS:
            between = account_rows[(account_rows["date"] > end) & (account_rows["date"] <= first["as_of"])]
            put_in = between.loc[(between["transfer"] == 1) | between["category"].isin(MONEY_IN_CATEGORIES), "amount"].sum()
            return {"balance": cents(first["balance"] - put_in), "as_of": first["as_of"], "exact": False,
                    "estimated": True}
    distance = account_readings["as_of"].map(lambda day: abs((day - end).days))
    nearest = account_readings.loc[distance.idxmin()]
    if kind in config.INVESTMENT_KINDS:
        if distance.min() <= READING_WINDOW_DAYS:
            return {"balance": cents(nearest["balance"]), "as_of": nearest["as_of"], "exact": True, "estimated": False}
        earlier = account_readings[account_readings["as_of"] <= end]
        if earlier.empty:
            return None
        last = earlier.iloc[-1]
        return {"balance": cents(last["balance"]), "as_of": last["as_of"], "exact": False, "estimated": False}
    as_of = nearest["as_of"]
    if as_of <= end:
        between = account_rows.loc[(account_rows["date"] > as_of) & (account_rows["date"] <= end), "amount"].sum()
    else:
        between = -account_rows.loc[(account_rows["date"] > end) & (account_rows["date"] <= as_of), "amount"].sum()
    return {"balance": cents(nearest["balance"] + between), "as_of": as_of, "exact": True, "estimated": False}


def money_in(account_rows: pd.DataFrame, month: str) -> float:
    """What went into (or out of) an investment account in a month that wasn't the market: its
    transfer legs and its retirement and employer_match rows."""
    rows = account_rows[account_rows["month"] == month]
    own = rows[(rows["transfer"] == 1) | rows["category"].isin(MONEY_IN_CATEGORIES)]
    return cents(own["amount"].sum())


def as_of_label(kind: str, as_of: date, exact: bool, month: str, estimated: bool = False) -> str:
    """How a month-end balance was known: the reading's day; "carried" after it for an older
    investment reading; "est. from" it for an investment balance estimated back from a later
    reading; "from transactions" when a cash, card or loan reading was brought to the month end by
    the transactions in between."""
    day = f"{as_of:%b} {as_of.day}"
    if estimated:
        return f"est. from {day}"
    if kind in config.INVESTMENT_KINDS:
        return day if exact else f"{day}, carried"
    return day if as_of == month_end(month) else f"{day}, from transactions"


def reach_dates(rows: pd.DataFrame) -> dict[str, date]:
    """How far back each account's transactions reach: the earliest transaction from its
    institution, since one export covers all of an institution's accounts (a Fidelity history file
    holds every Fidelity account, even one with no activity). Accounts with none are left out."""
    institutions = {key: spec["institution"] for key, spec in config.ACCOUNTS.items()}
    earliest = rows.assign(institution=rows["account"].map(institutions)).groupby("institution")["date"].min()
    return {key: earliest[institution] for key, institution in institutions.items() if institution in earliest}


def month_end_balances(today: date | None = None, db_path: Path | None = None) -> tuple[list[str], dict[str, dict[str, dict]]]:
    """Every account's balance at every month end before today, from the first month end any
    account has a balance for (a reading's, or earlier where transactions reach back from one):
    the months, and {account: {month: month_end_balance}} with only the months the account has a
    balance for. Both empty with no readings."""
    today = today or date.today()
    all_readings, all_rows = readings(db_path), balance_rows(db_path)
    if all_readings.empty:
        return [], {}
    reach = reach_dates(all_rows)
    starts = [min(served_month(day), day.strftime("%Y-%m")) for day in all_readings["as_of"]]
    starts += [reach[key].strftime("%Y-%m") for key in set(all_readings["account"]) if key in reach]
    first = min(starts)
    last = (today.replace(day=1) - pd.Timedelta(days=1)).strftime("%Y-%m")
    if first > last:
        return [], {}
    months = [str(period) for period in pd.period_range(first, last, freq="M")]
    balances: dict[str, dict[str, dict]] = {}
    for key, spec in config.ACCOUNTS.items():
        own_readings = all_readings[all_readings["account"] == key].reset_index(drop=True)
        own_rows = all_rows[all_rows["account"] == key]
        found = {month: month_end_balance(spec["kind"], own_readings, own_rows, month, reach.get(key)) for month in months}
        balances[key] = {month: found for month, found in found.items() if found}
    shown = [month for month in months if any(month in by_month for by_month in balances.values())]
    return (months[months.index(shown[0]):] if shown else []), balances


def net_worth(today: date | None = None, db_path: Path | None = None) -> dict:
    """Everything the Net worth tab shows, for the month ends before today from the first one any
    account has a balance for:

    "months": those months; "by_kind": a table of each kind's total per month (debts negative);
    "total": net worth per month; "accounts": one row per account with a balance at the latest
    month end (key, name, kind, balance, as_of, exact, estimated, change, money_in, growth; the
    last three None when they can't be known); "estimated": the months whose net worth includes an
    estimate; "latest" and "previous": those months; "change": net worth's change since the
    previous month end; "market": the investment accounts' growth, summed, when every one with a
    balance has it (else None); "you": the rest of the change.
    """
    today = today or date.today()
    empty = {"months": [], "by_kind": pd.DataFrame(), "total": pd.Series(dtype=float), "accounts": pd.DataFrame(),
             "estimated": [], "latest": None, "previous": None, "change": None, "market": None, "you": None}
    months, balances = month_end_balances(today, db_path)
    if not months:
        return empty
    all_rows = balance_rows(db_path)

    by_kind = pd.DataFrame(0.0, index=months, columns=sorted({spec["kind"] for spec in config.ACCOUNTS.values()}))
    for key, by_month in balances.items():
        for month, found in by_month.items():
            by_kind.loc[month, config.ACCOUNTS[key]["kind"]] += found["balance"]
    by_kind = by_kind.loc[:, (by_kind != 0).any()].round(2) + 0.0
    total = (by_kind.sum(axis=1).round(2) + 0.0) if not by_kind.empty else pd.Series(0.0, index=months)
    estimated = [month for month in months
                 if any(by_month.get(month, {}).get("estimated") for by_month in balances.values())]

    latest, previous = months[-1], (months[-2] if len(months) > 1 else None)
    rows = []
    for key, spec in config.ACCOUNTS.items():
        now = balances[key].get(latest)
        if now is None:
            continue
        before = balances[key].get(previous) if previous else None
        investment = spec["kind"] in config.INVESTMENT_KINDS
        put_in = money_in(all_rows[all_rows["account"] == key], latest) if investment else None
        growth = None
        if investment and before and now["exact"] and before["exact"]:
            growth = cents(now["balance"] - before["balance"] - put_in)
        rows.append({"key": key, "name": spec["name"], "kind": spec["kind"], "balance": now["balance"],
                     "as_of": now["as_of"], "exact": now["exact"], "estimated": now["estimated"],
                     "change": cents(now["balance"] - before["balance"]) if before else None,
                     "money_in": put_in, "growth": growth})
    accounts = pd.DataFrame(rows, columns=["key", "name", "kind", "balance", "as_of", "exact", "estimated", "change",
                                           "money_in", "growth"])
    for column in ("change", "money_in", "growth"):  # None where a figure can't be known, never NaN
        accounts[column] = accounts[column].astype(object).where(accounts[column].notna(), None)
    change = cents(total[latest] - total[previous]) if previous else None
    growths = accounts.loc[accounts["kind"].isin(config.INVESTMENT_KINDS), "growth"]
    market = cents(growths.sum()) if change is not None and len(growths) and growths.notna().all() else None
    return {"months": months, "by_kind": by_kind, "total": total, "accounts": accounts, "estimated": estimated,
            "latest": latest,
            "previous": previous, "change": change, "market": market,
            "you": cents(change - market) if market is not None else None}


# ---------------------------------------------------------------- periods, for Finance mode

PERIOD_KINDS = ["Month", "Quarter", "Year to date", "Year"]


def complete_months(today: date | None = None, db_path: Path | None = None) -> list[str]:
    """The months with data whose last day is before today, oldest first."""
    today = today or date.today()
    return [month for month in months(db_path) if month_end(month) < today]


def span(first: str, last: str) -> list[str]:
    """The months from first through last, as "YYYY-MM"."""
    return [str(period) for period in pd.period_range(first, last, freq="M")]


def shift(month: str, by: int) -> str:
    """The month `by` months after (or before, negative) a month."""
    return str(pd.Period(month, freq="M") + by)


def short(month: str) -> str:
    """ "2026-10" becomes "Oct"."""
    return month_label(month)[:3]


def period_of(kind: str, month: str) -> dict:
    """The period of a kind that holds `month`: its key, label, months and end (its last month).
    For a month and for year to date, that is the period ending with the month itself."""
    year = month[:4]
    if kind == "Month":
        period = {"key": month, "label": month_label(month), "months": [month], "end": month}
    elif kind == "Quarter":
        number = (int(month[5:7]) - 1) // 3 + 1
        first, last = f"{year}-{3 * number - 2:02d}", f"{year}-{3 * number:02d}"
        period = {"key": f"{year}-Q{number}", "label": f"Q{number} {year}", "months": span(first, last), "end": last}
    elif kind == "Year to date":
        label = f"{short(f'{year}-01')}–{short(month)} {year}" if month[5:7] != "01" else f"{short(month)} {year}"
        period = {"key": f"{year}-ytd-{month[5:7]}", "label": label, "months": span(f"{year}-01", month), "end": month}
    else:
        period = {"key": year, "label": year, "months": span(f"{year}-01", f"{year}-12"), "end": f"{year}-12"}
    return {"kind": kind, **period}


def previous_period(kind: str, period: dict) -> dict:
    """The period before one: the previous month, quarter or year; for year to date, the same
    months of the year before."""
    if kind == "Year to date":
        return period_of(kind, shift(period["end"], -12))
    return period_of(kind, shift(period["end"], -len(period["months"])))


def periods(kind: str, today: date | None = None, db_path: Path | None = None) -> list[dict]:
    """The complete periods of a kind that hold data, newest first, each with the period before it
    under "previous". A period is complete when its last month ended before today, so a quarter or
    year in progress isn't offered; year to date is how the current year reads."""
    today = today or date.today()
    found: dict[str, dict] = {}
    for month in complete_months(today, db_path):
        period = period_of(kind, month)
        if month_end(period["end"]) >= today:
            continue  # still in progress
        found.setdefault(period["key"], {**period, "previous": previous_period(kind, period)})
    return sorted(found.values(), key=lambda period: period["end"], reverse=True)


# ---------------------------------------------------------------- statements

# The cash-account legs of transfers whose partner is an investment or loan account: money out
# negative, money back positive, dated by the cash leg. Brokerage inflow seen from the cash side,
# for every investment kind, plus loan payments and proceeds.
CASH_LEGS_SQL = """
SELECT leg.month, leg.amount, partner_account.kind AS partner_kind
FROM transactions AS leg
JOIN accounts AS account ON account.name = leg.account
JOIN transactions AS partner ON partner.id = leg.transfer_id
JOIN accounts AS partner_account ON partner_account.name = partner.account
WHERE account.kind = 'cash' AND partner_account.kind IN ('brokerage', 'retirement', 'hsa', 'loan')
  AND leg.month BETWEEN ? AND ?
"""


def category_totals(first: str, last: str, db_path: Path | None = None) -> pd.Series:
    """The sum of amounts in each category over the months first through last, signed, to cents."""
    rows = read("SELECT category, SUM(amount) AS amount FROM transactions WHERE month BETWEEN ? AND ? GROUP BY category",
                (first, last), db_path)
    return rows.set_index("category")["amount"].round(2) + 0.0


def is_investment_income(description: str) -> bool:
    """Whether a description says money was received: a dividend, interest or a capital gain
    distribution (config.INVESTMENT_INCOME), by pass 1's own word matching."""
    text = normalize(description)
    return any(categorize.mentions(text, word) for word in config.INVESTMENT_INCOME)


def investment_income(first: str, last: str, db_path: Path | None = None) -> pd.DataFrame:
    """Dividends, interest and capital gain distributions received in the months first through last:
    the positive `investment` rows whose description says so, as account, kind (the account's) and
    amount. Reinvestments, buys, sells, fees and a 401(k)'s change in market value are never income."""
    rows = read(
        "SELECT t.account, a.kind, t.amount, t.description FROM transactions AS t JOIN accounts AS a ON a.name = t.account"
        " WHERE t.category = 'investment' AND t.amount > 0 AND t.month BETWEEN ? AND ?",
        (first, last),
        db_path,
    )
    received = rows["description"].map(is_investment_income).astype(bool)
    return rows.loc[received, ["account", "kind", "amount"]].reset_index(drop=True)


def cash_legs(first: str, last: str, db_path: Path | None = None) -> pd.DataFrame:
    """The cash-account legs of transfers to or from investment and loan accounts in the months first
    through last: month, amount (money out negative) and the partner account's kind."""
    return read(CASH_LEGS_SQL, (first, last), db_path)


def contributions_between(first: str, last: str, db_path: Path | None = None) -> pd.Series:
    """Contributions in the months first through last (`retirement` rows on accounts with a cap),
    summed by cap, to cents."""
    rows = read(
        "SELECT a.cap_key, SUM(t.amount) AS amount FROM transactions AS t JOIN accounts AS a ON a.name = t.account"
        " WHERE t.category = 'retirement' AND a.cap_key IS NOT NULL AND t.month BETWEEN ? AND ? GROUP BY a.cap_key",
        (first, last),
        db_path,
    )
    return rows.set_index("cap_key")["amount"].round(2) + 0.0


def non_cash_investing(first: str, last: str, db_path: Path | None = None) -> float:
    """Payroll deferrals and employer money in the months first through last: `retirement` and
    `employer_match` rows with no transfer partner, which reached the accounts without passing
    through cash."""
    rows = read(
        "SELECT COALESCE(SUM(amount), 0) AS amount FROM transactions"
        " WHERE category IN ('retirement', 'employer_match') AND transfer_id IS NULL AND month BETWEEN ? AND ?",
        (first, last),
        db_path,
    )
    return cents(rows["amount"].iloc[0])


def last_import(db_path: Path | None = None) -> pd.DataFrame:
    """The imports rows of the most recent import run."""
    return read(
        "SELECT file, institution, rows_read, rows_new, rows_duplicate, imported_at FROM imports"
        " WHERE imported_at = (SELECT MAX(imported_at) FROM imports) ORDER BY file",
        db_path=db_path,
    )


def import_summary(imports: pd.DataFrame) -> str:
    """ "3 files, 212 rows, 0 new" for a set of imports rows."""
    files = len(imports)
    return f"{files} file{'s' * (files != 1)}, {int(imports['rows_read'].sum())} rows, {int(imports['rows_new'].sum())} new"
