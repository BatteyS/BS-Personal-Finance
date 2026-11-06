"""Tests for teaching the tracker a new file: reading it, guessing its columns, previewing it,
adding an account, and saving a layout or an OFX match, then the page's form. All made up."""

import shutil
from contextlib import closing
from pathlib import Path

import pytest

import actions
import config
import db
import formats

CARD = """Transaction Date,Post Date,Description,Category,Type,Amount,Memo
10/14/2026,10/15/2026,CORNER CAFE,Food & Drink,Sale,-12.50,
10/20/2026,10/20/2026,Payment Thank You-Mobile,,Payment,250.00,
10/21/2026,10/22/2026,BOOK NOOK,Shopping,Sale,-30.00,
"""
UNION = """Credit Union Export
Generated 11/01/2026

Date,Description,Withdrawal,Deposit,Balance
2026-10-02,GROCERY TOWN,45.10,,1000.00
2026-10-03,ACME PAYROLL,,1500.00,2454.90
"""
OFX = """OFXHEADER:100
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>0000004321</BANKACCTFROM>
<BANKTRANLIST><STMTTRN><DTPOSTED>20261005<TRNAMT>-42.10<NAME>GROCERY TOWN</STMTTRN></BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""
ONE_COLUMN = {"name": "Chase card", "date": "transaction date", "mode": "amount", "amount": "amount",
              "spending_is": "negative", "description": ["description"]}


@pytest.fixture(autouse=True)
def data_folder(tmp_path, monkeypatch):
    """A fresh data folder: the test accounts copied in as accounts.toml, an empty database."""
    shutil.copyfile(Path(__file__).resolve().parent / "accounts.toml", tmp_path / "accounts.toml")
    for name, value in {"ACCOUNTS_PATH": tmp_path / "accounts.toml", "ACCOUNTS_SOURCE": tmp_path / "accounts.toml",
                        "DB_PATH": tmp_path / "finance.db", "RULES_PATH": tmp_path / "rules.csv",
                        "EXPORTS_DIR": tmp_path / "exports"}.items():
        monkeypatch.setattr(config, name, value)


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def stored() -> list[tuple]:
    with closing(db.connect()) as connection:
        return connection.execute("SELECT account, date, amount, description FROM transactions ORDER BY date").fetchall()


def test_an_unknown_csv_is_read_with_first_guesses(tmp_path):
    info = actions.read_unknown(write(tmp_path, "card.csv", CARD))
    assert info["header"][0] == "Transaction Date" and len(info["rows"]) == 3
    assert info["suggest"] == {"date": "transaction date", "mode": "amount", "amount": "amount", "debit": None,
                               "credit": None, "spending_is": "negative", "description": ["description"]}


def test_lines_above_the_header_are_passed_over_and_split_columns_guessed(tmp_path):
    info = actions.read_unknown(write(tmp_path, "union.csv", UNION))
    assert info["header"] == ["Date", "Description", "Withdrawal", "Deposit", "Balance"]
    guess = info["suggest"]
    assert (guess["mode"], guess["debit"], guess["credit"]) == ("debit and credit", "withdrawal", "deposit")


def test_spending_shown_positive_is_guessed():
    flipped = CARD.replace("-12.50", "12.50").replace("250.00", "-250.00").replace("-30.00", "30.00")
    rows = [line.split(",") for line in flipped.splitlines()]
    assert formats.suggest(rows[0], rows[1:], actions.is_date, actions.cell_amount)["spending_is"] == "positive"


def test_the_preview_shows_what_would_be_stored(tmp_path):
    path = write(tmp_path, "card.csv", CARD)
    header = actions.read_unknown(path)["header"]
    preview = actions.preview_layout(path, ONE_COLUMN, header)
    assert [(row["date"], row["amount"]) for row in preview] == [("2026-10-14", -12.5), ("2026-10-20", 250.0),
                                                                  ("2026-10-21", -30.0)]
    flipped = actions.preview_layout(path, {**ONE_COLUMN, "spending_is": "positive"}, header)
    assert flipped[0]["amount"] == 12.5


def test_a_new_card_is_added_with_the_pair_that_pays_it():
    key = actions.add_account("Chase Freedom", "credit", "Chase", paid_from="bofa_checking", payment_hint="chase credit crd")
    assert key == "chase_freedom"
    assert config.ACCOUNTS[key] == {"institution": "chase", "kind": "credit", "cap_key": None, "name": "Chase Freedom"}
    assert {"accounts": ("bofa_checking", "chase_freedom"), "hints": {"bofa_checking": "CHASE CREDIT CRD"}} in config.TRANSFER_PAIRS
    assert actions.add_account("Chase Freedom", "credit", "chase") == "chase_freedom_2"  # a name taken twice


def test_a_first_account_starts_from_the_example(tmp_path, monkeypatch):
    (tmp_path / "accounts.toml").unlink()
    monkeypatch.setattr(config, "ACCOUNTS_SOURCE", config.ACCOUNTS_EXAMPLE)
    actions.add_account("Credit Union", "cash", "union")
    assert (tmp_path / "accounts.toml").exists()
    assert {"bofa_checking", "credit_union"} <= set(config.ACCOUNTS)
    assert config.ACCOUNTS_SOURCE == tmp_path / "accounts.toml"


def test_teaching_a_csv_saves_its_layout_and_imports_it(tmp_path):
    path = write(tmp_path, "card.csv", CARD)
    header = actions.read_unknown(path)["header"]
    key = actions.add_account("Chase card", "credit", "chase")
    [result] = actions.teach_csv(path, ONE_COLUMN, header, key)
    assert result["rows_new"] == 3 and result["institution"] == "Chase card"
    assert stored()[0] == ("chase_card", "2026-10-14", -12.5, "CORNER CAFE")
    again = write(tmp_path, "card2.csv", CARD.replace("CORNER CAFE", "CORNER CAFE TOO"))
    assert actions.import_files([again])[0]["institution"] == "Chase card"  # the layout is remembered


def test_teaching_debit_and_credit_columns(tmp_path):
    path = write(tmp_path, "union.csv", UNION)
    header = actions.read_unknown(path)["header"]
    fields = {"name": "Credit union", "date": "date", "mode": "debit and credit", "debit": "withdrawal",
              "credit": "deposit", "description": ["description"]}
    actions.teach_csv(path, fields, header, "bofa_checking")
    assert [row[2] for row in stored()] == [-45.1, 1500.0]


def test_teaching_an_ofx_account(tmp_path):
    [result] = actions.teach_ofx(write(tmp_path, "statement.qfx", OFX), "4321", "bofa_checking")
    assert result["rows_new"] == 1
    assert 'account_ends = "4321"' in config.FORMATS_PATH.read_text()


def test_a_bad_layout_changes_nothing(tmp_path):
    path = write(tmp_path, "card.csv", CARD)
    header = actions.read_unknown(path)["header"]
    with pytest.raises(formats.FormatsError):
        actions.teach_csv(path, {**ONE_COLUMN, "description": []}, header, "bofa_checking")
    assert not config.FORMATS_PATH.exists() and not list(tmp_path.glob("*.new"))


def test_a_file_name_gives_its_words_without_digits():
    assert actions.plain_name("20270201-093000_Chase1234_Activity20261101.CSV") == ["Chase", "Activity"]
    assert actions.plain_name("20270201-093000_2_export.csv") == ["export"]


def test_the_page_teaches_a_file_it_does_not_know(tmp_path):
    from streamlit.testing.v1 import AppTest

    path = write(tmp_path, "20261101-090000_Chase1234_Activity.csv", CARD)
    [unknown] = actions.import_files([path])
    page = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "dashboard.py"), default_timeout=60)
    page.session_state["import_results"] = [unknown]
    page.run()
    assert not page.exception
    assert page.subheader[0].value == "Teach this file"
    page.text_input(key=f"csv_{unknown['file']}_name").set_value("Chase Freedom").run()
    assert page.dataframe[1].value["amount"].tolist() == [-12.5, 250.0, -30.0]  # the preview
    page.button(key=f"csv_{unknown['file']}_save").click().run()
    assert not page.exception
    assert "chase_freedom" in config.ACCOUNTS
    assert len(stored()) == 3
    assert not [heading for heading in page.subheader if heading.value.startswith("Teach")]
