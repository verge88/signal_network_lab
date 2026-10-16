import threading
import traceback
from concurrent.futures import CancelledError
from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class JobSignals(QObject):
    progress=Signal(int)
    result=Signal(object)
    failed=Signal(str)
    cancelled=Signal()
    finished=Signal()


class Job(QRunnable):
    def __init__(self,fn):
        super().__init__();self.signals=JobSignals();self.fn=fn;self.stop_event=threading.Event()
    def cancel(self):self.stop_event.set()
    @Slot()
    def run(self):
        try:
            result=self.fn(self.signals.progress.emit,self.stop_event.is_set)
            if self.stop_event.is_set():self.signals.cancelled.emit()
            else:self.signals.result.emit(result)
        except CancelledError:self.signals.cancelled.emit()
        except Exception as e:self.signals.failed.emit(str(e))
        finally:self.signals.finished.emit()
