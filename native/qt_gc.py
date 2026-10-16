"""Collect Python cycles on the GUI thread, never inside a simulation worker.

Qt objects must be destroyed on their owning thread:
https://doc.qt.io/qt-6/threads-qobject.html
"""
import gc
from PySide6.QtCore import QObject, QThread, QTimer, Slot


class GuiGarbageCollector(QObject):
    def __init__(self,app,interval=2000):
        super().__init__(app)
        if QThread.currentThread()!=app.thread():raise RuntimeError('GUI collection must be installed on the application thread')
        self.was_enabled=gc.isenabled();self.ticks=0;self.stopped=False;gc.disable()
        self.timer=QTimer(self);self.timer.setInterval(interval);self.timer.timeout.connect(self.collect);self.timer.start()
    @Slot()
    def collect(self):
        if QThread.currentThread()!=self.thread() or self.stopped:return False
        self.ticks+=1
        # Ordinary reference counting continues normally. Reclaim young cycles
        # often and older Qt callback cycles every 30 seconds, between UI events.
        gc.collect(2 if self.ticks%15==0 else 0)
        return True
    def stop(self):
        if QThread.currentThread()!=self.thread():raise RuntimeError('GUI collection must stop on the application thread')
        if self.stopped:return
        self.timer.stop();gc.collect();self.stopped=True
        if self.was_enabled:gc.enable()
