"""Statement-file router: dispatch by extension, parse, persist statement + rows."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..auth.session import require_login
from ..db.engine import session_scope
from ..db.models import AuditLog, Client, Statement, Transaction, utcnow
from ..utils.hashing import sha256_file
from ..utils.logging import logger
from .csv_parser import parse_csv
from .normalize import RawTransaction
from .pdf_text import average_chars_per_page, extract_lines, parse_pdf_text
from .xlsx_parser import parse_xlsx


@dataclass
class IngestResult:
    statement_id: int
    file_kind: str
    transaction_count: int
    duplicate: bool = False
    message: str = ""


class IngestError(Exception):
    pass


_PDF_OCR_THRESHOLD = 40.0  # avg chars/page below this = treat as scanned


def _detect_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".csv":
        return "csv"
    if ext in {".xlsx", ".xlsm"}:
        return "xlsx"
    # Fall back to sniffing magic bytes for PDF.
    try:
        with path.open("rb") as f:
            head = f.read(5)
        if head == b"%PDF-":
            return "pdf"
    except OSError:
        pass
    raise IngestError(f"Unsupported file type: {path.suffix}")


def _parse(path: Path, kind: str) -> tuple[list[RawTransaction], str, int | None]:
    if kind == "csv":
        return parse_csv(path), "csv", None
    if kind == "xlsx":
        return parse_xlsx(path), "xlsx", None
    if kind == "pdf":
        # First try text extraction; OCR fallback only if sparse.
        lines, page_count = extract_lines(path)
        avg = average_chars_per_page(lines, page_count)
        if avg >= _PDF_OCR_THRESHOLD:
            txs, _, _ = parse_pdf_text(path)
            return txs, "pdf_text", page_count
        # Sparse text — try OCR (will raise if deps missing).
        logger.info("PDF {} looks scanned (avg={:.1f} chars/page); running OCR", path.name, avg)
        from .pdf_ocr import OCRUnavailable, parse_pdf_ocr
        try:
            return parse_pdf_ocr(path), "pdf_ocr", page_count
        except OCRUnavailable as e:
            raise IngestError(
                f"{path.name} appears to be a scanned PDF. {e}"
            ) from e
    raise IngestError(f"Unhandled file kind: {kind}")


def ingest_statement(client_id: int, source_path: Path) -> IngestResult:
    """Copy the file into the client folder, parse, and persist rows."""
    user = require_login()
    source_path = Path(source_path)
    if not source_path.is_file():
        raise IngestError(f"File not found: {source_path}")
    kind = _detect_kind(source_path)
    file_hash = sha256_file(source_path)

    with session_scope() as s:
        client = s.get(Client, client_id)
        if client is None:
            raise IngestError("Client not found")
        # Dedupe by (client_id, file_sha256).
        existing = s.execute(
            select(Statement).where(
                Statement.client_id == client_id, Statement.file_sha256 == file_hash
            )
        ).scalar_one_or_none()
        if existing is not None:
            return IngestResult(
                statement_id=existing.id,
                file_kind=existing.file_kind,
                transaction_count=existing.row_count or 0,
                duplicate=True,
                message="File already imported for this client",
            )

        # Copy the file into the client's statements folder.
        target_dir = Path(client.folder_path) / "statements"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{file_hash[:12]}-{source_path.name}"
        if not target_path.exists():
            shutil.copy2(source_path, target_path)

        # Parse.
        raw_txs, detected_kind, page_count = _parse(target_path, kind)
        if not raw_txs:
            raise IngestError(f"No transactions could be parsed from {source_path.name}")

        stmt = Statement(
            client_id=client_id,
            original_name=source_path.name,
            stored_path=str(target_path),
            file_sha256=file_hash,
            file_kind=detected_kind,
            imported_by=user.id,
            imported_at=utcnow(),
            page_count=page_count,
            row_count=len(raw_txs),
        )
        s.add(stmt)
        try:
            s.flush()
        except IntegrityError as e:
            s.rollback()
            raise IngestError("Statement already imported") from e

        for t in raw_txs:
            s.add(
                Transaction(
                    statement_id=stmt.id,
                    client_id=client_id,
                    posted_date=t.posted_date.isoformat(),
                    description_raw=t.description_raw,
                    merchant_normalized=t.merchant_normalized,
                    amount=t.amount,
                    direction=t.direction,
                    currency=t.currency,
                    balance_after=t.balance_after,
                    category_group=None,
                    category=None,
                    confidence=None,
                    needs_review=1,
                    source="llm",
                )
            )

        s.add(
            AuditLog(
                user_id=user.id,
                action="import_statement",
                entity_type="statement",
                entity_id=stmt.id,
                detail_json=(
                    f'{{"client_id":{client_id},"rows":{len(raw_txs)},'
                    f'"kind":"{detected_kind}","sha256":"{file_hash}"}}'
                ),
            )
        )
        s.commit()

        return IngestResult(
            statement_id=stmt.id,
            file_kind=detected_kind,
            transaction_count=len(raw_txs),
        )
