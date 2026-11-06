"""Read OFX and QFX files, the "Quicken" or "Money" download most banks and brokerages offer.

Both dialects: the older SGML one, where a field's tag is often never closed
(<TRNAMT>-42.10 on its own line), and XML. A file holds one or more statements (bank, credit card,
or a brokerage's cash), each with its account number and its transactions. Amounts are already
signed from the account's point of view, as the rest of the tracker wants: money out negative.
"""

from __future__ import annotations

import re

STATEMENT = re.compile(r"<(STMTRS|CCSTMTRS|INVSTMTRS)>(.*?)</\1>", re.S | re.I)
TRANSACTION = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.S | re.I)
ACCOUNT = re.compile(r"<(?:BANKACCTFROM|CCACCTFROM|INVACCTFROM)>.*?<ACCTID>\s*([^<\r\n]+)", re.S | re.I)
KINDS = {"STMTRS": "bank", "CCSTMTRS": "credit card", "INVSTMTRS": "investment"}


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


def ofx_amount(text: str) -> float | None:
    """A TRNAMT, which some banks write with a comma for the decimal point."""
    cleaned = text.strip().replace(",", ".") if text.count(",") == 1 and "." not in text else text.strip().replace(",", "")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def statements(text: str) -> list[dict]:
    """Each statement in the file: its kind, its account number, and its transactions as
    {date, amount, description}. A transaction with no date or amount is left out."""
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
        found.append({"kind": KINDS[kind.upper()], "account_number": account.group(1).strip() if account else "",
                      "transactions": rows})
    return found
