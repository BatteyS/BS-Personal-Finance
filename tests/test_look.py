"""The page wears seanbattey.com's look, in the computer's light or dark setting (docs/spec.md,
Dashboard: The look)."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_theme_comes_in_light_and_dark_with_the_colors_the_page_reads():
    """The page follows the computer's setting, so both themes exist, and each holds the ink and
    the gold that dashboard.py reads for what a theme can't style."""
    with (ROOT / ".streamlit" / "config.toml").open("rb") as file:
        theme = tomllib.load(file)["theme"]
    for mode in ("light", "dark"):
        assert theme[mode]["base"] == mode
        assert all(theme[mode][option].startswith("#") for option in ("textColor", "primaryColor"))
