"""Editing the budget, its tax assumptions and the pay schedule on the page: budget.with_inputs and
with_lines, the actions that save them, and the Edit switches on the Budget and Assumptions & taxes
tabs. Everything runs on the made-up budget in test_budget.py and tests/accounts.toml; nothing here
is real."""

import shutil
from pathlib import Path

import pytest

import actions
import budget
import config
from test_budget import BUDGET, LINES, budget_file

HERE = Path(__file__).resolve().parent
EXAMPLE = budget.BUDGET_EXAMPLE.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def data_folder(tmp_path, monkeypatch):
    """A fresh data folder: the test accounts as accounts.toml, and no budget.toml yet."""
    shutil.copyfile(HERE / "accounts.toml", tmp_path / "accounts.toml")
    for name, value in {"ACCOUNTS_PATH": tmp_path / "accounts.toml", "ACCOUNTS_SOURCE": tmp_path / "accounts.toml",
                        "DB_PATH": tmp_path / "finance.db", "RULES_PATH": tmp_path / "rules.csv",
                        "EXPORTS_DIR": tmp_path / "exports", "BUDGET_PATH": tmp_path / "budget.toml"}.items():
        monkeypatch.setattr(config, name, value)


def changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    """The lines that differ between two texts of the same length, as (before, after) pairs."""
    old, new = before.splitlines(), after.splitlines()
    assert len(old) == len(new)
    return [(a, b) for a, b in zip(old, new) if a != b]


def rows_of(plan: dict) -> list[dict]:
    """The line editor's rows, untouched, each with the position it shows."""
    return [{**row, "at": at} for at, row in enumerate(budget.line_rows(plan))]


# ---------------------------------------------------------------- the inputs

def test_a_changed_input_is_the_only_change_and_its_comment_stays_in_its_column():
    text = budget.with_inputs(EXAMPLE, {"income": {"salary": 105_000}, "tax": {"illinois_rate": 0.05}})
    assert changed_lines(EXAMPLE, text) == [
        ("salary = 100_000                      # annual, gross", "salary = 105_000                      # annual, gross"),
        ("illinois_rate = 0.0495                # flat; Chicago has no city income tax",
         "illinois_rate = 0.05                  # flat; Chicago has no city income tax"),
    ]


def test_an_input_set_to_what_it_already_is_keeps_how_it_was_written():
    assert budget.with_inputs(EXAMPLE, {"income": {"salary": 100_000.0}, "tax": {"medicare_rate": 0.0145}}) == EXAMPLE


def test_new_brackets_keep_the_list_one_pair_to_a_line_with_its_comment(tmp_path):
    brackets = [[0, 0.1], [10_310, 0.12], [41_870, 0.22], [93_430, 0.24]]  # made up, not the IRS's
    text = budget.with_inputs(EXAMPLE, {"tax": {"federal_brackets": brackets}})
    assert "federal_brackets = [                  # [taxable income over, marginal rate]\n" in text
    assert "    [41_870, 0.22],\n    [93_430, 0.24],\n]\n" in text
    old = [over for over, _ in budget.load(budget.BUDGET_EXAMPLE)["tax"]["federal_brackets"] if over]
    assert old and not [over for over in old if f"{over:_}" in text]  # the example's own list is gone
    path = tmp_path / "budget.toml"
    path.write_text(text)
    assert budget.load(path)["tax"]["federal_brackets"] == brackets


def test_the_editor_takes_percents_as_percents_and_sorts_the_brackets():
    answers = {("income", "salary"): 72_000.0, ("income", "hdhp_premium_monthly"): 47.5,
               ("tax", "social_security_rate"): 6.2, ("employer_match", "rate"): 50.0}
    brackets = [(20_000.0, 22.0), (float("nan"), float("nan")), (0.0, 10.0)]  # an added row, an empty one
    inputs = budget.typed_inputs(answers, brackets)
    assert inputs["income"] == {"salary": 72_000, "hdhp_premium_monthly": 47.5}
    assert isinstance(inputs["income"]["salary"], int)  # written 72_000, not 72000.0
    assert inputs["tax"]["social_security_rate"] == 0.062  # exactly, without float noise
    assert inputs["employer_match"]["rate"] == 0.5
    assert inputs["tax"]["federal_brackets"] == [[0, 0.1], [20_000, 0.22]]


def test_a_bracket_with_one_number_is_refused():
    with pytest.raises(budget.BudgetError, match="needs both numbers"):
        budget.typed_inputs({}, [(0.0, 10.0), (20_000.0, None)])


def test_only_the_inputs_that_changed_are_kept(tmp_path):
    plan = budget.load(budget_file(tmp_path))
    answers = {(section, key): plan[section][key] * (100 if kind == "percent" else 1)
               for section, key, _, kind in budget.QUESTIONS}
    brackets = [(over, rate * 100) for over, rate in plan["tax"]["federal_brackets"]]
    assert budget.changed_inputs(plan, budget.typed_inputs(answers, brackets)) == {}
    answers["income", "salary"] = 72_000.0
    assert budget.changed_inputs(plan, budget.typed_inputs(answers, brackets)) == {"income": {"salary": 72_000}}


# ---------------------------------------------------------------- the lines

def test_an_untouched_line_editor_gives_back_the_file_unchanged():
    plan = budget.load(budget.BUDGET_EXAMPLE)
    blank = {"group": None, "item": None, "class": "Discretionary", "monthly": float("nan"), "category": None,
             "note": None}  # a row added by mistake and left as it came
    lines = budget.edited_lines(plan["line"], [*rows_of(plan), blank])
    assert [line for _, line in lines] == plan["line"]
    assert budget.with_lines(EXAMPLE, lines) == EXAMPLE


def test_edited_lines_are_written_in_place_and_new_ones_go_before_the_surplus(tmp_path):
    path = budget_file(tmp_path)
    plan = budget.load(path)
    rows = rows_of(plan)
    by_item = {row["item"]: row for row in rows}
    by_item["Rent"]["monthly"] = 1_650.0
    by_item["CTA 30-day pass"]["item"] = "Ventra pass"  # the commuter benefit covers it by name
    by_item["Dining out"]["category"] = "dining"
    rows.remove(by_item["Social Security"])
    rows.append({"group": "Lifestyle", "item": "Pet care", "class": "Discretionary", "monthly": 60.0,
                 "calculated": None, "category": None, "note": "New"})
    path.write_text(budget.with_lines(path.read_text(), budget.edited_lines(plan["line"], rows)))
    saved = {line["item"]: line for line in budget.load(path)["line"]}
    assert list(saved)[-2:] == ["Pet care", "Unallocated"]
    assert saved["Pet care"] == {"group": "Lifestyle", "item": "Pet care", "class": "Discretionary", "monthly": 60,
                                 "note": "New"}  # no category by its name, and none chosen: not compared
    assert saved["Rent"]["monthly"] == 1_650 and "category" not in saved["Rent"]  # housing, by its name
    assert saved["Ventra pass"]["category"] == "transit"  # the renamed pass keeps the category it had by name
    assert saved["Employer commuter benefit"]["covers"] == "Ventra pass"
    assert saved["Dining out"]["category"] == "dining"
    assert "Social Security" not in saved
    totals = budget.line_items(budget.load(path)).set_index("item")["monthly"]
    assert totals.sum() == pytest.approx(5_000.0)  # the surplus still takes the rest


def test_a_table_keeps_its_comments_and_keys_this_code_does_not_know(tmp_path):
    text = budget_file(tmp_path).read_text().replace(
        'item = "Rent"\n', 'item = "Rent"\n# the landlord raises it in June\nlandlord = "Acme"\n')
    path = tmp_path / "budget.toml"
    path.write_text(text)
    plan = budget.load(path)
    rows = rows_of(plan)
    next(row for row in rows if row["item"] == "Rent")["monthly"] = 1_650.0
    edited = budget.with_lines(text, budget.edited_lines(plan["line"], rows))
    assert changed_lines(text, edited) == [("monthly = 1_500", "monthly = 1_650")]


@pytest.mark.parametrize(("change", "message"), [
    (lambda rows: rows[0].update(monthly=400.0), "Federal income tax is calculated"),
    (lambda rows: rows[8].update(monthly=None), "Rent: type its monthly amount"),
    (lambda rows: rows[8].update(monthly=-5.0), "zero or more"),
    (lambda rows: rows[8].update(item="  "), "Row 9: each line needs a group and a line item"),
    (lambda rows: rows.append({"group": "Food", "item": "Snacks", "class": None, "monthly": 5.0}), "class must be"),
    (lambda rows: rows.append({"group": "Lifestyle", "item": None, "class": "Discretionary", "monthly": 60.0}),
     "Row 14: each line needs a group and a line item"),
    (lambda rows: rows.pop(), "surplus line can't be removed"),
])
def test_rows_that_cannot_be_lines_are_named(tmp_path, change, message):
    plan = budget.load(budget_file(tmp_path))
    rows = rows_of(plan)
    change(rows)
    with pytest.raises(budget.BudgetError, match=message):
        budget.edited_lines(plan["line"], rows)


# ---------------------------------------------------------------- saving to the data folder

def test_a_first_save_starts_your_budget_from_the_example():
    assert actions.save_budget_inputs({"income": {"salary": 90_000}})
    text = config.BUDGET_PATH.read_text()
    assert text.startswith(actions.FIRST_BUDGET + "# Lines mirror a budget spreadsheet")
    assert "Example budget" not in text
    assert budget.load(config.BUDGET_PATH)["income"]["salary"] == 90_000


def test_nothing_changed_writes_nothing():
    assert not actions.save_budget_inputs({})
    plan = budget.load(budget.BUDGET_EXAMPLE)
    assert not actions.save_budget_lines(budget.edited_lines(plan["line"], rows_of(plan)), plan["line"])
    assert not config.BUDGET_PATH.exists()


def test_a_budget_that_would_not_load_is_not_written(tmp_path):
    path = budget_file(tmp_path)
    before = path.read_text()
    with pytest.raises(budget.BudgetError, match="employer can't be more than the limit"):
        actions.save_budget_inputs({"hsa": {"employer": 9_000}})
    assert path.read_text() == before and not list(tmp_path.glob("*.new"))


def test_lines_changed_by_hand_since_the_page_showed_them_are_not_overwritten(tmp_path):
    path = budget_file(tmp_path)
    plan = budget.load(path)
    path.write_text(path.read_text().replace("monthly = 200", "monthly = 250"))  # edited by hand meanwhile
    rows = rows_of(plan)
    rows[8]["monthly"] = 1_650.0
    with pytest.raises(budget.BudgetError, match="changed after the page showed them"):
        actions.save_budget_lines(budget.edited_lines(plan["line"], rows), plan["line"])
    assert "monthly = 250" in path.read_text() and "1_650" not in path.read_text()


def test_the_pay_schedule_is_saved_with_your_accounts_and_read_again_in_place():
    per_year = config.CONTRIBUTIONS_PER_YEAR  # the dict the queries read
    before = config.ACCOUNTS_PATH.read_text()
    assert actions.save_pay_schedule({"401k": 24, "hsa": 24, "roth": 12}, ["hsa"])
    assert changed_lines(before, config.ACCOUNTS_PATH.read_text()) == [
        ("401k = 26", "401k = 24"), ("hsa = 26", "hsa = 24"), ("caps = []", 'caps = ["hsa"]')]
    assert per_year == {"401k": 24, "hsa": 24, "roth": 12} and config.CONTRIBUTIONS_PER_YEAR is per_year
    assert config.PLANNED_CAPS == {"hsa"}
    assert not actions.save_pay_schedule({"401k": 24, "hsa": 24, "roth": 12}, ["hsa"])


def test_on_the_example_accounts_there_is_no_pay_schedule_of_yours_to_save(monkeypatch):
    monkeypatch.setattr(config, "ACCOUNTS_SOURCE", config.ACCOUNTS_EXAMPLE)
    with pytest.raises(config.AccountsError, match="kept with your accounts"):
        actions.save_pay_schedule({"401k": 24, "hsa": 24, "roth": 12}, [])


# ---------------------------------------------------------------- on the page

def open_page():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(HERE.parent / "dashboard.py"), default_timeout=60).run()


def table_value(page, tab: int, label: str) -> str:
    """The value beside a label in one of a tab's tables: the second column of the row whose first
    column is the label."""
    for table in page.tabs[tab].table:
        frame = table.value
        match = frame[frame.iloc[:, 0] == label]
        if len(match):
            return match.iloc[0, 1]
    raise AssertionError(f"no {label!r} on tab {tab}")


TAKE_HOME = "Monthly take-home pay (compare with a real paycheck)"


def test_saving_an_assumption_updates_every_tab_at_once(tmp_path):
    """Salary 72,000 in place of 60,000: taxable wages 57,800, federal 7,400, Social Security
    4,203.60, Medicare 983.10, Illinois 2,722.50, so take-home is 42,490.80 a year, 3,540.90 a month."""
    budget_file(tmp_path)
    page = open_page()
    assert not page.exception
    assert table_value(page, 4, TAKE_HOME) == "$2,866.90"
    page.toggle(key="edit_assumptions").set_value(True).run()
    assert page.number_input(key="edit_income_salary").value == 60_000
    assert page.number_input(key="edit_tax_social_security_rate").value == pytest.approx(6.2)
    page.number_input(key="edit_income_salary").set_value(72_000.0)
    page.number_input(key="edit_per_year_401k").set_value(24)
    page.button(key="save_assumptions").click().run()
    assert not page.exception and not page.error
    assert not page.toggle(key="edit_assumptions").value  # back to the tables
    assert [toast.value for toast in page.toast] == ["Saved to budget.toml and accounts.toml."]
    assert table_value(page, 4, TAKE_HOME) == "$3,540.90"
    assert table_value(page, 4, "401(k) contributions a year") == "24"
    assert table_value(page, 3, "Gross income") == "$6,000.00"  # the Budget tab, on the same run
    assert "salary = 72_000\n" in config.BUDGET_PATH.read_text()
    assert config.CONTRIBUTIONS_PER_YEAR["401k"] == 24


def test_a_save_with_nothing_changed_says_so(tmp_path):
    path = budget_file(tmp_path)
    before = path.read_text()
    page = open_page()
    page.toggle(key="edit_assumptions").set_value(True).run()
    page.button(key="save_assumptions").click().run()
    page.toggle(key="edit_lines").set_value(True).run()
    assert not page.exception
    page.button(key="save_lines").click().run()
    assert not page.exception and not page.error
    assert [toast.value for toast in page.toast] == ["Nothing changed."]
    assert path.read_text() == before


def test_a_refused_save_keeps_the_editor_open_and_names_the_problem(tmp_path):
    budget_file(tmp_path)
    page = open_page()
    page.toggle(key="edit_assumptions").set_value(True).run()
    page.number_input(key="edit_hsa_employer").set_value(9_000.0)
    page.button(key="save_assumptions").click().run()
    assert "employer can't be more than the limit" in page.error[0].value
    assert page.toggle(key="edit_assumptions").value
    assert "employer = 1_000" in config.BUDGET_PATH.read_text()


def test_on_the_example_accounts_the_pay_schedule_shows_but_is_not_yours_to_edit(monkeypatch):
    monkeypatch.setattr(config, "ACCOUNTS_SOURCE", config.ACCOUNTS_EXAMPLE)
    page = open_page()
    page.toggle(key="edit_assumptions").set_value(True).run()
    assert not page.exception
    assert page.number_input(key="edit_per_year_401k").disabled
    assert page.number_input(key="edit_income_salary").value == 100_000  # the example budget, until you save
