"""Tests for Finance mode: periods (queries.periods), the three statements (statements.balance_sheet,
income_statement and cash_flow) and the Personal | Finance toggle on the page. All made up, on the
accounts in tests/accounts.toml."""

from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

import config
import db
import queries
import statements

TODAY = date(2026, 11, 7)


def database(tmp_path: Path, months: tuple = (), readings: tuple = ()) -> Path:
    """A database with one made-up spending row in each of `months` and the given
    (account, as_of, balance) readings."""
    path = tmp_path / "finance.db"
    with closing(db.connect(path)) as connection, connection:
        connection.executemany(
            "INSERT INTO transactions (id, account, date, amount, description, category, month, imported_at)"
            " VALUES (?, 'amex', ?, -10.0, 'CORNER CAFE', 'dining', ?, 'test')",
            [(f"row{month}", f"{month}-05", month) for month in months],
        )
        connection.executemany(
            "INSERT INTO balances (account, as_of, balance, source, recorded_at) VALUES (?, ?, ?, 'test', 'test')", readings)
    return path


def keys(found: list[dict]) -> list[str]:
    return [period["key"] for period in found]


# A month of made-up books whose readings agree with the transactions, so the cash flow reconciles:
# (id, account, date, amount, description, category, transfer partner).
OCTOBER_ROWS = [
    ("pay", "bofa_checking", "2026-10-15", 3000.0, "ACME CORP PAYROLL", "income", None),
    ("groceries", "bofa_checking", "2026-10-03", -500.0, "WHOLE FOODS", "groceries", None),
    ("card_out", "bofa_checking", "2026-10-20", -800.0, "AMERICAN EXPRESS PAYMENT", "transfer", "card_in"),
    ("card_in", "amex", "2026-10-21", 800.0, "PAYMENT RECEIVED", "transfer", "card_out"),
    ("dining", "amex", "2026-10-08", -300.0, "CORNER CAFE", "dining", None),
    ("shopping", "amex", "2026-10-12", -200.0, "BOOK BARN", "shopping", None),
    ("brokerage_out", "bofa_checking", "2026-10-25", -1000.0, "FID BKG SVC LLC", "transfer", "brokerage_in"),
    ("brokerage_in", "fidelity_brokerage", "2026-10-26", 1000.0, "ELECTRONIC FUNDS TRANSFER RECEIVED", "transfer", "brokerage_out"),
    ("dividend", "fidelity_brokerage", "2026-10-30", 50.0, "DIVIDEND RECEIVED SPAXX", "investment", None),
    ("reinvest", "fidelity_brokerage", "2026-10-30", -50.0, "REINVESTMENT SPAXX", "investment", None),
    ("sweep", "fidelity_cash", "2026-10-31", 5.0, "DIVIDEND RECEIVED", "investment", None),
    ("roth_out", "fidelity_cash", "2026-10-10", -600.0, "TRANSFERRED TO ROTH IRA", "transfer", "roth_in"),
    ("roth_in", "fidelity_roth", "2026-10-11", 600.0, "CONTRIBUTION", "retirement", "roth_out"),
    ("deferral", "fidelity_401k", "2026-10-15", 500.0, "CONTRIBUTION", "retirement", None),
    ("match", "fidelity_401k", "2026-10-15", 250.0, "EMPLOYER MATCH", "employer_match", None),
    ("market", "fidelity_401k", "2026-10-31", 100.0, "CHANGE IN MARKET VALUE", "investment", None),
]
SEPTEMBER_ROWS = [
    ("pay0", "bofa_checking", "2026-09-15", 2900.0, "ACME CORP PAYROLL", "income", None),
    ("groceries0", "bofa_checking", "2026-09-04", -450.0, "WHOLE FOODS", "groceries", None),
    ("gas0", "amex", "2026-09-09", -60.0, "SHELL", "gas", None),
]
BOOK_READINGS = (
    ("bofa_checking", "2026-09-30", 1000.0), ("fidelity_cash", "2026-09-30", 1000.0), ("amex", "2026-09-30", -500.0),
    ("fidelity_brokerage", "2026-09-30", 4000.0), ("fidelity_401k", "2026-09-30", 8500.0), ("fidelity_roth", "2026-09-30", 3000.0),
    ("bofa_checking", "2026-10-31", 1700.0), ("fidelity_cash", "2026-10-31", 405.0), ("amex", "2026-10-31", -200.0),
    ("fidelity_brokerage", "2026-10-31", 5050.0), ("fidelity_401k", "2026-10-31", 9350.0), ("fidelity_roth", "2026-10-31", 3600.0),
)


def books(tmp_path: Path, rows: list[tuple] = OCTOBER_ROWS + SEPTEMBER_ROWS, readings: tuple = BOOK_READINGS) -> Path:
    """A database holding the rows and readings."""
    path = tmp_path / "finance.db"
    with closing(db.connect(path)) as connection, connection:
        connection.executemany(
            "INSERT INTO transactions (id, account, date, amount, description, category, transfer_id, month, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test')",
            [(*row, row[2][:7]) for row in rows],
        )
        connection.executemany(
            "INSERT INTO balances (account, as_of, balance, source, recorded_at) VALUES (?, ?, ?, 'test', 'test')", readings)
    return path


def unmatched(rows: list[tuple], *ids: str) -> list[tuple]:
    """The rows with those transfer legs left unmatched: uncategorized, with no partner."""
    return [(*row[:5], "uncategorized", None) if row[0] in ids else row for row in rows]


def amounts(statement: dict) -> dict[str, float | None]:
    return statement["lines"].set_index("label")["amount"].to_dict()


def priors(statement: dict) -> dict[str, float | None]:
    return statement["lines"].set_index("label")["prior"].to_dict()


# ---------------------------------------------------------------- periods


def test_months_are_offered_newest_first_each_with_the_month_before(tmp_path):
    path = database(tmp_path, months=("2026-07", "2026-08", "2026-09", "2026-10", "2026-11"))
    found = queries.periods("Month", TODAY, path)
    assert keys(found) == ["2026-10", "2026-09", "2026-08", "2026-07"]  # November hasn't ended
    assert found[0]["label"] == "October 2026" and found[0]["months"] == ["2026-10"]
    assert found[-1]["previous"]["key"] == "2026-06"  # the month before, data or not


def test_only_complete_quarters_are_offered(tmp_path):
    path = database(tmp_path, months=("2026-07", "2026-08", "2026-09", "2026-10"))
    found = queries.periods("Quarter", TODAY, path)
    assert keys(found) == ["2026-Q3"]  # Q4 is in progress
    assert found[0]["label"] == "Q3 2026" and found[0]["months"] == ["2026-07", "2026-08", "2026-09"]
    assert found[0]["previous"]["key"] == "2026-Q2" and found[0]["previous"]["months"] == ["2026-04", "2026-05", "2026-06"]


def test_year_to_date_runs_from_january_and_compares_with_the_same_months_last_year(tmp_path):
    path = database(tmp_path, months=("2026-09", "2026-10"))
    found = queries.periods("Year to date", TODAY, path)
    assert [period["label"] for period in found] == ["Jan–Oct 2026", "Jan–Sep 2026"]
    assert found[0]["months"] == queries.span("2026-01", "2026-10") and found[0]["end"] == "2026-10"
    assert found[0]["previous"]["label"] == "Jan–Oct 2025" and found[0]["previous"]["months"][-1] == "2025-10"


def test_a_year_is_offered_once_it_has_ended(tmp_path):
    path = database(tmp_path, months=("2026-09", "2026-10", "2026-12"))
    assert queries.periods("Year", TODAY, path) == []
    found = queries.periods("Year", date(2027, 1, 5), path)
    assert keys(found) == ["2026"] and found[0]["previous"]["key"] == "2025"
    assert found[0]["months"] == queries.span("2026-01", "2026-12")


def test_no_data_means_no_periods(tmp_path):
    assert queries.periods("Month", TODAY, database(tmp_path)) == []


# ---------------------------------------------------------------- the balance sheet


@pytest.mark.parametrize(("value", "shown"), [(1234.4, "$1,234"), (-1234.5, "($1,235)"), (0.0, "$0"), (None, "—")])
def test_accounting_style(value, shown):
    assert statements.accounting(value) == shown


OCTOBER = (("fidelity_brokerage", "2026-10-31", 5000.0), ("fidelity_401k", "2026-10-31", 9000.0),
           ("bofa_checking", "2026-10-31", 1000.0), ("amex", "2026-10-31", -300.0))
SEPTEMBER = (("fidelity_brokerage", "2026-09-30", 4000.0), ("fidelity_401k", "2026-09-30", 8500.0),
             ("bofa_checking", "2026-09-30", 1200.0), ("amex", "2026-09-30", -500.0))


def october(path: Path) -> dict:
    [period] = [period for period in queries.periods("Month", TODAY, path) if period["key"] == "2026-10"]
    return period


def test_the_balance_sheet_regroups_net_worth_and_balances(tmp_path):
    path = database(tmp_path, months=("2026-10",), readings=OCTOBER)
    sheet = statements.balance_sheet(october(path), TODAY, path)
    lines = sheet["lines"].set_index("label")
    assert lines["amount"].to_dict() == {
        "Assets": None, "Cash and cash equivalents": 1000.0, "Investments": 5000.0, "Retirement accounts": 9000.0,
        "Health savings": None, "Total assets": 15000.0,
        "Liabilities": None, "Credit cards": 300.0, "Loans": None, "Total liabilities": 300.0,
        "Net worth": 14700.0,
    }
    assert lines.loc["Net worth", "amount"] == queries.net_worth(TODAY, path)["total"]["2026-10"]
    assert lines["level"].tolist() == ["section", "line", "line", "line", "line", "subtotal",
                                       "section", "line", "line", "subtotal", "total"]
    assert lines["prior"].isna().all() and lines["change"].isna().all()  # no September balances
    assert (sheet["end"], sheet["prior_end"]) == ("2026-10", None)


def test_the_prior_column_is_the_previous_period_end_with_the_change(tmp_path):
    path = database(tmp_path, months=("2026-09", "2026-10"), readings=OCTOBER + SEPTEMBER)
    sheet = statements.balance_sheet(october(path), TODAY, path)
    lines = sheet["lines"].set_index("label")
    assert (lines.loc["Net worth", "prior"], lines.loc["Net worth", "change"]) == (13200.0, 1500.0)
    assert (lines.loc["Credit cards", "prior"], lines.loc["Credit cards", "change"]) == (500.0, -200.0)  # less owed
    assert sheet["prior_end"] == "2026-09"
    cards = sheet["accounts"]["Credit cards"]
    assert cards[["name", "amount", "prior", "as_of", "prior_as_of"]].values.tolist() == [
        ["Amex", 300.0, 500.0, "Oct 31", "Sep 30"]]
    assert "Loans" not in sheet["accounts"] and "Health savings" not in sheet["accounts"]


def test_a_period_end_without_balances_says_so(tmp_path):
    path = database(tmp_path, months=("2026-09", "2026-10"), readings=OCTOBER)
    [september] = [period for period in queries.periods("Month", TODAY, path) if period["key"] == "2026-09"]
    sheet = statements.balance_sheet(september, TODAY, path)
    assert sheet["end"] is None and sheet["lines"]["amount"].isna().all()


def test_a_quarter_is_as_of_its_last_month_end(tmp_path):
    path = database(tmp_path, months=("2026-07", "2026-08", "2026-09"),
                    readings=(("bofa_checking", "2026-09-30", 1200.0), ("bofa_checking", "2026-06-30", 900.0)))
    [quarter] = queries.periods("Quarter", TODAY, path)
    sheet = statements.balance_sheet(quarter, TODAY, path)
    lines = sheet["lines"].set_index("label")
    assert (sheet["end"], sheet["prior_end"]) == ("2026-09", "2026-06")
    assert (lines.loc["Net worth", "amount"], lines.loc["Net worth", "prior"]) == (1200.0, 900.0)


# ---------------------------------------------------------------- the income statement


def test_investment_income_is_what_was_received_not_bought_sold_or_revalued(tmp_path):
    path = books(tmp_path)
    income = queries.investment_income("2026-10", "2026-10", path)
    assert sorted(income[["account", "kind", "amount"]].values.tolist()) == [
        ["fidelity_brokerage", "brokerage", 50.0], ["fidelity_cash", "cash", 5.0]]  # not the reinvestment or the 401(k)'s market value
    assert queries.investment_income("2026-09", "2026-09", path).empty


@pytest.mark.parametrize(("description", "income"), [
    ("DIVIDEND RECEIVED", True), ("INTEREST EARNED", True), ("LONG-TERM CAPITAL GAIN", True), ("DIVIDENDS", True),
    ("REINVESTMENT", False), ("YOU BOUGHT", False), ("CHANGE IN MARKET VALUE", False), ("REVENUE CREDIT", False),
])
def test_the_words_that_mean_money_received(description, income):
    assert queries.is_investment_income(description) is income


def test_the_income_statement_on_a_cash_basis(tmp_path):
    path = books(tmp_path)
    statement = statements.income_statement(october(path), path)
    assert amounts(statement) == {
        "Income": None, "Salary and wages": 3000.0, "Employer retirement contributions": 250.0, "Investment income": 55.0,
        "Total income": 3305.0,
        "Expenses": None, "Groceries": 500.0, "Dining": 300.0, "Shopping": 200.0, "Gas": 0.0, "Total expenses": 1000.0,
        "Net income": 2305.0,
        "Memo: contributions to retirement and health accounts": None, "401(k)": 500.0, "Roth IRA": 600.0, "HSA": 0.0,
    }
    assert priors(statement)["Salary and wages"] == 2900.0 and priors(statement)["Gas"] == 60.0
    assert priors(statement)["Dining"] == 0.0 and priors(statement)["Net income"] == 2390.0
    levels = statement["lines"].set_index("label")["level"]
    assert levels["Total income"] == "subtotal" and levels["Net income"] == "total" and levels["Groceries"] == "line"


def test_salary_and_expenses_equal_this_months_income_and_spending(tmp_path):
    path = books(tmp_path)
    found = amounts(statements.income_statement(october(path), path))
    totals = queries.income_and_spending(path).loc["2026-10"]
    assert found["Salary and wages"] == totals["income"] and found["Total expenses"] == totals["spending"]
    by_category = queries.spending_by_category("2026-10", path).set_index("category")["amount"]
    assert {name: found[statements.category_label(name)] for name in by_category.index} == by_category.to_dict()


def test_expenses_run_largest_first_with_uncategorized_last(tmp_path):
    rows = OCTOBER_ROWS + [("odd", "amex", "2026-10-14", -900.0, "MYSTERY SHOP", "uncategorized", None),
                           ("refund", "amex", "2026-10-15", 40.0, "BOOK BARN REFUND", "shopping", None)]
    path = books(tmp_path, rows)
    lines = statements.income_statement(october(path), path)["lines"]
    labels = lines["label"].tolist()
    expenses = lines.iloc[labels.index("Expenses") + 1:labels.index("Total expenses")]
    assert expenses["label"].tolist() == ["Groceries", "Dining", "Shopping", "Uncategorized"]  # no September, so no Gas
    assert expenses["amount"].tolist() == [500.0, 300.0, 160.0, 900.0]  # shopping net of the refund


def test_a_period_without_transactions_shows_dashes(tmp_path):
    path = books(tmp_path, OCTOBER_ROWS)  # nothing in September
    statement = statements.income_statement(october(path), path)
    assert statement["prior"] is None and statement["lines"]["prior"].isna().all()
    assert statement["lines"]["change"].isna().all()


# ---------------------------------------------------------------- the cash flow


def test_the_cash_flow_reconciles_to_zero_on_consistent_books(tmp_path):
    path = books(tmp_path)
    flow = statements.cash_flow(october(path), TODAY, path)
    assert amounts(flow) == {
        "Operating activities": None, "Net income": 2305.0, "Investment income kept in investment accounts": -50.0,
        "Employer contributions, received in retirement accounts": -250.0,
        "Increase (decrease) in credit card balances": -300.0, "Net cash from operating activities": 1705.0,
        "Investing activities": None, "Transfers to investment accounts": -1600.0, "Net cash from investing activities": -1600.0,
        "Financing activities": None, "Loan payments and proceeds": 0.0, "Net cash from financing activities": 0.0,
        "Net change in cash": 105.0, "Cash at the start of the period": 2000.0, "Cash at the end of the period": 2105.0,
        "Not explained by the transactions": 0.0,
        "Memo": None, "Non-cash investing: payroll deferrals and employer money": 750.0,
    }
    sheet = statements.balance_sheet(october(path), TODAY, path)
    assert amounts(flow)["Cash at the end of the period"] == amounts(sheet)["Cash and cash equivalents"]
    assert amounts(flow)["Cash at the start of the period"] == priors(sheet)["Cash and cash equivalents"]
    assert amounts(flow)["Net income"] == amounts(statements.income_statement(october(path), path))["Net income"]


def test_an_unmatched_transfer_shows_in_the_last_line(tmp_path):
    path = books(tmp_path, unmatched(OCTOBER_ROWS + SEPTEMBER_ROWS, "brokerage_out", "brokerage_in"))
    flow = amounts(statements.cash_flow(october(path), TODAY, path))
    assert flow["Net income"] == 2305.0  # the two uncategorized legs cancel in spending
    assert flow["Transfers to investment accounts"] == -600.0  # only the Roth contribution is seen
    assert flow["Net change in cash"] == 1105.0 and flow["Cash at the end of the period"] - flow["Cash at the start of the period"] == 105.0
    assert flow["Not explained by the transactions"] == -1000.0


def test_a_missing_export_shows_in_the_last_line(tmp_path):
    rows = [row for row in OCTOBER_ROWS + SEPTEMBER_ROWS if row[0] != "card_in"]  # the card's payment never imported
    path = books(tmp_path, unmatched(rows, "card_out"))
    flow = amounts(statements.cash_flow(october(path), TODAY, path))
    assert flow["Net income"] == 1505.0  # the payment counted as spending
    assert flow["Not explained by the transactions"] == 800.0


def test_the_prior_column_needs_the_balance_sheet_before_it(tmp_path):
    path = books(tmp_path)
    flow = statements.cash_flow(october(path), TODAY, path)
    before = priors(flow)
    assert before["Net income"] == 2390.0 and before["Cash at the end of the period"] == 2000.0
    assert before["Increase (decrease) in credit card balances"] is None  # no August balances
    assert before["Net cash from operating activities"] is None and before["Not explained by the transactions"] is None
    assert (flow["end"], flow["prior_end"]) == ("2026-10", "2026-09")


def test_a_quarters_cash_flow_spans_its_months(tmp_path):
    rows = OCTOBER_ROWS + SEPTEMBER_ROWS + [("pay8", "bofa_checking", "2026-08-14", 2800.0, "ACME CORP PAYROLL", "income", None)]
    readings = (("bofa_checking", "2026-06-30", 1000.0), ("fidelity_cash", "2026-06-30", 1000.0), ("amex", "2026-06-30", -440.0),
                ("bofa_checking", "2026-09-30", 6250.0), ("fidelity_cash", "2026-09-30", 1000.0), ("amex", "2026-09-30", -500.0))
    path = books(tmp_path, rows, readings)
    [quarter] = queries.periods("Quarter", TODAY, path)  # Q3; Q4 is in progress
    flow = amounts(statements.cash_flow(quarter, TODAY, path))
    assert flow["Net income"] == 2800.0 + 2900.0 - 450.0 - 60.0
    assert flow["Increase (decrease) in credit card balances"] == 60.0
    assert (flow["Cash at the start of the period"], flow["Cash at the end of the period"]) == (2000.0, 7250.0)
    assert flow["Not explained by the transactions"] == 0.0


# ---------------------------------------------------------------- the page


def test_the_toggle_switches_the_page_and_the_query_string_remembers_it(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    path = books(tmp_path)
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "RULES_PATH", tmp_path / "rules.csv")
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(config, "BUDGET_PATH", tmp_path / "budget.toml")
    monkeypatch.setattr(config, "CAPS", {2026: {"401k": 24_000, "roth": 7_200}})
    dashboard = str(Path(__file__).resolve().parent.parent / "dashboard.py")

    page = AppTest.from_file(dashboard, default_timeout=60).run()
    assert not page.exception
    assert page.segmented_control[0].value == "Personal"
    assert [tab.label for tab in page.tabs] == ["This month", "Net worth", "Budget vs actual", "Budget", "Assumptions & taxes"]

    page.segmented_control[0].set_value("Finance").run()
    assert not page.exception
    assert page.query_params["mode"] == "finance"
    assert [tab.label for tab in page.tabs] == ["Balance sheet", "Income statement", "Cash flow"]
    assert page.radio[0].value == "Month" and page.selectbox[0].value["label"] == "October 2026"
    sheet = page.tabs[0].table[0].value
    assert sheet.iloc[-1].tolist() == ["Net worth", "$19,905", "$17,000", "$2,905"]
    income = page.tabs[1].table[0].value.set_index("Line")
    assert income.loc["Net income"].tolist() == ["$2,305", "$2,390", "($85)"]
    flow = page.tabs[2].table[0].value.set_index("Line")
    assert flow.loc["Not explained by the transactions"].tolist() == ["$0", "—", "—"]
    assert flow.loc["Net change in cash"].tolist() == ["$105", "—", "—"]

    opened = AppTest.from_file(dashboard, default_timeout=60)
    opened.query_params["mode"] = "finance"
    opened.run()
    assert not opened.exception and [tab.label for tab in opened.tabs] == ["Balance sheet", "Income statement", "Cash flow"]
