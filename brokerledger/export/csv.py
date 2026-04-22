"""Plain-text CSV/TSV export — matches the XLSX Transactions sheet columns."""
from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import select

from ..db.engine import session_scope
from ..db.models import Client, Transaction


COLUMNS = [
    "Client",
    "Date", "Description", "Merchant", "Amount (GBP)", "Direction",
    "Category", "Group", "Certainty", "Method", "Flags", "Flagged",
]


def export_transactions_csv(
    client_id: int,
    out_path: Path,
    *,
    delimiter: str = ",",
) -> Path:
    """Write every transaction for ``client_id`` to a UTF-8 CSV/TSV file.

    ``delimiter=','`` gives a CSV (Google Sheets, Excel), ``'\\t'`` gives a
    tab-separated file that pastes cleanly into any text editor.
    """
    from ..categorize.flags import deserialize_flags, flag_display_name

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with session_scope() as s:
        client = s.get(Client, client_id)
        client_name = client.display_name if client else ""
        rows = s.execute(
            select(Transaction).where(Transaction.client_id == client_id)
            .order_by(Transaction.posted_date.asc(), Transaction.id.asc())
        ).scalars().all()
        payload = [
            [
                client_name,
                r.posted_date,
                r.description_raw,
                r.merchant_normalized,
                f"{r.amount:.2f}" if r.amount is not None else "",
                r.direction or "",
                r.category or "",
                r.category_group or "",
                f"{r.confidence:.2f}" if r.confidence is not None else "",
                r.source or "",
                ", ".join(flag_display_name(f) for f in deserialize_flags(r.flags)),
                "yes" if r.needs_review else "",
            ]
            for r in rows
        ]
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=delimiter)
        w.writerow(COLUMNS)
        w.writerows(payload)
    return out_path
