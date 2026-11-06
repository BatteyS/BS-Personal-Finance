"""Every number on the page, computed from the transactions table. See docs/spec.md, Measures.

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
