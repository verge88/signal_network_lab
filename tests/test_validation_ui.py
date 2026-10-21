import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QSettings,Qt,QPoint
from PySide6.QtWidgets import QApplication,QWidget,QDialog
from PySide6.QtTest import QTest
from lab import storage
from lab.validation import Options,STATUS
from native.validation_dialog import ValidationDialog,ValidationOptions
from native.exporting import chart_data,table_data,export_file,ExportStyle
from test_validation import fixture_report,measured_rows,csv_text,simulation_fixture
from test_procedures import network


class Owner(QWidget):
    def __init__(self,directory):
        super().__init__();self.settings=QSettings(str(directory/'settings.ini'),QSettings.Format.IniFormat);self.compute_buttons=[];self.active_job=None;self.project=network().model_dump();self.jobs=[]
    def call(self,fn,*args):return fn(*args)
    def start_job(self,title,work,done):self.jobs.append(title);done(work(lambda value:None,lambda:False))


class ValidationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.directory=Path(self.temporary.name);self.storage_patch=patch.object(storage,'DATA',self.directory);self.storage_patch.start()
        self.owner=Owner(self.directory);self.dialog=ValidationDialog(self.owner)
    def tearDown(self):
        self.dialog.close();self.dialog.deleteLater();self.owner.close();self.owner.deleteLater();self.app.processEvents();self.storage_patch.stop();self.temporary.cleanup()
    def test_native_workspace_keeps_plots_history_and_frozen_plan(self):
        result=fixture_report();self.dialog.accept_result(result,'Fixture');self.app.processEvents()
        self.assertEqual(self.dialog.conditions.rowCount(),1);self.assertEqual(len(self.dialog.cdf_chart.series),2)
        self.assertTrue(self.dialog.cdf_chart.series[1]['dash']);self.assertEqual(self.dialog.metric_combo.currentData(),'latency_p95')
        plan=copy.deepcopy(self.dialog.record['policy']);self.dialog.options.max_ks=.9;self.assertEqual(self.dialog.record['policy'],plan)
        record_id=self.dialog.record['id'];self.dialog.reload_history();self.assertEqual(self.dialog.history.currentData(),record_id)
        self.dialog.history.setCurrentIndex(0);self.dialog.history.setCurrentIndex(self.dialog.history.findData(record_id));self.assertEqual(self.dialog.record['id'],record_id)
        self.assertIn('Внешняя точность',self.dialog.info.text());self.assertIn(result['policy_sha256'],self.dialog.report_view.toPlainText())
    def test_options_validate_required_metrics_and_expected_conditions(self):
        dialog=ValidationOptions(self.dialog,Options())
        try:
            dialog.add_condition('отказ HSS',55);dialog.accept();self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
            self.assertEqual(dialog.result_options.expected_conditions[0].offered_rate,55)
            for index in range(dialog.metrics.rowCount()):dialog.metrics.cellWidget(index,1).setChecked(False)
            dialog.setResult(0);dialog.accept();self.assertEqual(dialog.result(),0);self.assertTrue(dialog.error.text())
        finally:dialog.deleteLater()
    def test_compare_uses_snapshot_and_detects_saved_calibration_evidence(self):
        self.dialog.set_measurements(csv_text(measured_rows()),'Synthetic');self.dialog.options.seeds=1
        storage.save('calibration','Used measurement',dict(evidence=dict(observations=[dict(group_id='m-0')])) )
        before=copy.deepcopy(self.owner.project)
        with patch('lab.validation.simulate',side_effect=simulation_fixture):self.dialog.compare()
        self.assertEqual(self.owner.project,before);self.assertEqual(self.dialog.record['conditions'][0]['independent_runs'],2)
        self.assertEqual(self.dialog.record['conditions'][0]['status'],'insufficient');self.assertEqual(len(self.owner.jobs),1)
    def test_visual_exports_and_complete_json_html_report(self):
        self.dialog.accept_result(fixture_report(),'Exports');self.assertEqual(len(self.dialog.export_sources()),4)
        data=chart_data(self.dialog.cdf_chart,'CDF');export_file(self.directory/'cdf.svg',data,ExportStyle(),'svg')
        self.assertIn('<svg',(self.directory/'cdf.svg').read_text(encoding='utf-8'))
        with patch('native.validation_dialog.QFileDialog.getSaveFileName',return_value=(str(self.directory/'report.json'),'JSON (*.json)')):self.dialog.save_report()
        saved=json.loads((self.directory/'report.json').read_text(encoding='utf-8'));self.assertEqual(saved['policy_sha256'],self.dialog.record['policy_sha256'])
        with patch('native.validation_dialog.QFileDialog.getSaveFileName',return_value=(str(self.directory/'report.html'),'HTML (*.html)')):self.dialog.save_report()
        html=(self.directory/'report.html').read_text(encoding='utf-8');self.assertIn('<svg',html);self.assertNotIn('<script',html)
    def test_partial_distribution_is_safe_to_hover_and_clear(self):
        rows=measured_rows()
        for row in rows:row.pop('latency_samples_ms')
        self.dialog.accept_result(fixture_report(rows),'Missing distribution');self.dialog.show();self.app.processEvents()
        chart=self.dialog.cdf_chart;QTest.mouseMove(chart,QPoint(chart.width()//2,chart.height()//2));self.app.processEvents()
        self.dialog.history.setCurrentIndex(0);self.assertEqual(self.dialog.cdf_chart.series,[])
