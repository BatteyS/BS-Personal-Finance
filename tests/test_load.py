"""Tests for db.py and load.py, on synthetic exports shaped like the layouts in docs/spec.md.

Nothing here is real data. test_samples_load_and_dedupe runs once scrubbed samples are in samples/.
"""

import codecs
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import config
import db
import load


BOFA = """\
Description,,Summary Amt.
Beginning balance as of 10/01/2026,,"5,000.00"
Total credits,,"3,000.00"
Total debits,,"-1,240.00"
Ending balance as of 10/31/2026,,"6,760.00"

Date,Description,Amount,Running Bal.
10/01/2026,Beginning balance as of 10/01/2026,,"5,000.00"
10/02/2026,"AMERICAN EXPRESS DES:ACH PMT ID:W00000001 INDN:REDACTED CO ID:0000000002 WEB","-1,235.00","3,765.00"
10/03/2026,"ACME CORP DES:PAYROLL ID:000003 INDN:REDACTED CO ID:0000000004 PPD","3,000.00","6,765.00"
10/04/2026,CTA VENTRA,-2.50,"6,762.50"
10/04/2026,CTA VENTRA,-2.50,"6,760.00"
"""

AMEX = """\
Date,Description,Card Member,Account #,Amount
10/03/2026,AUTOPAY PAYMENT - THANK YOU,REDACTED,-00001,-1235.00
10/04/2026,BLUE BOTTLE COFFEE,REDACTED,-00001,5.00
10/06/2026,AMAZON MARKETPLACE REFUND,REDACTED,-00001,-20.00
"""

FIDELITY_HEADER = (
    "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price ($),"
    "Commission ($),Fees ($),Accrued Interest ($),Amount ($),Cash Balance ($),Settlement Date"
)


def fidelity(*rows: str) -> str:
    """A Fidelity export around the given data rows: a blank line, the header, then disclaimers."""
    footer = [
        "",
        "",
        '"The data and information in this spreadsheet is provided to you solely for your use."',
        '"Date downloaded 11/06/2026 10:23 am"',
        "",
    ]
    return "\r\n".join(["", FIDELITY_HEADER, *rows, *footer])


FIDELITY = fidelity(
    " 10/15/2026,ROTH IRA,X00000001,CONTRIBUTION CURRENT YEAR (Cash), ,No Description,Cash,0.000,,,,,625,0.00,",
    " 10/16/2026,ROTH IRA,X00000001,YOU BOUGHT FIDELITY 500 INDEX FUND (FXAIX) (Cash),FXAIX,"
    "FIDELITY 500 INDEX FUND,Cash,3.000,214.25,,,,-625,0.00,10/17/2026",
    ' 10/20/2026,Cash Management,Z00000003,TRANSFERRED TO VS Z00000002-1 (Cash), ,No Description,Cash,0.000,,,,,"-1,850",0.00,',
    ' 10/20/2026,Individual - TOD,Z00000002,TRANSFERRED FROM VS Z00000003-1 (Cash), ,No Description,Cash,0.000,,,,,"1,850",0.00,',
    " 10/22/2026,Health Savings Account,X00000004,CONTRIBUTION EMPLOYER (Cash), ,No Description,Cash,0.000,,,,,50,0.00,",
    " 10/25/2026,Individual - TOD,Z00000002,TRANSFER OF ASSETS ACAT RECEIVE (Shares),VTI,"
    "VANGUARD TOTAL STOCK MARKET ETF,Shares,10.000,,,,,,0.00,",
)


def write(folder: Path, name: str, text: str, bom: bool = False) -> Path:
    """Write an export file, optionally with the UTF-8 byte-order mark Fidelity files carry."""
    path = folder / name
    path.write_bytes((codecs.BOM_UTF8 if bom else b"") + text.encode())
    return path


def stored(db_path: Path) -> list[tuple]:
    """(account, date, month, amount, description) for every stored row, in a stable order."""
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(
            "SELECT account, date, month, amount, description FROM transactions"
            " ORDER BY account, date, amount, description"
        ).fetchall()


def test_bofa_skips_the_summary_and_beginning_balance(tmp_path):
    db_path = tmp_path / "finance.db"
    [result] = load.load_files([write(tmp_path, "stmt.csv", BOFA)], db_path)
    assert result["institution"] == "bofa"
    assert stored(db_path) == [
        ("bofa_checking", "2026-10-02", "2026-10", -1235.0,
         "AMERICAN EXPRESS DES:ACH PMT ID:W00000001 INDN:REDACTED CO ID:0000000002 WEB"),
        ("bofa_checking", "2026-10-03", "2026-10", 3000.0,
         "ACME CORP DES:PAYROLL ID:000003 INDN:REDACTED CO ID:0000000004 PPD"),
        ("bofa_checking", "2026-10-04", "2026-10", -2.5, "CTA VENTRA"),
        ("bofa_checking", "2026-10-04", "2026-10", -2.5, "CTA VENTRA"),
    ]


def test_amex_signs_are_flipped(tmp_path):
    db_path = tmp_path / "finance.db"
    load.load_files([write(tmp_path, "activity.csv", AMEX)], db_path)
    assert [(row[4], row[3]) for row in stored(db_path)] == [
        ("AUTOPAY PAYMENT - THANK YOU", 1235.0),  # a payment arrives on the card
        ("BLUE BOTTLE COFFEE", -5.0),  # a charge leaves it
        ("AMAZON MARKETPLACE REFUND", 20.0),
    ]


def test_fidelity_maps_accounts_and_builds_descriptions(tmp_path):
    db_path = tmp_path / "finance.db"
    [result] = load.load_files([write(tmp_path, "Accounts_History.csv", FIDELITY, bom=True)], db_path)
    assert result["institution"] == "fidelity"
    assert result["rows_read"] == 5  # the share-only transfer has no amount
    assert stored(db_path) == [
        ("fidelity_brokerage", "2026-10-20", "2026-10", 1850.0, "TRANSFERRED FROM VS Z00000003-1 (Cash)"),
        ("fidelity_cash", "2026-10-20", "2026-10", -1850.0, "TRANSFERRED TO VS Z00000002-1 (Cash)"),
        ("fidelity_hsa", "2026-10-22", "2026-10", 50.0, "CONTRIBUTION EMPLOYER (Cash)"),
        ("fidelity_roth", "2026-10-15", "2026-10", 625.0, "CONTRIBUTION CURRENT YEAR (Cash)"),
        ("fidelity_roth", "2026-10-16", "2026-10", -625.0,
         "YOU BOUGHT FIDELITY 500 INDEX FUND (FXAIX) (Cash) FIDELITY 500 INDEX FUND"),
    ]


@pytest.mark.parametrize(
    ("label", "account"),
    [
        ("Cash Management (Individual)", "fidelity_cash"),
        ("Individual - TOD", "fidelity_brokerage"),
        ("Joint WROS", "fidelity_household"),
        ("ACME CORP 401(K) PLAN", "fidelity_401k"),
        ("OLDCO 401(K) PLAN", "fidelity_401k_old"),
        ("ROTH IRA", "fidelity_roth"),
        ("Health Savings Account", "fidelity_hsa"),
    ],
)
def test_fidelity_account_labels(label, account):
    assert load.fidelity_account(label) == account


def test_the_longest_fidelity_label_wins(monkeypatch):
    monkeypatch.setattr(config, "ACCOUNTS", {
        "cash": {"institution": "fidelity", "kind": "cash", "cap_key": None, "fidelity_label": "CASH MANAGEMENT"},
        "brokerage": {"institution": "fidelity", "kind": "brokerage", "cap_key": None, "fidelity_label": "INDIVIDUAL"},
    })
    assert load.fidelity_account("Cash Management (Individual)") == "cash"
    assert load.fidelity_account("Individual - TOD") == "brokerage"


def test_row_ids_follow_the_spec():
    row = {"account": "bofa_checking", "date": "2026-10-04", "amount": -2.5, "description": "CTA VENTRA"}
    expected = [
        hashlib.sha256(b"bofa_checking|2026-10-04|-2.50|CTA VENTRA|1").hexdigest(),
        hashlib.sha256(b"bofa_checking|2026-10-04|-2.50|CTA VENTRA|2").hexdigest(),
    ]
    assert load.row_ids([row, dict(row)]) == expected  # two identical rows, two ids


def test_loading_twice_inserts_nothing_new(tmp_path):
    db_path = tmp_path / "finance.db"
    paths = [
        write(tmp_path, "stmt.csv", BOFA),
        write(tmp_path, "activity.csv", AMEX),
        write(tmp_path, "Accounts_History.csv", FIDELITY, bom=True),
    ]
    first = load.load_files(paths, db_path)
    second = load.load_files(paths, db_path)
    assert [result["rows_new"] for result in first] == [4, 3, 5]
    assert [result["rows_new"] for result in second] == [0, 0, 0]
    assert [result["rows_duplicate"] for result in second] == [4, 3, 5]
    assert len(stored(db_path)) == 12
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT count(*) FROM imports").fetchone() == (6,)


def test_an_overlapping_export_inserts_only_its_new_rows(tmp_path):
    db_path = tmp_path / "finance.db"
    earlier = "Date,Description,Amount,Running Bal.\n10/04/2026,CTA VENTRA,-2.50,0.00\n10/04/2026,CTA VENTRA,-2.50,0.00\n"
    later = earlier + "10/04/2026,CTA VENTRA,-2.50,0.00\n10/05/2026,TRADER JOE S #0007,-40.00,0.00\n"
    load.load_files([write(tmp_path, "earlier.csv", earlier)], db_path)
    [result] = load.load_files([write(tmp_path, "later.csv", later)], db_path)
    assert (result["rows_read"], result["rows_new"], result["rows_duplicate"]) == (4, 2, 2)


def test_an_unknown_header_is_rejected_with_its_first_three_lines(tmp_path):
    db_path = tmp_path / "finance.db"
    text = "Name,Balance\nREDACTED,1.00\nREDACTED,2.00\nREDACTED,3.00\n"
    [result] = load.load_files([write(tmp_path, "mystery.csv", text)], db_path)
    assert "layout isn't one this tracker knows yet" in result["error"] and result["unknown"] == "csv"
    assert "Name,Balance" in result["error"] and "REDACTED,2.00" in result["error"]
    assert "3.00" not in result["error"]


def test_amex_plain_download_is_recognized(tmp_path):
    db_path = tmp_path / "finance.db"
    [result] = load.load_files([write(tmp_path, "activity.csv", "Date,Description,Amount\n10/04/2026,COFFEE,5.00\n")], db_path)
    assert result["institution"] == "amex"
    assert [row[3] for row in stored(db_path)] == [-5.0]


def test_other_three_column_files_are_rejected(tmp_path):
    [result] = load.load_files(
        [write(tmp_path, "other.csv", "Posted Date,Payee,Amount\n10/04/2026,COFFEE,5.00\n")], tmp_path / "finance.db"
    )
    assert "error" in result


def test_an_unknown_fidelity_account_stops_that_file(tmp_path):
    db_path = tmp_path / "finance.db"
    text = fidelity(" 10/15/2026,Rollover IRA,X00000009,CONTRIBUTION (Cash), ,No Description,Cash,0.000,,,,,100,0.00,")
    [result] = load.load_files([write(tmp_path, "Accounts_History.csv", text)], db_path)
    assert result["error"] == "row 3: the Fidelity account 'Rollover IRA' matches no fidelity_label in accounts.toml"
    assert stored(db_path) == []


def test_a_bad_amount_names_its_row(tmp_path):
    text = "Date,Description,Amount,Running Bal.\n10/04/2026,COFFEE,abc,0.00\n"
    [result] = load.load_files([write(tmp_path, "stmt.csv", text)], tmp_path / "finance.db")
    assert result["error"] == "row 2: the amount 'abc' is not a number"


def test_the_accounts_table_follows_config(tmp_path):
    db_path = tmp_path / "finance.db"
    db.connect(db_path).close()
    with closing(db.connect(db_path)) as connection:  # a second connect rewrites, never duplicates
        rows = connection.execute("SELECT name, institution, kind, cap_key FROM accounts ORDER BY name").fetchall()
    assert rows == sorted(
        (name, spec["institution"], spec["kind"], spec["cap_key"]) for name, spec in config.ACCOUNTS.items()
    )


def test_the_command_line_prints_one_line_per_file(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "finance.db")
    folder = tmp_path / "exports"
    folder.mkdir()
    write(folder, "stmt.csv", BOFA)
    write(folder, "activity.csv", AMEX)
    write(folder, "notes.txt", "not an export")
    assert load.main([str(folder)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "activity.csv: amex, 3 read, 3 new, 0 duplicate",
        "stmt.csv: bofa, 4 read, 4 new, 0 duplicate",
    ]
