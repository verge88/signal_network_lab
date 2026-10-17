import os
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from shiboken6 import delete
from lab import storage
from native.window import MainWindow


class WindowShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_dirty_history_does_not_call_deleted_window_during_destruction(self):
        for expanded in (False,True):
            with self.subTest(expanded=expanded),tempfile.TemporaryDirectory() as directory,patch.object(storage,'DATA',Path(directory)):
                errors=[]
                with patch.object(sys,'excepthook',lambda *args:errors.append(args)):
                    window=MainWindow();window.settings=QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat);window.show()
                    window.mutate('Shutdown regression',lambda p:p['nodes'][0].update(capacity=1234))
                    self.assertFalse(window.undo.isClean());self.assertTrue(window.isWindowModified())
                    window.undo.setClean();self.assertFalse(window.isWindowModified());window.undo.undo();self.assertTrue(window.isWindowModified())
                    if expanded:window.set_topology_expanded(True)
                    self.app.processEvents();window.timer.stop();delete(window);self.app.processEvents()
                self.assertEqual(errors,[],''.join(''.join(traceback.format_exception(*error)) for error in errors))
