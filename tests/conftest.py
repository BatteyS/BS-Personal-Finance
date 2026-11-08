"""Every test runs against the made-up accounts in tests/accounts.toml, never the data folder's,
and with only the built-in categories, never the data folder's categories.toml.

Tests that load the scrubbed samples (tests/test_samples.py, which stays private) ask for
`sample_accounts` instead: the samples carry their owner's account names, which
tests/accounts.samples.toml describes. Where that file isn't there, as in the public copy, those
tests skip.
"""

from pathlib import Path

import pytest

import config

HERE = Path(__file__).resolve().parent


def use_accounts(monkeypatch, path: Path) -> None:
    """Point config's account settings at one accounts file for the rest of the test."""
    settings = config.read_accounts(path)
    monkeypatch.setattr(config, "ACCOUNTS", settings["accounts"])
    monkeypatch.setattr(config, "TRANSFER_PAIRS", settings["transfer_pairs"])
    monkeypatch.setattr(config, "PLANNED_CAPS", settings["planned_caps"])
    monkeypatch.setattr(config, "CONTRIBUTIONS_PER_YEAR", settings["contributions_per_year"])
    monkeypatch.setattr(config, "ACCOUNTS_SOURCE", path)


@pytest.fixture(autouse=True)
def test_accounts(monkeypatch, tmp_path):
    use_accounts(monkeypatch, HERE / "accounts.toml")
    monkeypatch.setattr(config, "FORMATS_PATH", tmp_path / "formats.toml")  # no taught layouts unless a test writes some
    # Only the built-in categories unless a test adds some. Fresh lists, since adding one updates
    # them in place.
    monkeypatch.setattr(config, "CATEGORIES_PATH", tmp_path / "categories.toml")
    yours = {kind: [] for kind in config.CATEGORY_KINDS}
    spending, income = config.with_yours(yours)
    monkeypatch.setattr(config, "YOUR_CATEGORIES", yours)
    monkeypatch.setattr(config, "SPENDING_CATEGORIES", spending)
    monkeypatch.setattr(config, "INCOME_CATEGORIES", income)


@pytest.fixture
def sample_accounts(monkeypatch):
    """The accounts the scrubbed samples were exported from."""
    path = HERE / "accounts.samples.toml"
    if not path.exists():
        pytest.skip("the scrubbed samples' accounts aren't in this copy")
    use_accounts(monkeypatch, path)
