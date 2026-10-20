import copy
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QDialog
from lab.presets import preset
from native.dialogs import node_editor,link_editor,flow_editor,Editor
from native.service_lists import ConfigList,ListDialog


class ServiceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):self.project=preset('5g-mobile').model_dump()
    def tearDown(self):self.app.processEvents()
    def test_node_flow_and_link_resource_fields_roundtrip(self):
        n=node_editor(None,self.project['nodes'][0]);f=flow_editor(None,self.project,self.project['flows'][0]);l=link_editor(None,self.project)
        try:
            self.assertEqual(n.values()['context_limit'],2000);self.assertEqual(f.values()['mobility'][0]['gnb'],'gnb2')
            self.assertIsNone(l.values()['peer_routing_context']);self.assertIsNone(l.values()['shutdown_at_s']);l.fields['peer_routing_context'].setText('123');self.assertEqual(l.values()['peer_routing_context'],123)
        finally:
            for d in (n,f,l):d.deleteLater()
    def test_rules_use_native_field_controls_and_keep_copy_isolated(self):
        dialog=ListDialog(None,self.project,'upf',[])
        def fill():
            d=dialog.findChildren(Editor)[-1];d.fields['name'].setText('Drop interactive');d.fields['action'].setCurrentIndex(d.fields['action'].findData('drop'));d.fields['dnn'].setText('internet');d.accept()
        try:
            QTimer.singleShot(0,fill);dialog.edit();self.assertEqual(dialog.values()[0]['action'],'drop');self.assertEqual(dialog.values()[0]['dnn'],'internet')
            dialog.table.selectRow(0);dialog.remove();self.assertEqual(dialog.values(),[])
        finally:dialog.deleteLater()
    def test_mobility_editor_uses_typed_target_selectors(self):
        dialog=ListDialog(None,self.project,'mobility',[])
        def fill():
            d=dialog.findChildren(Editor)[-1]
            for key,target in [('gnb','gnb2'),('amf','amf2'),('upf','upf2')]:d.fields[key].setCurrentIndex(d.fields[key].findData(target))
            d.fields['time'].setValue(4);d.accept()
        try:
            QTimer.singleShot(0,fill);dialog.edit();self.assertEqual(dialog.values()[0]['upf'],'upf2');self.assertEqual(dialog.values()[0]['time'],4)
        finally:dialog.deleteLater()
    def test_config_list_cannot_mutate_owner_values_before_accept(self):
        items=copy.deepcopy(self.project['flows'][0]['mobility']);w=ConfigList(None,self.project,'mobility',items)
        try:
            values=w.value();values[0]['time']=99;self.assertEqual(w.value()[0]['time'],20);self.assertEqual(items[0]['time'],20)
        finally:w.deleteLater()
