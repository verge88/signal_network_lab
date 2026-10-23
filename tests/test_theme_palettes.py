import os
import tempfile
import unittest
from unittest.mock import patch,Mock
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor,QPalette
from PySide6.QtWidgets import QApplication
from native import style
from native.theme_palettes import EDITOR_PALETTES,PALETTE_LABELS,LEGACY_ROLES
from native.palette_dialog import PaletteDialog,palette_text
from native.startup import SPLASH_COLORS


def contrast(a,b):
    def luminance(color):
        channels=QColor(color).getRgbF()[:3]
        channels=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in channels]
        return sum(v*weight for v,weight in zip(channels,(.2126,.7152,.0722)))
    values=sorted((luminance(a),luminance(b)))
    return (values[1]+.05)/(values[0]+.05)


class ThemePaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):self.original=style.current_theme()
    def tearDown(self):style.set_theme(self.original);self.app.processEvents()
    def test_palette_roles_and_readable_text_pairs(self):
        for key,palette in EDITOR_PALETTES.items():
            with self.subTest(theme=key):
                self.assertEqual(set(palette)-{'name'},set(PALETTE_LABELS))
                for value in list(palette.values())[1:]:self.assertTrue(QColor(value).isValid())
                for foreground,background in [('text','bg'),('text','panel'),('text','input'),('muted','bg'),('muted','panel'),('on_accent','accent'),('on_accent','accent_hover'),('selection_text','selection'),('accent_text','bg')]:
                    self.assertGreaterEqual(contrast(palette[foreground],palette[background]),4.5,(key,foreground,background))
    def test_resolved_painter_colors_are_stable_and_preserve_alpha(self):
        for key,palette in EDITOR_PALETTES.items():
            style.set_theme(key)
            for source in LEGACY_ROLES:
                value=QColor(source);value.setAlpha(39);resolved=style.themed_color(value)
                self.assertEqual(resolved.alpha(),39)
                self.assertEqual(style.themed_color(resolved),resolved,(key,source))
            for source in ('#11151c','#141c27'):
                self.assertEqual(style.themed_color(source).name(),palette['bg'])
            self.assertEqual(style.application_palette().color(QPalette.ColorRole.HighlightedText).name(),palette['selection_text'])
            self.assertIn('QPushButton#Primary:disabled',style.stylesheet())
    def test_theme_selection_persists_and_splash_uses_same_colors(self):
        with tempfile.TemporaryDirectory() as directory:
            for key,palette in EDITOR_PALETTES.items():
                settings=QSettings(directory+'/themes.ini',QSettings.Format.IniFormat)
                settings.setValue('theme',key);settings.sync()
                self.assertEqual(style.saved_theme(QSettings(directory+'/themes.ini',QSettings.Format.IniFormat)),key)
                self.assertEqual(SPLASH_COLORS[key],tuple(palette[role] for role in ('bg','text','muted','border','accent')))
                self.assertNotIn('vscode',style.THEMES[key].lower())
    def test_palette_preview_does_not_apply_until_requested_and_copies_exact_colors(self):
        callback=Mock();dialog=PaletteDialog(theme='graphite',apply=callback)
        try:
            dialog.selector.setCurrentIndex(dialog.selector.findData('porcelain'))
            callback.assert_not_called();self.assertEqual(style.current_theme(),self.original)
            self.assertEqual(len(dialog.swatches),len(PALETTE_LABELS))
            clipboard=Mock()
            with patch('native.palette_dialog.QApplication') as app:
                app.clipboard.return_value=clipboard
                dialog.color_buttons[0].click();clipboard.setText.assert_called_with('#F3F6FB')
                dialog.copy_all.click();clipboard.setText.assert_called_with(palette_text('porcelain'))
            dialog.apply_button.click();callback.assert_called_once_with('porcelain')
        finally:dialog.deleteLater()
