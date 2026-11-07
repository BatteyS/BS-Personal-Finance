"""Tests for export_public.py's choice of files and its personal-data check. Nothing here is real."""

import re

import pytest

import export_public


def test_the_engine_is_shared_and_private_files_are_not():
    assert export_public.is_engine("dashboard.py")
    assert export_public.is_engine("tests/test_load.py")
    assert export_public.is_engine(".github/workflows/tests.yml")
    for private in ("docs/spec.md", "samples/stmt.scrubbed.csv", "tests/accounts.samples.toml",
                    "tests/test_samples.py", "tests/test_export_due.py", "export_due.py", "CLAUDE.local.md",
                    "data/finance.db", "data/budget.toml", "VERSION"):
        assert not export_public.is_engine(private), private


def test_the_tracked_engine_files_include_the_code_and_leave_out_the_rest():
    if not (export_public.ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    files = export_public.engine_files(untracked=True)
    assert {"config.py", "dashboard.py", "README.md", "LICENSE", "accounts.example.toml"} <= set(files)
    assert not [path for path in files if path.startswith(("samples/", "docs/", "data/"))]


def test_an_export_reads_a_commits_tree_with_its_modes():
    if not (export_public.ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    modes = export_public.tree_files("HEAD")
    assert modes["install.sh"] == "100755" and modes["dashboard.py"] == "100644"
    committed = export_public.engine_files(ref="HEAD")
    assert "dashboard.py" in committed and "docs/spec.md" not in committed
    assert export_public.committed_texts(["pyproject.toml"], "HEAD")["pyproject.toml"].startswith("[project]")


def test_emails_and_phone_numbers_are_found_but_safe_ones_pass():
    texts = {
        "a.md": "write to pat@example.com or 74278913+someone@users.noreply.github.com",
        "b.md": "call (312) 555-0100 any time",
        "c.md": "or pat.jones" + "@" + "mail.test",  # built here so the check never finds it in this file
        "d.md": "or 312-867-" + "5309",
    }
    assert export_public.scan(texts, []) == ["c.md:1: an email address", "d.md:1: a phone number"]


def test_personal_terms_match_whole_words_without_regard_to_case():
    terms = [("an account", re.compile(r"(?<![\w$])WIDGETCO(?![\w,.]\d|\w)", re.I))]
    texts = {"x.py": "# from the Widgetco account\n# widgetcoish is fine\n"}
    assert export_public.scan(texts, terms) == ["x.py:1: an account"]


def test_a_term_is_found_inside_snake_case_names_and_across_spaces():
    texts = {"t.py": "def test_from_rainy_day_account():\n# rainy-day\n# rainyday is another word\n"}
    found = export_public.scan(texts, [("an account", export_public.term_pattern("Rainy Day"))])
    assert found == ["t.py:1: an account", "t.py:2: an account"]


def test_the_copyright_line_may_name_its_holder():
    terms = [("your name", re.compile(r"(?<!\w)Pat Jones(?!\w)", re.I))]
    texts = {"LICENSE": "Copyright (c) 2026 Pat Jones\n", "README.md": "by Pat Jones\n"}
    assert export_public.scan(texts, terms) == ["README.md:1: your name"]


def test_an_amount_is_looked_for_in_the_ways_it_might_be_written():
    assert export_public.amount_patterns(2150) == ["2,150", "2150", "2_150"]
    assert "2,150.50" in export_public.amount_patterns(2150.5)
