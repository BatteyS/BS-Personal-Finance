"""Tests for scrub.py, on synthetic exports shaped like the real ones. Nothing here is real data."""

import codecs
import csv
import io
from collections import Counter
from decimal import Decimal

import pytest

import scrub

BOFA = """\
Description,,Summary Amt.
Beginning balance as of 10/01/2026,,"12,345.67"
Total credits,,"5,432.10"
Total debits,,"-4,321.98"
Ending balance as of 10/31/2026,,"13,455.79"

Date,Description,Amount,Running Bal.
10/01/2026,Beginning balance as of 10/01/2026,,"12,345.67"
10/02/2026,"AMERICAN EXPRESS DES:ACH PMT ID:W76543210 INDN:TEST PERSON CO ID:1234567890 WEB","-1,234.56","11,111.11"
10/03/2026,"ACME CORP DES:PAYROLL ID:987654 INDN:TEST PERSON CO ID:9876543210 PPD","3,210.45","14,321.56"
10/04/2026,COSTCO WHSE #1234 CHICAGO IL,-0.40,"14,321.16"
10/05/2026,ZELLE PAYMENT TO JANE EXAMPLE CONF# 8a7b6c,-45.67,"14,275.49"
"""

BOFA_SECRETS = [
    "12,345.67", "5,432.10", "4,321.98", "13,455.79", "11,111.11", "14,321.56", "14,321.16",
    "14,275.49", "1,234.56", "3,210.45", "76543210", "987654", "1234567890", "9876543210",
    "TEST PERSON", "JANE EXAMPLE",
]

AMEX = """\
Date,Description,Card Member,Account #,Amount,Reference
10/03/2026,AUTOPAY PAYMENT - THANK YOU,TEST PERSON,-41007,-1234.56,'320262760123456789'
10/04/2026,BLUE BOTTLE COFFEE,TEST PERSON,-41007,5.25,'320262770123456780'
10/04/2026,BLUE BOTTLE COFFEE,TEST PERSON,-41007,5.25,'320262770123456781'
10/06/2026,AMAZON MKTPL*AB12CD34,TEST PERSON,-41007,-19.99,'320262790123456782'
"""

FIDELITY = "\r\n".join([
    "",
    "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price ($),Commission ($),Fees ($),Accrued Interest ($),Amount ($),Cash Balance ($),Settlement Date",
    ' 10/15/2026,ROTH IRA,X12345678,CONTRIBUTION CURRENT YEAR (Cash), ,No Description,Cash,0.000,,,,,625,"8,765.43",',
    ' 10/16/2026,ROTH IRA,X12345678,YOU BOUGHT FIDELITY 500 INDEX FUND (FXAIX) (Cash),FXAIX,FIDELITY 500 INDEX FUND,Cash,2.917,214.25,,,,-625,"8,140.43",10/17/2026',
    ' 10/20/2026,Individual - TOD,Z87654321,TRANSFERRED FROM VS Z11223344-1 (Cash), ,No Description,Cash,0.000,,,,,"1,850",0.00,',
    ' 10/31/2026,ROTH IRA,X12345678,DIVIDEND RECEIVED FIDELITY 500 INDEX FUND (FXAIX) (Cash),FXAIX,FIDELITY 500 INDEX FUND,Cash,0.000,,,,,12.34,"8,152.77",',
    "",
    "",
    '"The data and information in this spreadsheet is provided to you solely for your use."',
    '"Date downloaded 11/06/2026 10:23 am"',
    "",
])


def scrub_rows(text: str, *redact: str) -> tuple[list[list[str]], str, Counter]:
    """Scrub `text` with fresh stand-ins; return the parsed rows, the text and the counts."""
    scrubbed, counts = scrub.scrub_text(text, list(redact), 1, {})
    return list(csv.reader(io.StringIO(scrubbed, newline=""))), scrubbed, counts


def test_bofa_balances_names_and_ids_are_gone():
    _, scrubbed, _ = scrub_rows(BOFA, "Jane Example")
    for secret in BOFA_SECRETS:
        assert secret not in scrubbed


def test_bofa_keeps_its_layout_dates_and_rounded_amounts():
    rows, _, counts = scrub_rows(BOFA, "Jane Example")
    original = list(csv.reader(io.StringIO(BOFA, newline="")))
    assert len(rows) == len(original)
    assert rows[6] == ["Date", "Description", "Amount", "Running Bal."]
    assert [row[0] for row in rows[7:]] == [row[0] for row in original[7:]]
    assert rows[1] == ["Beginning balance as of 10/01/2026", "", "0.00"]  # summary zeroed, year kept
    assert rows[7][2:] == ["", "0.00"]  # the beginning-balance row has no amount
    assert [row[2] for row in rows[8:]] == ["-1,235.00", "3,210.00", "-1.00", "-46.00"]
    assert rows[8][1] == "AMERICAN EXPRESS DES:ACH PMT ID:W00000001 INDN:REDACTED CO ID:0000000002 WEB"
    assert rows[11][1] == "ZELLE PAYMENT TO REDACTED CONF# 8a7b6c"
    assert counts["data rows"] == 5


def test_amex_hides_card_member_account_and_references():
    rows, scrubbed, _ = scrub_rows(AMEX)
    data = rows[1:]
    assert "41007" not in scrubbed and "TEST PERSON" not in scrubbed
    assert {row[2] for row in data} == {"REDACTED"}
    assert len({row[3] for row in data}) == 1  # one card, masked the same way on every row
    assert len({row[5] for row in data}) == 4  # references stay distinct
    assert not any("3202627" in row[5] for row in data)
    assert [row[4] for row in data] == ["-1235.00", "5.00", "5.00", "-20.00"]
    assert data[1][:5] == data[2][:5]  # identical same-day charges stay two rows


def test_both_legs_of_a_card_payment_round_to_the_same_amount():
    bofa_rows, _, _ = scrub_rows(BOFA)
    amex_rows, _, _ = scrub_rows(AMEX)
    bofa_leg = Decimal(bofa_rows[8][2].replace(",", ""))
    amex_leg = Decimal(amex_rows[1][4])  # negative in Amex's own file; the loader flips it
    assert bofa_leg == amex_leg == Decimal("-1235.00")


def test_fidelity_keeps_bom_line_endings_labels_and_footer(tmp_path):
    source = tmp_path / "Accounts_History.csv"
    source.write_bytes(codecs.BOM_UTF8 + FIDELITY.encode())
    target, counts = scrub.scrub_file(source, tmp_path / "samples", [], 1, {})

    assert target.name == "Accounts_History.scrubbed.csv"
    raw = target.read_bytes()
    assert raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")
    for secret in ["12345678", "87654321", "11223344", "8,765.43", "8,140.43", "8,152.77"]:
        assert secret not in text
    assert "solely for your use" in text and "Date downloaded 11/06/2026" in text

    data = [row for row in csv.reader(io.StringIO(text, newline="")) if row and scrub.DATE.match(row[0])]
    assert counts["data rows"] == len(data) == 4
    assert [row[1] for row in data] == ["ROTH IRA", "ROTH IRA", "Individual - TOD", "ROTH IRA"]
    assert data[0][2] == data[1][2] == data[3][2] != data[2][2]  # one stand-in per account
    assert [row[12] for row in data] == ["625", "-625", "1,850", "12.00"]
    assert [row[13] for row in data] == ["0.00"] * 4
    assert data[1][7:9] == ["3.000", "214.25"]  # quantity rounded, price kept


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("-1,234.56", "-1,235.00"),
        ("1234.5", "1235.0"),
        ("0.40", "1.00"),
        ("-0.40", "-1.00"),
        ("(45.50)", "(46.00)"),
        ("$12.49", "$12.00"),
        ("625", "625"),
        ("0.00", "0.00"),
        ("", ""),
        ("n/a", "n/a"),
    ],
)
def test_round_amount(cell, expected):
    assert scrub.round_amount(cell, 1, Counter()) == expected


def test_coarser_step_rounds_to_tens_and_never_to_zero():
    assert scrub.round_amount("-1,234.56", 10, Counter()) == "-1,230.00"
    assert scrub.round_amount("4.99", 10, Counter()) == "10.00"


def test_unknown_numeric_columns_are_rounded_or_masked():
    rows, _, _ = scrub_rows('Date,Amount,Mystery Total,Store\n10/01/2026,-1.00,"4,567.89",60601\n')
    assert rows[1] == ["10/01/2026", "-1.00", "4,568.00", "00001"]


def test_rows_as_wide_as_the_header_are_scrubbed_even_with_odd_dates():
    rows, _, _ = scrub_rows("Date,Description,Card Member,Amount\nOct 15 2026,COFFEE,TEST PERSON,-4.50\n")
    assert rows[1] == ["Oct 15 2026", "COFFEE", "REDACTED", "-5.00"]


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Zelle Recurring payment to Jane Example Conf# abc123", "Zelle Recurring payment to REDACTED Conf# abc123"),
        ('Zelle payment to JOHN EXAMPLE for Move"; Conf# xyz', 'Zelle payment to REDACTED for Move"; Conf# xyz'),
        ("Zelle payment from Jane Example; Conf# abc", "Zelle payment from REDACTED; Conf# abc"),
        (
            "WIRE TYPE:WIRE OUT BNF:1/ACME REALTY ID:12 BNF BK:SOME BANK PMT DET:X /ACC/Jane Example",
            "WIRE TYPE:WIRE OUT BNF:REDACTED ID:12 BNF BK:SOME BANK PMT DET:X /ACC/REDACTED",
        ),
        ("WIRE TYPE:WIRE IN ORIG:JOHN EXAMPLE ID:34", "WIRE TYPE:WIRE IN ORIG:REDACTED ID:34"),
    ],
)
def test_counterparty_names_are_redacted(description, expected):
    assert scrub.mask_text(description, [], {}, Counter()) == expected


def test_header_with_no_data_rows_is_refused():
    with pytest.raises(ValueError, match="no data rows"):
        scrub.scrub_text('Date,Description,Amount\n"No activity for this period"\n', [], 1, {})


def test_cli_prints_counts_never_values(tmp_path, capsys):
    source = tmp_path / "stmt.csv"
    source.write_text(BOFA)
    assert scrub.main([str(source), "--out", str(tmp_path / "samples"), "--redact", "Jane Example"]) == 0
    printed = capsys.readouterr().out
    assert "stmt.scrubbed.csv" in printed and "5 data rows" in printed
    for secret in BOFA_SECRETS:
        assert secret not in printed


def test_existing_output_is_kept_unless_forced(tmp_path):
    source = tmp_path / "stmt.csv"
    source.write_text(BOFA)
    out_dir = tmp_path / "samples"
    scrub.scrub_file(source, out_dir, [], 1, {})
    with pytest.raises(ValueError, match="already exists"):
        scrub.scrub_file(source, out_dir, [], 1, {})
    scrub.scrub_file(source, out_dir, [], 1, {}, force=True)


def test_unrecognized_file_is_refused_without_echoing_it(tmp_path, capsys):
    source = tmp_path / "mystery.csv"
    source.write_text("Name,Balance\nTEST PERSON,12345.67\n")
    assert scrub.main([str(source), "--out", str(tmp_path / "samples")]) == 1
    printed = capsys.readouterr()
    assert "no header row" in printed.err
    assert "TEST PERSON" not in printed.out + printed.err
    assert "12345.67" not in printed.out + printed.err
    assert not (tmp_path / "samples").exists()


def test_a_debit_and_credit_export_is_scrubbed_too():
    text = "Date,Description,Debit,Credit,Balance\n10/02/2026,GROCERY TOWN 0042,45.10,,1234.56\n"
    scrubbed, counts = scrub.scrub_text(text, [], 10, {})
    assert counts["data rows"] == 1
    assert "1234.56" not in scrubbed and "45.10" not in scrubbed
