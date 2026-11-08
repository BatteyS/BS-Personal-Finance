"""Read OFX and QFX files, the "Quicken" or "Money" download most banks and brokerages offer.

Both dialects: the older SGML one, where a field's tag is often never closed
(<TRNAMT>-42.10 on its own line), and XML. A file holds one or more statements (bank, credit card,
or a brokerage's cash), each with its account number and its transactions. Amounts are already
signed from the account's point of view, as the rest of the tracker wants: money out negative.

A brokerage's statement can also list what the account holds (INVPOSLIST): each position's
security, units, unit price and market value, as of the statement's DTASOF, with the account's
uninvested cash in INVBAL's AVAILCASH. The securities are named once for the whole file, in
SECLIST, by the same id (usually a CUSIP) the positions use. OFX has no cost basis.
"""

from __future__ import annotations

import re

STATEMENT = re.compile(r"<(STMTRS|CCSTMTRS|INVSTMTRS)>(.*?)</\1>", re.S | re.I)
TRANSACTION = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.S | re.I)
ACCOUNT = re.compile(r"<(?:BANKACCTFROM|CCACCTFROM|INVACCTFROM)>.*?<ACCTID>\s*([^<\r\n]+)", re.S | re.I)
KINDS = {"STMTRS": "bank", "CCSTMTRS": "credit card", "INVSTMTRS": "investment"}
POSITION_LIST = re.compile(r"<INVPOSLIST>(.*?)</INVPOSLIST>", re.S | re.I)
POSITION = re.compile(r"<INVPOS>(.*?)</INVPOS>", re.S | re.I)
BALANCE = re.compile(r"<INVBAL>(.*?)</INVBAL>", re.S | re.I)
SECURITY = re.compile(r"<SECINFO>(.*?)</SECINFO>", re.S | re.I)
SECURITY_ID = re.compile(r"<SECID>.*?<UNIQUEID>\s*([^<\r\n]+)", re.S | re.I)


def is_ofx(text: str) -> bool:
    """Whether a file's text is OFX rather than CSV."""
    head = text.lstrip()[:2000].upper()
    return head.startswith("OFXHEADER") or "<OFX>" in head or (head.startswith("<?XML") and "<OFX" in text.upper())


def field(block: str, tag: str) -> str:
    """One field's text in a block, whether or not its tag is closed; empty when it's missing."""
    found = re.search(rf"<{tag}>\s*([^<\r\n]*)", block, re.I)
    return found.group(1).strip() if found else ""


def ofx_date(text: str) -> str | None:
    """ "20261015120000.000[-5:EST]" becomes "2026-10-15"; None if it isn't a date."""
    digits = text[:8]
    if len(digits) != 8 or not digits.isdigit():
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def ofx_number(text: str) -> float | None:
    """A number as OFX writes it, which some institutions do with a comma for the decimal point;
    None if it isn't one."""
    cleaned = text.strip().replace(",", ".") if text.count(",") == 1 and "." not in text else text.strip().replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def ofx_amount(text: str) -> float | None:
    """A TRNAMT or MKTVAL, rounded to cents."""
    number = ofx_number(text)
    return None if number is None else round(number, 2) + 0.0


def securities(text: str) -> dict[str, dict]:
    """The file's SECLIST: each security's id, with its ticker and name."""
    found = {}
    for block in SECURITY.findall(text):
        unique_id = SECURITY_ID.search(block)
        if unique_id:
            found[unique_id.group(1).strip()] = {"ticker": field(block, "TICKER"), "name": field(block, "SECNAME")}
    return found


def positions(body: str, named: dict[str, dict]) -> list[dict] | None:
    """An investment statement's holdings as {symbol, description, quantity, price, value,
    cost_basis}: one per position, then the uninvested cash when there is any, or when there is
    nothing else, so an account that holds nothing still reads zero. None when the statement has
    no INVPOSLIST, which says nothing about what the account holds (a download of activity only).
    OFX has no cost basis, so it is always None."""
    listed = POSITION_LIST.search(body)
    if listed is None:
        return None
    held = []
    for block in POSITION.findall(listed.group(1)):
        value = ofx_amount(field(block, "MKTVAL"))
        if value is None:
            continue
        unique_id = SECURITY_ID.search(block)
        security_id = unique_id.group(1).strip() if unique_id else ""
        security = named.get(security_id, {})
        held.append({"symbol": security.get("ticker") or security_id, "description": security.get("name", ""),
                     "quantity": ofx_number(field(block, "UNITS")), "price": ofx_number(field(block, "UNITPRICE")),
                     "value": value, "cost_basis": None})
    balance = BALANCE.search(body)
    cash = (ofx_amount(field(balance.group(1), "AVAILCASH")) if balance else None) or 0.0
    if cash or not held:
        held.append({"symbol": "", "description": "Cash", "quantity": None, "price": None, "value": cash,
                     "cost_basis": None})
    return held


def statements(text: str) -> list[dict]:
    """Each statement in the file: its kind, its account number, and its transactions as
    {date, amount, description}. A transaction with no date or amount is left out. An investment
    statement also has "as_of", the day its DTASOF names (None without one), and "positions", what
    positions() finds; any other statement's positions are None."""
    named = securities(text)
    found = []
    for kind, body in STATEMENT.findall(text):
        account = ACCOUNT.search(body)
        rows = []
        for block in TRANSACTION.findall(body):
            date, amount = ofx_date(field(block, "DTPOSTED")), ofx_amount(field(block, "TRNAMT"))
            if date is None or amount is None:
                continue
            name, memo = field(block, "NAME"), field(block, "MEMO")
            description = name if not memo or memo.upper() in name.upper() else f"{name} {memo}".strip()
            rows.append({"date": date, "amount": amount + 0.0, "description": description or field(block, "TRNTYPE")})
        investment = kind.upper() == "INVSTMTRS"
        found.append({"kind": KINDS[kind.upper()], "account_number": account.group(1).strip() if account else "",
                      "transactions": rows, "as_of": ofx_date(field(body, "DTASOF")) if investment else None,
                      "positions": positions(body, named) if investment else None})
    return found
