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


def test_a_click_on_the_monogram_opens_seanbattey_com(tmp_path, monkeypatch):
    """The SB monogram in the top-left corner links to the site. AppTest doesn't keep the logo, so
    the test watches st.logo itself, lets Streamlit check the link, and stops the page there."""
    import streamlit
    from streamlit.testing.v1 import AppTest

    import phone

    monkeypatch.setattr(phone, "PHONE", tmp_path / "phone.json")  # no phone access set up
    logos = []
    streamlit_logo = streamlit.logo

    def logo(image, **options):
        logos.append(options)
        streamlit_logo(image, **options)  # raises on a link Streamlit won't take
        streamlit.stop()  # the rest of the page isn't this test's

    monkeypatch.setattr(streamlit, "logo", logo)
    page = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=60).run()
    assert not page.exception
    assert [options.get("link") for options in logos] == ["https://seanbattey.com"]
