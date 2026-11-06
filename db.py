"""The SQLite database: its tables, opening it, and keeping the accounts table in step with config.

See docs/spec.md, Data model.
"""

import sqlite3
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    description TEXT NOT NULL,
    category TEXT,
    transfer_id TEXT,
    month TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS transactions_by_month ON transactions (month);
CREATE INDEX IF NOT EXISTS transactions_by_account_date ON transactions (account, date);

CREATE TABLE IF NOT EXISTS accounts (
    name TEXT PRIMARY KEY,
    institution TEXT NOT NULL,
    kind TEXT NOT NULL,
    cap_key TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    file TEXT NOT NULL,
    institution TEXT NOT NULL,
    rows_read INTEGER NOT NULL,
    rows_new INTEGER NOT NULL,
    rows_duplicate INTEGER NOT NULL,
    imported_at TEXT NOT NULL
);
"""


# How long a connection waits for another one's write (an import, a re-run) before giving up.
BUSY_TIMEOUT_SECONDS = 30


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open the database at config.DB_PATH (or db_path). The tables are created when the file is
    new, and the accounts table is rewritten only when it differs from config, so opening the
    database to read it doesn't write."""
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    try:
        stored = set(connection.execute("SELECT name, institution, kind, cap_key FROM accounts"))
    except sqlite3.OperationalError:  # a new file: no tables yet
        connection.executescript(SCHEMA)
        stored = set()
    if stored != set(account_rows()):
        refresh_accounts(connection)
    return connection


def account_rows() -> list[tuple]:
    """config.ACCOUNTS as rows of the accounts table."""
    return [(name, spec["institution"], spec["kind"], spec["cap_key"]) for name, spec in config.ACCOUNTS.items()]


def refresh_accounts(connection: sqlite3.Connection) -> None:
    """Rewrite the accounts table from config.ACCOUNTS, so queries can join on kind and cap."""
    with connection:
        connection.execute("DELETE FROM accounts")
        connection.executemany("INSERT INTO accounts (name, institution, kind, cap_key) VALUES (?, ?, ?, ?)", account_rows())
