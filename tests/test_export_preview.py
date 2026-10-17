import copy
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QRectF,QSize,Qt
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import QApplication
from native.exporting import ExportData,ExportStyle
from native.export_preview import ExportPreview


class ExportPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.preview=ExportPreview();self.preview.resize(760,520);self.preview.show();self.app.processEvents()
        self.style=ExportStyle(width=1800,height=1100,grid=False)
        self.data=ExportData('topology','Чёткий предпросмотр',nodes=[dict(id='amf',name='UniquePreviewAMF',kind='AMF',x=100,y=100,enabled=True,capacity=1000,queue_limit=100)],links=[])
    def tearDown(self):self.preview.close();self.preview.deleteLater();self.app.processEvents()
    def test_actual_size_zoom_and_fit_follow_output_dimensions(self):
        self.preview.set_content(self.data,self.style);self.assertEqual(self.preview.sceneRect(),QRectF(0,0,1800,1100))
        fitted=self.preview.transform().m11();self.assertLess(fitted,1)
        self.preview.set_zoom(1);self.assertEqual(self.preview.transform().m11(),1);self.assertGreater(self.preview.horizontalScrollBar().maximum(),0)
        self.preview.zoom_by(2);self.assertEqual(self.preview.transform().m11(),2)
        self.preview.fit_page();self.assertAlmostEqual(self.preview.transform().m11(),fitted)
        self.preview.resize(1000,650);self.app.processEvents();self.assertGreater(self.preview.transform().m11(),fitted)
    def test_preview_scene_renders_vector_text_instead_of_a_scaled_bitmap(self):
        self.preview.set_content(self.data,self.style);self.preview.set_zoom(2)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'preview.svg';svg=QSvgGenerator();svg.setFileName(str(path));svg.setSize(QSize(3600,2200));svg.setViewBox(QRectF(0,0,3600,2200))
            painter=QPainter(svg)
            try:self.preview.scene().render(painter,QRectF(0,0,3600,2200),self.preview.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)
            finally:painter.end()
            del painter,svg
            root=ET.parse(path).getroot();self.assertFalse(root.findall('.//{http://www.w3.org/2000/svg}image'))
            self.assertIn('UniquePreviewAMF',path.read_text(encoding='utf-8'))
    def test_style_updates_preserve_zoom_and_table_preview_keeps_first_twelve_rows(self):
        before=copy.deepcopy(self.data);self.preview.set_content(self.data,self.style);self.preview.set_zoom(1.5)
        self.preview.set_content(self.data,replace(self.style,font_size=20));self.assertEqual(self.preview.transform().m11(),1.5);self.assertEqual(self.data,before)
        table=ExportData('table','Rows',headers=['Column'],rows=[[f'UNIQUE_ROW_{i:02d}'] for i in range(20)])
        self.preview.set_content(table,self.style);text=self.preview.page.document.toPlainText()
        self.assertIn('UNIQUE_ROW_11',text);self.assertNotIn('UNIQUE_ROW_12',text);self.assertEqual(len(table.rows),20)
