import copy
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QDialog,QDialogButtonBox
from lab.presets import preset
from lab.attacks import from_preset
from native.attacks import AttackEditor,StepEditor


class AttackUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):self.project=preset('diameter');self.project.duration=20;self.source=self.project.model_dump();self.scenario=from_preset(self.project,'diameter_flood').model_dump()
    def tearDown(self):self.app.processEvents()
    def test_old_run_snapshots_keep_their_topology_overlay(self):
        from native.window import fingerprint
        old=copy.deepcopy(self.source);old.pop('attacks',None)
        self.assertEqual(fingerprint(old),fingerprint(self.source))
    def test_staged_editor_does_not_modify_project_before_accept(self):
        before=copy.deepcopy(self.source);dialog=AttackEditor(None,self.source,self.scenario)
        try:
            dialog.clone_step();dialog.start.setValue(4);dialog.name.setText('Custom');self.assertEqual(self.source,before)
            dialog.accept();self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted);self.assertEqual(len(dialog.result_data['steps']),2);self.assertEqual(dialog.result_data['name'],'Custom')
        finally:dialog.deleteLater()
    def test_invalid_schedule_disables_save_and_remains_editable(self):
        dialog=AttackEditor(None,self.source,self.scenario)
        try:
            dialog.repeats.setValue(10);self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled());self.assertIn('выходит',dialog.error.text())
            dialog.repeats.setValue(1);self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        finally:dialog.deleteLater()
    def test_imported_missing_target_requires_explicit_remapping(self):
        self.scenario['steps'][0]['target']='another-network';dialog=AttackEditor(None,self.source,self.scenario)
        try:
            self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
            step=StepEditor(dialog,self.source,self.scenario['steps'][0]);self.assertEqual(step.target.currentData(),'another-network');step.accept();self.assertNotEqual(step.result(),QDialog.DialogCode.Accepted)
            step.target.setCurrentIndex(0);step.accept();dialog.steps[0]=step.result_data;dialog.refresh();self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled());step.deleteLater()
        finally:dialog.deleteLater()
    def test_empty_scenario_requires_at_least_one_step(self):
        dialog=AttackEditor(None,self.source,self.scenario)
        try:
            dialog.remove_step();self.assertEqual(dialog.steps,[]);self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        finally:dialog.deleteLater()
    def test_effect_switch_updates_resource_choices_and_numeric_units(self):
        dialog=StepEditor(None,self.source)
        try:
            dialog.effect.setCurrentIndex(dialog.effect.findData('node_capacity'));self.assertEqual(dialog.magnitude.maximum(),1);self.assertFalse(dialog.direction.isEnabled());self.assertEqual(dialog.target.count(),len(self.source['nodes']))
            dialog.effect.setCurrentIndex(dialog.effect.findData('link_down'));self.assertFalse(dialog.profile.isEnabled());self.assertEqual(dialog.target.count(),len(self.source['links']))
            dialog.effect.setCurrentIndex(dialog.effect.findData('loss_absolute'));dialog.profile.setCurrentIndex(dialog.profile.findData('pulse'));self.assertTrue(dialog.period.isEnabled());self.assertTrue(dialog.direction.isEnabled())
        finally:dialog.deleteLater()
