import copy
import json
import os
import tempfile
import unittest
from dataclasses import asdict,replace
from pathlib import Path
from xml.etree import ElementTree as ET
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage,QFontDatabase,QPainterPathStroker
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication,QMainWindow
from lab.presets import preset
from native.graph import NetworkView
from native.exporting import ExportData,ExportStyle,FORMATS,PRESETS,topology_data,export_file,render_preview
from native.topology_export import geometry


class TopologyExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        for filename in ('segoeui.ttf','seguisb.ttf','segoeuib.ttf'):
            font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/filename
            if font.exists():QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.graph=NetworkView();self.project=preset('5g').model_dump();self.graph.set_project(self.project)
        self.data=topology_data(self.graph,'Топология 5G');self.style=replace(ExportStyle(),width=1280,height=800,grid=False)

    def tearDown(self):self.graph.close();self.graph.deleteLater();self.app.processEvents();self.temp.cleanup()

    def write(self,fmt,style=None,data=None):
        path=self.path/('topology.'+fmt);export_file(path,data or self.data,style or self.style,fmt);self.assertGreater(path.stat().st_size,100);return path

    def test_snapshot_includes_offscreen_nodes_and_live_positions_without_mutation(self):
        self.graph.fit_mode=False;self.graph.scale(.05,.05);self.graph.translate(500,100)
        self.graph.node_items['gnb'].setPos(19000,18000);snapshot=topology_data(self.graph,'Live')
        self.assertEqual(len(snapshot.nodes),len(self.project['nodes']));self.assertEqual(next(n for n in snapshot.nodes if n['id']=='gnb')['x'],19000)
        self.graph.node_items['gnb'].setPos(500,500)
        self.assertEqual(next(n for n in snapshot.nodes if n['id']=='gnb')['x'],19000);self.assertEqual(self.project['nodes'][0]['x'],100)
        for style in PRESETS.values():self.assertFalse(render_preview(snapshot,style).isNull())

    def test_selected_edge_includes_endpoints_and_not_unselected_parallel_links(self):
        project=copy.deepcopy(self.project);link=copy.deepcopy(next(l for l in project['links'] if l['id']=='n3'));link['id']='parallel';project['links'].append(link);self.graph.set_project(project)
        self.graph.edge_items['n3'].setSelected(True);snapshot=topology_data(self.graph,'Selected',True)
        self.assertEqual({n['id'] for n in snapshot.nodes},{'gnb','upf'});self.assertEqual([l['id'] for l in snapshot.links],['n3'])
        self.graph.scene_model.clearSelection();self.graph.node_items['gnb'].setSelected(True);self.graph.node_items['upf'].setSelected(True)
        self.assertEqual(len(topology_data(self.graph,'Selected nodes',True).links),2)

    def test_raster_size_dpi_and_transparent_background(self):
        transparent=replace(self.style,transparent_background=True)
        image=QImage(str(self.write('png',transparent)));self.assertEqual((image.width(),image.height()),(1280,800));self.assertEqual(image.pixelColor(0,0).alpha(),0)
        self.assertLess(abs(image.dotsPerMeterX()-round(self.style.dpi/.0254)),2)
        image=QImage(str(self.write('jpeg',replace(transparent,background='#112233'))));self.assertEqual(image.pixelColor(0,0).alpha(),255)
        for actual,expected in zip(image.pixelColor(0,0).getRgb()[:3],(17,34,51)):self.assertLessEqual(abs(actual-expected),3)

    def test_svg_is_vector_and_label_controls_remove_node_names(self):
        self.data.nodes[0]['name']='UniqueNodeLabel';path=self.write('svg');self.assertTrue(QSvgRenderer(str(path)).isValid())
        root=ET.parse(path).getroot();self.assertFalse(root.findall('.//{http://www.w3.org/2000/svg}image'));self.assertIn('UniqueNodeLabel',path.read_text(encoding='utf-8'))
        path=self.write('svg',replace(self.style,node_names=False,node_ids=True,transparent_background=True));text=path.read_text(encoding='utf-8')
        self.assertNotIn('UniqueNodeLabel',text);self.assertIn('gnb',text)

    def test_pdf_contains_searchable_nodes_and_respects_orientation(self):
        from pypdf import PdfReader
        self.data.nodes[0]['name']='UniqueNodeLabel'
        for landscape in (True,False):
            page=PdfReader(self.write('pdf',replace(self.style,landscape=landscape,node_ids=True))).pages[0]
            self.assertIn('UniqueNodeLabel',page.extract_text());self.assertIn('PFCP',page.extract_text());self.assertEqual(len(page.images),0)
            self.assertEqual(float(page.mediabox.width)>float(page.mediabox.height),landscape)

    def test_rendering_does_not_depend_on_ui_theme_or_selection(self):
        from native.style import current_theme,set_theme
        previous=current_theme()
        try:
            set_theme('monochrome');a=render_preview(self.data,PRESETS['Научный'])
            self.graph.node_items['amf'].setSelected(True);set_theme('light_monochrome');b=render_preview(topology_data(self.graph,self.data.title),PRESETS['Научный'])
            self.assertEqual(bytes(a.bits()),bytes(b.bits()))
        finally:set_theme(previous)

    def test_parallel_links_and_far_coordinates_stay_in_export_bounds(self):
        data=copy.deepcopy(self.data);data.nodes[-1].update(x=20000,y=20000)
        link=copy.deepcopy(next(l for l in data.links if l['id']=='n3'))
        for i in range(2):extra=copy.deepcopy(link);extra['id']='parallel-'+str(i);data.links.append(extra)
        nodes,edges,bounds=geometry(data,replace(self.style,node_details=True,node_ids=True))
        self.assertTrue(all(bounds.contains(item['rect']) for item in nodes.values()))
        self.assertTrue(all(bounds.contains(edge['label_rect']) for edge in edges if edge['label_rect'] is not None))
        paths=[edge['path'].pointAtPercent(.5) for edge in edges if edge['link']['id'] in ('n3','parallel-0','parallel-1')]
        self.assertEqual(len({(round(p.x(),3),round(p.y(),3)) for p in paths}),3)
        stroker=QPainterPathStroker();stroker.setWidth(2)
        n3=next(edge for edge in edges if edge['link']['id']=='n3')
        self.assertFalse(any(stroker.createStroke(n3['path']).intersects(nodes[nid]['rect']) for nid in ('amf','scp5g','smf')))
        self.assertFalse(render_preview(data,self.style).isNull())

    def test_invalid_snapshot_keeps_existing_destination_intact(self):
        path=self.path/'topology.png';path.write_bytes(b'existing')
        invalid=copy.deepcopy(self.data);invalid.links[0]['target']='missing'
        for data in (ExportData('topology','Empty'),invalid):
            with self.assertRaises(ValueError):export_file(path,data,self.style,'png')
            self.assertEqual(path.read_bytes(),b'existing')
        self.assertEqual(len(list(self.path.iterdir())),1)

    def test_native_dialog_snapshots_selection_and_loads_separate_style(self):
        from native.export_dialog import ExportDialog
        parent=QMainWindow();parent.settings=QSettings(str(self.path/'settings.ini'),QSettings.Format.IniFormat)
        parent.settings.setValue('export/topologyStyle',json.dumps(asdict(replace(self.style,node_background='#123456'))))
        sources=[dict(key='topology',kind='topology',title='Topology',widget=self.graph)]
        dialog=ExportDialog(parent,sources,'topology');dialog.refresh_preview();self.assertTrue(dialog.save_button.isEnabled(),dialog.error_label.text())
        self.assertEqual(dialog.style().node_background,'#123456');self.assertEqual(tuple(dialog.format_combo.itemData(i) for i in range(dialog.format_combo.count())),FORMATS['topology'])
        self.graph.node_items['gnb'].setPos(500,500);self.assertEqual(dialog.data().nodes[0]['x'],100)
        dialog.scope_combo.setCurrentIndex(1);dialog.refresh_preview();self.assertFalse(dialog.save_button.isEnabled())
        dialog.close();dialog.deleteLater();parent.close();parent.deleteLater();self.app.processEvents()
