"""Tests for updates.py on throwaway git repositories: versions, noticing a newer release, and
updating, which refuses to touch a copy with changes of its own."""

import subprocess
from pathlib import Path

import pytest

import updates


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


def test_release_names_are_read_from_commit_subjects():
    assert updates.release_of(["Fix a thing", "BS-Personal-Finance-v1.2", "BS-Personal-Finance-v1.1"]) == ("v1.2", 1)
    assert updates.release_of(["Merge pull request #8"]) == (None, 1)


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
    (tmp_path / "VERSION").write_text("something else")
    assert updates.version(tmp_path) == ""
