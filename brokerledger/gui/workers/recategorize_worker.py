"""Background worker: re-run AI categorisation for all non-user transactions."""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from ...categorize.categorizer import recategorize_client
from ...utils.logging import logger


class RecategorizeWorker(QObject):
    progress = Signal(int, int, str)   # (done, total, message)
    done = Signal(int)                 # count of rows updated
    error = Signal(str)
    tx_categorized = Signal(str, str, object, str)  # (category, group, amount, direction)
    tx_persisted = Signal(int, int)    # (client_id, tx_id) — after DB flush
    starting = Signal()                # emitted before first tx so UI can reset totals

    def __init__(self, client_id: int, current_user) -> None:
        super().__init__()
        self.client_id = client_id
        self._current_user = current_user

    def run(self) -> None:
        from ...auth.session import set_current
        set_current(self._current_user)
        try:
            self.starting.emit()
            count = recategorize_client(
                self.client_id,
                progress_cb=self._on_progress,
                tx_cb=self._on_decision,
                tx_id_cb=self._on_persisted,
            )
            self.done.emit(count)
        except Exception as e:  # noqa: BLE001
            logger.exception("Recategorize failed for client {}", self.client_id)
            self.error.emit(str(e))
            self.done.emit(0)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.emit(done, total, f"Re-categorising… {done}/{total}")

    def _on_decision(self, category: str, group: str, amount, direction: str) -> None:
        self.tx_categorized.emit(category or "", group or "", amount, direction or "")

    def _on_persisted(self, client_id: int, tx_id: int) -> None:
        self.tx_persisted.emit(client_id, tx_id)


def run_recategorize_in_thread(client_id: int):
    """Factory returning (thread, worker). Caller must call thread.start()."""
    from ...auth.session import get_current
    worker = RecategorizeWorker(client_id, get_current())
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.done.connect(thread.quit)
    worker.done.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    return thread, worker
