"""Tests for budget.py on a made-up budget whose taxes are worked out by hand. Nothing here is real."""

from pathlib import Path

import pytest

import budget

# Salary 60,000; 401(k) 10,000; HSA limit 4,000 with 1,000 from the employer, so 3,000 by payroll;
# premium 100 a month (1,200 a year). Two federal brackets: 10% from 0 and 20% from 10,000.
#   taxable wages   60,000 - 10,000 - 3,000 - 1,200 = 45,800
#   federal taxable 45,800 - 15,800 = 30,000; tax 30,000 x 10% + 20,000 x 10% = 5,000
#   FICA wages      60,000 - 3,000 - 1,200 = 55,800; Social Security 3,459.60; Medicare 809.10
#   Illinois        (45,800 - 2,800) x 4.95% = 2,128.50
#   total taxes 11,397.20; take-home 45,800 - 11,397.20 = 34,402.80
#   match           min(10,000, 6% of 60,000) x 50% = 1,800
BUDGET = """
[income]
salary = 60_000
retirement_401k = 10_000
roth_ira = 6_000
hdhp_premium_monthly = 100
commuter_benefit_cap_monthly = 50

[hsa]
limit = 4_000
employer = 1_000

[employer_match]
rate = 0.5
cap_share_of_salary = 0.06

[tax]
federal_standard_deduction = 15_800
social_security_rate = 0.062
social_security_wage_base = 200_000
medicare_rate = 0.0145
illinois_rate = 0.0495
illinois_exemption = 2_800
federal_brackets = [[0, 0.10], [10_000, 0.20]]
"""
LINES = [
    ("Taxes", "Federal income tax", "Necessary", 'from = "federal_income_tax"'),
    ("Taxes", "Social Security", "Necessary", 'from = "social_security"'),
    ("Taxes", "Medicare", "Necessary", 'from = "medicare"'),
    ("Taxes", "Illinois income tax", "Necessary", 'from = "illinois_income_tax"'),
    ("Pre-tax savings", "401(k)", "Necessary", 'from = "retirement_401k"'),
    ("Pre-tax savings", "HSA", "Necessary", 'from = "hsa"'),
    ("After-tax savings", "Roth IRA", "Necessary", 'from = "roth_ira"'),
    ("Health", "HDHP premium", "Necessary", 'from = "hdhp_premium"'),
    ("Housing", "Rent", "Necessary", "monthly = 1_500"),
    ("Transportation", "CTA 30-day pass", "Necessary", "monthly = 75"),
    ("Transportation", "Employer commuter benefit", "Necessary", 'from = "commuter_benefit"\ncovers = "CTA 30-day pass"'),
    ("Food", "Dining out", "Discretionary", "monthly = 200"),
    ("Surplus", "Unallocated", "Surplus", 'from = "surplus"'),
]


def budget_file(tmp_path: Path, lines=LINES, inputs: str = BUDGET) -> Path:
    """A budget.toml with the inputs above and the given (group, item, class, amount) lines."""
    blocks = [f'[[line]]\ngroup = "{group}"\nitem = "{item}"\nclass = "{kind}"\n{amount}\n'
              for group, item, kind, amount in lines]
    path = tmp_path / "budget.toml"
    path.write_text(inputs + "\n" + "\n".join(blocks))
    return path


def test_the_tax_calculation_matches_the_hand_worked_numbers(tmp_path):
    calc = budget.tax_calculation(budget.load(budget_file(tmp_path)))
    expected = {
        "hsa_payroll": 3_000, "taxable_wages": 45_800, "federal_taxable_income": 30_000,
        "federal_income_tax": 5_000, "fica_wages": 55_800, "social_security": 3_459.60, "medicare": 809.10,
        "illinois_income_tax": 2_128.50, "total_taxes": 11_397.20, "take_home": 34_402.80, "employer_match": 1_800,
    }
    assert {key: calc[key] for key in expected} == pytest.approx(expected)


@pytest.mark.parametrize(("taxable", "tax"), [(0, 0), (5_000, 500), (10_000, 1_000), (30_000, 5_000)])
def test_federal_brackets_are_progressive(taxable, tax):
    assert budget.federal_income_tax(taxable, [[0, 0.10], [10_000, 0.20]]) == pytest.approx(tax)


def test_the_lines_add_up_to_gross_and_the_surplus_takes_the_rest(tmp_path):
    plan = budget.load(budget_file(tmp_path))
    lines = budget.line_items(plan).set_index("item")
    assert lines.loc["Federal income tax", "monthly"] == pytest.approx(5_000 / 12)
    assert lines.loc["HSA", "monthly"] == 250.0
    assert lines.loc["Employer commuter benefit", "monthly"] == -50.0  # the cap, not the 75 pass
    assert lines["monthly"].sum() == pytest.approx(5_000.00)
    assert lines["annual"].sum() == pytest.approx(60_000.00)
    assert lines.loc["Rent", "share"] == pytest.approx(0.3)


def test_the_summaries(tmp_path):
    plan = budget.load(budget_file(tmp_path))
    lines = budget.line_items(plan)
    split, pay = budget.summary_by_class(plan, lines)
    split, pay = split.set_index("label")["monthly"], pay.set_index("label")["monthly"]
    surplus = lines.set_index("item").loc["Unallocated", "monthly"]
    assert split["Discretionary"] == 200.0
    assert split["Total allocated (equals gross)"] == pytest.approx(5_000.0)
    assert pay["Paycheck take-home (after taxes and pre-tax deductions)"] == pytest.approx(2_866.9)  # 34,402.80 / 12
    assert pay["Your saving (401(k), HSA, Roth IRA and surplus)"] == pytest.approx(10_000 / 12 + 250 + 500 + surplus)
    assert pay["Employer contributions (401(k) match and HSA)"] == pytest.approx(2_800 / 12)
    assert pay["Employer commuter benefit (tax-free, on top of gross)"] == 50.0
    groups = budget.summary_by_group(lines, 5_000)
    assert groups["label"].tolist() == ["Taxes", "Pre-tax savings", "After-tax savings", "Health", "Housing",
                                        "Transportation", "Food", "Surplus", "Total"]
    assert groups.set_index("label").loc["Transportation", "monthly"] == 25.0


def test_an_overspent_budget_has_a_negative_surplus(tmp_path):
    lines = [*LINES[:-1], ("Housing", "Penthouse", "Discretionary", "monthly = 9_000"), LINES[-1]]
    frame = budget.line_items(budget.load(budget_file(tmp_path, lines))).set_index("item")
    assert frame.loc["Unallocated", "monthly"] < 0


@pytest.mark.parametrize(
    ("lines", "inputs", "message"),
    [
        (LINES, BUDGET.replace("roth_ira = 6_000\n", ""), r"\[income\] needs roth_ira"),
        ([*LINES, ("Food", "Coffee", "Necessary", 'monthly = 5\nfrom = "roth_ira"')], BUDGET, "not both"),
        ([*LINES, ("Food", "Coffee", "Necessary", "")], BUDGET, "not both"),
        ([*LINES, ("Food", "Coffee", "Treats", "monthly = 5")], BUDGET, "class must be"),
        ([*LINES, ("Food", "Coffee", "Necessary", 'from = "lottery"')], BUDGET, "from must be"),
        ([*LINES, ("Surplus", "Second surplus", "Surplus", 'from = "surplus"')], BUDGET, "exactly one"),
        ([(g, i, c, a.replace("CTA 30-day pass", "Metra")) for g, i, c, a in LINES], BUDGET, "covers must name"),
        (LINES, BUDGET.replace("[[0, 0.10]", "[[100, 0.10]"), "starting at"),
        (LINES, BUDGET + "\nnot toml at all", "isn't valid TOML"),
    ],
)
def test_problems_in_the_file_are_named(tmp_path, lines, inputs, message):
    with pytest.raises(budget.BudgetError, match=message):
        budget.load(budget_file(tmp_path, lines, inputs))


def test_the_example_budget_loads_and_balances():
    plan = budget.load(budget.BUDGET_EXAMPLE)
    lines = budget.line_items(plan)
    assert lines["annual"].sum() == pytest.approx(plan["income"]["salary"])
    assert set(lines["class"]) == set(budget.CLASSES)


def scripted(answers: dict[str, str]):
    """A prompt that answers each question by the first key found in it, and presses Enter otherwise."""
    asked: list[str] = []

    def prompt(question: str) -> str:
        asked.append(question)
        for key, answer in answers.items():
            if key in question:
                answers.pop(key)  # each scripted answer is used once
                return answer
        return ""

    return prompt, asked


def test_the_written_file_loads_back_the_same(tmp_path):
    plan = budget.load(budget.BUDGET_EXAMPLE)
    budget.save(plan, tmp_path / "budget.toml")
    assert budget.load(tmp_path / "budget.toml") == plan


def test_enter_everywhere_keeps_every_value():
    plan = budget.load(budget.BUDGET_EXAMPLE)
    prompt, asked = scripted({})
    assert budget.interview(plan, prompt, say=lambda *_: None) == plan
    assert any("Rent, monthly $ [1,800]" in question for question in asked)
    assert any("Groceries, monthly (a placeholder in your spreadsheet) $" in question for question in asked)
    assert not any("Federal income tax" in question for question in asked)  # calculated, never asked


def test_answers_replace_the_defaults_and_new_lines_go_before_the_surplus():
    plan = budget.load(budget.BUDGET_EXAMPLE)
    prompt, _ = scripted({
        "Annual salary": "$123,456",
        "match: employer adds": "50%",
        "Social Security rate": "6.2",
        "Rent, monthly": "oops",  # refused, asked again
        "Rent, monthly ": "2,150.50",
        "New line item name": "Pet care",
        "Group": "Personal & household",
        "Necessary or Discretionary": "d",
        "Monthly": "60",
    })
    messages: list[str] = []
    result = budget.interview(plan, prompt, say=messages.append)
    assert result["income"]["salary"] == 123_456
    assert result["employer_match"]["rate"] == 0.5
    assert result["tax"]["social_security_rate"] == pytest.approx(0.062)
    assert {line["item"]: line.get("monthly") for line in result["line"]}["Rent"] == 2_150.5
    assert any("Type a number" in message for message in messages)
    assert [line["item"] for line in result["line"]][-2:] == ["Pet care", "Unallocated → taxable brokerage"]
    assert plan["income"]["salary"] == 100_000  # the starting budget isn't changed


def test_new_brackets_can_be_typed():
    plan = budget.load(budget.BUDGET_EXAMPLE)
    answers = iter(["n", "10", "20,000", "20", ""])
    brackets = budget.ask_brackets(plan["tax"]["federal_brackets"], prompt=lambda _: next(answers), say=lambda *_: None)
    assert brackets == [[0, 0.1], [20_000, 0.2]]


def test_a_budget_that_would_not_load_is_not_saved(tmp_path):
    plan = budget.load(budget.BUDGET_EXAMPLE)
    plan["line"] = [line for line in plan["line"] if line.get("from") != "surplus"]
    with pytest.raises(budget.BudgetError, match="exactly one"):
        budget.save(plan, tmp_path / "budget.toml")
    assert not list(tmp_path.iterdir())


def test_lines_compare_with_a_category_by_name_unless_they_say_otherwise(tmp_path):
    lines = [
        *LINES[:-1],
        ("Food", "Groceries", "Necessary", "monthly = 300"),
        ("Food", "Bagel runs", "Discretionary", 'monthly = 40\ncategory = "dining"'),
        ("Housing", "Storage unit", "Necessary", "monthly = 60"),
        ("Housing", "Rent buffer", "Necessary", 'monthly = 20\ncategory = ""'),
        LINES[-1],
    ]
    plan = budget.load(budget_file(tmp_path, lines))
    by_category = budget.budget_by_category(plan).set_index("category")
    assert by_category.loc["housing", "monthly"] == 1_500  # Rent; the buffer opted out, storage has no default
    assert by_category.loc["groceries", "monthly"] == 300  # matched by its name
    assert by_category.loc["dining", "monthly"] == 40  # the bagels, by their own category
    assert by_category.loc["transit", "lines"] == "CTA 30-day pass, Employer commuter benefit"
    assert by_category.loc["transit", "monthly"] == 25  # 75 less the 50 cap
    assert "Federal income tax" not in " ".join(by_category["lines"])
    assert budget.uncompared_lines(plan) == ["Dining out", "Storage unit", "Rent buffer"]  # "Dining out" has no default


def test_a_category_that_does_not_exist_is_named(tmp_path):
    lines = [*LINES[:-1], ("Food", "Snacks", "Discretionary", 'monthly = 5\ncategory = "snacks"'), LINES[-1]]
    with pytest.raises(budget.BudgetError, match="category must be one of"):
        budget.load(budget_file(tmp_path, lines))


def test_the_interview_asks_a_category_only_for_lines_it_cannot_match(tmp_path):
    lines = [*LINES[:-1], ("Housing", "Storage unit", "Necessary", "monthly = 60"), LINES[-1]]
    plan = budget.load(budget_file(tmp_path, lines))
    prompt, asked = scripted({"Spending category for Storage unit": "nonsense",
                              "Spending category for Storage unit ": "housing"})
    result = budget.interview(plan, prompt, say=lambda *_: None)
    assert sum("Spending category for Storage unit" in question for question in asked) == 2  # refused, then accepted
    assert not any("Spending category for Rent" in question for question in asked)  # matched by name, never asked
    assert {line["item"]: line.get("category") for line in result["line"]}["Storage unit"] == "housing"
    budget.save(result, tmp_path / "saved.toml")
    assert 'category = "housing"' in (tmp_path / "saved.toml").read_text()
