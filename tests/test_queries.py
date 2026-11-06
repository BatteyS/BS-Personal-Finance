"""Tests for queries.py and actions.py on synthetic rows, and a smoke test of the page on the samples.

Nothing here is real data. Caps are set per test so the arithmetic stays round.
"""

import shutil
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import pytest

import actions
import budget
import categorize
import config
import db
import load
import queries



@pytest.fixture(autouse=True)
def round_caps(monkeypatch):
    """24 contributions of $1,000 a year for the 401(k), 12 of $600 for the Roth, nothing planned."""
    monkeypatch.setattr(config, "CAPS", {2026: {"401k": 24_000, "roth": 7_200}, 2027: {"401k": 26_000, "roth": 7_200}})
    monkeypatch.setattr(config, "CONTRIBUTIONS_PER_YEAR", {"401k": 24, "roth": 12, "hsa": 26})
    monkeypatch.setattr(config, "PLANNED_CAPS", set())


def database(tmp_path: Path, *rows: tuple) -> Path:
    """A database holding (id, account, date, amount, description, category, transfer_id) rows."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "finance.db"
    with closing(db.connect(path)) as connection, connection:
        connection.executemany(
            "INSERT INTO transactions (id, account, date, amount, description, category, transfer_id, month, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test')",
            [(*row[:7], row[2][:7]) for row in rows],
        )
    return path


def to_brokerage(amount: float, day: str = "2026-10-20", prefix: str = "b") -> tuple:
    """A BofA-to-brokerage transfer pair (negative amount: the brokerage back to BofA)."""
    return (
        (f"{prefix}1", "bofa_checking", day, -amount, "FID BKG SVC LLC DES:MONEYLINE", "transfer", f"{prefix}2"),
        (f"{prefix}2", "fidelity_brokerage", day, amount, "Electronic Funds Transfer Received", "transfer", f"{prefix}1"),
    )


def contribution(row_id: str, account: str, day: str, amount: float, text: str = "CONTRIBUTION CURRENT YEAR") -> tuple:
    return (row_id, account, day, amount, text, "retirement", None)


ON_PACE = (
    contribution("k1", "fidelity_401k", "2026-10-01", 19_000.0),  # pace 20,000; one contribution of slack
    contribution("r1", "fidelity_roth", "2026-10-01", 6_000.0),  # pace 6,000
)


@pytest.mark.parametrize(
    ("inflow", "roth", "spent", "green", "text"),
    [
        (1850.0, 6000.0, 2000.0, True, "October 2026: +$1,850 to brokerage"),
        (0.0, 6000.0, 2000.0, False, "October 2026: $0 to brokerage"),
        (-500.0, 6000.0, 2000.0, False, "October 2026: -$500 to brokerage"),
        (1850.0, 3000.0, 2000.0, False, "October 2026: +$1,850 to brokerage · Roth IRA behind pace"),
        (1850.0, 6000.0, 3400.0, False, "October 2026: +$1,850 to brokerage, $400 short"),  # short is red
        (0.0, 3000.0, 3400.0, False, "October 2026: $0 to brokerage, $400 short · Roth IRA behind pace"),
        (1850.0, 6000.0, 3000.0, True, "October 2026: +$1,850 to brokerage"),  # even: not short
    ],
)
def test_the_verdict(tmp_path, inflow, roth, spent, green, text):
    rows = [ON_PACE[0], contribution("r1", "fidelity_roth", "2026-10-01", roth),
            ("i1", "bofa_checking", "2026-10-15", 3000.0, "ACME PAYROLL", "income", None),
            ("s1", "amex", "2026-10-03", -spent, "COSTCO", "groceries", None)]
    if inflow:
        rows += to_brokerage(inflow)
    result = queries.verdict("2026-10", database(tmp_path, *rows))
    assert (result["green"], result["text"]) == (green, text)


def test_short_counts_refunds_and_uncategorized_rows_but_not_transfers(tmp_path):
    path = database(
        tmp_path,
        ("i1", "bofa_checking", "2026-10-15", 1000.0, "ACME PAYROLL", "income", None),
        ("s1", "amex", "2026-10-02", -900.0, "TARGET", "shopping", None),
        ("s2", "amex", "2026-10-09", 50.0, "TARGET REFUND", "shopping", None),
        ("u1", "amex", "2026-10-10", -250.0, "NEW MERCHANT", "uncategorized", None),
        ("t1", "bofa_checking", "2026-10-20", -1104.0, "AMERICAN EXPRESS ACH PMT", "transfer", None),
    )
    result = queries.verdict("2026-10", path)
    assert (result["income"], result["spending"], result["short"]) == (1000.0, 1100.0, 100.0)
    assert queries.verdict("2026-09", path)["short"] == 0.0  # a month with no rows


def test_a_two_paycheck_january_is_not_behind_but_a_missed_paycheck_is(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONTRIBUTIONS_PER_YEAR", {"401k": 26, "roth": 12})  # biweekly, $1,000 each
    two = database(tmp_path, contribution("k1", "fidelity_401k", "2027-01-08", 1000.0),
                   contribution("k2", "fidelity_401k", "2027-01-22", 1000.0))
    caps = queries.cap_status("2027-01", two).set_index("cap_key")
    assert caps.loc["401k", "pace"] == 2166.67 and not caps.loc["401k", "behind"]

    one = database(tmp_path / "missed", contribution("k1", "fidelity_401k", "2027-01-08", 1000.0))
    caps = queries.cap_status("2027-01", one).set_index("cap_key")
    assert caps.loc["401k", "behind"] and caps.loc["401k", "short"] == 1166.67


def test_both_401k_plans_count_toward_one_cap(tmp_path):
    path = database(tmp_path, contribution("k1", "fidelity_401k", "2026-06-01", 9_000.0),
                    contribution("k2", "fidelity_401k_old", "2026-02-01", 3_000.0))
    assert queries.contributions("401k", "2026-10", path) == 12_000.0


def test_caps_come_from_the_selected_year(tmp_path):
    path = database(tmp_path, contribution("k1", "fidelity_401k", "2026-12-15", 24_000.0))
    assert queries.cap_status("2026-12", path).set_index("cap_key").loc["401k", "cap"] == 24_000
    assert queries.cap_status("2027-01", path).set_index("cap_key").loc["401k", "contributions"] == 0.0
    with pytest.raises(queries.MissingCapsError, match="2028"):
        queries.cap_status("2028-01", path)


def test_prior_year_contributions_count_toward_the_previous_year(tmp_path):
    path = database(
        tmp_path,
        contribution("r1", "fidelity_roth", "2027-02-10", 600.0, "CONTRIBUTION PRIOR YEAR (Cash)"),
        contribution("r2", "fidelity_roth", "2027-02-11", 600.0, "CONTRIBUTION CURRENT YEAR (Cash)"),
    )
    assert queries.contributions("roth", "2027-02", path) == 600.0


def test_a_planned_cap_follows_that_years_cap_and_is_never_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CAPS", {2026: {"hsa": 4_400}, 2027: {"hsa": 4_500}})
    monkeypatch.setattr(config, "PLANNED_CAPS", {"hsa"})
    path = database(tmp_path)
    row = queries.cap_status("2026-10", path).iloc[0]
    assert (row["contributions"], row["pace"], bool(row["planned"]), bool(row["behind"])) == (3666.67, 3666.67, True, False)
    assert queries.cap_status("2027-06", path).iloc[0]["contributions"] == 2250.0


def test_brokerage_inflow_ignores_moves_between_the_two_brokerages(tmp_path):
    path = database(
        tmp_path,
        *to_brokerage(1000.0, "2026-10-05", "a"),
        ("h1", "fidelity_brokerage", "2026-10-06", -400.0, "TRANSFERRED TO", "transfer", "h2"),
        ("h2", "fidelity_household", "2026-10-06", 400.0, "TRANSFERRED FROM", "transfer", "h1"),
        ("d1", "fidelity_brokerage", "2026-10-30", 56.0, "DIVIDEND RECEIVED", "investment", None),
    )
    assert queries.brokerage_inflow("2026-10", path) == 1000.0
    total, by_month = queries.brokerage_year_to_date("2026-10", path)
    assert total == 1000.0 and len(by_month) == 10


@pytest.mark.parametrize("source", ["fidelity_brokerage", "fidelity_household"])
def test_a_roth_contribution_from_a_brokerage_fills_the_bar_and_leaves_inflow_alone(tmp_path, source):
    path = database(
        tmp_path,
        *to_brokerage(1000.0, "2026-10-05"),
        ("x1", source, "2026-10-10", -600.0, "TRANSFERRED TO VS X1-1 (Cash)", "transfer", "x2"),
        ("x2", "fidelity_roth", "2026-10-10", 600.0, "CONTRIBUTION CURRENT YEAR (Cash)", "retirement", "x1"),
    )
    assert queries.brokerage_inflow("2026-10", path) == 1000.0
    assert queries.contributions("roth", "2026-10", path) == 600.0


def test_a_roth_contribution_from_cash_fills_the_bar(tmp_path):
    path = database(
        tmp_path,
        ("x1", "fidelity_cash", "2026-10-10", -600.0, "TRANSFERRED TO VS X1-1 (Cash)", "transfer", "x2"),
        ("x2", "fidelity_roth", "2026-10-10", 600.0, "CONTRIBUTION CURRENT YEAR (Cash)", "retirement", "x1"),
    )
    assert (queries.contributions("roth", "2026-10", path), queries.brokerage_inflow("2026-10", path)) == (600.0, 0.0)


def test_a_refund_reduces_its_category(tmp_path):
    path = database(
        tmp_path,
        ("s1", "amex", "2026-10-02", -100.0, "TARGET", "shopping", None),
        ("s2", "amex", "2026-10-09", 30.0, "TARGET REFUND", "shopping", None),
        ("g1", "amex", "2026-10-03", -210.0, "COSTCO", "groceries", None),
        ("t1", "amex", "2026-10-04", 1104.0, "AUTOPAY PAYMENT", "transfer", None),  # never spending
    )
    spending = queries.spending_by_category("2026-10", path)
    assert spending[["category", "amount"]].values.tolist() == [["groceries", 210.0], ["shopping", 70.0]]
    assert spending["share"].sum() == pytest.approx(1.0)


def test_budget_vs_actual_by_month_and_year_to_date(tmp_path):
    plan = budget.load(budget.BUDGET_EXAMPLE)  # groceries 350, dining 250, transit 40 a month
    path = database(
        tmp_path,
        ("g1", "amex", "2026-09-05", -300.0, "COSTCO", "groceries", None),  # the year's first month with data
        ("g2", "amex", "2026-10-05", -420.0, "COSTCO", "groceries", None),
        ("d1", "amex", "2026-10-07", -100.0, "STEAKHOUSE", "dining", None),
        ("x1", "amex", "2026-10-09", -55.0, "AUTOZONE", "gas", None),  # spent, never budgeted
        ("t1", "bofa_checking", "2026-10-20", -1104.0, "AMERICAN EXPRESS", "transfer", None),  # not spending
    )
    frame, first = queries.budget_vs_actual(plan, "2026-10", path)
    rows = frame.set_index("category")
    assert first == "2026-09"
    assert rows.loc["groceries", ["budget", "spent", "difference"]].tolist() == [350.0, 420.0, -70.0]
    assert rows.loc["groceries", ["ytd_budget", "ytd_spent", "ytd_difference"]].tolist() == [700.0, 720.0, -20.0]
    assert rows.loc["dining", "difference"] == 150.0
    assert rows.loc["transit", "budget"] == 40.0  # the pass, less the commuter benefit, plus rideshare
    assert rows.loc["gas", ["lines", "budget", "spent"]].tolist() == ["(no budget line)", 0.0, 55.0]
    assert frame.iloc[-1]["category"] == "Total"
    assert rows.loc["Total", "spent"] == 575.0
    assert rows.loc["Total", "budget"] == pytest.approx(budget.budget_by_category(plan)["monthly"].sum())


def test_year_to_date_starts_at_january_once_the_year_has_it(tmp_path):
    path = database(
        tmp_path,
        ("a", "amex", "2026-01-05", -1.0, "X", "dining", None),
        ("b", "amex", "2026-10-05", -1.0, "X", "dining", None),
        ("c", "amex", "2025-12-05", -1.0, "X", "dining", None),
    )
    assert queries.year_start("2026-10", path) == "2026-01"
    assert queries.year_start("2025-12", path) == "2025-12"


def test_line_items_list_a_categorys_biggest_spends_first(tmp_path):
    path = database(
        tmp_path,
        ("g1", "amex", "2026-10-03", -40.0, "TRADER JOE", "groceries", None),
        ("g2", "bofa_checking", "2026-10-12", -210.0, "COSTCO", "groceries", None),
        ("g3", "amex", "2026-10-20", 15.0, "COSTCO REFUND", "groceries", None),
        ("g4", "amex", "2026-09-28", -500.0, "COSTCO", "groceries", None),  # another month
        ("d1", "amex", "2026-10-04", -900.0, "STEAKHOUSE", "dining", None),  # another category
    )
    items = queries.line_items("2026-10", "groceries", db_path=path)
    assert items[["description", "account", "amount"]].values.tolist() == [
        ["COSTCO", "BofA checking", 210.0], ["TRADER JOE", "Amex", 40.0], ["COSTCO REFUND", "Amex", -15.0]]
    assert str(items.loc[0, "date"]) == "2026-10-12"
    assert len(queries.line_items("2026-10", "groceries", limit=1, db_path=path)) == 1


def test_a_category_that_nets_to_a_refund_has_no_share(tmp_path):
    path = database(
        tmp_path,
        ("g1", "amex", "2026-10-03", -300.0, "COSTCO", "groceries", None),
        ("s1", "amex", "2026-10-09", 100.0, "TARGET REFUND", "shopping", None),
    )
    spending = queries.spending_by_category("2026-10", path).set_index("category")
    assert spending.loc["groceries", "share"] == 1.0 and spending["share"].isna().loc["shopping"]
    only_refunds = queries.spending_by_category("2026-10", database(tmp_path / "refunds", *[(
        "s1", "amex", "2026-10-09", 100.0, "TARGET REFUND", "shopping", None)]))
    assert only_refunds["share"].isna().all()


def test_contributions_past_the_cap_show_as_over(tmp_path):
    row = queries.cap_status("2026-10", database(tmp_path, contribution("r1", "fidelity_roth", "2026-03-01", 7_700.0)))
    roth = row.set_index("cap_key").loc["roth"]
    assert (roth["over"], bool(roth["behind"])) == (500.0, False)
    path = database(tmp_path / "over", contribution("r1", "fidelity_roth", "2026-03-01", 7_700.0),
                    contribution("k1", "fidelity_401k", "2026-10-01", 20_000.0))
    assert "Roth IRA over the cap by $500" in queries.verdict("2026-10", path)["text"]


def test_the_default_month_skips_the_current_partial_month(tmp_path):
    path = database(
        tmp_path,
        ("a", "amex", "2026-10-05", -1.0, "X", "dining", None),
        ("b", "amex", "2026-11-02", -1.0, "X", "dining", None),
    )
    assert queries.default_month(date(2026, 11, 6), path) == "2026-10"
    assert queries.default_month(date(2026, 10, 6), path) == "2026-11"  # nothing earlier: the latest
    assert queries.default_month(date(2026, 11, 6), database(tmp_path / "empty")) is None


def test_the_trend_covers_twelve_months(tmp_path):
    path = database(
        tmp_path,
        ("i1", "bofa_checking", "2026-10-15", 3000.0, "ACME PAYROLL", "income", None),
        ("g1", "amex", "2026-10-03", -210.0, "COSTCO", "groceries", None),
        *to_brokerage(1850.0),
    )
    frame = queries.trend("2026-10", db_path=path)
    assert frame["month"].tolist()[0] == "2025-11" and frame["month"].tolist()[-1] == "2026-10"
    assert frame.iloc[-1][["income", "spending", "brokerage"]].tolist() == [3000.0, 210.0, 1850.0]
    assert frame.iloc[0][["income", "spending", "brokerage"]].tolist() == [0.0, 0.0, 0.0]


def test_uncategorized_rows_group_by_merchant(tmp_path):
    path = database(
        tmp_path,
        ("a", "amex", "2026-10-01", -4.0, "7-ELEVEN 00036 CHICAGO IL", "uncategorized", None),
        ("b", "amex", "2026-10-08", -5.0, "7-ELEVEN 00041 CHICAGO IL", "uncategorized", None),
    )
    frame = queries.uncategorized("2026-10", path)
    assert frame[["merchant", "count", "total", "pattern"]].values.tolist() == [["ELEVEN CHICAGO IL", 2, -9.0, "7-ELEVEN"]]
    assert len(frame.loc[0, "descriptions"]) == 2


def test_money_formats():
    assert [queries.money(v) for v in (1850.0, 0.0, -500.4)] == ["$1,850", "$0", "-$500"]
    assert [queries.signed_money(v) for v in (1850.0, 0.0, -500.0)] == ["+$1,850", "$0", "-$500"]
    assert queries.month_label("2026-10") == "October 2026"


def test_uploads_keep_a_timestamped_name(tmp_path):
    when = datetime(2027, 2, 1, 9, 30, 0)
    first = actions.save_upload("../../stmt.csv", b"a", when, tmp_path)
    second = actions.save_upload("stmt.csv", b"b", when, tmp_path)
    assert (first.name, second.name) == ("20270201-093000_stmt.csv", "20270201-093000_2_stmt.csv")
    assert first.parent == tmp_path and first.read_bytes() == b"a"


def test_add_rule_refuses_a_rule_that_matches_none_of_its_rows(tmp_path):
    path = tmp_path / "rules.csv"
    rows = ["7-ELEVEN 00036 CHICAGO IL", "7-ELEVEN 00041 CHICAGO IL"]
    with pytest.raises(categorize.RulesError, match="doesn't match"):
        actions.add_rule("ELEVEN CHICAGO IL", "dining", rules_path=path, examples=rows)
    actions.add_rule("7-ELEVEN", "dining", rules_path=path, examples=rows)
    assert categorize.read_rules(path)[-1]["pattern"] == "7-ELEVEN"


def test_an_import_with_broken_rules_keeps_the_rows_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "finance.db")
    monkeypatch.setattr(config, "RULES_PATH", tmp_path / "rules.csv")
    (tmp_path / "rules.csv").write_text("pattern,category,match\nCOSTCO,snacks,contains\n")
    export = tmp_path / "activity.csv"
    export.write_text("Date,Description,Amount\n10/03/2026,COSTCO WHSE #0123,210.00\n")
    results = actions.import_files([export])
    assert results[0]["rows_new"] == 1
    assert results[-1]["file"] == "rules.csv" and "files were loaded" in results[-1]["error"]
    assert queries.uncategorized("2026-10")["count"].tolist() == [1]
    assert queries.verdict("2026-10")["spending"] == 210.0


def test_opening_the_database_to_read_writes_nothing(tmp_path, monkeypatch):
    path = database(tmp_path)
    with closing(db.connect(path)) as connection:
        assert connection.total_changes == 0
    accounts = {**config.ACCOUNTS, "new_card": {"institution": "amex", "kind": "credit", "cap_key": None}}
    monkeypatch.setattr(config, "ACCOUNTS", accounts)
    with closing(db.connect(path)) as connection:
        assert connection.total_changes > 0
        assert ("new_card",) in connection.execute("SELECT name FROM accounts").fetchall()


def test_add_rule_appends_and_a_bad_rule_changes_nothing(tmp_path):
    path = tmp_path / "rules.csv"
    actions.add_rule("METRA, MOBILE", "transit", rules_path=path)
    assert categorize.read_rules(path)[-1]["pattern"] == "METRA, MOBILE"
    before = path.read_text()
    with pytest.raises(categorize.RulesError, match="rules.csv line"):
        actions.add_rule("COSTCO", "snacks", rules_path=path)
    assert path.read_text() == before and not (tmp_path / "rules.csv.new").exists()


def test_the_page_renders_its_four_tabs(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    path = database(
        tmp_path, *ON_PACE, *to_brokerage(1850.0),
        ("i1", "bofa_checking", "2026-10-15", 3000.0, "ACME PAYROLL", "income", None),
        ("g1", "amex", "2026-10-03", -210.0, "COSTCO WHSE #0123", "groceries", None),
        ("u1", "amex", "2026-10-04", -12.0, "CORNER CAFE 00042", "uncategorized", None),
    )
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "RULES_PATH", tmp_path / "rules.csv")
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(config, "BUDGET_PATH", tmp_path / "budget.toml")
    dashboard = str(Path(__file__).resolve().parent.parent / "dashboard.py")

    page = AppTest.from_file(dashboard, default_timeout=60).run()
    assert not page.exception
    assert [tab.label for tab in page.tabs] == ["This month", "Budget vs actual", "Budget", "Assumptions & taxes"]
    assert page.tabs[0].success[0].value == "October 2026: +$1,850 to brokerage"
    assert [heading.value for heading in page.tabs[0].subheader] == [
        "Where the month went", "Brokerage", "Twelve months", "Uncategorized"]
    # No budget.toml yet: the comparison waits for one, and the budget shows the example.
    assert "Fill in your budget first" in page.tabs[1].info[0].value
    assert "made-up example" in page.tabs[2].warning[0].value
    assert "Tax calculation (annual)" in [heading.value for heading in page.tabs[3].subheader]

    shutil.copyfile(budget.BUDGET_EXAMPLE, tmp_path / "budget.toml")
    page = AppTest.from_file(dashboard, default_timeout=60).run()
    assert not page.exception
    comparison = page.tabs[1].table[0].value.set_index("Category")
    assert comparison.loc["Groceries", "Spent in Oct"] == "$210"
    assert comparison.loc["Groceries", "Oct, under or over"] == "$140 under"  # 350 budgeted
