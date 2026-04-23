"""CSV bank-statement parser with heuristic column detection."""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dateutil import parser as dateparser

from .normalize import RawTransaction


_DATE_HEADERS = {"date", "posted date", "transaction date", "txn date", "date posted"}
_DESC_HEADERS = {"description", "details", "narrative", "reference", "memo", "payee"}
_DEBIT_HEADERS = {"debit", "paid out", "money out", "out", "withdrawal"}
_CREDIT_HEADERS = {"credit", "paid in", "money in", "in", "deposit"}
_AMOUNT_HEADERS = {"amount", "value", "transaction amount"}
_BALANCE_HEADERS = {"balance", "running balance"}


def _parse_amount(raw: str) -> Decimal | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Handle parentheses negatives, strip currency symbols and thousand sep.
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace("£", "").replace(",", "").replace(" ", "")
    if s in {"-", "--"}:
        return None
    try:
        v = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return -v if neg else v


def _parse_date(raw: str) -> date | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return dateparser.parse(s, dayfirst=True).date()
    except (ValueError, TypeError, OverflowError):
        return None


def _pick(header: list[str], candidates: set[str]) -> int | None:
    for i, h in enumerate(header):
        key = re.sub(r"[^a-z ]", "", h.lower()).strip()
        if key in candidates:
            return i
    return None


def _detect_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        class _D(csv.excel):
            pass
        return _D()


def parse_csv(path: Path) -> list[RawTransaction]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    sample = text[:4096]
    dialect = _detect_dialect(sample)
    rows = list(csv.reader(text.splitlines(), dialect=dialect))

    # Find the header row — first row where ≥2 cells match known header vocab.
    vocab = _DATE_HEADERS | _DESC_HEADERS | _DEBIT_HEADERS | _CREDIT_HEADERS | _AMOUNT_HEADERS | _BALANCE_HEADERS
    header_idx = None
    for idx, row in enumerate(rows[:20]):
        norm = [re.sub(r"[^a-z ]", "", c.lower()).strip() for c in row]
        hits = sum(1 for c in norm if c in vocab)
        if hits >= 2:
            header_idx = idx
            break
    if header_idx is None:
        raise ValueError("Could not detect header row in CSV")

    header = [c.strip() for c in rows[header_idx]]
    date_col = _pick(header, _DATE_HEADERS)
    desc_col = _pick(header, _DESC_HEADERS)
    debit_col = _pick(header, _DEBIT_HEADERS)
    credit_col = _pick(header, _CREDIT_HEADERS)
    amount_col = _pick(header, _AMOUNT_HEADERS)
    balance_col = _pick(header, _BALANCE_HEADERS)

    if date_col is None or desc_col is None:
        raise ValueError("CSV missing required Date or Description column")
    if amount_col is None and (debit_col is None and credit_col is None):
        raise ValueError("CSV missing Amount / Debit / Credit columns")

    out: list[RawTransaction] = []
    for row in rows[header_idx + 1:]:
        if not any(c.strip() for c in row):
            continue
        # Pad row to header length so indexing is safe
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        d = _parse_date(row[date_col])
        if d is None:
            continue
        desc = row[desc_col].strip()
        if not desc:
            continue
        amount: Decimal | None = None
        # Prefer explicit debit/credit columns — unambiguous sign.
        if debit_col is not None:
            dv = _parse_amount(row[debit_col])
            if dv is not None and dv != 0:
                amount = -abs(dv)
        if amount is None and credit_col is not None:
            cv = _parse_amount(row[credit_col])
            if cv is not None and cv != 0:
                amount = abs(cv)
        # Single amount column is the fallback.
        if amount is None and amount_col is not None:
            amount = _parse_amount(row[amount_col])
        if amount is None:
            continue
        balance = _parse_amount(row[balance_col]) if balance_col is not None else None
        out.append(RawTransaction(posted_date=d, description_raw=desc, amount=amount, balance_after=balance))
    return out
