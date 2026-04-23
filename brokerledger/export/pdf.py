"""PDF export of a client affordability report.

Uses PySide6's :class:`QTextDocument` + :class:`QPdfWriter` — zero additional
dependencies.  The report mirrors the XLSX sheets so brokers get an immutable,
printable record alongside their spreadsheet (used for data-compliance
archiving under ``{client_folder}/exports/``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from pathlib import Path

from sqlalchemy import select

from ..affordability.calculator import AffordabilityReport, compute_for_client
from ..auth.session import get_current
from ..categorize.flags import deserialize_flags, flag_display_name
from ..categorize.taxonomy import COMMITTED_CATEGORIES, DISCRETIONARY_CATEGORIES
from ..db.engine import session_scope
from ..db.models import Client, Statement, Transaction, User


_BRAND_PURPLE = "#4A1766"
_BRAND_MAGENTA = "#D63A91"


def _html_cover(client: Client, user_label: str, accounts: list[tuple[str, str]]) -> str:
    ref = escape(client.reference or "")
    name = escape(client.display_name)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    user = escape(user_label)
    account_rows = "".join(
        f"<li>{escape(bank or '—')} — <span style='color:#555'>{escape(src)}</span></li>"
        for bank, src in accounts
    ) or "<li style='color:#777'>No statements imported.</li>"
    return f"""
    <div style="margin-bottom:18pt">
      <div style="color:{_BRAND_MAGENTA};font-size:10pt;letter-spacing:0.5pt">
        MORTGAGE BROKER AFFORDABILITY ASSISTANT
      </div>
      <h1 style="color:{_BRAND_PURPLE};margin:0;padding:0">
        Affordability Report
      </h1>
      <div style="color:#555;font-size:10pt">
        Generated {now} by {user}
      </div>
      <div style="margin-top:10pt;padding:10pt;background:#F3EDF9;
                  border:1px solid {_BRAND_PURPLE};border-radius:4pt">
        <div style="font-size:13pt;color:{_BRAND_PURPLE}">
          <b>Client:</b> {name}
        </div>
        <div style="color:#333;font-size:11pt">
          <b>System reference:</b> {ref or '—'}
        </div>
        <div style="margin-top:6pt;color:#333;font-size:11pt">
          <b>Accounts covered:</b>
          <ul style="margin:4pt 0 0 0;padding-left:16pt">{account_rows}</ul>
        </div>
      </div>
    </div>
    """


def _html_summary(report: AffordabilityReport) -> str:
    def m(v: Decimal | None) -> str:
        if v is None:
            return "—"
        return f"£{float(v):,.2f}"

    period = f"{report.period_start} to {report.period_end}" if report.period_start and report.period_end else "—"
    return f"""
    <h2 style="color:{_BRAND_PURPLE}">Affordability summary</h2>
    <table style="border-collapse:collapse;width:100%">
      <tr><td style="padding:4pt 8pt;background:#F3EDF9"><b>Period</b></td><td style="padding:4pt 8pt">{escape(period)}</td></tr>
      <tr><td style="padding:4pt 8pt;background:#F3EDF9"><b>Months in window</b></td><td style="padding:4pt 8pt">{report.months_in_window:.2f}</td></tr>
      <tr><td style="padding:4pt 8pt;background:#F3EDF9"><b>Declared income</b></td><td style="padding:4pt 8pt">{m(report.declared_income)}</td></tr>
      <tr><td style="padding:4pt 8pt;background:#F3EDF9"><b>Detected income (total)</b></td><td style="padding:4pt 8pt">{m(report.income_total)}</td></tr>
      <tr><td style="padding:4pt 8pt;background:#F3EDF9"><b>Committed expenditure</b></td><td style="padding:4pt 8pt">{m(report.committed_total)}</td></tr>
      <tr><td style="padding:4pt 8pt;background:#F3EDF9"><b>Discretionary expenditure</b></td><td style="padding:4pt 8pt">{m(report.discretionary_total)}</td></tr>
      <tr><td style="padding:4pt 8pt;background:#F3EDF9"><b>Total outgoings</b></td><td style="padding:4pt 8pt">{m(report.outgoings_total)}</td></tr>
      <tr><td style="padding:4pt 8pt;background:#F3EDF9"><b>Net disposable</b></td><td style="padding:4pt 8pt"><b>{m(report.net_disposable)}</b></td></tr>
      <tr><td style="padding:4pt 8pt;background:#F3EDF9"><b>Monthly net disposable</b></td><td style="padding:4pt 8pt"><b>{m(report.monthly_net_disposable)}</b></td></tr>
    </table>
    """


def _html_category_totals(report: AffordabilityReport) -> str:
    rows: list[str] = []
    months = max(report.months_in_window, 1.0)
    for cat in list(COMMITTED_CATEGORIES) + list(DISCRETIONARY_CATEGORIES):
        t = report.per_category.get(cat)
        if t is None:
            rows.append(
                f"<tr><td>{escape(cat)}</td><td>—</td><td style='text-align:right'>0</td>"
                f"<td style='text-align:right'>£0.00</td><td style='text-align:right'>£0.00</td></tr>"
            )
            continue
        monthly = float((t.total / Decimal(str(months))).quantize(Decimal("0.01")))
        rows.append(
            f"<tr>"
            f"<td>{escape(t.category)}</td>"
            f"<td>{escape(t.group)}</td>"
            f"<td style='text-align:right'>{t.count}</td>"
            f"<td style='text-align:right'>£{float(t.total):,.2f}</td>"
            f"<td style='text-align:right'>£{monthly:,.2f}</td>"
            f"</tr>"
        )
    return f"""
    <h2 style="color:{_BRAND_PURPLE};page-break-before:always">Category totals</h2>
    <table style="border-collapse:collapse;width:100%" border="1" cellspacing="0" cellpadding="4">
      <thead>
        <tr style="background:{_BRAND_PURPLE};color:white">
          <th>Category</th><th>Group</th>
          <th style="text-align:right">Count</th>
          <th style="text-align:right">Total</th>
          <th style="text-align:right">Monthly avg</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _html_transactions(client_id: int, client_name: str) -> str:
    rows: list[str] = []
    with session_scope() as s:
        txs = s.execute(
            select(Transaction)
            .where(Transaction.client_id == client_id)
            .order_by(Transaction.posted_date.asc(), Transaction.id.asc())
        ).scalars().all()
        for idx, t in enumerate(txs):
            zebra = "background:#FAF7FC" if idx % 2 else ""
            flags = ", ".join(flag_display_name(f) for f in deserialize_flags(t.flags))
            flagged = "Yes" if t.needs_review else ""
            amount = f"£{float(t.amount):,.2f}" if t.amount is not None else ""
            cert = f"{float(t.confidence):.2f}" if t.confidence is not None else ""
            rows.append(
                f"<tr style='{zebra}'>"
                f"<td>{escape(t.posted_date)}</td>"
                f"<td>{escape(t.description_raw)}</td>"
                f"<td>{escape(t.merchant_normalized or '')}</td>"
                f"<td style='text-align:right'>{amount}</td>"
                f"<td>{escape(t.direction or '')}</td>"
                f"<td>{escape(t.category or '')}</td>"
                f"<td>{escape(t.category_group or '')}</td>"
                f"<td style='text-align:right'>{cert}</td>"
                f"<td>{escape(t.source or '')}</td>"
                f"<td>{escape(flags)}</td>"
                f"<td>{flagged}</td>"
                f"</tr>"
            )
    return f"""
    <h2 style="color:{_BRAND_PURPLE};page-break-before:always">Transactions — {escape(client_name)}</h2>
    <table style="border-collapse:collapse;width:100%;font-size:9pt"
           border="1" cellspacing="0" cellpadding="3">
      <thead>
        <tr style="background:{_BRAND_PURPLE};color:white">
          <th>Date</th><th>Description</th><th>Merchant</th>
          <th style="text-align:right">Amount</th>
          <th>Direction</th><th>Category</th><th>Group</th>
          <th style="text-align:right">Certainty</th>
          <th>Method</th><th>Flags</th><th>Flagged</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _html_audit(
    client: Client,
    user_label: str,
    stmts: list[Statement] | None = None,
) -> str:
    rows: list[str] = []

    def _build_rows(s: object, stmt_list: list[Statement]) -> None:
        verifier_cache: dict[int, str] = {}
        for st in stmt_list:
            verifier = ""
            if st.verified_by is not None:
                verifier = verifier_cache.get(st.verified_by) or ""
                if not verifier:
                    u = s.get(User, st.verified_by)  # type: ignore[union-attr]
                    verifier = u.username if u else f"user#{st.verified_by}"
                    verifier_cache[st.verified_by] = verifier
            rows.append(
                f"<tr>"
                f"<td>{escape(st.original_name)}</td>"
                f"<td>{escape(st.file_kind)}</td>"
                f"<td>{escape(st.imported_at.isoformat())}</td>"
                f"<td>{escape(verifier)}</td>"
                f"<td>{escape(st.verified_at.isoformat() if st.verified_at else '')}</td>"
                f"</tr>"
            )

    if stmts is not None:
        with session_scope() as s:
            _build_rows(s, stmts)
    else:
        with session_scope() as s:
            fetched = s.execute(
                select(Statement)
                .where(Statement.client_id == client.id)
                .order_by(Statement.imported_at.asc())
            ).scalars().all()
            _build_rows(s, fetched)
    return f"""
    <h2 style="color:{_BRAND_PURPLE};page-break-before:always">Audit trail</h2>
    <p style="color:#555;margin-top:0">
      Exported by {escape(user_label)} on {datetime.now(timezone.utc).isoformat()}.
    </p>
    <table style="border-collapse:collapse;width:100%;font-size:9pt"
           border="1" cellspacing="0" cellpadding="3">
      <thead>
        <tr style="background:{_BRAND_PURPLE};color:white">
          <th>Statement file</th><th>Kind</th>
          <th>Imported at</th><th>Verified by</th><th>Verified at</th>
        </tr>
      </thead>
      <tbody>{''.join(rows) or '<tr><td colspan="5" style="color:#777">No statements imported yet.</td></tr>'}</tbody>
    </table>
    """


def export_client_pdf(
    client_id: int,
    out_path: Path,
    declared_income: Decimal | None = None,
) -> Path:
    """Write a formatted PDF affordability report.

    Uses :class:`QTextDocument` + :class:`QPdfWriter` — no new deps.  Requires
    a QGuiApplication/QApplication to exist (true for any GUI-driven caller).
    """
    # Deferred Qt imports so this module can be inspected in headless contexts
    # that don't want to touch Qt's font machinery eagerly.
    from PySide6.QtCore import QMarginsF, QSize
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report = compute_for_client(client_id, declared_income=declared_income)
    with session_scope() as s:
        client = s.get(Client, client_id)
        if client is None:
            raise ValueError(f"Client {client_id} not found")
        current = get_current()
        user_label = ""
        if current is not None:
            u = s.get(User, current.id)
            user_label = f"{u.username} ({u.role})" if u else current.username
        stmt_list = s.execute(
            select(Statement)
            .where(Statement.client_id == client_id)
            .order_by(Statement.imported_at.asc())
        ).scalars().all()
        accounts = [(st.bank_profile or "", st.original_name) for st in stmt_list]
        stmt_snaps = list(stmt_list)
        client_snap = Client(
            id=client.id,
            display_name=client.display_name,
            reference=client.reference,
            folder_path=client.folder_path,
            created_by=client.created_by,
            created_at=client.created_at,
            archived_at=client.archived_at,
        )

    html = (
        "<html><body style='font-family:Helvetica,Arial,sans-serif;"
        "font-size:10pt;color:#1F1030'>"
        + _html_cover(client_snap, user_label, accounts)
        + _html_summary(report)
        + _html_category_totals(report)
        + _html_transactions(client_id, client_snap.display_name)
        + _html_audit(client_snap, user_label, stmt_snaps)
        + "</body></html>"
    )

    writer = QPdfWriter(str(out_path))
    writer.setResolution(150)
    layout = QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4),
        QPageLayout.Orientation.Portrait,
        QMarginsF(15, 15, 15, 15),
    )
    writer.setPageLayout(layout)

    doc = QTextDocument()
    # Size the text document to the printable area so QTextDocument wraps
    # and paginates rather than squashing everything onto one page.
    page_px = writer.pageLayout().paintRectPixels(writer.resolution())
    doc.setPageSize(QSize(page_px.width(), page_px.height()).toSizeF())
    doc.setHtml(html)
    doc.print_(writer)
    return out_path
