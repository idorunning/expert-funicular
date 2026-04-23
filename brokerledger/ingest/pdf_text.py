"""Text-PDF bank-statement parser using pdfplumber.

Primary strategy: extract structured tables using pdfplumber's geometry-aware
table detection, then map columns by fuzzy header-vocabulary matching.

Fallback: line-by-line heuristic scan for PDFs without detectable table borders
(e.g. whitespace-separated columns, some older bank formats).
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dateutil import parser as dateparser

from .normalize import RawTransaction, normalize_merchant


# ── Column-header vocabulary ──────────────────────────────────────────────────
# Each set covers every known synonym a UK (or generic) bank might use for that
# column.  Header cells are normalised to lowercase + letters/spaces only before
# matching, so punctuation and case variations are handled automatically.

_DATE_HEADERS: set[str] = {
    "date", "posted date", "transaction date", "txn date", "date posted",
    "posting date", "value date", "entry date", "process date", "booking date",
    "processed date", "settlement date", "trans date", "trade date",
}
_DESC_HEADERS: set[str] = {
    "description", "details", "transaction details", "narrative", "reference",
    "memo", "payee", "payment details", "particulars", "beneficiary",
    "transaction type", "type", "information", "transaction description",
    "merchant", "counterparty", "trans description", "payment description",
    "remarks", "details of transactions", "transaction narrative",
}
_DEBIT_HEADERS: set[str] = {
    "debit", "dr", "paid out", "money out", "out", "withdrawal",
    "withdrawals", "charges", "debits", "debit amount", "amount debited",
    "payments out", "amount paid out", "payment out",
}
_CREDIT_HEADERS: set[str] = {
    "credit", "cr", "paid in", "money in", "in", "deposit",
    "deposits", "credits", "credit amount", "amount credited",
    "payments in", "receipts", "amount paid in", "payment in",
}
_AMOUNT_HEADERS: set[str] = {
    "amount", "value", "transaction amount", "net amount", "sum",
    "amount gbp", "gbp amount", "sterling amount", "transaction value",
    "debit credit",
}
_BALANCE_HEADERS: set[str] = {
    "balance", "running balance", "closing balance", "available balance",
    "balance gbp", "account balance", "bal",
}
_ALL_VOCAB = (
    _DATE_HEADERS | _DESC_HEADERS | _DEBIT_HEADERS | _CREDIT_HEADERS
    | _AMOUNT_HEADERS | _BALANCE_HEADERS
)

# ── Fallback line-by-line patterns ────────────────────────────────────────────

_DATE_RE = re.compile(
    r"^(?P<d>"
    r"(?:\d{1,2}[/\-\.](?:[A-Za-z]{3,9}|\d{1,2})[/\-\.]\d{2,4})"   # 01/Jan/25
    r"|(?:\d{4}-\d{2}-\d{2})"                                         # 2025-01-01
    r"|(?:\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})"                       # 01 Jan 2025
    r"|(?:\d{1,2}\s+[A-Za-z]{3})"                                     # 01 Jan (no year)
    r")"
)
_AMOUNT_RE = re.compile(r"-?£?\s*\(?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\)?")


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _normalize_header(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", h.lower())).strip()


def _pick_col(headers: list[str], candidates: set[str]) -> int | None:
    for i, h in enumerate(headers):
        if _normalize_header(h) in candidates:
            return i
    return None


def _score_row(row: list[str]) -> int:
    """Count how many cells in this row match known header vocabulary."""
    return sum(1 for c in row if c and _normalize_header(c) in _ALL_VOCAB)


def _parse_amount(raw: str) -> Decimal | None:
    """Parse a bank amount string; handles £, commas, parentheses, DR/CR suffixes."""
    s = str(raw or "").strip()
    if not s or s in {"-", "--", "nil", "n/a", "—"}:
        return None
    dr = bool(re.search(r"\bDR\b", s, re.IGNORECASE))
    cr = bool(re.search(r"\bCR\b", s, re.IGNORECASE))
    s = re.sub(r"\s*\b(DR|CR)\b\s*", " ", s, flags=re.IGNORECASE).strip()
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = re.sub(r"[£$€¥\s]", "", s).replace(",", "")
    if not s:
        return None
    try:
        v = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if neg or dr:
        return -abs(v)
    if cr:
        return abs(v)
    return v


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return dateparser.parse(s.strip(), dayfirst=True).date()
    except (ValueError, TypeError, OverflowError):
        return None


# ── Public helpers (used by router for OCR decision) ─────────────────────────

def extract_lines(path: Path) -> tuple[list[str], int]:
    """Extract text lines and page count. Returns (lines, page_count)."""
    try:
        import pdfplumber
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


# ── Table-based extraction (primary) ─────────────────────────────────────────

def _parse_via_tables(path: Path) -> tuple[list[RawTransaction], int, float]:
    """Extract transactions from PDF tables using pdfplumber geometry detection."""
    import pdfplumber

    txs: list[RawTransaction] = []
    all_lines: list[str] = []

    # Column mapping — persists across pages once a header row is found.
    header: list[str] | None = None
    date_col = desc_col = debit_col = credit_col = amount_col = balance_col = None
    last_tx: RawTransaction | None = None

    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)

        for page in pdf.pages:
            txt = page.extract_text() or ""
            for line in txt.splitlines():
                line = line.strip()
                if line:
                    all_lines.append(line)

            tables = page.extract_tables() or []
            for table in tables:
                for raw_row in table:
                    if raw_row is None:
                        continue
                    row = [str(c or "").strip() for c in raw_row]

                    # (Re-)detect header row — also handles repeated headers on each page.
                    if _score_row(row) >= 2:
                        header = row
                        date_col    = _pick_col(header, _DATE_HEADERS)
                        desc_col    = _pick_col(header, _DESC_HEADERS)
                        debit_col   = _pick_col(header, _DEBIT_HEADERS)
                        credit_col  = _pick_col(header, _CREDIT_HEADERS)
                        amount_col  = _pick_col(header, _AMOUNT_HEADERS)
                        balance_col = _pick_col(header, _BALANCE_HEADERS)
                        last_tx = None
                        continue

                    if header is None or date_col is None:
                        continue
                    if amount_col is None and debit_col is None and credit_col is None:
                        continue

                    # Pad to header width so index access is always safe.
                    row = (row + [""] * len(header))[:len(header)]

                    date_str = row[date_col]
                    desc_str = row[desc_col].strip() if desc_col is not None else ""

                    d = _parse_date(date_str)
                    if d is None:
                        # Continuation row: append extra description text to the previous tx.
                        if desc_str and last_tx is not None:
                            last_tx.description_raw = (
                                last_tx.description_raw + " " + desc_str
                            ).strip()
                            last_tx.merchant_normalized = normalize_merchant(
                                last_tx.description_raw
                            )
                        continue

                    if not desc_str:
                        continue

                    # Resolve signed amount from whichever columns are available.
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

                    balance = (
                        _parse_amount(row[balance_col]) if balance_col is not None else None
                    )

                    tx = RawTransaction(
                        posted_date=d,
                        description_raw=desc_str,
                        amount=amount,
                        balance_after=balance,
                    )
                    txs.append(tx)
                    last_tx = tx

    avg = average_chars_per_page(all_lines, page_count)
    return txs, page_count, avg


# ── Line-by-line extraction (fallback) ───────────────────────────────────────

def _parse_via_lines(path: Path) -> tuple[list[RawTransaction], int, float]:
    """Fallback for PDFs with no detectable table borders (whitespace-column layouts)."""
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
        if len(amounts) >= 2:
            balance_str = amounts[-1].group()
            amount_str = amounts[-2].group()
            description = rest[:amounts[-2].start()].strip()
        else:
            balance_str = None
            amount_str = amounts[-1].group()
            description = rest[:amounts[-1].start()].strip()

        amount = _parse_amount(amount_str)
        balance = _parse_amount(balance_str) if balance_str else None

        if amount is None or not description:
            continue

        # UK bank PDFs (line-fallback path) mark credits with an explicit CR/DR
        # suffix and show debits as bare numbers. Respect explicit markers first;
        # when neither is present, default to debit — this matches the printed
        # convention and avoids every un-marked purchase being mis-classed as
        # income by the categoriser.
        if re.search(r"\bCR\b", rest, re.IGNORECASE):
            amount = abs(amount)
        elif re.search(r"\bDR\b", rest, re.IGNORECASE):
            amount = -abs(amount)
        elif amount > 0 and not amount_str.lstrip().startswith("-"):
            amount = -abs(amount)

        txs.append(RawTransaction(
            posted_date=d,
            description_raw=description,
            amount=amount,
            balance_after=balance,
        ))

    return txs, page_count, avg


# ── Entry point ───────────────────────────────────────────────────────────────

def parse_pdf_text(path: Path) -> tuple[list[RawTransaction], int, float]:
    """Parse a text-based PDF bank statement.

    Tries geometry-aware table extraction first (works for virtually any column
    layout from any bank); falls back to line-by-line heuristics when no table
    borders are present.
    """
    txs, page_count, avg = _parse_via_tables(path)
    if txs:
        return txs, page_count, avg
    return _parse_via_lines(path)
