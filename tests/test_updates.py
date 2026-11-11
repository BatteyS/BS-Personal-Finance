"""Tests for updates.py on throwaway git repositories: versions, noticing a newer release, and
updating, which refuses to touch a copy with changes of its own; and the version at the top of the
page."""

import subprocess
from pathlib import Path

import pytest

import updates

ROOT = Path(__file__).resolve().parent.parent


def run(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def commit(root: Path, name: str, subject: str) -> None:
    (root / name).write_text(subject)
    run(root, "add", name)
    run(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", subject)


@pytest.fixture
def published(tmp_path):
    """A public repository with release v1.2, and a copy cloned from it."""
    public = tmp_path / "public"
    public.mkdir()
    run(public, "init", "-q", "-b", "main")
    commit(public, "a.txt", "BS-Personal-Finance-v1.1")
    commit(public, "b.txt", "BS-Personal-Finance-v1.2")
    run(tmp_path, "clone", "-q", str(public), "copy")
    return public, tmp_path / "copy"


@pytest.fixture
def private(tmp_path):
    """A working repository like the private one: pull requests merged into main, and no release."""
    repo = tmp_path / "private"
    repo.mkdir()
    run(repo, "init", "-q", "-b", "main")
    commit(repo, "a.txt", "Start")
    for number, branch in ((41, "first"), (42, "second")):
        run(repo, "switch", "-q", "-c", branch)
        commit(repo, f"{branch}.txt", f"The {branch} change")
        run(repo, "switch", "-q", "main")
        run(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "merge", "-q", "--no-ff",
            "-m", f"Merge pull request #{number} from someone/{branch}", branch)
    return repo


def short_head(root: Path) -> str:
    return run(root, "rev-parse", "--short", "HEAD").strip()


def test_release_names_are_read_from_commit_subjects():
    assert updates.release_of(["Fix a thing", "BS-Personal-Finance-v1.2", "BS-Personal-Finance-v1.1"]) == ("v1.2", 1)
    assert updates.release_of(["Merge pull request #8"]) == (None, 1)


def test_merged_pull_requests_are_read_from_commit_subjects():
    subjects = ["Fix a thing", "Merge pull request #42 from someone/second", "Merge pull request #41 from someone/first"]
    assert updates.merged_of(subjects) == "#42"
    assert updates.merged_of(["BS-Personal-Finance-v1.2", "Merge branch 'main' into mine"]) is None


def test_the_version_is_the_latest_release_plus_commits_since(published):
    public, copy = published
    assert updates.version(copy) == "v1.2"
    commit(copy, "mine.txt", "A change of my own")
    assert updates.version(copy) == "v1.2 +1"


def test_a_newer_release_is_noticed_and_updated_to(published):
    public, copy = published
    assert updates.check(copy) == {"behind": 0, "latest": "v1.2"}
    commit(public, "c.txt", "BS-Personal-Finance-v1.3")
    assert updates.check(copy) == {"behind": 1, "latest": "v1.3"}
    changed, message = updates.update(copy)
    assert changed and "v1.2 to v1.3" in message
    assert (copy / "c.txt").exists() and updates.version(copy) == "v1.3"
    assert updates.update(copy) == (False, "Already up to date (v1.3).")


def test_a_copy_with_changes_of_its_own_is_left_alone(published):
    public, copy = published
    commit(public, "c.txt", "BS-Personal-Finance-v1.3")
    (copy / "a.txt").write_text("edited")
    changed, message = updates.update(copy)
    assert not changed and "changes of yours" in message
    assert not (copy / "c.txt").exists()


def test_a_copy_off_main_is_neither_checked_nor_updated(published):
    public, copy = published
    run(copy, "switch", "-q", "-c", "experiment")
    assert updates.check(copy) is None
    assert updates.update(copy)[0] is False


def test_outside_a_git_checkout_there_is_no_version(tmp_path):
    assert updates.version(tmp_path) == ""
    assert updates.check(tmp_path) is None


def test_a_version_file_names_the_release_where_there_is_no_git(tmp_path):
    (tmp_path / "VERSION").write_text("BS-Personal-Finance-v1.20\n")
    assert updates.version(tmp_path) == "v1.20"
    assert updates.describe(tmp_path) == {"version": "v1.20", "commit": "", "date": "", "edited": False}
    (tmp_path / "VERSION").write_text("something else")
    assert updates.version(tmp_path) == ""


def test_the_version_names_its_commit_and_the_day_it_was_made(published):
    public, copy = published
    commit_id, day = run(copy, "log", "-1", "--format=%h %cs").split()
    assert updates.describe(copy) == {"version": "v1.2", "commit": commit_id, "date": day, "edited": False}


def test_a_copy_whose_files_differ_from_its_commit_says_so(published):
    """Edited, as update() would see it: a change to a tracked file, never a file git doesn't track."""
    public, copy = published
    (copy / "notes.txt").write_text("a file of my own")
    assert updates.version(copy) == "v1.2"
    (copy / "a.txt").write_text("edited")
    assert updates.version(copy) == "v1.2 · edited"
    assert updates.describe(copy)["edited"]
    commit(copy, "a.txt", "My edit")
    assert updates.version(copy) == "v1.2 +1"


def test_the_private_repository_goes_by_its_latest_pull_request_and_commit(private):
    """No release is ever committed there, and one pull request spans many commits, so the number
    comes with the commit."""
    assert updates.version(private) == f"#42 · {short_head(private)}"
    run(private, "switch", "-q", "-c", "third")
    commit(private, "c.txt", "A change not merged yet")
    assert updates.version(private) == f"#42 · {short_head(private)}"
    (private / "c.txt").write_text("edited")
    assert updates.version(private) == f"#42 · {short_head(private)} · edited"


def test_a_history_with_no_release_or_pull_request_is_its_commit(tmp_path):
    run(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "a.txt", "Start")
    assert updates.version(tmp_path) == short_head(tmp_path)


def test_the_private_repository_hears_of_its_next_pull_request(private, tmp_path):
    run(tmp_path, "clone", "-q", str(private), "mine")
    mine = tmp_path / "mine"
    assert updates.check(mine) == {"behind": 0, "latest": "#42"}
    commit(private, "d.txt", "Merge pull request #43 from someone/third")
    assert updates.check(mine) == {"behind": 1, "latest": "#43"}
    changed, message = updates.update(mine)
    assert changed and f"to #43 · {short_head(mine)}" in message


# ---------------------------------------------------------------- the page

MADE_UP_VERSION = {"version": "#42 · d037abf · edited", "commit": "d037abf", "date": "2026-11-08", "edited": True}


def open_page(tmp_path, monkeypatch, found=None):
    """The page on an empty data folder, with this copy's version and what GitHub says made up."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    import config
    import phone

    for name, value in {"DB_PATH": tmp_path / "finance.db", "RULES_PATH": tmp_path / "rules.csv",
                        "EXPORTS_DIR": tmp_path / "exports", "BUDGET_PATH": tmp_path / "budget.toml"}.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(phone, "PHONE", tmp_path / "phone.json")  # no phone access set up
    monkeypatch.setattr(updates, "describe", lambda root=ROOT: MADE_UP_VERSION)
    monkeypatch.setattr(updates, "check", lambda root=ROOT: found)
    st.cache_data.clear()  # the page keeps the version and GitHub's answer a while
    page = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=60).run()
    assert not page.exception
    return page


def version_lines(block) -> list[str]:
    return [element.value for element in block.markdown if 'class="pf-version"' in element.value]


def test_the_sidebar_names_the_version_beside_the_monogram_in_both_modes(tmp_path, monkeypatch):
    """The sidebar's first element, which the page's style lifts out of the sidebar's flow into its
    header, to the right of the monogram; hovering names the app, the commit and its day. The page
    itself doesn't repeat it."""
    page = open_page(tmp_path, monkeypatch)
    shown = ('<div class="pf-version" title="Personal Finance, commit d037abf of Nov 8, 2026, with files edited since">'
             "#42 · d037abf · edited</div>")
    assert page.sidebar.markdown[0].value == shown and version_lines(page.sidebar) == [shown]
    assert version_lines(page.main) == []
    [style] = [element.value for element in page.main.markdown if "<style>" in element.value]
    assert ":has(.pf-version) {position: absolute; top: 0;" in style
    page.segmented_control[0].set_value("Finance").run()
    assert not page.exception and version_lines(page.sidebar) == [shown]


def test_a_newer_version_is_offered_beside_this_ones(tmp_path, monkeypatch):
    page = open_page(tmp_path, monkeypatch, found={"behind": 1, "latest": "#43"})
    assert [note.value for note in page.sidebar.info if "is out" in note.value] == ["#43 is out; this copy is #42 · d037abf · edited."]
    assert [button.label for button in page.sidebar.button if button.label == "Update"] == ["Update"]
