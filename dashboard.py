"""The monthly page, laid out wide for a laptop screen: the verdict, where the month went with its
biggest line items, brokerage so far beside a twelve-month trend, and what is uncategorized beside
the add-rule form. It only lays out what queries.py returns; every change goes through actions.py.
See docs/spec.md, Dashboard.

    uv run streamlit run dashboard.py
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

import actions
import budget
import categorize
import config
import formats
import load
import phone
import queries

# Categorical slots 1-3 from the reference palette (dataviz skill). Text never wears these colors;
# the Streamlit theme supplies surfaces and ink in light and dark mode.
INCOME, SPENDING, BROKERAGE = "#2a78d6", "#eb6834", "#1baf7a"
NEUTRAL = "#888780"  # zero line and grid: mid-gray reads on both surfaces
# One fixed color per spending category, so a category keeps its color from month to month. The
# first eight are the reference palette's categorical hues; transit, health, going out and hobbies take four more, and
# uncategorized is the neutral gray. Every slice is labeled, so no category is told by color alone.
CATEGORY_COLORS = {
    "housing": "#2a78d6", "dining": "#eb6834", "groceries": "#1baf7a", "gas": "#eda100",
    "shopping": "#e87ba4", "utilities": "#008300", "subscriptions": "#6250d6", "travel": "#e34948",
    "transit": "#9c6b30", "health": "#3fa7b5", "going out": "#b5338a",
    "hobbies": "#5b7a1e", "uncategorized": NEUTRAL,
}
CHART_CONFIG = {"displayModeBar": False}
RULE_CATEGORIES = [name for name in config.SPENDING_CATEGORIES if name != "uncategorized"] + config.OTHER_CATEGORIES

st.set_page_config(page_title="Finance", page_icon="💵", layout="wide")
# A request Tailscale forwarded from a phone must come from the account phone access was set up
# for (phone.py); one on the Mac itself carries no Tailscale login and always passes.
reason = phone.refusal(st.context.headers, phone.read_access())
if reason:
    st.error(reason)
    st.stop()
# The home-screen icon and full-screen mode, for "Add to Home Screen" on a phone. Streamlit owns
# the page's <head>, so a zero-height component adds the tags to it.
components.html("""<script>
const page = window.parent.document;
for (const [tag, attrs] of [
    ["link", {rel: "apple-touch-icon", href: "./app/static/apple-touch-icon.png"}],
    ["meta", {name: "apple-mobile-web-app-capable", content: "yes"}],
    ["meta", {name: "mobile-web-app-capable", content: "yes"}],
    ["meta", {name: "apple-mobile-web-app-title", content: "Finance"}],
]) {
  const key = Object.keys(attrs)[0];
  if (page.head.querySelector(`${tag}[${key}="${attrs[key]}"]`)) continue;
  const element = page.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) element.setAttribute(name, value);
  page.head.appendChild(element);
}
</script>""", height=0)
# Streamlit leaves a tall empty band above the page; a laptop screen needs that room for the data.
# Its button rows scroll sideways when full, hiding the last categories; wrap them instead.
st.markdown("<style>[data-testid='stMainBlockContainer'] {padding-top: 2.5rem; padding-bottom: 2rem;}"
            " [data-testid='stButtonGroup'] > div {flex-wrap: wrap;}</style>", unsafe_allow_html=True)


def import_panel() -> None:
    """Sidebar: upload exports, import them once on click, show the last import, re-run the rules."""
    with st.sidebar:
        if config.ACCOUNTS_SOURCE == config.ACCOUNTS_EXAMPLE:
            st.warning(f"Using the example accounts. Copy accounts.example.toml to {config.ACCOUNTS_PATH} and make "
                       "it describe your own accounts, then restart the page.")
        st.header("Import")
        round_number = st.session_state.get("upload_round", 0)
        uploads = st.file_uploader("Bank exports (CSV, OFX or QFX)", type=["csv", "ofx", "qfx"],
                                   accept_multiple_files=True, key=f"uploads_{round_number}")
        if st.button("Import", type="primary", disabled=not uploads):
            paths = [actions.save_upload(upload.name, upload.getvalue()) for upload in uploads]
            st.session_state["import_results"] = actions.import_files(paths)
            st.session_state["upload_round"] = round_number + 1  # a fresh key empties the uploader
            st.rerun()
        for result in st.session_state.get("import_results", []):
            if result.get("unknown"):
                st.warning(f"{result['file']}: a layout this tracker doesn't know yet. Teach it on the right.")
            elif "error" in result:
                st.error(f"{result['file']}: {result['error']}")
        last = queries.last_import()
        if not last.empty:
            st.caption(f"Last import: {queries.import_summary(last)}")
        if st.button("Re-run rules"):
            try:
                counts = actions.rerun_rules()
                st.session_state.pop("import_results", None)  # the rules work now: old errors are stale
                st.toast(f"Recategorized {sum(counts.values())} rows")
            except categorize.RulesError as error:
                st.error(str(error))


def account_picker(prefix: str, institution: str) -> dict | None:
    """Pick one of your accounts for a file, or describe a new one. Returns {"key": ...} for an
    existing account, the new account's fields, or None while a new one is missing its name."""
    keys = list(config.ACCOUNTS)
    choice = st.selectbox("Which account is this file for?", [*keys, "new"], key=f"{prefix}_account",
                          index=len(keys), format_func=lambda key: "A new account…" if key == "new"
                          else config.ACCOUNTS[key]["name"])
    if choice != "new":
        return {"key": choice}
    name, kind = st.columns(2)
    account_name = name.text_input("New account's name", key=f"{prefix}_name", placeholder="Chase Freedom")
    kind_key = kind.radio("It's", list(actions.KINDS), format_func=actions.KINDS.get, key=f"{prefix}_kind")
    bank = st.text_input("Institution, as one short word", value=institution, key=f"{prefix}_institution")
    paid_from, hint = None, ""
    if kind_key == "credit":
        payers = [key for key, spec in config.ACCOUNTS.items() if spec["kind"] == "cash"]
        paid_from = st.selectbox("Which account pays this card?", [None, *payers], key=f"{prefix}_payer",
                                 format_func=lambda key: "None of these" if key is None else config.ACCOUNTS[key]["name"])
        if paid_from:
            hint = st.text_input("How the payment reads on that account (optional, helps match it)",
                                 key=f"{prefix}_hint", placeholder="CHASE CREDIT CRD")
    if not account_name.strip() or not bank.strip():
        return None
    return {"name": account_name, "kind": kind_key, "institution": bank, "paid_from": paid_from, "hint": hint}


def chosen_account(account: dict) -> str:
    """The key of the picked account, adding the new one to accounts.toml first."""
    if "key" in account:
        return account["key"]
    return actions.add_account(account["name"], account["kind"], account["institution"],
                               account["paid_from"], account["hint"])


def finish_teaching(result: dict, outcome: list[dict]) -> None:
    """Swap a taught file's old result for its import, and redraw."""
    st.session_state["import_results"] = [
        *(other for other in st.session_state.get("import_results", []) if other is not result), *outcome]
    st.rerun()


def teach_ofx(result: dict) -> None:
    """Match an OFX file's account, by its last four digits, to one of yours."""
    st.markdown(f"An OFX file for an account ending **{result['account_ends'] or '(no number)'}**. "
                "Which of your accounts is it? Only those four digits are kept.")
    words = actions.plain_name(result["file"])
    account = account_picker(f"ofx_{result['file']}", (words[0] if words else "").lower())
    if st.button("Save and import", type="primary", disabled=account is None, key=f"ofx_{result['file']}_save"):
        try:
            finish_teaching(result, actions.teach_ofx(Path(result["path"]), result["account_ends"], chosen_account(account)))
        except (config.AccountsError, formats.FormatsError, load.LoadError) as error:
            st.error(str(error))


def teach_csv(result: dict) -> None:
    """Show a CSV no layout matches, let the columns be named, preview the result, and save it."""
    path, prefix = Path(result["path"]), f"csv_{result['file']}"
    info = actions.read_unknown(path)
    if not info.get("header"):
        st.error("No header row with dates under it was found, so this file can't be taught. Is it a CSV export?")
        return
    header, rows, guess = info["header"], info["rows"], info["suggest"]
    width = len(header)
    st.dataframe(pd.DataFrame([(row + [""] * width)[:width] for row in rows], columns=[c or " " for c in header]),
                 hide_index=True, width="stretch")
    columns = [name for name in (formats.header_key(cell) for cell in header) if name]
    words = actions.plain_name(result["file"])
    left, right = st.columns(2)
    fields = {"name": left.text_input("What to call this layout", value=" ".join(words[:3]) or "New bank",
                                      key=f"{prefix}_layout")}
    fields["date"] = right.selectbox("Date column", columns, key=f"{prefix}_date",
                                     index=columns.index(guess["date"]) if guess["date"] in columns else 0)
    modes = ["amount", "debit and credit"]
    fields["mode"] = st.radio("Amounts are", modes, horizontal=True, key=f"{prefix}_mode", index=modes.index(guess["mode"]),
                              format_func={"amount": "in one column", "debit and credit": "in separate debit and credit columns"}.get)
    left, right = st.columns(2)
    if fields["mode"] == "amount":
        fields["amount"] = left.selectbox("Amount column", columns, key=f"{prefix}_amount",
                                          index=columns.index(guess["amount"]) if guess["amount"] in columns else 0)
        signs = ["negative", "positive"]
        fields["spending_is"] = right.radio("Spending shows as", signs, key=f"{prefix}_spending",
                                            index=signs.index(guess["spending_is"]),
                                            format_func={"negative": "negative, like -12.50", "positive": "positive, like 12.50"}.get)
    else:
        fields["debit"] = left.selectbox("Debit (money out) column", columns, key=f"{prefix}_debit",
                                         index=columns.index(guess["debit"]) if guess["debit"] in columns else 0)
        fields["credit"] = right.selectbox("Credit (money in) column", columns, key=f"{prefix}_credit",
                                           index=columns.index(guess["credit"]) if guess["credit"] in columns else 0)
    fields["description"] = st.multiselect("Description column(s)", columns, key=f"{prefix}_description",
                                           default=[name for name in guess["description"] if name in columns])
    account = account_picker(prefix, (words[0] if words else "").lower())
    ready = bool(fields["description"]) and account is not None and bool(fields["name"].strip())
    if fields["description"]:
        try:
            preview = actions.preview_layout(path, fields, header)
            st.caption("The first transactions as they'll be stored (money out negative):")
            st.dataframe(pd.DataFrame(preview, columns=["date", "description", "amount"]), hide_index=True,
                         width="stretch", column_config={"amount": st.column_config.NumberColumn("amount", format="dollar")})
        except load.LoadError as error:
            st.error(f"That column doesn't hold amounts: {error}")
            ready = False
    if st.button("Save and import", type="primary", disabled=not ready, key=f"{prefix}_save"):
        try:
            finish_teaching(result, actions.teach_csv(path, fields, header, chosen_account(account)))
        except (config.AccountsError, formats.FormatsError, load.LoadError) as error:
            st.error(str(error))


def teach_panel() -> None:
    """Above the tabs, while an import has files no layout matches: a form for each, to teach it."""
    pending = [result for result in st.session_state.get("import_results", []) if result.get("unknown")]
    if not pending:
        return
    st.subheader("Teach this file" if len(pending) == 1 else f"Teach these {len(pending)} files")
    st.caption("Once taught, files with the same layout import by themselves. Layouts are saved to "
               "data/formats.toml, and new accounts to data/accounts.toml.")
    for result in pending:
        with st.expander(result["file"], expanded=True):
            (teach_ofx if result["unknown"] == "ofx" else teach_csv)(result)


def sparkline(frame) -> go.Figure:
    """Brokerage inflow by month this year: a 2px line with markers and exact values on hover."""
    figure = go.Figure(go.Scatter(
        x=[queries.month_label(month) for month in frame["month"]], y=frame["inflow"], mode="lines+markers",
        line={"color": BROKERAGE, "width": 2}, marker={"size": 8},
        hovertemplate="%{x}: %{y:$,.0f}<extra></extra>",
    ))
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False, zeroline=True, zerolinecolor=NEUTRAL)
    figure.update_layout(height=110, margin={"l": 0, "r": 0, "t": 8, "b": 8}, showlegend=False)
    return figure


def slice_order(categories: list[str]) -> list[str]:
    """Largest-first categories reordered big, small, big, small... around the pie, so the small
    slices sit between big ones and their outside labels don't pile up in one place."""
    order, front, back = [], 0, len(categories) - 1
    while front <= back:
        order.append(categories[front])
        if front != back:
            order.append(categories[back])
        front, back = front + 1, back - 1
    return order


def spending_pie(spending, categories: list[str]) -> go.Figure:
    """The month's spending as a ring, one fixed-color slice per category in `categories` order, each
    labeled outside with its name and amount, and the month's total in the middle."""
    amounts = spending.set_index("category")["amount"]
    figure = go.Figure(go.Pie(
        labels=[name.capitalize() for name in categories], values=[amounts[name] for name in categories],
        marker={"colors": [CATEGORY_COLORS.get(name, NEUTRAL) for name in categories]},
        hole=0.6, sort=False, direction="clockwise", rotation=0, automargin=True,
        textposition="outside", texttemplate="<b>%{label}</b><br>%{value:$,.0f}",
        hovertemplate="%{label}: %{value:$,.0f} (%{percent})<extra></extra>",
    ))
    total = sum(amounts[name] for name in categories)
    figure.add_annotation(text=f"<b>{queries.money(total)}</b><br>spent", showarrow=False, font={"size": 18})
    figure.update_layout(height=440, margin={"l": 20, "r": 20, "t": 20, "b": 20}, showlegend=False)
    return figure


def trend_chart(frame) -> go.Figure:
    """Twelve months of income, spending and brokerage inflow, as grouped bars on one dollar axis."""
    labels = [f"{queries.month_label(month)[:3]} {month[2:4]}" for month in frame["month"]]
    figure = go.Figure()
    for name, column, color in (("Income", "income", INCOME), ("Spending", "spending", SPENDING),
                                ("Brokerage inflow", "brokerage", BROKERAGE)):
        figure.add_bar(name=name, x=labels, y=frame[column], marker={"color": color},
                       hovertemplate=f"{name}: %{{y:$,.0f}}<extra></extra>")
    figure.update_yaxes(tickprefix="$", tickformat=",.0f", gridcolor="rgba(136,135,128,0.25)", zerolinecolor=NEUTRAL)
    figure.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08, barcornerradius=4, height=300,
                         margin={"l": 0, "r": 0, "t": 30, "b": 0}, hovermode="x unified",
                         legend={"orientation": "h", "y": 1.12, "x": 0})
    return figure


def add_rule_form(loose) -> None:
    """Pick an uncategorized merchant, adjust the suggested pattern, choose a category, save."""
    merchant = st.selectbox("Add a rule for", loose["merchant"].tolist())
    group = loose.set_index("merchant").loc[merchant]
    with st.form("add_rule"):
        pattern = st.text_input("Pattern", value=group["pattern"], key=f"pattern_{merchant}",
                                help="Matched against the uppercased description. The suggestion is the part every row shares.")
        category = st.selectbox("Category", RULE_CATEGORIES)
        match = st.radio("Match", ["contains", "regex"], horizontal=True)
        if st.form_submit_button("Save rule and re-run"):
            try:
                actions.add_rule(pattern, category, match, examples=group["descriptions"])
                actions.rerun_rules()
                st.session_state.pop("import_results", None)
                st.rerun()
            except categorize.RulesError as error:
                st.error(str(error))



def month_page() -> None:
    """The first tab: the verdict, where the month went, brokerage, the trend and what is uncategorized."""
    available = queries.months()
    if not available:
        st.title("Finance")
        st.info("No data yet. Import your bank exports from the sidebar.")
        return

    choices = list(reversed(available))
    pick, banner = st.columns([1, 5], vertical_alignment="bottom")
    month = pick.selectbox("Month", choices, index=choices.index(queries.default_month()), format_func=queries.month_label)
    inflow_by_month = queries.brokerage_inflow_by_month()
    totals = queries.income_and_spending()
    try:
        verdict = queries.verdict(month, inflow_by_month=inflow_by_month, totals=totals)
    except queries.MissingCapsError as error:
        banner.error(str(error))
        return
    if verdict["green"]:
        banner.success(verdict["text"], icon="✅")
    else:
        banner.error(verdict["text"], icon="🔴")

    st.subheader("Where the month went")
    spending = queries.spending_by_category(month)
    categories = slice_order(spending.loc[spending["amount"] > 0, "category"].tolist())
    if not categories:
        st.caption("No spending recorded this month.")
    else:
        ring, items = st.columns([5, 7], gap="large")
        ring.plotly_chart(spending_pie(spending, categories), width="stretch", config=CHART_CONFIG, key="pie")
        with items:
            # Streamlit doesn't report clicks on pie slices, so a row of buttons picks the category instead.
            amounts = spending.set_index("category")["amount"]
            by_size = sorted(categories, key=lambda name: -amounts[name])
            largest = by_size[0]
            category = st.pills("Show the biggest items in", by_size, default=largest, key=f"category_{month}",
                                format_func=lambda name: f"{name.capitalize()} {queries.money(amounts[name])}") or largest
            items = queries.line_items(month, category)
            # Amount sits next to Date so a long description can never push it out of view.
            st.dataframe(items, hide_index=True, width="stretch", height=35 * (len(items) + 1) + 3,
                         column_order=["date", "amount", "account", "description"], column_config={
                "date": st.column_config.DateColumn("Date", format="MMM D", width="small"),
                "amount": st.column_config.NumberColumn("Amount", format="dollar", width="small"),
                "account": st.column_config.TextColumn("Account", width=130),
                "description": st.column_config.TextColumn("Description"),
            })

    brokerage, months_chart = st.columns([1, 3], gap="large")
    with brokerage:
        st.subheader("Brokerage")
        total, by_month = queries.brokerage_year_to_date(month, inflow_by_month=inflow_by_month)
        st.metric("Inflow, year to date", queries.signed_money(total))
        st.plotly_chart(sparkline(by_month), width="stretch", config=CHART_CONFIG, key="sparkline")
    with months_chart:
        st.subheader("Twelve months")
        st.plotly_chart(trend_chart(queries.trend(month, inflow_by_month=inflow_by_month, totals=totals)),
                        width="stretch", config=CHART_CONFIG, key="trend")

    st.subheader("Uncategorized")
    loose = queries.uncategorized(month)
    if loose.empty:
        st.caption("Nothing uncategorized this month.")
    else:
        merchants, rule = st.columns([3, 2], gap="large")
        merchants.dataframe(loose, hide_index=True, width="stretch", height=360, column_order=["merchant", "count", "total"],
                            column_config={
                                "merchant": "Merchant",
                                "count": "Rows",
                                "total": st.column_config.NumberColumn("Total", format="dollar"),
                            })
        with rule:
            add_rule_form(loose)


def static_table(frame: pd.DataFrame, headers: dict[str, str]) -> None:
    """A plain table with the given columns, renamed to their headers. The budget tabs use these
    instead of interactive tables, which lay themselves out at zero width inside a hidden tab."""
    st.table(frame[list(headers)].rename(columns=headers), hide_index=True, border="horizontal")


def money_table(frame: pd.DataFrame, label: str) -> None:
    """A summary table of label, monthly, annual and share of gross, formatted as the spreadsheet does."""
    shown = frame.assign(monthly=frame["monthly"].map(dollars), annual=frame["annual"].map(dollars),
                         share=frame["share"].map(lambda share: f"{share:.1%}"))
    static_table(shown, {"label": label, "monthly": "Monthly", "annual": "Annual", "share": "% gross"})


def rows(pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """A two-column table of labels and already-formatted values."""
    return pd.DataFrame([{"label": label, "value": shown} for label, shown in pairs])


def dollars(amount: float) -> str:
    """Dollars and cents, as the spreadsheet shows them: "$1,234.56", "-$500.00"."""
    return f"-${-amount:,.2f}" if amount < 0 else f"${amount:,.2f}"


def read_budget() -> tuple[dict | None, bool]:
    """The live budget, or the example when there is none yet (second value True)."""
    path = budget.budget_path()
    example = not path.exists()
    try:
        return budget.load(budget.BUDGET_EXAMPLE if example else path), example
    except budget.BudgetError as error:
        st.error(str(error))
        return None, example


def under_or_over(difference: float) -> str:
    """Budget minus spent in words, so the state never rests on color: "$80 under", "⚠️ $120 over"."""
    if round(difference) < 0:
        return f"⚠️ {queries.money(-difference)} over"
    return f"{queries.money(difference)} under" if round(difference) > 0 else "on budget"


def comparison_page(plan: dict, example: bool) -> None:
    """The Budget vs actual tab: each spending category's monthly budget against what was spent, for
    one month and for the year so far."""
    if example:
        st.info("Fill in your budget first (`uv run budget.py --interview`); comparing your spending with the "
                "made-up example would mean nothing.")
        return
    available = queries.months()
    if not available:
        st.info("No spending yet. Import your bank exports from the sidebar.")
        return
    choices = list(reversed(available))
    pick, summary_month, summary_year = st.columns([1, 2, 2], vertical_alignment="bottom")
    month = pick.selectbox("Month", choices, index=choices.index(queries.default_month()),
                           format_func=queries.month_label, key="compare_month")
    frame, first = queries.budget_vs_actual(plan, month)
    total = frame.iloc[-1]
    span = queries.month_label(month)[:3] if first == month else (
        f"{queries.month_label(first)[:3]}–{queries.month_label(month)[:3]}")
    summary_month.metric(f"{queries.month_label(month)}, against budget", under_or_over(total["difference"]))
    summary_year.metric(f"{span} {month[:4]}, against budget", under_or_over(total["ytd_difference"]))

    shown = frame.assign(
        category=[name.capitalize() if name != "Total" else name for name in frame["category"]],
        budget=frame["budget"].map(queries.money), spent=frame["spent"].map(queries.money),
        difference=frame["difference"].map(under_or_over),
        ytd_budget=frame["ytd_budget"].map(queries.money), ytd_spent=frame["ytd_spent"].map(queries.money),
        ytd_difference=frame["ytd_difference"].map(under_or_over),
    )
    short_month = queries.month_label(month)[:3]
    static_table(shown, {
        "category": "Category", "budget": "Monthly budget", "spent": f"Spent in {short_month}",
        "difference": f"{short_month}, under or over", "ytd_budget": "Budget, year to date",
        "ytd_spent": "Spent, year to date", "ytd_difference": "Year to date, under or over", "lines": "Budget lines",
    })
    st.caption(f"Year to date is {span} {month[:4]}: it starts in {queries.month_label(first)}, the first month with data this year. Spending "
               "is net, so refunds count against it. Each category's budget is the sum of the budget lines beside "
               "it; change which line counts where with `category = \"…\"` on the line in budget.toml.")
    left_out = budget.uncompared_lines(plan)
    if left_out:
        st.caption(f"Not compared with any category: {', '.join(left_out)}.")


def budget_page(plan: dict, example: bool) -> None:
    """The Budget tab: the spreadsheet's summary by class, summary by group and every line."""
    if example:
        st.warning(f"Showing the made-up example. Copy budget.example.toml to {budget.budget_path()} "
                   "and fill in your amounts to see your own budget.")
    lines = budget.line_items(plan)
    gross = plan["income"]["salary"] / 12
    split, pay = budget.summary_by_class(plan, lines)
    surplus = split.loc[split["label"].str.startswith("Surplus"), "monthly"].iloc[0]
    if round(surplus, 2) < 0:
        st.error(f"Over budget: the lines add up to {queries.money(-surplus)} a month more than gross pay.", icon="🔴")
    st.caption("Every dollar of gross pay is assigned to a line; whatever is left is surplus for the taxable "
               "brokerage. Calculated lines (taxes, payroll savings, the premium and the commuter benefit) "
               "come from the Assumptions & taxes tab.")
    by_class, by_group = st.columns(2, gap="large")
    with by_class:
        st.subheader("Summary by class")
        money_table(split, "Class")
        money_table(pay, "Pay and saving")
    with by_group:
        st.subheader("Summary by group")
        money_table(budget.summary_by_group(lines, gross), "Group")
    st.subheader("Line items")
    shown = lines.assign(monthly=lines["monthly"].map(dollars), annual=lines["annual"].map(dollars),
                         share=lines["share"].map(lambda share: f"{share:.1%}"),
                         calculated=lines["calculated"].map(lambda calculated: "calculated" if calculated else ""))
    static_table(shown, {"group": "Group", "item": "Line item", "class": "Class", "monthly": "Monthly",
                         "annual": "Annual", "share": "% gross", "calculated": "Source", "note": "Notes"})


def assumptions_page(plan: dict, example: bool) -> None:
    """The Assumptions & taxes tab: the inputs, the tax parameters and brackets, and the annual
    tax calculation that feeds the Budget tab."""
    if example:
        st.warning("Showing the made-up example's assumptions.")
    calc = budget.tax_calculation(plan)
    income, hsa, tax, match = plan["income"], plan["hsa"], plan["tax"], plan["employer_match"]
    value = {"label": "Input", "value": "Value"}
    inputs, parameters = st.columns(2, gap="large")
    with inputs:
        st.subheader("Income and savings")
        static_table(rows([
            ("Annual salary", dollars(income["salary"])),
            ("401(k) contribution, annual (pre-tax)", dollars(income["retirement_401k"])),
            ("HSA contribution, annual via payroll (the limit minus the employer's)", dollars(calc["hsa_payroll"])),
            ("Roth IRA contribution, annual (after-tax)", dollars(income["roth_ira"])),
            ("HDHP premium, monthly (pre-tax)", dollars(income["hdhp_premium_monthly"])),
            ("Employer commuter benefit cap, monthly", dollars(income["commuter_benefit_cap_monthly"])),
        ]), value)
        st.subheader("Employer money")
        static_table(rows([
            ("401(k) match rate", f"{match['rate']:.0%}"),
            ("Match cap (share of salary)", f"{match['cap_share_of_salary']:.0%}"),
            ("Annual employer match", dollars(calc["employer_match"])),
            ("Monthly employer match", dollars(calc["employer_match"] / 12)),
            ("HSA annual limit (self-only)", dollars(hsa["limit"])),
            ("Employer HSA contribution, annual", dollars(hsa["employer"])),
        ]), value)
    with parameters:
        st.subheader("Tax parameters")
        static_table(rows([
            ("Federal standard deduction (single)", dollars(tax["federal_standard_deduction"])),
            ("Social Security rate", f"{tax['social_security_rate']:.2%}"),
            ("Social Security wage base", dollars(tax["social_security_wage_base"])),
            ("Medicare rate", f"{tax['medicare_rate']:.2%}"),
            ("Illinois income tax rate (flat)", f"{tax['illinois_rate']:.2%}"),
            ("Illinois personal exemption", dollars(tax["illinois_exemption"])),
        ]), value)
        st.subheader("Federal brackets (single)")
        static_table(rows([(f"Taxable income over {dollars(over)}", f"{rate:.0%}") for over, rate in tax["federal_brackets"]]),
              {"label": "Bracket", "value": "Rate"})
    st.subheader("Tax calculation (annual)")
    static_table(rows([
        ("Gross salary", dollars(calc["gross_salary"])),
        ("Less: 401(k)", dollars(-calc["retirement_401k"])),
        ("Less: HSA (payroll)", dollars(-calc["hsa_payroll"])),
        ("Less: HDHP premium", dollars(-calc["hdhp_premium"])),
        ("Taxable wages (federal and Illinois)", dollars(calc["taxable_wages"])),
        ("Federal taxable income (after the standard deduction)", dollars(calc["federal_taxable_income"])),
        ("Federal income tax", dollars(calc["federal_income_tax"])),
        ("FICA wages (the 401(k) doesn't reduce them; payroll HSA and the premium do)", dollars(calc["fica_wages"])),
        ("Social Security tax", dollars(calc["social_security"])),
        ("Medicare tax", dollars(calc["medicare"])),
        ("Illinois taxable income", dollars(calc["illinois_taxable_income"])),
        ("Illinois income tax", dollars(calc["illinois_income_tax"])),
        ("Total taxes", dollars(calc["total_taxes"])),
        ("Effective tax rate on gross", f"{calc['effective_tax_rate']:.1%}"),
        ("Annual take-home pay", dollars(calc["take_home"])),
        ("Monthly take-home pay (compare with a real paycheck)", dollars(calc["take_home"] / 12)),
    ]), {"label": "Step", "value": "Amount"})


import_panel()
teach_panel()
this_month, comparison_tab, budget_tab, assumptions_tab = st.tabs(
    ["This month", "Budget vs actual", "Budget", "Assumptions & taxes"])
with this_month:
    month_page()
plan, example = read_budget()
if plan:
    with comparison_tab:
        comparison_page(plan, example)
    with budget_tab:
        budget_page(plan, example)
    with assumptions_tab:
        assumptions_page(plan, example)
