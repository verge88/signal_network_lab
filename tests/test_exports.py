import csv
import io
import json
import os
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QTableWidget,QTableWidgetItem
from PySide6.QtGui import QImage,QFontDatabase
from PySide6.QtSvg import QSvgRenderer
from native.exporting import ExportData,ExportStyle,PRESETS,export_file,table_data,render_preview


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        for filename in ('segoeui.ttf','seguisb.ttf','segoeuib.ttf'):
            font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/filename
            if font.exists():QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name);self.style=replace(ExportStyle(),width=960,height=540)
        self.table=ExportData('table','Тест <SS7> & 5G',headers=['Узел','Значение'],rows=[['AMF; "ядро"','1,25'],['=SUM(A1:A5)','две\nстроки'],['UE <&>','Привет']])
        self.chart=ExportData('chart','Задержка · p95',series=[dict(name='Тестовый ряд',points=[(0.,1.25),(1.,2.5),(2.,1.)])],x_label='Время, s',y_label='ms',metadata={'record_id':'fixture'})

    def tearDown(self):self.temp.cleanup()

    def write(self,data,fmt,style=None):
        path=self.directory/('export.'+fmt);export_file(path,data,style or self.style,fmt);self.assertGreater(path.stat().st_size,40);return path

    def test_delimited_roundtrip_preserves_quotes_unicode_newlines_and_rows(self):
        for fmt,delimiter in [('csv',';'),('tsv','\t')]:
            path=self.write(self.table,fmt);rows=list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig')),delimiter=delimiter))
            self.assertEqual(rows,[self.table.headers]+self.table.rows)

    def test_json_keeps_chart_coordinates_and_metadata(self):
        result=json.loads(self.write(self.chart,'json').read_text(encoding='utf-8'))
        self.assertEqual(result['series'][0]['points'],[[0,1.25],[1,2.5],[2,1]])
        self.assertEqual(result['metadata'],{'record_id':'fixture'})
        self.assertEqual(json.loads(self.write(self.table,'json').read_text(encoding='utf-8'))['rows'],self.table.rows)

    def test_raster_dimensions_dpi_and_custom_style_independent_of_ui(self):
        from native.style import current_theme,set_theme
        old=current_theme()
        try:
            set_theme('monochrome');style=replace(self.style,background='#112233',accent='#ee4400',markers=True)
            for fmt in ('png','jpeg'):
                image=QImage(str(self.write(self.chart,fmt,style)));self.assertFalse(image.isNull());self.assertEqual((image.width(),image.height()),(960,540))
                color=image.pixelColor(0,0)
                for actual,expected in zip((color.red(),color.green(),color.blue()),(17,34,51)):self.assertLessEqual(abs(actual-expected),3)
                self.assertLess(abs(image.dotsPerMeterX()-round(style.dpi/.0254)),40)
        finally:set_theme(old)

    def test_svg_is_valid_vector_with_unicode_title(self):
        path=self.write(self.chart,'svg');self.assertTrue(QSvgRenderer(str(path)).isValid())
        root=ET.parse(path).getroot();self.assertTrue(root.findall('.//{http://www.w3.org/2000/svg}path'));self.assertFalse(root.findall('.//{http://www.w3.org/2000/svg}image'))
        self.assertIn(self.chart.title,path.read_text(encoding='utf-8'))

    def test_html_contains_all_rows_and_styling_and_escapes_markup(self):
        text=self.write(self.table,'html',replace(self.style,accent='#123456')).read_text(encoding='utf-8')
        self.assertIn('Тест &lt;SS7&gt; &amp; 5G',text);self.assertIn('UE &lt;&amp;&gt;',text);self.assertIn('две<br>строки',text)
        self.assertEqual(text.count('<td>'),6);self.assertIn('nth-child(even)',text)

    def test_xlsx_has_literal_cells_styles_filter_and_frozen_headers(self):
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(self.write(self.table,'xlsx')) as z:
            self.assertIsNone(z.testzip())
            for name in z.namelist():ET.fromstring(z.read(name))
            sheet=ET.fromstring(z.read('xl/worksheets/sheet1.xml'));styles=ET.fromstring(z.read('xl/styles.xml'))
            cells={c.attrib['r']:c for c in sheet.findall('.//s:c',ns)}
            self.assertEqual(cells['A4'].find('s:is/s:t',ns).text,'=SUM(A1:A5)');self.assertEqual(cells['A4'].attrib['t'],'inlineStr');self.assertIsNone(cells['A4'].find('s:f',ns))
            self.assertEqual(sheet.find('s:autoFilter',ns).attrib['ref'],'A2:B5');self.assertEqual(sheet.find('.//s:pane',ns).attrib['ySplit'],'2')
            self.assertEqual(len(styles.find('s:cellXfs',ns)),4);self.assertIn('FFEEEEEE',z.read('xl/styles.xml').decode())

    def test_pdf_paginates_complete_table_and_repeats_header(self):
        from pypdf import PdfReader
        data=ExportData('table','Исследование',headers=['UniqueColumn','Состояние'],rows=[[f'ROW_{i:04d}','AMF регистрация'] for i in range(160)])
        reader=PdfReader(self.write(data,'pdf'));self.assertGreater(len(reader.pages),2)
        text='\n'.join(page.extract_text() for page in reader.pages)
        for i in range(160):self.assertIn(f'ROW_{i:04d}',text)
        for page in reader.pages:self.assertIn('UniqueColumn',page.extract_text())
        self.assertIn('Задержка',PdfReader(self.write(self.chart,'pdf')).pages[0].extract_text())

    def test_table_scope_hidden_rows_columns_and_visual_order(self):
        w=QTableWidget(4,3);w.setHorizontalHeaderLabels(['A','B','C'])
        for r in range(4):
            for c in range(3):w.setItem(r,c,QTableWidgetItem(f'{r}-{c}'))
        w.hideRow(1);w.hideColumn(1);w.horizontalHeader().moveSection(2,0);w.selectRow(2)
        data=table_data(w,'Selection',True);self.assertEqual(data.headers,['C','A']);self.assertEqual(data.rows,[['2-2','2-0']])
        self.assertEqual(len(table_data(w,'Full').rows),3)

    def test_invalid_export_preserves_destination_and_cleans_temporary_files(self):
        path=self.directory/'export.png';path.write_bytes(b'original')
        for data,style in [(ExportData('chart','Empty'),self.style),(self.chart,replace(self.style,accent='invalid'))]:
            with self.assertRaises(ValueError):export_file(path,data,style,'png')
            self.assertEqual(path.read_bytes(),b'original')
        table=ExportData('table','Long cell',headers=['A'],rows=[['x'*32768]])
        path=self.directory/'export.xlsx';path.write_bytes(b'original')
        with self.assertRaises(ValueError):export_file(path,table,self.style,'xlsx')
        self.assertEqual(path.read_bytes(),b'original');self.assertEqual(len(list(self.directory.iterdir())),2)

    def test_all_presets_render_chart_and_table_previews(self):
        for style in PRESETS.values():
            for data in (self.table,self.chart):
                image=render_preview(data,style);self.assertFalse(image.isNull());self.assertEqual(image.pixelColor(0,0).name(),style.background)


if __name__=='__main__':unittest.main()
