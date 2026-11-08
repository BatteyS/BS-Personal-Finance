"""Tests for categories of your own: adding one, where it shows up at once (rules, the add-rule form,
the budget, spending and income), renaming or removing one that nothing uses, hand edits, and
existing data carrying on as it was. All made up; conftest gives each test an empty categories.toml
path and the built-in lists."""

import shutil
from contextlib import closing
from pathlib import Path

import pytest

import actions
import budget
import categorize
import config
import db
import queries

DASHBOARD = str(Path(__file__).resolve().parent.parent / "dashboard.py")
VET_LINE = '\n[[line]]\ngroup = "Lifestyle"\nitem = "Vet"\nclass = "Discretionary"\nmonthly = 40\ncategory = "pets"\n'


@pytest.fixture(autouse=True)
def data_folder(tmp_path, monkeypatch):
    """An empty data folder: no rules.csv, budget.toml or database yet."""
    for name, value in {"DB_PATH": tmp_path / "finance.db", "RULES_PATH": tmp_path / "rules.csv",
                        "BUDGET_PATH": tmp_path / "budget.toml", "EXPORTS_DIR": tmp_path / "exports"}.items():
        monkeypatch.setattr(config, name, value)


def rows(*entries: tuple) -> None:
    """Store (id, account, date, amount, description, category) rows."""
    with closing(db.connect()) as connection, connection:
        connection.executemany(
            "INSERT INTO transactions (id, account, date, amount, description, category, month, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'test')", [(*entry, entry[2][:7]) for entry in entries])


def stored_categories() -> dict[str, str]:
    with closing(db.connect()) as connection:
        return dict(connection.execute("SELECT id, category FROM transactions").fetchall())


def budget_with_vet_line() -> None:
    """The example budget plus a Vet line compared with pets."""
    config.BUDGET_PATH.write_text(budget.BUDGET_EXAMPLE.read_text() + VET_LINE)


# ---------------------------------------------------------------- with none of your own


def test_without_a_categories_file_the_lists_are_the_built_in_ones():
    assert config.read_categories(config.CATEGORIES_PATH) == {"spending": [], "income": []}
    config.reload_categories()
    assert config.SPENDING_CATEGORIES == config.BUILT_IN_SPENDING
    assert config.INCOME_CATEGORIES == ["income"]
    assert config.all_categories() == [*config.BUILT_IN_SPENDING, *config.OTHER_CATEGORIES]


def test_rows_already_categorized_keep_their_categories_when_one_is_added():
    rows(("g1", "amex", "2026-10-03", -210.0, "COSTCO WHSE #0123", "uncategorized"),
         ("p1", "bofa_checking", "2026-10-15", 3000.0, "ACME PAYROLL", "uncategorized"),
         ("u1", "amex", "2026-10-04", -12.0, "CORNER CAFE 00042", "uncategorized"))
    actions.rerun_rules()  # rules.csv starts from the example
    before = (stored_categories(), queries.spending_by_category("2026-10"), queries.income_and_spending())
    actions.add_category("pets", "spending")
    actions.add_category("freelance", "income")
    actions.rerun_rules()
    assert stored_categories() == before[0] == {"g1": "groceries", "p1": "income", "u1": "uncategorized"}
    assert queries.spending_by_category("2026-10").equals(before[1])
    assert queries.income_and_spending().equals(before[2])


# ---------------------------------------------------------------- adding one


def test_a_new_category_is_saved_in_the_data_folder_and_offered_at_once():
    assert actions.add_category("  Pet   Care ", "spending") == "pet care"
    assert actions.add_category("Freelance", "income") == "freelance"
    assert config.read_categories(config.CATEGORIES_PATH) == {"spending": ["pet care"], "income": ["freelance"]}
    assert config.SPENDING_CATEGORIES[-2:] == ["pet care", "uncategorized"]  # uncategorized stays last
    assert config.INCOME_CATEGORIES == ["income", "freelance"]
    assert "freelance" not in config.SPENDING_CATEGORIES
    assert {"pet care", "freelance"} <= set(config.all_categories())
    assert not config.CATEGORIES_PATH.with_name("categories.toml.new").exists()


def test_rules_sort_rows_into_a_new_spending_category_and_spending_counts_it():
    rows(("v1", "amex", "2026-10-03", -80.0, "PAWS VET CLINIC 0042", "uncategorized"),
         ("g1", "amex", "2026-10-04", -50.0, "COSTCO WHSE #0123", "uncategorized"))
    with pytest.raises(categorize.RulesError, match="isn't a category"):
        actions.add_rule("PAWS VET", "pets")
    actions.add_category("pets", "spending")
    actions.add_rule("PAWS VET", "pets", examples=["PAWS VET CLINIC 0042"])
    actions.rerun_rules()
    assert stored_categories() == {"v1": "pets", "g1": "groceries"}
    spending = queries.spending_by_category("2026-10").set_index("category")["amount"]
    assert spending.to_dict() == {"pets": 80.0, "groceries": 50.0}
    assert queries.income_and_spending().loc["2026-10", "spending"] == 130.0


def test_a_new_income_category_counts_as_the_months_income():
    rows(("f1", "bofa_checking", "2026-10-10", 500.0, "UPWORK ESCROW 7781", "uncategorized"),
         ("p1", "bofa_checking", "2026-10-15", 3000.0, "ACME PAYROLL", "uncategorized"),
         ("g1", "amex", "2026-10-04", -50.0, "COSTCO WHSE #0123", "uncategorized"))
    actions.add_category("Freelance", "income")
    actions.add_rule("UPWORK", "freelance")
    actions.rerun_rules()
    assert stored_categories()["f1"] == "freelance"
    assert queries.income_and_spending().loc["2026-10"].to_dict() == {"income": 3500.0, "spending": 50.0}
    assert queries.verdict("2026-10")["income"] == 3500.0
    assert "freelance" not in queries.spending_by_category("2026-10")["category"].tolist()


def test_a_budget_line_can_be_compared_with_a_new_spending_category():
    budget_with_vet_line()
    with pytest.raises(budget.BudgetError, match="category must be one of"):
        budget.load(config.BUDGET_PATH)
    actions.add_category("pets", "spending")
    plan = budget.load(config.BUDGET_PATH)
    planned = budget.budget_by_category(plan).set_index("category")
    assert planned.loc["pets", "monthly"] == 40.0 and planned.loc["pets", "lines"] == "Vet"
    assert budget.ask_category("Vet", prompt=lambda _: "Pets", say=lambda _: None) == "pets"  # the interview offers it

    rows(("v1", "amex", "2026-10-03", -55.0, "PAWS VET", "pets"), ("k1", "amex", "2026-10-05", -20.0, "TOY BOX", "kids"))
    actions.add_category("kids", "spending")
    frame, _ = queries.budget_vs_actual(plan, "2026-10")
    compared = frame.set_index("category")
    assert compared.loc["pets", ["budget", "spent", "difference"]].tolist() == [40.0, 55.0, -15.0]
    assert compared.loc["kids", "budget"] == 0.0 and compared.loc["kids", "lines"] == "(no budget line)"


@pytest.mark.parametrize(("name", "kind", "problem"), [
    ("Groceries", "spending", "already a category"),
    ("income", "income", "already a category"),
    ("Total", "spending", "already a category"),
    ("pets", "spending", "already a category"),  # added just before
    ("PETS", "income", "already a category"),  # one name, one kind
    ("   ", "spending", "type a name"),
    ("cats, dogs", "spending", "can't be a category name"),
    ("9 lives", "spending", "can't be a category name"),
    ("x" * 31, "spending", "can't be a category name"),
    ("savings", "saving", "counts as spending or income"),
])
def test_a_name_that_is_taken_or_unusable_changes_nothing(name, kind, problem):
    actions.add_category("pets", "spending")
    before = config.CATEGORIES_PATH.read_text()
    with pytest.raises(config.CategoriesError, match=problem):
        actions.add_category(name, kind)
    assert config.CATEGORIES_PATH.read_text() == before
    assert config.YOUR_CATEGORIES == {"spending": ["pets"], "income": []}
    assert not config.CATEGORIES_PATH.with_name("categories.toml.new").exists()


# ---------------------------------------------------------------- renaming and removing


def test_one_that_nothing_uses_can_be_renamed_then_removed():
    actions.add_category("pest", "spending")
    actions.add_category("gifts", "income")
    assert actions.category_uses("pest") == ""
    assert actions.rename_category("pest", "Pets") == "pets"
    assert config.YOUR_CATEGORIES == {"spending": ["pets"], "income": ["gifts"]}
    assert "pest" not in config.all_categories() and "pets" in config.SPENDING_CATEGORIES
    actions.remove_category("gifts")
    assert config.read_categories(config.CATEGORIES_PATH) == {"spending": ["pets"], "income": []}
    assert config.INCOME_CATEGORIES == ["income"]


@pytest.mark.parametrize(("use", "said"), [
    (lambda: actions.add_rule("PAWS VET", "pets"), "1 rule"),
    (budget_with_vet_line, "the budget line Vet"),
    (lambda: rows(("v1", "amex", "2026-10-03", -80.0, "PAWS VET", "pets")), "1 transaction"),
])
def test_one_in_use_stays_as_it_is(use, said):
    actions.add_category("pets", "spending")
    use()
    assert actions.category_uses("pets") == said
    for change in (lambda: actions.rename_category("pets", "animals"), lambda: actions.remove_category("pets")):
        with pytest.raises(config.CategoriesError, match=f"pets is used by {said}"):
            change()
    assert config.YOUR_CATEGORIES["spending"] == ["pets"]


def test_every_use_is_named():
    actions.add_category("pets", "spending")
    actions.add_rule("PAWS VET", "pets")
    actions.add_rule("PETCO", "pets")
    budget_with_vet_line()
    rows(("v1", "amex", "2026-10-03", -80.0, "PAWS VET", "pets"))
    assert actions.category_uses("pets") == "2 rules, the budget line Vet and 1 transaction"


def test_the_built_in_categories_stay():
    for change in (lambda: actions.rename_category("groceries", "food"), lambda: actions.remove_category("groceries")):
        with pytest.raises(config.CategoriesError, match="isn't one of your categories"):
            change()
    assert not config.CATEGORIES_PATH.exists()


# ---------------------------------------------------------------- by hand


def test_a_hand_edited_categories_file_counts_from_re_run_rules():
    config.CATEGORIES_PATH.write_text('spending = ["Garden"]\n')
    config.RULES_PATH.write_text("pattern,category,match\nHOME DEPOT,garden,contains\n")
    rows(("h1", "amex", "2026-10-03", -45.0, "THE HOME DEPOT #1950", "uncategorized"))
    actions.rerun_rules()
    assert config.SPENDING_CATEGORIES[-2:] == ["garden", "uncategorized"]
    assert stored_categories() == {"h1": "garden"}


def test_a_broken_categories_file_is_named_and_changes_nothing(tmp_path):
    config.CATEGORIES_PATH.write_text('spending = ["groceries"]\n')
    with pytest.raises(config.CategoriesError, match="categories.toml: 'groceries' is already a category"):
        actions.rerun_rules()
    assert config.SPENDING_CATEGORIES == config.BUILT_IN_SPENDING
    export = tmp_path / "activity.csv"
    export.write_text("Date,Description,Amount\n10/03/2026,COSTCO WHSE #0123,210.00\n")
    results = actions.import_files([export])
    assert results[0]["rows_new"] == 1  # the file is loaded all the same
    assert results[-1]["file"] == "categories.toml" and "files were loaded" in results[-1]["error"]


# ---------------------------------------------------------------- on the page


def test_the_page_adds_a_category_that_the_rule_form_offers_at_once():
    from streamlit.testing.v1 import AppTest

    rows(("v1", "amex", "2026-10-03", -80.0, "PAWS VET CLINIC 0042", "uncategorized"))
    page = AppTest.from_file(DASHBOARD, default_timeout=60).run()
    assert not page.exception
    assert "pets" not in page.selectbox(key="rule_category").options
    [name] = [box for box in page.sidebar.text_input if box.label == "New category"]
    name.input("Pets")
    [add] = [button for button in page.sidebar.button if button.label == "Add category"]
    add.click().run()
    assert not page.exception
    assert "Added pets" in page.sidebar.success[0].value
    assert config.read_categories(config.CATEGORIES_PATH)["spending"] == ["pets"]
    picker = page.selectbox(key="rule_category")
    assert "pets" in picker.options and picker.value == "pets"  # the rule form starts on the new one

    assert page.selectbox(key="category_pick").value == "pets"
    [rename_to] = [box for box in page.sidebar.text_input if box.label == "Rename to"]
    rename_to.input("Animals")
    [rename] = [button for button in page.sidebar.button if button.label == "Rename"]
    rename.click().run()
    assert not page.exception
    assert "Renamed pets to animals." in page.sidebar.success[0].value
    options = page.selectbox(key="rule_category").options
    assert "animals" in options and "pets" not in options


def test_the_page_says_what_keeps_a_category_in_use(tmp_path):
    from streamlit.testing.v1 import AppTest

    shutil.copyfile(categorize.RULES_EXAMPLE, config.RULES_PATH)
    actions.add_category("pets", "spending")
    rows(("v1", "amex", "2026-10-03", -80.0, "PAWS VET CLINIC 0042", "pets"))
    page = AppTest.from_file(DASHBOARD, default_timeout=60).run()
    assert not page.exception
    assert any("Used by 1 transaction" in caption.value for caption in page.sidebar.caption)
    assert not [button for button in page.sidebar.button if button.label in ("Rename", "Remove")]
