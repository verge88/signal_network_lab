import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget
from native.startup import StartupSplash,SPLASH_COLORS


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_small_card_respects_all_themes_and_display_density(self):
        for theme,colors in SPLASH_COLORS.items():
            with self.subTest(theme=theme):
                splash=StartupSplash(theme)
                try:
                    self.assertEqual((splash.width(),splash.height()),(400,176))
                    image=splash.pixmap().toImage();dpr=image.devicePixelRatio()
                    self.assertEqual(image.pixelColor(round(200*dpr),round(95*dpr)).name(),colors[0])
                    self.assertEqual(round(image.width()/dpr),400)
                    splash.update_status('Подготовка интерфейса…');self.assertEqual(splash.status,'Подготовка интерфейса…')
                finally:splash.close();splash.deleteLater()
        self.app.processEvents()
    def test_click_cannot_dismiss_loading_card_before_main_window_is_shown(self):
        window=QWidget();splash=StartupSplash('monochrome')
        try:
            splash.show_startup();self.assertTrue(splash.isVisible());self.assertFalse(splash.complete(window))
            QTest.mouseClick(splash,Qt.MouseButton.LeftButton,pos=QPoint(150,90));self.assertTrue(splash.isVisible())
            window.show();self.app.processEvents();self.assertTrue(splash.complete(window));self.assertTrue(splash.ready)
            self.assertTrue(window.isVisible());self.assertFalse(splash.isVisible())
        finally:splash.close();window.close();splash.deleteLater();window.deleteLater();self.app.processEvents()
