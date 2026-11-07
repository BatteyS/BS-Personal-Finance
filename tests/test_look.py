"""The page wears seanbattey.com's look, in the computer's light or dark setting (docs/spec.md,
Dashboard: The look)."""

import re
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


def test_the_charts_take_the_palette_from_each_theme():
    """The charts are drawn in placeholders Streamlit swaps for the theme's chart colors, so each
    mode needs all ten of both: categorical (the chart roles) and sequential (the ring's ramp)."""
    with (ROOT / ".streamlit" / "config.toml").open("rb") as file:
        theme = tomllib.load(file)["theme"]
    for mode in ("light", "dark"):
        for option in ("chartCategoricalColors", "chartSequentialColors"):
            colors = theme[mode][option]
            assert len(colors) == 10 and all(color.startswith("#") and len(color) == 7 for color in colors)
        roles = theme[mode]["chartCategoricalColors"]
        assert roles[4] == theme[mode]["backgroundColor"]  # the gaps between slices are the page itself
    assert theme["dark"]["chartCategoricalColors"][0] == theme["dark"]["primaryColor"]  # the site's gold
    assert theme["dark"]["chartSequentialColors"][-1] == theme["dark"]["textColor"]  # ends at the cream ink


def this_month_page(tmp_path, monkeypatch):
    """The page on made-up rows: four kinds of spending in October, and money into brokerage in
    September and back out in October."""
    from contextlib import closing

    from streamlit.testing.v1 import AppTest

    import config
    import db

    path = tmp_path / "finance.db"
    rows = [
        ("groceries", "bofa_checking", "2026-10-03", -500.0, "WHOLE FOODS", "groceries", None),
        ("dining", "bofa_checking", "2026-10-04", -300.0, "CORNER CAFE", "dining", None),
        ("travel", "bofa_checking", "2026-10-05", -100.0, "METRA", "travel", None),
        ("gas", "bofa_checking", "2026-10-06", -50.0, "SHELL", "gas", None),
        ("pay", "bofa_checking", "2026-10-15", 3000.0, "ACME CORP PAYROLL", "income", None),
        ("in_cash", "bofa_checking", "2026-09-10", -1000.0, "FID BKG SVC", "transfer", "in_broker"),
        ("in_broker", "fidelity_brokerage", "2026-09-10", 1000.0, "TRANSFER RECEIVED", "transfer", "in_cash"),
        ("out_broker", "fidelity_brokerage", "2026-10-20", -200.0, "TRANSFER PAID", "transfer", "out_cash"),
        ("out_cash", "bofa_checking", "2026-10-20", 200.0, "FID BKG SVC", "transfer", "out_broker"),
    ]
    with closing(db.connect(path)) as connection, connection:
        connection.executemany(
            "INSERT INTO transactions (id, account, date, amount, description, category, transfer_id, month, imported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test')", [(*row, row[2][:7]) for row in rows])
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "RULES_PATH", tmp_path / "rules.csv")
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(config, "BUDGET_PATH", tmp_path / "budget.toml")
    monkeypatch.setattr(config, "CAPS", {2026: {"401k": 24_000, "roth": 7_200, "hsa": 4_400}})
    page = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=60).run()
    assert not page.exception
    return {key: json_spec(element) for key, element in zip(("pie", "inflow", "trend"), page.tabs[0].get("plotly_chart"))}


def json_spec(element) -> dict:
    import json

    return json.loads(element.proto.spec)


def numbers(array) -> list[float]:
    """A Plotly array as numbers: Plotly writes numeric ones as base64 typed arrays."""
    import base64

    import numpy

    if isinstance(array, dict):
        return numpy.frombuffer(base64.b64decode(array["bdata"]), dtype=array["dtype"]).tolist()
    return list(array)


def test_the_ring_runs_from_darkest_yellow_for_the_largest_to_lightest_off_white(tmp_path, monkeypatch):
    pie = this_month_page(tmp_path, monkeypatch)["pie"]["data"][0]
    colors = dict(zip(pie["labels"], pie["marker"]["colors"]))
    # The ramp's ten steps are #000011 (darkest yellow) to #000020 (lightest off-white).
    assert colors == {"Groceries": "#000011", "Dining": "#000014", "Travel": "#000017", "Gas": "#000020"}
    assert pie["marker"]["line"] == {"color": "#000005", "width": 2}  # a gap of background between slices


def test_brokerage_bars_are_gold_for_money_in_and_a_step_off_the_background_for_money_out(tmp_path, monkeypatch):
    charts = this_month_page(tmp_path, monkeypatch)
    inflow = charts["inflow"]["data"][0]
    assert inflow["type"] == "bar"
    by_month = dict(zip(inflow["x"], zip(numbers(inflow["y"]), inflow["marker"]["color"])))
    assert by_month["September 2026"] == (1000.0, "#000001") and by_month["October 2026"] == (-200.0, "#000003")
    trend = {trace["name"]: trace["marker"]["color"] for trace in charts["trend"]["data"]}
    assert trend["Income"] == "#000002" and trend["Spending"] == "#000004"
    assert {"#000001", "#000003"} <= set(trend["Brokerage inflow"])


STREAMLIT_PAGE = ('<!doctype html><html><head><link rel="shortcut icon" href="./favicon.png" />'
                  '<title>Streamlit</title></head><body></body></html>')


def test_safari_gets_the_app_icon_at_an_address_of_its_own(tmp_path, monkeypatch):
    """Safari shows the favicon Streamlit's own page names as it loads, which Streamlit serves as
    immutable for a year: the icon goes in that file, and the page's link gets the icon's hash, so a
    browser holding Streamlit's logo fetches the new one, and a changed icon gets a new address."""
    import icon

    (tmp_path / "favicon.png").write_bytes(b"streamlit's logo")
    (tmp_path / "index.html").write_text(STREAMLIT_PAGE)
    assert icon.install_favicon(tmp_path)
    assert (tmp_path / "favicon.png").read_bytes() == icon.draw(icon.FAVICON_SIZE)
    page = (tmp_path / "index.html").read_text()
    first = re.search(r'<link rel="shortcut icon" href="\./favicon\.png\?v=([0-9a-f]{12})" />', page)
    assert first and page.count("favicon") == 1
    assert not icon.install_favicon(tmp_path)  # already so: nothing written

    monkeypatch.setattr(icon, "draw", lambda size: b"a new icon")
    assert icon.install_favicon(tmp_path)
    again = re.search(r'favicon\.png\?v=([0-9a-f]{12})"', (tmp_path / "index.html").read_text())
    assert again.group(1) != first.group(1) and (tmp_path / "favicon.png").read_bytes() == b"a new icon"


def test_the_installed_streamlit_is_where_the_icon_goes():
    import icon

    static = icon.streamlit_static()
    assert static and (static / "index.html").exists()
    assert icon.FAVICON_LINK.search((static / "index.html").read_text(encoding="utf-8"))  # Streamlit still names it so


def test_every_launcher_puts_the_icon_in_the_tab_before_the_page_starts():
    launch = (ROOT / "launch.sh").read_text()
    for mode in ("--serve-always", "--serve"):
        block = launch[launch.index(f'if [ "${{1:-}}" = {mode} ]; then'):]
        assert block.index("tab_icon") < block.index("streamlit run")
    assert "icon.py --favicon" in launch
    assert re.search(r"RUN .*icon\.py .*--favicon", (ROOT / "Dockerfile").read_text())


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
