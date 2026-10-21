import json
import os
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtGui import QImage,QPainter
from PySide6.QtSvg import QSvgRenderer
from shiboken6 import delete
from lab.simulator import simulate
from lab import storage
from lab.procedure_trace import causal_chain
from native.procedure_dialog import ProcedureDialog
from native.style import set_theme,current_theme,stylesheet,application_palette,themed_color
from test_procedures import network
from test_fiveg import core


class Owner(QWidget):
    def __init__(self,directory):
        super().__init__();self.settings=QSettings(str(directory/'test.ini'),QSettings.Format.IniFormat);self.compute_buttons=[];self.active_job=None;self.run_record=None
    def call(self,fn,*args):return fn(*args)
    def start_job(self,title,fn,done):done(fn(lambda value:None,lambda:False))
    def refresh_measurements(self):pass
    def refresh_library(self):pass


class ProcedureUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);project=network();project.journal_procedure_id=1
        cls.record=dict(id='fixture',project=project.model_dump(),**simulate(project))
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.path=Path(self.temporary.name);self.owner=Owner(self.path);self.dialog=ProcedureDialog(self.owner);self.original_theme=current_theme()
    def tearDown(self):
        self.dialog.close();self.dialog.deleteLater();self.owner.close();self.owner.deleteLater();self.app.processEvents();set_theme(self.original_theme);self.app.setStyleSheet(stylesheet());self.app.setPalette(application_palette());self.temporary.cleanup()
    def test_stepper_and_cause_links_project_state_at_the_selected_event(self):
        self.dialog.set_run(self.record,1);self.dialog.show();self.app.processEvents();self.assertEqual(self.dialog.session['procedure_id'],1)
        last=self.dialog.cursor;self.assertGreater(last,10);self.dialog.goto(0)
        self.assertEqual(self.dialog.timers.rowCount(),0);self.assertEqual(self.dialog.contexts.rowCount(),0)
        self.assertIn('Начало процедуры',self.dialog.details.toPlainText());self.dialog.goto(last)
        event=self.dialog.session['events'][last]
        if event['cause_event']:self.dialog.goto_cause();self.assertEqual(self.dialog.cursor,event['cause_event']-1)
        self.dialog.slider.setValue(5);self.assertEqual(self.dialog.cursor,5)
        self.dialog.events_table.selectRow(8);self.assertEqual(self.dialog.cursor,8)
        self.dialog.goto(last);self.app.processEvents();self.assertGreater(self.dialog.diagram.verticalScrollBar().value(),0)
    def test_no_journal_and_old_runs_are_explicit_and_can_be_recorded_separately(self):
        legacy=dict(self.record);legacy.pop('procedure_journal');self.dialog.set_run(legacy,1)
        self.assertIn('Подробного журнала нет',self.dialog.info.text());self.assertEqual(self.dialog.diagram.scene_model.items(),[])
        with patch.object(storage,'DATA',self.path):self.dialog.replay()
        self.assertNotEqual(self.owner.run_record['id'],'fixture');self.assertEqual(self.dialog.session['procedure_id'],1)
        self.assertTrue(self.owner.run_record['replay']['same_summary'])
    def test_playback_stops_on_hide_and_session_switch(self):
        self.dialog.set_run(self.record,1);self.dialog.show();self.dialog.goto(0);self.dialog.toggle_play();self.assertTrue(self.dialog.timer.isActive())
        self.dialog.play_step();self.assertEqual(self.dialog.cursor,1);self.dialog.hide();self.assertFalse(self.dialog.timer.isActive())
        self.dialog.choose_session();self.assertFalse(self.dialog.timer.isActive())
        extra_owner=Owner(self.path);extra=ProcedureDialog(extra_owner);extra.set_run(self.record,1)
        item=next(item for item in extra.diagram.scene_model.items() if item.data(0)==extra.cursor)
        item.setSelected(True);self.assertTrue(extra.diagram.scene_model.selectedItems());errors=[]
        with patch.object(sys,'excepthook',lambda kind,value,tb:errors.append(str(value))):delete(extra_owner);self.app.processEvents()
        self.assertFalse(errors)
    def test_fragment_exports_are_native_and_session_json_round_trips(self):
        self.dialog.set_run(self.record,1);self.dialog.show();self.app.processEvents()
        for fmt in ('svg','png','pdf'):
            target=self.path/('fragment.'+fmt);self.dialog.diagram.export_fragment(target,fmt);self.assertGreater(target.stat().st_size,100)
        self.assertIn('<svg',(self.path/'fragment.svg').read_text(encoding='utf-8'))
        rendered=QImage(300,300,QImage.Format.Format_ARGB32);rendered.fill(0xffffffff);painter=QPainter(rendered)
        try:QSvgRenderer(str(self.path/'fragment.svg')).render(painter)
        finally:painter.end()
        self.assertEqual(rendered.pixelColor(1,1).name(),themed_color('#141c27').name())
        with patch('native.procedure_dialog.QFileDialog.getSaveFileName',return_value=(str(self.path/'session.json'),'JSON (*.json)')):self.dialog.export_json()
        saved=json.loads((self.path/'session.json').read_text(encoding='utf-8'));self.assertEqual(saved['session']['events'],self.dialog.session['events'])
    def test_all_themes_and_switching_between_detailed_and_missing_sessions(self):
        for theme in ('monochrome','light_monochrome','dark','graphite','porcelain'):
            set_theme(theme);self.app.setPalette(application_palette());self.app.setStyleSheet(stylesheet());self.dialog.set_run(self.record,1);self.dialog.show();self.app.processEvents()
            self.dialog.future.setChecked(True);self.dialog.goto(0);self.assertGreater(len(self.dialog.diagram.scene_model.items()),0)
        self.dialog.set_run(dict(id='empty',project=self.record['project'],traces=[]),None);self.assertIsNone(self.dialog.session)
    def test_wide_fiveg_sequence_scrolls_to_the_selected_network_function(self):
        project=core();project.journal_procedure_id=1;record=dict(id='5g',project=project.model_dump(),**simulate(project))
        self.dialog.set_run(record,1);self.dialog.show();self.app.processEvents()
        index=next(i for i,event in enumerate(self.dialog.session['events']) if event.get('node')=='udr')
        self.dialog.goto(index);self.app.processEvents();self.assertGreater(self.dialog.diagram.horizontalScrollBar().value(),0)
