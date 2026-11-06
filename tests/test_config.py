"""Tests for reading accounts files. Nothing here is real data."""

from pathlib import Path

import pytest

import config

HERE = Path(__file__).resolve().parent
GOOD = """
[contributions_per_year]
401k = 24
hsa = 24
roth = 12

[planned]
caps = ["hsa"]

[[account]]
key = "cash"
name = "Cash Management"
institution = "fidelity"
kind = "cash"
fidelity_label = "CASH MANAGEMENT"

[[account]]
key = "card"
institution = "amex"
kind = "credit"

[[transfer]]
accounts = ["cash", "card"]
hints = { card = "PAYMENT" }
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "accounts.toml"
    path.write_text(text)
    return path


def test_a_good_file_reads_into_the_shapes_the_code_uses(tmp_path):
    settings = config.read_accounts(write(tmp_path, GOOD))
    assert settings["accounts"]["cash"] == {"institution": "fidelity", "kind": "cash", "cap_key": None,
                                            "name": "Cash Management", "fidelity_label": "CASH MANAGEMENT"}
    assert settings["accounts"]["card"]["name"] == "card"  # the key, when no name is given
    assert settings["transfer_pairs"] == [{"accounts": ("cash", "card"), "hints": {"card": "PAYMENT"}}]
    assert settings["planned_caps"] == {"hsa"}
    assert settings["contributions_per_year"] == {"401k": 24, "hsa": 24, "roth": 12}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('institution = "amex"', 'institution = "Big Bank!"'), "institution must be a short lowercase name"),
        (('kind = "credit"', 'kind = "loan"'), "kind must be one of"),
        (('kind = "credit"', 'kind = "credit"\ncap = "ira"'), "cap must be one of"),
        (('fidelity_label = "CASH MANAGEMENT"', ''), "needs a fidelity_label"),
        (('key = "card"', 'key = "cash"'), "a key of its own"),
        (('accounts = ["cash", "card"]', 'accounts = ["cash", "savings"]'), "two different accounts"),
        (('hints = { card = "PAYMENT" }', 'hints = { bofa = "PAYMENT" }'), "a hint must belong"),
        (('caps = ["hsa"]', 'caps = ["pension"]'), r"\[planned\] caps"),
        (('roth = 12', ''), "contributions_per_year"),
        (('roth = 12', 'roth = 0'), "contributions_per_year"),
        (('[[account]]', '[[account'), "isn't valid TOML"),
    ],
)
def test_problems_are_named(tmp_path, change, message):
    old, new = change
    with pytest.raises(config.AccountsError, match=message):
        config.read_accounts(write(tmp_path, GOOD.replace(old, new, 1)))


def test_the_shipped_files_load():
    for path in (config.ACCOUNTS_EXAMPLE, HERE / "accounts.toml", HERE / "accounts.samples.toml"):
        if path.exists():  # the samples' accounts are only in the private copy
            assert config.read_accounts(path)["accounts"], path


def test_the_tests_never_use_the_data_folders_accounts():
    assert config.ACCOUNTS_SOURCE == HERE / "accounts.toml"
    assert config.ACCOUNTS["fidelity_cash"]["name"] == "Cash Management"
