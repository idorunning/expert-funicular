"""Scanned-PDF OCR fallback. Requires pytesseract + pdf2image + Tesseract binary."""
from __future__ import annotations

from pathlib import Path

from .normalize import RawTransaction


class OCRUnavailable(RuntimeError):
    pass


def parse_pdf_ocr(path: Path) -> list[RawTransaction]:
    try:
        import pytesseract  # type: ignore
        from pdf2image import convert_from_path  # type: ignore
    except ImportError as e:
        raise OCRUnavailable(
            "OCR dependencies not installed. Install with `pip install -e .[ocr]` "
            "and ensure Tesseract + Poppler are on PATH."
        ) from e

    # Convert each page to an image, run OCR, then reuse the text-PDF line parser
    # by writing the recognised text to a temp file and re-extracting.
    from .pdf_text import _AMOUNT_RE, _DATE_RE, _parse_date, _to_decimal  # reuse

    images = convert_from_path(str(path), dpi=300)
    all_lines: list[str] = []
    for img in images:
        text = pytesseract.image_to_string(img)
        for line in text.splitlines():
            line = line.strip()
            if line:
                all_lines.append(line)

    txs: list[RawTransaction] = []
    for raw in all_lines:
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
        amount = _to_decimal(amount_str)
        balance = _to_decimal(balance_str) if balance_str else None
        if amount is None or not description:
            continue
        if amount > 0 and not amount_str.startswith("-"):
            amount = -abs(amount)
        txs.append(RawTransaction(
            posted_date=d,
            description_raw=description,
            amount=amount,
            balance_after=balance,
        ))
    return txs
