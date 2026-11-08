"""Tests for holdings: what each investment account held, from a Fidelity positions download or an
OFX/QFX investment statement, stored beside the reading they add up to (queries.holdings), and
shown on the Net worth tab. Everything here is made up, on the accounts in tests/accounts.toml plus
one brokerage at an institution with no built-in parser."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

import actions
import config
import load
import queries

TODAY = date(2026, 11, 7)
POSITIONS_HEADER = ("Account number,Account name,Symbol,Description,Quantity,Last price,Last price change,Current value,"
                    "Today's gain/loss dollar,Today's gain/loss percent,Total gain/loss dollar,Total gain/loss percent,"
                    "Percent of account,Cost basis total,Average cost basis,Type")
HOLDINGS = [  # every row ends with a trailing comma, as Fidelity's do
    'Z00000001,Cash Management,SPAXX**,HELD IN MONEY MARKET,,,,"$1,250.00",,,,,100.00%,,,Cash,',
    'Z00000002,Individual,SPAXX**,HELD IN MONEY MARKET,,,,$100.00,,,,,3.70%,,,Cash,',
    'Z00000002,Individual,FXAIX,FIDELITY 500 INDEX FUND,10.000,$250.00,$1.00,"$2,500.00",$10.00,0.40%,$500.00,25.00%,'
    '92.59%,"$2,000.00",$200.00,Cash,',
    'Z00000002,Individual,WIDGT,WIDGET CO,3.5,$40.00,$0.10,$140.00,$0.35,0.25%,--,--,5.19%,--,--,Margin,',
    'Z00000002,Individual,Pending Activity,,,,,-$40.00,,,,,,,,,',
    '00004,ACME CORP,BONDX,EXAMPLE BOND FUND,100.00,$80.00,$0.50,"$8,000.00",$50.00,0.63%,$400.00,5.26%,100.00%,'
    '"$7,600.00",$76.00,,',
    'Z00000009,Someone Else,FXAIX,FIDELITY 500 INDEX FUND,1.000,$250.00,$1.00,$250.00,$1.00,0.40%,$50.00,25.00%,'
    '100.00%,$200.00,$200.00,Cash,',
]
FOOTER = ["", '"The data and information in this spreadsheet is provided to you solely for your use."', "",
          "Date downloaded Nov-02-2026 at 1:42 a.m ET", ""]

INVESTMENT_OFX = """OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX>
<INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS>
<DTASOF>20261102160000.000[-5:EST]
<CURDEF>USD
<INVACCTFROM><BROKERID>example.com<ACCTID>00005678</INVACCTFROM>
<INVTRANLIST><DTSTART>20261001<DTEND>20261102
<INVBANKTRAN><STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261015<TRNAMT>500.00<FITID>1<NAME>TRANSFER IN</STMTTRN><SUBACCTFUND>CASH</INVBANKTRAN>
</INVTRANLIST>
<INVPOSLIST>
<POSMF><INVPOS><SECID><UNIQUEID>000000001<UNIQUEIDTYPE>CUSIP</SECID><HELDINACCT>CASH<POSTYPE>LONG<UNITS>12.345<UNITPRICE>101.25<MKTVAL>1249.93<DTPRICEASOF>20261102</INVPOS><REINVDIV>Y<REINVCG>Y</POSMF>
<POSSTOCK><INVPOS><SECID><UNIQUEID>000000002<UNIQUEIDTYPE>CUSIP</SECID><HELDINACCT>CASH<POSTYPE>LONG<UNITS>20<UNITPRICE>40.00<MKTVAL>800.00<DTPRICEASOF>20261102</INVPOS></POSSTOCK>
</INVPOSLIST>
<INVBAL><AVAILCASH>150.07<MARGINBALANCE>0<SHORTBALANCE>0</INVBAL>
</INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1>
<SECLISTMSGSRSV1><SECLIST>
<MFINFO><SECINFO><SECID><UNIQUEID>000000001<UNIQUEIDTYPE>CUSIP</SECID><SECNAME>EXAMPLE TOTAL MARKET FUND<TICKER>EXTMF<UNITPRICE>101.25</SECINFO><MFTYPE>OPENEND</MFINFO>
<STOCKINFO><SECINFO><SECID><UNIQUEID>000000002<UNIQUEIDTYPE>CUSIP</SECID><SECNAME>WIDGET CO<TICKER>WIDGT</SECINFO></STOCKINFO>
</SECLIST></SECLISTMSGSRSV1>
</OFX>
"""
XML_INVESTMENT_OFX = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX><INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS>
<DTASOF>20261030</DTASOF>
<INVACCTFROM><BROKERID>example.com</BROKERID><ACCTID>XXXX5678</ACCTID></INVACCTFROM>
<INVPOSLIST>
<POSSTOCK><INVPOS><SECID><UNIQUEID>000000003</UNIQUEID><UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE></SECID>
<HELDINACCT>CASH</HELDINACCT><POSTYPE>LONG</POSTYPE><UNITS>4</UNITS><UNITPRICE>25.125</UNITPRICE><MKTVAL>100.50</MKTVAL>
</INVPOS></POSSTOCK>
</INVPOSLIST>
</INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1></OFX>
"""
OFX_MATCH = '[[ofx]]\naccount_ends = "5678"\naccount = "other_brokerage"\n'


@pytest.fixture(autouse=True)
def other_brokerage(monkeypatch):
    """A made-up brokerage account at an institution that exports OFX."""
    monkeypatch.setattr(config, "ACCOUNTS", {**config.ACCOUNTS, "other_brokerage": {
        "institution": "example", "kind": "brokerage", "cap_key": None, "name": "Other brokerage"}})


def positions(rows: list[str] = HOLDINGS) -> str:
    return "\r\n".join([POSITIONS_HEADER, *rows, *FOOTER])


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, newline="")
    return path


def stored_holdings(db_path: Path, account: str) -> list[tuple]:
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(
            "SELECT as_of, symbol, description, quantity, price, value, cost_basis, source FROM holdings"
            " WHERE account = ? ORDER BY as_of, line", (account,)).fetchall()


def unreconciled(db_path: Path) -> list[tuple]:
    """Every account and day whose holdings don't add up to its reading."""
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(
            "SELECT h.account, h.as_of, ROUND(SUM(h.value), 2), b.balance FROM holdings AS h"
            " LEFT JOIN balances AS b ON b.account = h.account AND b.as_of = h.as_of"
            " GROUP BY h.account, h.as_of HAVING b.balance IS NULL OR ROUND(SUM(h.value), 2) != ROUND(b.balance, 2)"
        ).fetchall()


def balance(db_path: Path, account: str) -> list[tuple]:
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute("SELECT as_of, balance, source FROM balances WHERE account = ?", (account,)).fetchall()


# ---------------------------------------------------------------- a Fidelity positions download


def test_a_positions_download_keeps_each_holding_beside_the_reading_they_add_up_to(tmp_path):
    db_path = tmp_path / "finance.db"
    [result] = load.load_files([write(tmp_path, "Portfolio_Positions_Nov-02-2026.csv", positions())], db_path)
    assert (result["rows_read"], result["accounts"], result["skipped"]) == (7, 3, ["Someone Else"])
    assert stored_holdings(db_path, "fidelity_brokerage") == [  # in the file's order
        ("2026-11-02", "SPAXX", "HELD IN MONEY MARKET", None, None, 100.0, None, "fidelity_positions"),
        ("2026-11-02", "FXAIX", "FIDELITY 500 INDEX FUND", 10.0, 250.0, 2500.0, 2000.0, "fidelity_positions"),
        ("2026-11-02", "WIDGT", "WIDGET CO", 3.5, 40.0, 140.0, None, "fidelity_positions"),  # "--": not known
        ("2026-11-02", "Pending Activity", "", None, None, -40.0, None, "fidelity_positions"),
    ]
    assert balance(db_path, "fidelity_brokerage") == [("2026-11-02", 2700.0, "fidelity_positions")]
    assert [row[1] for row in stored_holdings(db_path, "fidelity_cash")] == ["SPAXX"]  # kept, if never shown
    assert unreconciled(db_path) == []
    with closing(sqlite3.connect(db_path)) as connection:  # nothing kept from an account that isn't yours
        assert connection.execute("SELECT count(*) FROM holdings").fetchone() == (6,)


def test_a_second_download_for_the_same_day_replaces_that_days_holdings(tmp_path):
    db_path = tmp_path / "finance.db"
    load.load_files([write(tmp_path, "Portfolio_Positions_Nov-01-2026.csv", positions())], db_path)
    load.load_files([write(tmp_path, "Portfolio_Positions_Nov-02-2026.csv", positions())], db_path)
    sold = [row for row in HOLDINGS if ",WIDGT," not in row]
    sold[1] = sold[1].replace("$100.00", "$240.00")  # the sale's proceeds, in the core position
    [again] = load.load_files([write(tmp_path, "Portfolio_Positions_Nov-02-2026 (1).csv", positions(sold))], db_path)
    assert (again["rows_new"], again["rows_duplicate"]) == (0, 3)  # the same total, so the same reading
    held = stored_holdings(db_path, "fidelity_brokerage")
    assert [(day, symbol) for day, symbol, *_ in held] == [
        ("2026-11-01", "SPAXX"), ("2026-11-01", "FXAIX"), ("2026-11-01", "WIDGT"), ("2026-11-01", "Pending Activity"),
        ("2026-11-02", "SPAXX"), ("2026-11-02", "FXAIX"), ("2026-11-02", "Pending Activity")]
    assert unreconciled(db_path) == []


def test_a_typed_balance_replaces_that_days_holdings_too(tmp_path, monkeypatch):
    db_path = tmp_path / "finance.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    load.load_files([write(tmp_path, "Portfolio_Positions_Nov-02-2026.csv", positions())], db_path)
    actions.record_balance("fidelity_brokerage", date(2026, 11, 2), 2800.0)
    assert stored_holdings(db_path, "fidelity_brokerage") == []
    assert len(stored_holdings(db_path, "fidelity_401k")) == 1  # another account's are untouched
    assert unreconciled(db_path) == []


@pytest.mark.parametrize(("text", "number"), [
    ("10.000", 10.0), ("1,234.5678", 1234.5678), ("$40.00", 40.0), ("(3.00)", -3.0), ("-$2.5", -2.5),
    ("", None), ("--", None), ("n/a", None),
])
def test_shares_and_prices_keep_their_precision_and_unknowns_are_none(text, number):
    assert load.parse_number(text) == number


# ---------------------------------------------------------------- an OFX investment statement


def test_an_ofx_investment_statement_brings_its_positions_its_cash_and_a_reading(tmp_path):
    config.FORMATS_PATH.write_text(OFX_MATCH)
    db_path = tmp_path / "finance.db"
    [result] = load.load_files([write(tmp_path, "brokerage.qfx", INVESTMENT_OFX)], db_path)
    assert (result["institution"], result["rows_read"], result["as_of"], result["accounts"]) == ("ofx", 1, "2026-11-02", 1)
    assert stored_holdings(db_path, "other_brokerage") == [
        ("2026-11-02", "EXTMF", "EXAMPLE TOTAL MARKET FUND", 12.345, 101.25, 1249.93, None, "ofx"),  # named in SECLIST
        ("2026-11-02", "WIDGT", "WIDGET CO", 20.0, 40.0, 800.0, None, "ofx"),
        ("2026-11-02", "", "Cash", None, None, 150.07, None, "ofx"),  # INVBAL's AVAILCASH
    ]
    assert balance(db_path, "other_brokerage") == [("2026-11-02", 2200.0, "ofx")]
    with closing(sqlite3.connect(db_path)) as connection:  # its cash activity comes in as before
        assert connection.execute("SELECT date, amount, description FROM transactions").fetchall() == [
            ("2026-10-15", 500.0, "TRANSFER IN")]


def test_an_xml_position_not_named_in_the_file_goes_by_its_security_id(tmp_path):
    config.FORMATS_PATH.write_text(OFX_MATCH)
    db_path = tmp_path / "finance.db"
    load.load_files([write(tmp_path, "brokerage.ofx", XML_INVESTMENT_OFX)], db_path)
    assert stored_holdings(db_path, "other_brokerage") == [
        ("2026-10-30", "000000003", "", 4.0, 25.125, 100.5, None, "ofx")]  # no AVAILCASH: no cash line
    assert balance(db_path, "other_brokerage") == [("2026-10-30", 100.5, "ofx")]


def test_an_ofx_statement_without_a_position_list_records_no_reading(tmp_path):
    config.FORMATS_PATH.write_text(OFX_MATCH)
    db_path = tmp_path / "finance.db"
    start, end = INVESTMENT_OFX.index("<INVPOSLIST>"), INVESTMENT_OFX.index("</INVBAL>") + len("</INVBAL>")
    [result] = load.load_files([write(tmp_path, "activity.qfx", INVESTMENT_OFX[:start] + INVESTMENT_OFX[end:])], db_path)
    assert result["rows_new"] == 1 and "accounts" not in result  # the activity, and nothing about holdings
    assert stored_holdings(db_path, "other_brokerage") == [] and balance(db_path, "other_brokerage") == []


def test_an_account_holding_nothing_reads_zero(tmp_path):
    config.FORMATS_PATH.write_text(OFX_MATCH)
    db_path = tmp_path / "finance.db"
    start, end = INVESTMENT_OFX.index("<POSMF>"), INVESTMENT_OFX.index("</INVBAL>")
    empty = INVESTMENT_OFX[:start] + "</INVPOSLIST><INVBAL><AVAILCASH>0.00" + INVESTMENT_OFX[end:]
    load.load_files([write(tmp_path, "closed.qfx", empty)], db_path)
    assert stored_holdings(db_path, "other_brokerage") == [("2026-11-02", "", "Cash", None, None, 0.0, None, "ofx")]
    assert balance(db_path, "other_brokerage") == [("2026-11-02", 0.0, "ofx")]


# ---------------------------------------------------------------- the holdings behind the tab's balances


def loaded(tmp_path: Path, name: str = "Portfolio_Positions_Nov-02-2026.csv", rows: list[str] = HOLDINGS) -> Path:
    db_path = tmp_path / "finance.db"
    load.load_files([write(tmp_path, name, positions(rows))], db_path)
    return db_path


def test_each_investment_accounts_holdings_add_up_to_the_balance_the_tab_shows(tmp_path):
    db_path = loaded(tmp_path)
    worth = queries.net_worth(TODAY, db_path)
    held = queries.holdings(worth["accounts"], db_path)
    assert list(dict.fromkeys(held["key"])) == ["fidelity_brokerage", "fidelity_401k"]  # no cash account
    balances = worth["accounts"].set_index("key")["balance"]
    assert held.groupby("key")["value"].sum().round(2).to_dict() == {key: balances[key] for key in set(held["key"])}
    brokerage = held[held["key"] == "fidelity_brokerage"]
    assert brokerage["symbol"].tolist() == ["FXAIX", "WIDGT", "SPAXX", "Pending Activity"]  # largest first
    assert brokerage["gain"].tolist() == [500.0, None, None, None]  # only where the cost basis is known
    assert set(brokerage["as_of"]) == {date(2026, 11, 2)}


def test_a_carried_reading_brings_its_holdings_along(tmp_path):
    db_path = loaded(tmp_path, "Portfolio_Positions_Oct-15-2026.csv")
    worth = queries.net_worth(TODAY, db_path)
    brokerage = worth["accounts"].set_index("key").loc["fidelity_brokerage"]
    assert (brokerage["exact"], brokerage["balance"]) == (False, 2700.0)  # carried from Oct 15
    held = queries.holdings(worth["accounts"], db_path)
    assert held.loc[held["key"] == "fidelity_brokerage", "value"].sum() == 2700.0


def test_an_estimated_balance_has_no_holdings(tmp_path):
    db_path = loaded(tmp_path, "Portfolio_Positions_Nov-20-2026.csv")
    with closing(sqlite3.connect(db_path)) as connection, connection:  # Fidelity history reaching back to October
        connection.execute("INSERT INTO transactions (id, account, date, amount, description, category, month, imported_at)"
                           " VALUES ('a', 'fidelity_brokerage', '2026-10-03', 100.0, 'DIVIDEND', 'investment', '2026-10', 'test')")
    worth = queries.net_worth(date(2026, 11, 25), db_path)
    accounts = worth["accounts"].set_index("key")
    assert accounts.loc[["fidelity_brokerage", "fidelity_401k"], "estimated"].all()  # a month before the reading
    assert queries.holdings(worth["accounts"], db_path).empty


def test_no_balances_means_no_holdings(tmp_path):
    db_path = tmp_path / "finance.db"
    assert queries.holdings(queries.net_worth(TODAY, db_path)["accounts"], db_path).empty


def test_the_tab_lists_each_accounts_holdings_under_its_balance(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    db_path = loaded(tmp_path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "RULES_PATH", tmp_path / "rules.csv")
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(config, "BUDGET_PATH", tmp_path / "budget.toml")
    monkeypatch.setattr(queries, "date", type("Today", (date,), {"today": staticmethod(lambda: TODAY)}))
    page = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "dashboard.py"), default_timeout=60).run()
    assert not page.exception
    worth = page.tabs[1]
    accounts = worth.table[0].value.set_index("Account")
    assert [expander.label for expander in worth.expander] == ["Individual · $2,700 · Nov 2", "401(k) · $8,000 · Nov 2"]
    assert accounts.loc["Individual", "Balance"] == "$2,700" and accounts.loc["401(k)", "Balance"] == "$8,000"
    table = worth.expander[0].table[0].value.set_index("Symbol")
    assert table.loc["FXAIX"].to_dict() == {"Description": "FIDELITY 500 INDEX FUND", "Shares": "10", "Price": "$250.00",
                                            "Value": "$2,500", "Cost basis": "$2,000", "Gain": "+$500"}
    assert table.loc["WIDGT", "Shares"] == "3.5" and table.loc["WIDGT", "Gain"] == ""
    assert table.loc["Total"].to_dict() == {"Description": "", "Shares": "", "Price": "", "Value": "$2,700",
                                            "Cost basis": "$2,000", "Gain": "+$500"}
