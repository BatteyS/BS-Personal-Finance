"""Tests for Net worth: the readings the loader takes from Bank of America's running balance and a
Fidelity positions file, recording a balance on the page, and the measures (queries.net_worth).
Everything here is made up, on the accounts in tests/accounts.toml."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import actions
import config
import db
import load
import queries

TODAY = date(2026, 11, 7)

BOFA = """\
Description,,Summary Amt.
Beginning balance as of 10/01/2026,,"5,000.00"

Date,Description,Amount,Running Bal.
10/01/2026,Beginning balance as of 10/01/2026,,"5,000.00"
10/02/2026,"AMERICAN EXPRESS DES:ACH PMT ID:W00000001 INDN:REDACTED CO ID:0000000002 WEB","-1,235.00","3,765.00"
10/03/2026,"ACME CORP DES:PAYROLL ID:000003 INDN:REDACTED CO ID:0000000004 PPD","3,000.00","6,765.00"
10/04/2026,CTA VENTRA,-2.50,"6,762.50"
10/04/2026,CTA VENTRA,-2.50,"6,760.00"
"""
POSITIONS_HEADER = ("Account number,Account name,Symbol,Description,Quantity,Last price,Last price change,Current value,"
                    "Today's gain/loss dollar,Today's gain/loss percent,Total gain/loss dollar,Total gain/loss percent,"
                    "Percent of account,Cost basis total,Average cost basis,Type")
HOLDINGS = [  # every row ends with a trailing comma, as Fidelity's do
    'Z00000001,Cash Management,SPAXX**,HELD IN MONEY MARKET,,,,"$1,250.00",,,,,10.00%,,,Cash,',
    'Z00000002,Individual,SPAXX**,HELD IN MONEY MARKET,,,,$100.00,,,,,0.80%,,,Cash,',
    'Z00000002,Individual,FXAIX,FIDELITY 500 INDEX FUND,10.000,$250.00,$1.00,"$2,500.00",$10.00,0.40%,$500.00,25.00%,'
    '20.00%,"$2,000.00",$200.00,Cash,',
    '00004,ACME CORP,RFVTX,AF TRGT DATE 2065 R6,100.00,$80.00,$0.50,"$8,000.00",$50.00,0.63%,$400.00,5.26%,64.00%,'
    '"$7,600.00",$76.00,,',
    'Z00000009,Someone Else,FXAIX,FIDELITY 500 INDEX FUND,1.000,$250.00,$1.00,$250.00,$1.00,0.40%,$50.00,25.00%,'
    '2.00%,$200.00,$200.00,Cash,',
]
FOOTER = ["", '"The data and information in this spreadsheet is provided to you solely for your use."', "",
          "Date downloaded Nov-07-2026 at 1:42 a.m ET", ""]


def positions(footer: bool = True) -> str:
    return "\r\n".join([POSITIONS_HEADER, *HOLDINGS, *(FOOTER if footer else [""])])


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, newline="")
    return path


def stored_readings(db_path: Path) -> list[tuple]:
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute("SELECT account, as_of, balance, source FROM balances ORDER BY account, as_of").fetchall()


def database(tmp_path: Path, rows: tuple = (), readings: tuple = ()) -> Path:
    """A database holding (id, account, date, amount, description, category, transfer_id) rows and
    (account, as_of, balance) readings."""
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


def row(row_id: str, account: str, day: str, amount: float, category: str = "transfer", transfer_id: str | None = "x",
        description: str = "MOVE") -> tuple:
    return (row_id, account, day, amount, description, category, transfer_id)


def account_row(worth: dict, key: str) -> dict:
    return worth["accounts"].set_index("key").loc[key].to_dict()


# ---------------------------------------------------------------- readings from files


def test_bofa_gives_one_reading_per_day_the_balance_after_the_days_last_row(tmp_path):
    db_path = tmp_path / "finance.db"
    [result] = load.load_files([write(tmp_path, "stmt.csv", BOFA)], db_path)
    assert result["rows_read"] == 4  # the transactions, as before
    assert stored_readings(db_path) == [
        ("bofa_checking", "2026-10-01", 5000.0, "bofa"),  # the beginning balance counts
        ("bofa_checking", "2026-10-02", 3765.0, "bofa"),
        ("bofa_checking", "2026-10-03", 6765.0, "bofa"),
        ("bofa_checking", "2026-10-04", 6760.0, "bofa"),  # after the second of the day's two rows
    ]


def test_a_bofa_file_listed_newest_first_reads_the_same(tmp_path):
    lines = BOFA.splitlines()
    newest_first = "\n".join([*lines[:4], *reversed(lines[4:])]) + "\n"
    db_path = tmp_path / "finance.db"
    load.load_files([write(tmp_path, "stmt.csv", newest_first)], db_path)
    assert [(day, balance) for _, day, balance, _ in stored_readings(db_path)] == [
        ("2026-10-01", 5000.0), ("2026-10-02", 3765.0), ("2026-10-03", 6765.0), ("2026-10-04", 6760.0)]


def test_a_positions_file_records_one_reading_per_account_as_of_the_day_in_its_name(tmp_path):
    db_path = tmp_path / "finance.db"
    path = write(tmp_path, "20261107-090000_Portfolio_Positions_Nov-02-2026.csv", positions())
    [result] = load.load_files([path], db_path)
    assert result["institution"] == "fidelity positions"
    assert (result["rows_read"], result["rows_new"], result["rows_duplicate"]) == (5, 3, 0)
    assert (result["as_of"], result["accounts"], result["skipped"]) == ("2026-11-02", 3, ["Someone Else"])
    assert stored_readings(db_path) == [
        ("fidelity_401k", "2026-11-02", 8000.0, "fidelity_positions"),
        ("fidelity_brokerage", "2026-11-02", 2600.0, "fidelity_positions"),  # cash plus the fund
        ("fidelity_cash", "2026-11-02", 1250.0, "fidelity_positions"),
    ]
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT count(*) FROM transactions").fetchone() == (0,)


def test_importing_the_same_positions_again_changes_nothing_and_a_new_value_replaces(tmp_path):
    db_path = tmp_path / "finance.db"
    path = write(tmp_path, "Portfolio_Positions_Nov-02-2026.csv", positions())
    load.load_files([path], db_path)
    [again] = load.load_files([path], db_path)
    assert (again["rows_new"], again["rows_duplicate"]) == (0, 3)
    later = write(tmp_path, "later", positions().replace('"$8,000.00"', '"$8,100.00"'))
    [changed] = load.load_files([later.rename(tmp_path / "Portfolio_Positions_Nov-02-2026 (1).csv")], db_path)
    assert (changed["rows_new"], changed["rows_duplicate"]) == (1, 2)
    assert [balance for account, _, balance, _ in stored_readings(db_path) if account == "fidelity_401k"] == [8100.0]


def test_the_day_falls_back_to_the_footer_then_to_the_import_day(tmp_path):
    db_path = tmp_path / "finance.db"
    [footer] = load.load_files([write(tmp_path, "positions.csv", positions())], db_path)
    assert footer["as_of"] == "2026-11-07"
    [none] = load.load_files([write(tmp_path, "undated.csv", positions(footer=False))], db_path)
    assert none["as_of"] == date.today().isoformat()


def test_a_positions_file_is_told_from_the_history_file():
    assert load.detect_institution(set(load.header_name(cell) for cell in POSITIONS_HEADER.split(","))) == "fidelity positions"
    assert load.detect_institution({"run date", "account", "account number", "action", "amount"}) == "fidelity"


# ---------------------------------------------------------------- recording a balance


def test_record_balance_stores_what_is_owed_negative_and_replaces_the_same_day(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "finance.db")
    actions.record_balance("amex", date(2026, 10, 31), 250.0)
    actions.record_balance("fidelity_cash", date(2026, 10, 31), 100.0)
    actions.record_balance("amex", date(2026, 10, 31), 300.0)
    assert stored_readings(tmp_path / "finance.db") == [
        ("amex", "2026-10-31", -300.0, "page"), ("fidelity_cash", "2026-10-31", 100.0, "page")]
    with pytest.raises(config.AccountsError, match="isn't one of your accounts"):
        actions.record_balance("nowhere", date(2026, 10, 31), 1.0)


def test_loan_is_a_kind_the_page_can_add(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ACCOUNTS_PATH", tmp_path / "accounts.toml")
    key = actions.add_account("Student loan", "loan", "nelnet")
    assert key == "student_loan" and config.ACCOUNTS[key]["kind"] == "loan"
    config.reload_accounts()  # the autouse fixture restores the test accounts afterwards


# ---------------------------------------------------------------- the measures


def test_a_cash_reading_is_brought_to_the_month_end_by_the_transactions_since(tmp_path):
    path = database(tmp_path, rows=(
        row("a", "bofa_checking", "2026-10-29", -50.0, "groceries", None, "GROCERY TOWN"),
        row("b", "bofa_checking", "2026-10-30", 200.0, "income", None, "ACME PAYROLL"),
        row("c", "bofa_checking", "2026-11-01", -999.0, "housing", None, "RENT"),  # next month: not counted
    ), readings=(("bofa_checking", "2026-10-28", 1000.0),))
    worth = queries.net_worth(TODAY, path)
    assert worth["months"] == ["2026-10"]
    bofa = account_row(worth, "bofa_checking")
    assert (bofa["balance"], bofa["as_of"], bofa["exact"]) == (1150.0, date(2026, 10, 28), True)
    assert worth["total"]["2026-10"] == 1150.0 and worth["by_kind"].loc["2026-10", "cash"] == 1150.0
    assert worth["change"] is None and bofa["change"] is None  # no month before it


def test_a_cash_reading_after_the_month_end_is_brought_back_by_the_transactions_between(tmp_path):
    path = database(tmp_path, rows=(row("a", "fidelity_cash", "2026-11-01", -100.0, "dining", None, "CAFE"),),
                    readings=(("fidelity_cash", "2026-11-02", 500.0),))
    worth = queries.net_worth(TODAY, path)
    assert worth["months"] == ["2026-10"]  # a reading on the 2nd stands for the month just ended
    assert account_row(worth, "fidelity_cash")["balance"] == 600.0


def test_an_investment_reading_within_a_week_of_the_month_end_stands_for_it(tmp_path):
    path = database(tmp_path, rows=(row("a", "fidelity_brokerage", "2026-11-01", 400.0),),
                    readings=(("fidelity_brokerage", "2026-11-02", 5000.0),))
    brokerage = account_row(queries.net_worth(TODAY, path), "fidelity_brokerage")
    assert (brokerage["balance"], brokerage["as_of"], brokerage["exact"]) == (5000.0, date(2026, 11, 2), True)


def test_an_older_investment_reading_is_carried_forward_and_marked(tmp_path):
    path = database(tmp_path, readings=(("fidelity_brokerage", "2026-10-15", 4000.0),))
    worth = queries.net_worth(TODAY, path)
    brokerage = account_row(worth, "fidelity_brokerage")
    assert (brokerage["balance"], brokerage["as_of"], brokerage["exact"]) == (4000.0, date(2026, 10, 15), False)
    assert brokerage["growth"] is None and worth["market"] is None


def test_growth_is_the_change_the_money_put_in_does_not_explain(tmp_path):
    path = database(tmp_path, rows=(
        row("t1", "fidelity_cash", "2026-10-10", -600.0, "transfer", "t2"),
        row("t2", "fidelity_brokerage", "2026-10-10", 600.0, "transfer", "t1"),
        row("d", "fidelity_brokerage", "2026-10-20", 20.0, "investment", None, "DIVIDEND RECEIVED"),
        row("b", "fidelity_brokerage", "2026-10-21", -300.0, "investment", None, "YOU BOUGHT"),
    ), readings=(("fidelity_brokerage", "2026-09-30", 4000.0), ("fidelity_brokerage", "2026-10-31", 5000.0),
                 ("fidelity_cash", "2026-09-30", 1000.0)))
    worth = queries.net_worth(TODAY, path)
    assert worth["months"] == ["2026-09", "2026-10"]
    brokerage = account_row(worth, "fidelity_brokerage")
    assert (brokerage["change"], brokerage["money_in"], brokerage["growth"]) == (1000.0, 600.0, 400.0)
    cash = account_row(worth, "fidelity_cash")
    assert (cash["balance"], cash["change"], cash["money_in"], cash["growth"]) == (400.0, -600.0, None, None)
    assert (worth["change"], worth["market"], worth["you"]) == (400.0, 400.0, 0.0)  # the move itself nets to nothing


def test_growth_needs_a_real_reading_at_both_month_ends(tmp_path):
    path = database(tmp_path, readings=(("fidelity_brokerage", "2026-09-15", 4000.0), ("fidelity_brokerage", "2026-10-31", 5000.0)))
    worth = queries.net_worth(TODAY, path)
    brokerage = account_row(worth, "fidelity_brokerage")
    assert brokerage["change"] == 1000.0 and brokerage["growth"] is None  # September's reading was carried
    assert (worth["change"], worth["market"], worth["you"]) == (1000.0, None, None)


def test_the_split_needs_growth_for_every_investment_account_with_a_balance(tmp_path):
    path = database(tmp_path, readings=(
        ("fidelity_brokerage", "2026-09-30", 4000.0), ("fidelity_brokerage", "2026-10-31", 5000.0),
        ("fidelity_401k", "2026-10-31", 9000.0),  # its first reading: no growth yet
    ))
    worth = queries.net_worth(TODAY, path)
    assert worth["change"] == 10000.0 and worth["market"] is None


def test_debts_count_against_net_worth_by_kind(tmp_path):
    path = database(tmp_path, readings=(("amex", "2026-10-31", -300.0), ("bofa_checking", "2026-10-31", 1000.0)))
    worth = queries.net_worth(TODAY, path)
    assert worth["by_kind"].loc["2026-10"].to_dict() == {"cash": 1000.0, "credit": -300.0}
    assert worth["total"]["2026-10"] == 700.0


def test_the_months_run_from_the_first_reading_to_the_month_end_before_today(tmp_path):
    path = database(tmp_path, readings=(("bofa_checking", "2026-09-10", 100.0), ("bofa_checking", "2026-10-20", 200.0)))
    assert queries.net_worth(TODAY, path)["months"] == ["2026-09", "2026-10"]
    assert queries.net_worth(date(2026, 10, 15), path)["months"] == ["2026-09"]
    mid_month = database(tmp_path / "later", readings=(("bofa_checking", "2026-11-15", 100.0),))
    assert queries.net_worth(date(2026, 11, 20), mid_month)["months"] == []  # November hasn't ended


def test_nothing_recorded_means_nothing_to_show(tmp_path):
    worth = queries.net_worth(TODAY, database(tmp_path))
    assert worth["months"] == [] and worth["latest"] is None and worth["accounts"].empty


@pytest.mark.parametrize(("day", "month"), [
    (date(2026, 11, 2), "2026-10"), (date(2026, 11, 7), "2026-10"), (date(2026, 11, 8), "2026-11"),
    (date(2026, 10, 25), "2026-10"), (date(2026, 10, 15), "2026-10"), (date(2026, 10, 31), "2026-10"),
])
def test_which_month_end_a_reading_stands_for(day, month):
    assert queries.served_month(day) == month


# ---------------------------------------------------------------- reaching back before the first reading


def test_a_cash_account_reaches_back_through_its_institutions_transactions(tmp_path):
    path = database(tmp_path, rows=(
        row("a", "fidelity_brokerage", "2026-08-03", 50.0, "investment", None, "DIVIDEND RECEIVED"),  # Fidelity's earliest
        row("b", "fidelity_cash", "2026-09-10", -100.0, "dining", None, "CAFE"),
        row("c", "fidelity_cash", "2026-10-12", 300.0, "income", None, "ACME PAYROLL"),
    ), readings=(("fidelity_cash", "2026-11-02", 1000.0),))
    worth = queries.net_worth(TODAY, path)
    assert worth["months"] == ["2026-08", "2026-09", "2026-10"]
    _, balances = queries.month_end_balances(TODAY, path)
    cash = balances["fidelity_cash"]
    assert {month: found["balance"] for month, found in cash.items()} == {
        "2026-08": 800.0, "2026-09": 700.0, "2026-10": 1000.0}
    assert all(found["exact"] and not found["estimated"] for found in cash.values())
    assert worth["estimated"] == []


def test_nothing_reaches_back_before_the_institutions_earliest_transaction(tmp_path):
    path = database(tmp_path, rows=(
        row("a", "bofa_checking", "2026-06-02", -10.0, "dining", None, "CAFE"),  # another bank's history
        row("b", "fidelity_cash", "2026-09-10", -100.0, "dining", None, "CAFE"),
    ), readings=(("fidelity_cash", "2026-11-02", 1000.0),))
    months, balances = queries.month_end_balances(TODAY, path)
    assert sorted(balances["fidelity_cash"]) == ["2026-09", "2026-10"]  # August's end isn't covered
    assert months == ["2026-09", "2026-10"]  # and BofA, with no reading, has no balance to start earlier


def test_without_transactions_a_reading_reaches_back_only_its_week(tmp_path):
    path = database(tmp_path, readings=(("fidelity_roth", "2026-11-02", 7000.0), ("amex", "2026-11-20", -300.0)))
    months, balances = queries.month_end_balances(date(2026, 11, 25), path)
    assert months == ["2026-10"] and list(balances["fidelity_roth"]) == ["2026-10"] and balances["amex"] == {}


def test_before_its_first_reading_an_investment_balance_is_an_estimate(tmp_path):
    path = database(tmp_path, rows=(
        row("t1", "fidelity_cash", "2026-09-15", -600.0, "transfer", "t2"),
        row("t2", "fidelity_brokerage", "2026-09-15", 600.0, "transfer", "t1"),
        row("p", "fidelity_401k", "2026-10-10", 500.0, "retirement", None, "CONTRIBUTIONS"),
        row("m", "fidelity_401k", "2026-10-10", 50.0, "employer_match", None, "EMPLOYER CONTRIBUTION"),
        row("d", "fidelity_brokerage", "2026-10-20", 20.0, "investment", None, "DIVIDEND RECEIVED"),  # the market's
        row("b", "fidelity_brokerage", "2026-10-21", -300.0, "investment", None, "YOU BOUGHT"),  # not money in
        row("t3", "fidelity_brokerage", "2026-11-01", -200.0, "transfer", "t4"),  # taken out, before the reading
        row("t4", "bofa_checking", "2026-11-01", 200.0, "transfer", "t3"),
    ), readings=(("fidelity_brokerage", "2026-11-02", 5000.0), ("fidelity_401k", "2026-11-02", 9000.0),
                 ("fidelity_cash", "2026-11-02", 1000.0)))
    worth = queries.net_worth(TODAY, path)
    assert worth["months"] == ["2026-09", "2026-10"]
    _, balances = queries.month_end_balances(TODAY, path)
    september = balances["fidelity_brokerage"]["2026-09"]
    # 5,000 less what came in after September 30: nothing in October, 200 taken out in November
    assert september == {"balance": 5200.0, "as_of": date(2026, 11, 2), "exact": False, "estimated": True}
    assert balances["fidelity_401k"]["2026-09"]["balance"] == 8450.0  # less October's 500 and 50
    assert balances["fidelity_brokerage"]["2026-10"]["exact"]  # the reading on the 2nd stands for October
    assert worth["estimated"] == ["2026-09"]
    assert worth["total"]["2026-09"] == 5200.0 + 8450.0 + 1000.0
    brokerage = account_row(worth, "fidelity_brokerage")
    assert brokerage["growth"] is None and worth["market"] is None  # never growth from an estimate
    assert queries.as_of_label("brokerage", september["as_of"], False, "2026-09", True) == "est. from Nov 2"


def test_an_investment_account_with_no_activity_is_estimated_at_its_reading(tmp_path):
    path = database(tmp_path, rows=(row("a", "fidelity_cash", "2026-09-10", -100.0, "dining", None, "CAFE"),),
                    readings=(("fidelity_roth", "2026-11-02", 7000.0),))
    _, balances = queries.month_end_balances(TODAY, path)
    assert balances["fidelity_roth"]["2026-09"] == {"balance": 7000.0, "as_of": date(2026, 11, 2), "exact": False,
                                                    "estimated": True}


def test_a_late_first_reading_is_estimated_back_to_the_month_before(tmp_path):
    path = database(tmp_path, rows=(row("a", "fidelity_brokerage", "2026-10-03", 100.0),),
                    readings=(("fidelity_brokerage", "2026-11-20", 3000.0),))
    worth = queries.net_worth(date(2026, 11, 25), path)
    assert worth["months"] == ["2026-10"] and worth["estimated"] == ["2026-10"]
    brokerage = account_row(worth, "fidelity_brokerage")
    assert (brokerage["balance"], brokerage["estimated"], brokerage["exact"]) == (3000.0, True, False)


def test_the_tab_marks_estimates_in_the_chart_and_the_table(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    path = database(tmp_path, rows=(row("a", "fidelity_brokerage", "2026-08-03", 100.0),),
                    readings=(("fidelity_brokerage", "2026-11-20", 3000.0),))
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "RULES_PATH", tmp_path / "rules.csv")
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(config, "BUDGET_PATH", tmp_path / "budget.toml")
    monkeypatch.setattr(queries, "date", type("Today", (date,), {"today": staticmethod(lambda: date(2026, 11, 25))}))
    page = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "dashboard.py"), default_timeout=60).run()
    assert not page.exception
    worth = page.tabs[1]
    chart = next(element for element in worth.get("plotly_chart"))
    assert '"Estimated"' in chart.proto.spec and "circle-open" in chart.proto.spec
    assert worth.table[0].value.set_index("Account").loc["Individual", "As of"] == "est. from Nov 20"


def test_each_account_has_its_own_line(tmp_path):
    path = database(tmp_path, rows=(row("a", "fidelity_cash", "2026-09-10", -40.0, "dining", None, "CAFE"),
                                    row("b", "fidelity_cash", "2026-10-10", -100.0, "dining", None, "CAFE")),
                    readings=(("fidelity_cash", "2026-11-02", 1000.0), ("amex", "2026-10-31", -300.0)))
    worth = queries.net_worth(TODAY, path)
    assert list(worth["by_account"].columns) == ["fidelity_cash", "amex"]
    assert worth["by_account"]["fidelity_cash"].tolist() == [1100.0, 1000.0]
    assert pd.isna(worth["by_account"].loc["2026-09", "amex"]) and worth["by_account"].loc["2026-10", "amex"] == -300.0


def test_the_lines_toggle_and_the_axis_fits_the_ones_shown(tmp_path, monkeypatch):
    """Net worth is the gold line and each account a cream one, named at its end; a pill per line
    shows or hides it, and the dollar axis fits what is drawn."""
    import json

    from streamlit.testing.v1 import AppTest

    path = database(tmp_path, readings=(("fidelity_401k", "2026-10-31", 90000.0), ("fidelity_cash", "2026-10-31", 2000.0),
                                        ("fidelity_401k", "2026-09-30", 88000.0), ("fidelity_cash", "2026-09-30", 1500.0)))
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "RULES_PATH", tmp_path / "rules.csv")
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(config, "BUDGET_PATH", tmp_path / "budget.toml")
    page = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "dashboard.py"), default_timeout=60).run()
    assert not page.exception

    def drawn() -> dict:
        return json.loads(next(iter(page.tabs[1].get("plotly_chart"))).proto.spec)

    lines = page.tabs[1].pills[0]
    assert lines.value == ["total", "fidelity_cash", "fidelity_401k"]  # every line, to start
    figure = drawn()
    colors = {trace["name"]: trace["line"]["color"] for trace in figure["data"]}
    assert colors == {"Net worth": "#000001", "Cash Management": "#000002", "401(k)": "#000002"}  # gold, cream
    assert {note["text"] for note in figure["layout"]["annotations"]} == {"Net worth", "Cash Management", "401(k)"}
    assert figure["layout"]["yaxis"]["autorange"] is True

    lines.set_value(["fidelity_cash"]).run()  # just the small account: the axis fits it alone
    assert not page.exception
    assert [trace["name"] for trace in drawn()["data"]] == ["Cash Management"]
    lines.set_value([]).run()
    assert not page.tabs[1].get("plotly_chart")
    assert any("Pick a line" in caption.value for caption in page.tabs[1].caption)
