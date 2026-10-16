import gc
import os
import threading
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from native.qt_gc import GuiGarbageCollector


class GuiCollectionTests(unittest.TestCase):
    def test_worker_cycles_are_finalized_on_the_gui_thread(self):
        app=QApplication.instance() or QApplication([]);was_enabled=gc.isenabled();collector=GuiGarbageCollector(app)
        finalized=[];worker_results=[];main_thread=threading.get_ident()
        class Cycle:
            def __init__(self):self.loop=self
            def __del__(self):finalized.append(threading.get_ident())
        def worker():
            Cycle();worker_results.append(collector.collect());worker_results.append(gc.isenabled())
        try:
            thread=threading.Thread(target=worker);thread.start();thread.join()
            self.assertEqual(worker_results,[False,False]);self.assertEqual(finalized,[])
            self.assertTrue(collector.collect());self.assertEqual(finalized,[main_thread])
        finally:collector.stop()
        self.assertEqual(gc.isenabled(),was_enabled)
