"""Tests for taught CSV layouts (formats.py) and OFX/QFX files (ofx.py), loaded through load.py.
Every file here is made up: Chase-style column names with invented rows and amounts."""

from contextlib import closing
from pathlib import Path

import pytest

import config
import db
import formats
import load
import ofx

CARD_CSV = """Transaction Date,Post Date,Description,Category,Type,Amount,Memo
10/14/2026,10/15/2026,CORNER CAFE,Food & Drink,Sale,-12.50,
10/20/2026,10/20/2026,Payment Thank You-Mobile,,Payment,250.00,
10/21/2026,10/22/2026,CORNER CAFE,Food & Drink,Return,4.00,
"""
CARD_LAYOUT = """
[[csv]]
name = "Chase card"
headers = ["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"]
account = "chase_card"
date = "Transaction Date"
amount = "Amount"
spending_is = "negative"
description = ["Description"]
"""
SPLIT_CSV = """Date,Description,Debit,Credit,Balance
2026-10-02,GROCERY TOWN,45.10,,1000.00
2026-10-03,ACME PAYROLL,,1500.00,2454.90
2026-10-04,PENDING HOLD,,,2454.90
"""
SPLIT_LAYOUT = """
[[csv]]
name = "Credit union checking"
headers = ["date", "description", "debit", "credit", "balance"]
account = "chase_checking"
date = "date"
debit = "debit"
credit = "credit"
description = ["description"]
"""
SGML_OFX = """OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>USD
<BANKACCTFROM><BANKID>000000000<ACCTID>0000004321<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261005120000[-5:EST]<TRNAMT>-42.10<FITID>1<NAME>GROCERY TOWN<MEMO>STORE 12</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261015<TRNAMT>1500.00<FITID>2<NAME>ACME PAYROLL</STMTTRN>
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""
XML_OFX = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX><CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS>
<CCACCTFROM><ACCTID>XXXXXXXXXXXX9876</ACCTID></CCACCTFROM>
<BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20261011</DTPOSTED><TRNAMT>-30.00</TRNAMT><NAME>BOOK NOOK</NAME></STMTTRN>
</BANKTRANLIST>
</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>
"""


@pytest.fixture(autouse=True)
def chase_accounts(monkeypatch):
    """Two made-up accounts at an institution with no built-in parser."""
    accounts = {**config.ACCOUNTS,
                "chase_card": {"institution": "chase", "kind": "credit", "cap_key": None, "name": "Chase card"},
                "chase_checking": {"institution": "chase", "kind": "cash", "cap_key": None, "name": "Chase checking"}}
    monkeypatch.setattr(config, "ACCOUNTS", accounts)


def teach(text: str) -> None:
    config.FORMATS_PATH.write_text(text)


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def stored(db_path: Path) -> list[tuple]:
    with closing(db.connect(db_path)) as connection:
        return connection.execute("SELECT account, date, amount, description FROM transactions ORDER BY date, amount").fetchall()


def test_a_taught_card_layout_loads_with_spending_negative(tmp_path):
    teach(CARD_LAYOUT)
    [result] = load.load_files([write(tmp_path, "Chase1234_Activity.csv", CARD_CSV)], tmp_path / "finance.db")
    assert (result["institution"], result["rows_new"]) == ("Chase card", 3)
    assert stored(tmp_path / "finance.db") == [
        ("chase_card", "2026-10-14", -12.5, "CORNER CAFE"),
        ("chase_card", "2026-10-20", 250.0, "Payment Thank You-Mobile"),
        ("chase_card", "2026-10-21", 4.0, "CORNER CAFE"),
    ]


def test_a_layout_with_spending_positive_is_flipped(tmp_path):
    teach(CARD_LAYOUT.replace('spending_is = "negative"', 'spending_is = "positive"'))
    load.load_files([write(tmp_path, "card.csv", CARD_CSV)], tmp_path / "finance.db")
    assert [row[2] for row in stored(tmp_path / "finance.db")] == [12.5, -250.0, -4.0]  # by date


def test_debit_and_credit_columns_become_one_signed_amount(tmp_path):
    teach(SPLIT_LAYOUT)
    [result] = load.load_files([write(tmp_path, "export.csv", SPLIT_CSV)], tmp_path / "finance.db")
    assert result["rows_read"] == 2  # the hold has neither a debit nor a credit
    assert [(row[1], row[2]) for row in stored(tmp_path / "finance.db")] == [("2026-10-02", -45.1), ("2026-10-03", 1500.0)]


def test_an_untaught_csv_says_so_for_the_page_to_offer_teaching(tmp_path):
    path = write(tmp_path, "card.csv", CARD_CSV)
    [result] = load.load_files([path], tmp_path / "finance.db")
    assert result["unknown"] == "csv" and result["path"] == str(path)


def test_the_header_match_ignores_case_and_order():
    [layout] = formats.read_formats()["csv"] if config.FORMATS_PATH.exists() else [None]
    assert layout is None
    teach(CARD_LAYOUT)
    layouts = formats.read_formats()["csv"]
    assert formats.match_layout(["memo", "AMOUNT", "type", "category", "description", "post date", "transaction date"], layouts)
    assert formats.match_layout(["transaction date", "description", "amount"], layouts) is None


def test_an_sgml_ofx_statement_loads_on_the_account_its_number_ends_like(tmp_path):
    teach('[[ofx]]\naccount_ends = "4321"\naccount = "chase_checking"\n')
    [result] = load.load_files([write(tmp_path, "statement.qfx", SGML_OFX)], tmp_path / "finance.db")
    assert (result["institution"], result["rows_new"]) == ("ofx", 2)
    assert stored(tmp_path / "finance.db") == [
        ("chase_checking", "2026-10-05", -42.1, "GROCERY TOWN STORE 12"),
        ("chase_checking", "2026-10-15", 1500.0, "ACME PAYROLL"),
    ]


def test_an_xml_ofx_card_statement_loads(tmp_path):
    teach('[[ofx]]\naccount_ends = "9876"\naccount = "chase_card"\n')
    load.load_files([write(tmp_path, "card.ofx", XML_OFX)], tmp_path / "finance.db")
    assert stored(tmp_path / "finance.db") == [("chase_card", "2026-10-11", -30.0, "BOOK NOOK")]


def test_an_ofx_account_not_yet_matched_names_its_last_four_digits(tmp_path):
    [result] = load.load_files([write(tmp_path, "statement.qfx", SGML_OFX)], tmp_path / "finance.db")
    assert (result["unknown"], result["account_ends"]) == ("ofx", "4321")
    assert "0000004321" not in result["error"]  # never the whole account number


def test_loading_the_same_ofx_or_csv_twice_adds_nothing(tmp_path):
    teach(CARD_LAYOUT + '\n[[ofx]]\naccount_ends = "4321"\naccount = "chase_checking"\n')
    files = [write(tmp_path, "card.csv", CARD_CSV), write(tmp_path, "statement.qfx", SGML_OFX)]
    load.load_files(files, tmp_path / "finance.db")
    assert [result["rows_new"] for result in load.load_files(files, tmp_path / "finance.db")] == [0, 0]


def test_ofx_is_told_from_csv():
    assert ofx.is_ofx(SGML_OFX) and ofx.is_ofx(XML_OFX)
    assert not ofx.is_ofx(CARD_CSV)


def test_ofx_dates_and_amounts():
    assert ofx.ofx_date("20261015120000.000[-5:EST]") == "2026-10-15"
    assert ofx.ofx_date("garbage") is None
    assert (ofx.ofx_amount("-42.10"), ofx.ofx_amount("-42,10"), ofx.ofx_amount("1,500.00")) == (-42.1, -42.1, 1500.0)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (CARD_LAYOUT.replace('account = "chase_card"', 'account = "nowhere"'), "account must be a key"),
        (CARD_LAYOUT.replace('date = "Transaction Date"', 'date = "When"'), "When isn't one of its headers"),
        (CARD_LAYOUT.replace('spending_is = "negative"', 'spending_is = "sideways"'), "spending_is must be"),
        (CARD_LAYOUT.replace('amount = "Amount"', ''), "name an amount column"),
        (CARD_LAYOUT.replace('amount = "Amount"', 'amount = "Amount"\ndebit = "Type"\ncredit = "Memo"'), "not both"),
        (CARD_LAYOUT.replace('description = ["Description"]', ''), "at least one description"),
        ('[[ofx]]\naccount_ends = "12"\naccount = "chase_card"\n', "last four digits"),
        ("[[csv\n", "isn't valid TOML"),
    ],
)
def test_problems_in_formats_toml_are_named(text, message):
    teach(text)
    with pytest.raises(formats.FormatsError, match=message):
        formats.read_formats()


def test_a_broken_formats_toml_stops_every_file_with_its_message(tmp_path):
    teach("[[csv\n")
    results = load.load_files([write(tmp_path, "a.csv", CARD_CSV), write(tmp_path, "b.csv", CARD_CSV)], tmp_path / "f.db")
    assert all(result["error"].startswith("formats.toml:") for result in results)


def test_the_example_formats_file_reads(monkeypatch):
    monkeypatch.setattr(config, "ACCOUNTS", {**config.ACCOUNTS,
                        "example_card": {"institution": "x", "kind": "credit", "cap_key": None, "name": "card"},
                        "example_checking": {"institution": "x", "kind": "cash", "cap_key": None, "name": "checking"}})
    known = formats.read_formats(Path(__file__).resolve().parent.parent / "formats.example.toml")
    assert [layout["name"] for layout in known["csv"]] == ["Example card", "Example checking"]
    assert known["ofx"] == [{"account_ends": "1234", "account": "example_checking"}]
