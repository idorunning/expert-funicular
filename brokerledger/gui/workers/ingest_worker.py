"""Background worker: parse + categorise one or more statement files."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ...categorize.categorizer import categorize_statement
from ...ingest.router import IngestError, IngestResult, ingest_statement
from ...utils.logging import logger


class IngestWorker(QObject):
    progress = Signal(int, int, str)    # (done, total, message)
    file_done = Signal(object)          # IngestResult
    all_done = Signal(int, int)         # (success_count, failure_count)
    error = Signal(str, str)            # (file_name, message)
    tx_categorized = Signal(str, str, object, str)  # (category, group, amount Decimal, direction)

    def __init__(self, client_id: int, paths: list[Path], current_user) -> None:
        super().__init__()
        self.client_id = client_id
        self.paths = paths
        self._current_user = current_user

    def run(self) -> None:
        from ...auth.session import set_current
        # QThread starts this on a new thread; re-bind the current user.
        set_current(self._current_user)
        total = len(self.paths)
        ok = 0
        fail = 0
        for idx, p in enumerate(self.paths):
            self.progress.emit(idx * 100, total * 100, f"Parsing {p.name}…  (file {idx + 1} of {total})")
            try:
                result: IngestResult = ingest_statement(self.client_id, p)
                if not result.duplicate:
                    # Emit sub-file progress during categorisation so the bar
                    # advances smoothly rather than freezing on each file.
                    def _tx_cb(tx_done: int, tx_total: int, _idx: int = idx, _total: int = total, _name: str = p.name) -> None:
                        pct = int(tx_done / tx_total * 100) if tx_total else 100
                        bar_val = _idx * 100 + pct
                        self.progress.emit(
                            bar_val,
                            _total * 100,
                            f"Categorising {_name}…  {tx_done}/{tx_total} transactions  ({pct}%)"
                        )

                    def _decision_cb(category: str, group: str, amount, direction: str) -> None:
                        self.tx_categorized.emit(category or "", group or "", amount, direction or "")
                    categorize_statement(result.statement_id, progress_cb=_tx_cb, tx_cb=_decision_cb)
                self.file_done.emit(result)
                ok += 1
            except IngestError as e:
                logger.exception("Ingest failed for {}", p)
                self.error.emit(p.name, str(e))
                fail += 1
            except Exception as e:  # noqa: BLE001
                logger.exception("Unexpected ingest error for {}", p)
                self.error.emit(p.name, f"Unexpected error: {e}")
                fail += 1
        self.progress.emit(total * 100, total * 100, "Done")
        self.all_done.emit(ok, fail)


def run_ingest_in_thread(client_id: int, paths: list[Path]):
    """Convenience factory that returns (thread, worker) wired up and started
    by the caller."""
    from ...auth.session import get_current
    worker = IngestWorker(client_id, paths, get_current())
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.all_done.connect(thread.quit)
    worker.all_done.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    return thread, worker
