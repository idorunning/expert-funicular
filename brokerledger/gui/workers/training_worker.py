"""Background worker: consume pending training notes into merchant rules."""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from ...categorize.training import TrainingReport, run_training_pass
from ...utils.logging import logger


class TrainingWorker(QObject):
    done = Signal(object)   # TrainingReport
    error = Signal(str)

    def __init__(self, current_user) -> None:
        super().__init__()
        self._current_user = current_user

    def run(self) -> None:
        from ...auth.session import set_current
        set_current(self._current_user)
        try:
            report = run_training_pass(
                user_id=self._current_user.id if self._current_user else None
            )
            self.done.emit(report)
        except Exception as e:  # noqa: BLE001
            logger.exception("Training pass failed")
            self.error.emit(str(e))
            self.done.emit(TrainingReport())


def run_training_in_thread():
    """Factory returning (thread, worker). Caller must call thread.start()."""
    from ...auth.session import get_current
    worker = TrainingWorker(get_current())
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.done.connect(thread.quit)
    worker.done.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    return thread, worker
