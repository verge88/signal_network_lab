import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QUrl
from PySide6.QtWidgets import QApplication
from native.help_page import HelpPage,load_chapters,handbook_path
from lab.schema import NODE_KINDS,LINK_PROTOCOLS


class HandbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):self.page=HelpPage();self.page.resize(1050,700);self.page.show();self.app.processEvents()
    def tearDown(self):self.page.close();self.page.deleteLater();self.app.processEvents()
    def test_manual_has_full_coverage_and_no_duplicate_chapters(self):
        chapters=load_chapters();self.assertGreaterEqual(len(chapters),25);self.assertEqual(len({c['title'] for c in chapters}),len(chapters))
        glossary=next(c['text'] for c in chapters if c['title']=='Словарь сокращений')
        for term in NODE_KINDS+LINK_PROTOCOLS+['ACK','GTT','RTO','PDU','S-NSSAI','ROC','FPR','CSV','DPI']:self.assertIn(term,glossary)
        self.assertIn('не входят',next(c['text'] for c in chapters if c['title']=='Подготовка ML-датасета'))
    def test_search_opens_matching_chapter_highlights_and_recovers_from_no_results(self):
        self.page.search.setText('коэффициент вариации');self.app.processEvents()
        self.assertEqual(self.page.contents.currentItem().text(),'Словарь сокращений');self.assertTrue(self.page.matches)
        self.assertEqual(self.page.reader.textCursor().selectedText().casefold(),'коэффициент вариации')
        self.page.search.setText('слово_которого_точно_нет_в_справке');self.assertIn('Ничего не найдено',self.page.status.text());self.assertFalse(self.page.next.isEnabled())
        self.page.search.clear();self.assertTrue(all(not self.page.contents.item(i).isHidden() for i in range(self.page.contents.count())))
        self.page.open_section('Основы сигнальных сетей');self.assertIn('Сигнализация',self.page.reader.toPlainText())
    def test_matches_can_be_navigated_and_sources_do_not_open_automatically(self):
        self.page.open_section('Словарь сокращений');self.page.search.setText('Function');self.app.processEvents();self.assertGreater(len(self.page.matches),2)
        before=self.page.reader.textCursor().selectionStart();self.page.next_match(1);self.assertNotEqual(before,self.page.reader.textCursor().selectionStart())
        with patch('native.help_page.QDesktopServices.openUrl') as opener:
            self.page.open_reference(QUrl('file:///C:/private.txt'));self.page.open_reference(QUrl('https://example.com/'));opener.assert_not_called()
            self.page.open_reference(QUrl('https://www.rfc-editor.org/rfc/rfc6733.html'));opener.assert_called_once()
    def test_frozen_build_finds_bundled_handbook(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'docs').mkdir();(root/'docs'/'user-guide.md').write_text('## Проверка\n\nВстроенный текст.',encoding='utf-8')
            with patch.object(sys,'_MEIPASS',directory,create=True):self.assertEqual(handbook_path(),root/'docs'/'user-guide.md');self.assertEqual(load_chapters()[0]['title'],'Проверка')
