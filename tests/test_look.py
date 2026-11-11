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
        ("shopping", "bofa_checking", "2026-10-07", -30.0, "TARGET", "shopping", None),
        ("health", "bofa_checking", "2026-10-08", -20.0, "WALGREENS", "health", None),
        ("hobbies", "bofa_checking", "2026-10-09", -10.0, "REI", "hobbies", None),
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


def test_the_ring_fades_fast_from_darkest_yellow_and_the_smallest_share_off_white(tmp_path, monkeypatch):
    pie = this_month_page(tmp_path, monkeypatch)["pie"]["data"][0]
    colors = dict(zip(pie["labels"], pie["marker"]["colors"]))
    # The ramp's ten steps are #000011 (darkest yellow) to #000020 (lightest off-white); each slice
    # down in size is two steps lighter, and from the sixth on they share the last.
    assert colors == {"Groceries": "#000011", "Dining": "#000013", "Travel": "#000015", "Gas": "#000017",
                      "Shopping": "#000019", "Health": "#000020", "Hobbies": "#000020"}
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
    assert (tmp_path / "favicon.png").read_bytes() == icon.draw(icon.FAVICON_SIZE, square=True)
    page = (tmp_path / "index.html").read_text()
    first = re.search(r'<link rel="shortcut icon" href="\./favicon\.png\?v=([0-9a-f]{12})" />', page)
    assert first and page.count("favicon") == 1
    assert not icon.install_favicon(tmp_path)  # already so: nothing written

    monkeypatch.setattr(icon, "draw", lambda size, square=False: b"a new icon")
    assert icon.install_favicon(tmp_path)
    again = re.search(r'favicon\.png\?v=([0-9a-f]{12})"', (tmp_path / "index.html").read_text())
    assert again.group(1) != first.group(1) and (tmp_path / "favicon.png").read_bytes() == b"a new icon"


def test_safari_favorites_find_the_home_screen_icon_in_streamlits_page(tmp_path, monkeypatch):
    """Safari's Favorites show a site's home-screen icon, which Safari finds only in the page's markup
    or at /apple-touch-icon.png, never one a script adds; without one it drew the tab icon in a white
    tile. So it goes beside favicon.png in Streamlit's folder, which serves it at the root, and the
    page names it right after the tab icon, at the icon's hash, and only once however often it's
    installed."""
    import icon

    (tmp_path / "favicon.png").write_bytes(b"streamlit's logo")
    (tmp_path / "index.html").write_text(STREAMLIT_PAGE)
    assert icon.install_favicon(tmp_path)
    assert (tmp_path / "apple-touch-icon.png").read_bytes() == icon.draw(icon.TOUCH_SIZE, square=True)
    assert (f'<link rel="shortcut icon" href="./favicon.png?v={icon.version()}" />'
            f'<link rel="apple-touch-icon" href="./apple-touch-icon.png?v={icon.version()}" />'
            ) in (tmp_path / "index.html").read_text()
    assert not icon.install_favicon(tmp_path)  # already so: nothing written

    monkeypatch.setattr(icon, "draw", lambda size, square=False: b"a new icon, %d px" % size)
    assert icon.install_favicon(tmp_path)
    page = (tmp_path / "index.html").read_text()
    assert page.count('rel="apple-touch-icon"') == 1 and f"apple-touch-icon.png?v={icon.version()}" in page
    assert (tmp_path / "apple-touch-icon.png").read_bytes() == b"a new icon, 180 px"


def test_the_home_screen_icon_has_an_address_of_its_own_too(tmp_path, monkeypatch):
    """Safari can show the home-screen icon the page's script adds as the tab's icon, and keeps each
    address's picture for good: an unversioned apple-touch-icon.png kept Safari's tab on the old green
    ring after the icon turned gold. So its address carries the icon's hash, as the tab icon's does."""
    import streamlit
    import streamlit.components.v1 as components
    from streamlit.testing.v1 import AppTest

    import icon
    import phone

    monkeypatch.setattr(phone, "PHONE", tmp_path / "phone.json")  # no phone access set up
    added = []

    def html(source, **options):
        added.append(source)
        streamlit.stop()  # the rest of the page isn't this test's

    monkeypatch.setattr(components, "html", html)
    page = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=60).run()
    assert not page.exception
    [script] = added
    assert re.fullmatch(r"[0-9a-f]{12}", icon.version())
    assert f'href: "./app/static/apple-touch-icon.png?v={icon.version()}"' in script
    assert "apple-touch-icon.png\"" not in script  # no address without the hash


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


def test_on_a_phone_the_monogram_tells_the_sites_launch_button_where_this_page_is(tmp_path, monkeypatch):
    """seanbattey.com's Launch opens an app's phone address once the phone has it, and the site
    never names anyone's address. So the monogram, on a page reached over Tailscale, carries this
    page's address after "#phone=personal-finance@", which a browser never sends to a server, and
    keeps Streamlit's rel="noreferrer". On the Mac it links home as before."""
    import streamlit
    import streamlit.components.v1 as components
    from streamlit.testing.v1 import AppTest

    import phone

    monkeypatch.setattr(phone, "PHONE", tmp_path / "phone.json")  # no phone access set up
    added = []

    def html(source, **options):
        added.append(source)
        streamlit.stop()  # the rest of the page isn't this test's

    monkeypatch.setattr(components, "html", html)
    page = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=60).run()
    assert not page.exception
    [script] = added
    handler = script[script.index("page.logoInThisTab = "):script.index("page.addEventListener")]
    assert 'link.target = "_self"' in handler
    assert 'here.protocol === "https:" && here.hostname.endsWith(".ts.net")' in handler
    assert '"https://seanbattey.com" + "/#phone=personal-finance@" + encodeURIComponent(here.origin + "/")' in handler
    assert "rel" not in handler  # Streamlit's rel="noreferrer" stays
