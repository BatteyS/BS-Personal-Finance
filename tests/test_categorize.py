"""Tests for categorize.py, on synthetic rows. Nothing here is real data; the sample tests are in test_samples.py."""

from pathlib import Path

import pytest

import categorize
import db
import load

NO_RULES: list[dict] = []


def database(tmp_path: Path, *rows: tuple[str, str, float, str]):
    """A fresh database holding (account, date, amount, description) rows with ids r0, r1, ..."""
    connection = db.connect(tmp_path / "finance.db")
    with connection:
        connection.executemany(
            "INSERT INTO transactions (id, account, date, amount, description, month, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'test')",
            [(f"r{index}", account, day, amount, text, day[:7]) for index, (account, day, amount, text) in enumerate(rows)],
        )
    return connection


def results(connection) -> dict[str, tuple[str, str | None]]:
    """Each row's (category, transfer_id) by id."""
    return {row_id: (category, transfer_id)
            for row_id, category, transfer_id in connection.execute("SELECT id, category, transfer_id FROM transactions")}


def rules_from(tmp_path: Path, text: str) -> list[dict]:
    path = tmp_path / "rules.csv"
    path.write_text("pattern,category,match\n" + text)
    return categorize.read_rules(path)


def test_a_card_payment_nets_to_zero_and_links_both_ways(tmp_path):
    connection = database(
        tmp_path,
        ("bofa_checking", "2026-10-19", -1104.0, "AMERICAN EXPRESS DES:ACH PMT ID:A1 INDN:REDACTED CO ID:2 WEB"),
        ("amex", "2026-10-19", 1104.0, "AUTOPAY PAYMENT - THANK YOU"),
    )
    categorize.categorize(connection, NO_RULES)
    assert results(connection) == {"r0": ("transfer", "r1"), "r1": ("transfer", "r0")}


@pytest.mark.parametrize(("amex_day", "paired"), [("2026-10-10", True), ("2026-10-20", True), ("2026-10-09", False)])
def test_legs_pair_in_either_order_within_five_days(tmp_path, amex_day, paired):
    connection = database(
        tmp_path,
        ("bofa_checking", "2026-10-15", -500.0, "AMERICAN EXPRESS DES:ACH PMT"),
        ("amex", amex_day, 500.0, "ONLINE PAYMENT - THANK YOU"),
    )
    categorize.categorize(connection, NO_RULES)
    assert (results(connection)["r0"][0] == "transfer") is paired


def test_equal_transfers_pair_one_to_one_by_closest_date(tmp_path):
    connection = database(
        tmp_path,
        ("bofa_checking", "2026-10-01", -200.0, "FID BKG SVC LLC DES:MONEYLINE"),
        ("bofa_checking", "2026-10-04", -200.0, "FID BKG SVC LLC DES:MONEYLINE"),
        ("fidelity_brokerage", "2026-10-02", 200.0, "Electronic Funds Transfer Received (Cash)"),
        ("fidelity_brokerage", "2026-10-05", 200.0, "Electronic Funds Transfer Received (Cash)"),
    )
    categorize.categorize(connection, NO_RULES)
    linked = results(connection)
    assert linked["r0"] == ("transfer", "r2") and linked["r1"] == ("transfer", "r3")


def test_hints_keep_a_purchase_and_a_refund_apart(tmp_path):
    connection = database(
        tmp_path,
        ("bofa_checking", "2026-10-04", -50.0, "TARGET 00012 CHICAGO IL"),
        ("amex", "2026-10-05", 50.0, "TARGET REFUND"),
    )
    categorize.categorize(connection, NO_RULES)
    assert results(connection) == {"r0": ("uncategorized", None), "r1": ("uncategorized", None)}


def test_a_lone_leg_falls_through_to_the_rules(tmp_path):
    connection = database(tmp_path, ("bofa_checking", "2026-10-19", -1104.0, "AMERICAN EXPRESS DES:ACH PMT"))
    categorize.categorize(connection, rules_from(tmp_path, "AMERICAN EXPRESS,shopping,contains\n"))
    assert results(connection) == {"r0": ("shopping", None)}


def test_investment_activity_is_never_spending(tmp_path):
    connection = database(
        tmp_path,
        ("fidelity_brokerage", "2026-10-30", -56.0, "REINVESTMENT FIDELITY GOVERNMENT MONEY MARKET (SPAXX) (Cash)"),
        ("fidelity_brokerage", "2026-10-30", 56.0, "DIVIDEND RECEIVED FIDELITY GOVERNMENT MONEY MARKET (SPAXX) (Cash)"),
        ("fidelity_roth", "2026-10-16", -625.0, "YOU BOUGHT FIDELITY 500 INDEX FUND (FXAIX) (Cash)"),
        ("fidelity_401k_old", "2026-11-03", 392.0, "Change In Market Value FID FREEDOM 2065 K"),
        ("fidelity_401k_old", "2026-10-26", 3.0, "REVENUE CREDIT FID FREEDOM 2065 K"),
    )
    categorize.categorize(connection, NO_RULES)
    assert {category for category, _ in results(connection).values()} == {"investment"}


def test_employer_money_counts_for_the_hsa_but_not_the_401k(tmp_path):
    connection = database(
        tmp_path,
        ("fidelity_401k", "2026-10-15", 900.0, "Contributions AF TRGT DATE 2065 R6"),
        ("fidelity_401k", "2026-10-15", 300.0, "Contributions EMPLOYER MATCH AF TRGT DATE 2065 R6"),
        ("fidelity_hsa", "2026-10-15", 50.0, "CONTRIBUTION EMPLOYER (Cash)"),
        ("fidelity_brokerage", "2026-10-15", 75.0, "CONTRIBUTION (Cash)"),  # no cap: not a contribution
    )
    categorize.categorize(connection, NO_RULES)
    assert [category for category, _ in results(connection).values()] == [
        "retirement", "employer_match", "retirement", "uncategorized"
    ]


@pytest.mark.parametrize("source", ["fidelity_cash", "fidelity_brokerage", "fidelity_household"])
def test_a_roth_contribution_from_cash_or_a_brokerage_stays_retirement(tmp_path, source):
    connection = database(
        tmp_path,
        (source, "2026-10-15", -625.0, "TRANSFERRED TO VS X1-1 (Cash)"),
        ("fidelity_roth", "2026-10-15", 625.0, "CONTRIBUTION CURRENT YEAR (Cash)"),
    )
    categorize.categorize(connection, NO_RULES)
    assert results(connection) == {"r0": ("transfer", "r1"), "r1": ("retirement", "r0")}


def test_a_rollover_between_401k_plans_is_a_transfer(tmp_path):
    connection = database(
        tmp_path,
        ("fidelity_401k_old", "2026-11-03", -5326.0, "Withdrawals FID FREEDOM 2065 K"),
        ("fidelity_401k", "2026-11-04", 5326.0, "Contributions AF TRGT DATE 2065 R6"),
    )
    categorize.categorize(connection, NO_RULES)
    assert results(connection) == {"r0": ("transfer", "r1"), "r1": ("transfer", "r0")}


def test_coffee_on_the_hsa_card_is_not_a_fee(tmp_path):
    connection = database(
        tmp_path,
        ("fidelity_hsa", "2026-10-15", -5.0, "DEBIT CARD PURCHASE COFFEE SHOP (Cash)"),
        ("fidelity_cash", "2026-10-16", -20.0, "DEBIT CARD PURCHASE FEEDING AMERICA (Cash)"),
        ("fidelity_hsa", "2026-10-17", -2.0, "FEES CHARGED (Cash)"),
    )
    categorize.categorize(connection, rules_from(tmp_path, "COFFEE,dining,contains\n"))
    assert [category for category, _ in results(connection).values()] == ["dining", "uncategorized", "investment"]


def test_money_market_rows_in_a_fidelity_cash_account_are_investment(tmp_path):
    connection = database(
        tmp_path,
        ("fidelity_cash", "2026-10-30", 1.0, "DIVIDEND RECEIVED FIDELITY GOVERNMENT MONEY MARKET (SPAXX) (Cash)"),
        ("fidelity_cash", "2026-10-30", -1.0, "REINVESTMENT FIDELITY GOVERNMENT MONEY MARKET (SPAXX) (Cash)"),
        ("bofa_checking", "2026-10-30", 1.0, "INTEREST EARNED"),  # BofA rows never take pass 1
    )
    categorize.categorize(connection, NO_RULES)
    assert [category for category, _ in results(connection).values()] == ["investment", "investment", "uncategorized"]


def test_rules_apply_top_to_bottom_and_regex_works(tmp_path):
    rules = rules_from(tmp_path, "TRADER JOE,groceries,contains\nJOE,dining,contains\n^CTA,transit,regex\n")
    connection = database(
        tmp_path,
        ("amex", "2026-10-01", -40.0, "TRADER JOE S #0007 CHICAGO IL"),
        ("amex", "2026-10-02", -18.0, "JOE'S DINER"),
        ("bofa_checking", "2026-10-03", -2.5, "cta  ventra"),
        ("bofa_checking", "2026-10-04", -2.5, "PURCHASE CTA VENTRA"),
    )
    categorize.categorize(connection, rules)
    assert [category for category, _ in results(connection).values()] == ["groceries", "dining", "transit", "uncategorized"]


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("COSTCO,snacks,contains\n", "line 2: 'snacks' isn't a category"),
        ("(unclosed,dining,regex\n", "line 2: the regex doesn't compile"),
        ("COSTCO,groceries,startswith\n", "line 2: match must be contains or regex"),
        (",groceries,contains\n", "line 2: the pattern is empty"),
    ],
)
def test_bad_rules_are_rejected_with_their_line(tmp_path, line, message):
    with pytest.raises(categorize.RulesError, match=message):
        rules_from(tmp_path, line)


def test_a_new_rule_recategorizes_everything(tmp_path):
    connection = database(tmp_path, ("amex", "2026-09-01", -12.0, "METRA MOBILE"), ("amex", "2026-10-01", -12.0, "METRA MOBILE"))
    categorize.categorize(connection, NO_RULES)
    assert {category for category, _ in results(connection).values()} == {"uncategorized"}
    categorize.categorize(connection, rules_from(tmp_path, "METRA,transit,contains\n"))
    assert {category for category, _ in results(connection).values()} == {"transit"}


@pytest.mark.parametrize(
    ("description", "key"),
    [
        ("COSTCO WHSE #0123 CHICAGO IL", "COSTCO WHSE CHICAGO IL"),
        ("7-ELEVEN 00036 00037CHICAGO IL", "ELEVEN CHICAGO IL"),
        ("VENMO DES:PAYMENT ID:0000000000011 INDN:REDACTED CO ID:0000000012 WEB",
         "VENMO DES:PAYMENT ID INDN:REDACTED CO ID WEB"),
    ],
)
def test_merchant_keys_drop_store_numbers(description, key):
    assert categorize.merchant_key(description) == key


def test_the_report_groups_uncategorized_rows_largest_first(tmp_path):
    connection = database(
        tmp_path,
        ("amex", "2026-10-01", -4.0, "7-ELEVEN 00036 CHICAGO IL"),
        ("amex", "2026-10-08", -5.0, "7-ELEVEN 00041 CHICAGO IL"),
        ("amex", "2026-10-09", -82.0, "ANTHROPIC SAN FRANCISCO CA"),
        ("amex", "2026-09-09", -82.0, "ANTHROPIC SAN FRANCISCO CA"),
    )
    categorize.categorize(connection, NO_RULES)
    report = categorize.uncategorized_report(connection, "2026-10")
    assert [(group["merchant"], group["count"], group["total"], group["pattern"]) for group in report] == [
        ("ANTHROPIC SAN FRANCISCO CA", 1, -82.0, "ANTHROPIC SAN FRANCISCO CA"),
        ("ELEVEN CHICAGO IL", 2, -9.0, "7-ELEVEN"),
    ]


@pytest.mark.parametrize(
    ("descriptions", "pattern"),
    [
        (["COSTCO WHSE #0123 CHICAGO IL"], "COSTCO WHSE"),
        (["7-ELEVEN 00036 CHICAGO IL", "7-ELEVEN 00041 CHICAGO IL"], "7-ELEVEN"),
        (["UBER EATS 8005928996 CA"], "UBER EATS"),
        (["TST* POTBELLY - 0123 CHICAGO IL"], "TST* POTBELLY"),
        (["SQ *BLUE CAFE  Chicago", "SQ *BLUE CAFE EVANSTON"], "SQ *BLUE CAFE"),
        (["00036 METRA MOBILE", "00041 METRA MOBILE"], "MOBILE"),  # no leading word in common
    ],
)
def test_the_suggested_pattern_matches_every_row_it_came_from(descriptions, pattern):
    suggestion = categorize.suggested_pattern(descriptions)
    assert suggestion == pattern
    assert all(suggestion in load.normalize(description) for description in descriptions)


def test_the_rules_file_starts_from_the_example(tmp_path):
    path = categorize.ensure_rules_file(tmp_path / "data" / "rules.csv")
    assert path.read_text() == categorize.RULES_EXAMPLE.read_text()
    assert [rule["category"] for rule in categorize.read_rules(path)][:2] == ["groceries", "groceries"]


def test_going_out_is_a_spending_category_rules_can_use(tmp_path):
    rules = rules_from(tmp_path, "KINGS HEAD PUB,Going Out,contains\n")
    assert rules[0]["category"] == "going out"
    assert categorize.rule_category("KINGS HEAD PUB CHICAGO IL", rules) == "going out"
    assert "going out" in categorize.config.SPENDING_CATEGORIES


def test_hobbies_is_a_spending_category_rules_can_use(tmp_path):
    rules = rules_from(tmp_path, "GUITAR CENTER,hobbies,contains\n")
    assert categorize.rule_category("GUITAR CENTER #123 CHICAGO IL", rules) == "hobbies"
    assert "hobbies" in categorize.config.SPENDING_CATEGORIES
