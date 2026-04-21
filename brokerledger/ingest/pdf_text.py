"""Text-PDF bank-statement parser using pdfplumber."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dateutil import parser as dateparser

from .normalize import RawTransaction


_DATE_RE = re.compile(
    r"^(?P<d>(?:\d{1,2}[/\- ](?:[A-Za-z]{3,9}|\d{1,2})[/\- ]\d{2,4})|(?:\d{4}-\d{2}-\d{2}))"
)
_AMOUNT_RE = re.compile(r"-?£?\(?\d{1,3}(?:,\d{3})*(?:\.\d{2})\)?")


def _to_decimal(raw: str) -> Decimal | None:
    s = raw.strip()
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.replace("£", "").replace(",", "")
    try:
        v = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return -v if neg else v


def _parse_date(s: str) -> date | None:
    try:
        return dateparser.parse(s, dayfirst=True).date()
    except (ValueError, TypeError, OverflowError):
        return None


def extract_lines(path: Path) -> tuple[list[str], int]:
    """Extract text lines and page count. Returns (lines, page_count)."""
    try:
        import pdfplumber  # lazy import
    except ImportError as e:
        raise RuntimeError("pdfplumber is not installed") from e

    lines: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            txt = page.extract_text() or ""
            for line in txt.splitlines():
                line = line.strip()
                if line:
                    lines.append(line)
    return lines, page_count


def average_chars_per_page(lines: list[str], page_count: int) -> float:
    if page_count <= 0:
        return 0.0
    return sum(len(line) for line in lines) / page_count


def parse_pdf_text(path: Path) -> tuple[list[RawTransaction], int, float]:
    """Returns (transactions, page_count, avg_chars_per_page)."""
    lines, page_count = extract_lines(path)
    avg = average_chars_per_page(lines, page_count)
    txs: list[RawTransaction] = []
    for raw in lines:
        m = _DATE_RE.match(raw)
        if not m:
            continue
        d = _parse_date(m.group("d"))
        if d is None:
            continue
        rest = raw[m.end():].strip()
        amounts = list(_AMOUNT_RE.finditer(rest))
        if not amounts:
            continue
        # Heuristic: the last amount is often the running balance,
        # the penultimate is the transaction amount.
        if len(amounts) >= 2:
            balance_str = amounts[-1].group()
            amount_str = amounts[-2].group()
            description = rest[:amounts[-2].start()].strip()
        else:
            balance_str = None
            amount_str = amounts[-1].group()
            description = rest[:amounts[-1].start()].strip()
        amount = _to_decimal(amount_str)
        balance = _to_decimal(balance_str) if balance_str else None
        if amount is None or not description:
            continue
        # When the amount appears with no explicit sign, UK statements usually
        # place debits/credits in separate columns; pdfplumber flattens these
        # into the same line. If the rest has the literal " CR" marker, treat
        # as credit; otherwise assume debit.
        if re.search(r"\bCR\b", rest, re.IGNORECASE):
            amount = abs(amount)
        elif amount > 0 and not amount_str.startswith("-"):
            # Ambiguous sign — default to debit because the majority of
            # statement rows are outgoings; reviewer can correct.
            amount = -abs(amount)
        txs.append(RawTransaction(
            posted_date=d,
            description_raw=description,
            amount=amount,
            balance_after=balance,
        ))
    return txs, page_count, avg
