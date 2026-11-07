# finance-tracker

Once a month, Fidelity, Bank of America and Amex CSV exports go in and one verdict comes out: are the 401(k), Roth IRA and HSA on pace to max, did money reach brokerage, and did spending stay under income? The README says how it's used. Where this copy has `docs/spec.md`, it is the source of truth for behavior; if code and spec disagree, say so, and don't quietly change either.

## Real financial data stays out of the session

- Never read, print, search or run code over `data/`, `exports/`, any `*.db`, raw bank downloads, or a server's data folder. Anything you run, you read: the loader prints the first lines of a file it rejects, `categorize.py --report` prints merchant totals, `budget.py` prints the budget, and `export_public.py` reads the data folder to know what's personal.
- Work only with scrubbed samples in `samples/` (where this copy has them) and synthetic fixtures written in `tests/`.
- When you run the loader, categorize or the dashboard yourself, point `FINANCE_DATA_DIR` at a temporary directory.
- When a check needs real data, give the user the command to run in their own terminal, and ask for a scrubbed excerpt if something fails.
- No real account numbers, balances, names or merchant totals in code, tests, fixtures, commit messages or docs.
- New samples come from `uv run scrub.py`, which the user runs on the raw files.
- Personal setup lives in the data folder, never in code: `accounts.toml` (accounts, transfer pairs, pay schedule), `budget.toml` and `rules.csv`. The repo ships `*.example.*` versions with made-up values; tests use `tests/accounts.toml`.
- Before anything is shared, `uv run export_public.py --check` must pass. CI runs it on every push.

`.claude/settings.json` denies reads of these paths; the rules above cover what a deny list can't.

## Code

- Python 3.12 with uv: `uv run pytest`, `uv run <script>.py`. Modules sit at the repo root, one file per concern: config, db, load (with formats for taught layouts and ofx for OFX/QFX), categorize, queries, actions, budget, dashboard, plus `export_public.py` for the public copy.
- Readable over clever: plain functions, type hints, docstrings, no classes unless they earn it, no framework beyond Streamlit.
- Amounts are signed from the account's point of view everywhere: money out negative, money in positive. Round sums to cents before comparing or displaying them.
- Logic is tested on synthetic rows; parsers are also tested on the scrubbed samples, in `tests/test_samples.py`, which stays private with them.

## Changes

Changes reach `main` through pull requests. In the private repo, a build phase starts from its prompt in `docs/spec.md` (Phase prompts) and ends with that phase's check, which the user runs. Every PR numbered x5 or x0 is exported to the public copy as `BS-Personal-Finance-v1.<PR number>`: the private `export_due.py` hook says when and makes the export (it reads the data folder, so a session never runs it), and `CLAUDE.local.md` holds the procedure for the rest.
