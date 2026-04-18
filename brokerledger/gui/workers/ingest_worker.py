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
            self.progress.emit(idx, total, f"Parsing {p.name}")
            try:
                result: IngestResult = ingest_statement(self.client_id, p)
                if not result.duplicate:
                    self.progress.emit(idx, total, f"Categorising {p.name}")
                    categorize_statement(result.statement_id)
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
        self.progress.emit(total, total, "Done")
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
