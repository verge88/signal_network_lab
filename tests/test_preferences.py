import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QSettings,QPoint,QPointF,Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import delete
from lab import storage
from lab.presets import preset
from native import preferences,style
from native.window import MainWindow


class PreferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.original_theme=style.current_theme();self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=patch.object(storage,'DATA',self.root);self.store.start();self.settings=QSettings(str(self.root/'settings.ini'),QSettings.Format.IniFormat)
        self.windows=[];self.factory=patch('native.window.QSettings',return_value=self.settings);self.factory.start()
    def tearDown(self):
        for window in self.windows:window.timer.stop();delete(window)
        self.app.processEvents();self.factory.stop();self.store.stop();self.temp.cleanup();style.set_theme(self.original_theme)
    def window(self):
        window=MainWindow();window.show();self.app.processEvents();self.windows.append(window);return window
    def test_invalid_stored_preferences_fall_back_without_touching_research_options(self):
        for key,value in {'theme':'unknown','overviewHeight':'bad','graph/panSpeed':'nan','graph/zoomFactor':20,'preferences/startPage':50,'sidebarCollapsed':'not-a-bool','export/directory':str(self.root/'missing')}.items():self.settings.setValue(key,value)
        self.settings.setValue('calibration/options','{"keep":true}')
        self.assertEqual(preferences.load(self.settings),preferences.defaults())
        self.assertEqual(self.settings.value('calibration/options'),' {"keep":true}'.strip())
    def test_settings_navigation_and_live_updates(self):
        w=self.window();before=copy.deepcopy(w.project);history=w.undo.count();w.nav_buttons[7].click();self.app.processEvents()
        self.assertEqual(w.pages.currentIndex(),7);self.assertEqual(w.title.text(),'Настройки программы');self.assertTrue(w.run_button.isHidden())
        page=w.settings_page;page.fields['theme'].setCurrentIndex(page.fields['theme'].findData('porcelain'))
        self.assertEqual(w.theme,'porcelain');self.assertTrue(w.theme_actions['porcelain'].isChecked())
        page.fields['sidebarCollapsed'].setChecked(True);self.assertTrue(w.sidebar_collapsed);self.assertEqual(w.sidebar.width(),72)
        page.fields['graph/grid'].setChecked(False);page.fields['graph/linkLabels'].setChecked(False)
        self.assertFalse(w.graph.show_grid);self.assertTrue(all(not edge.show_label for edge in w.graph.edge_items.values()))
        page.fields['preferences/statusBar'].setChecked(False);self.assertTrue(w.statusBar().isHidden())
        page.fields['topologyMetrics'].setChecked(True);w.show_page(0);self.app.processEvents();self.assertTrue(w.metric_widget.isVisible())
        self.assertEqual(before,w.project);self.assertEqual(history,w.undo.count())
        w.install_project(w.project);self.assertTrue(all(not edge.show_label for edge in w.graph.edge_items.values()))
    def test_changes_persist_across_new_window_and_start_page(self):
        w=self.window();w.apply_preferences({'theme':'light_monochrome','sidebarCollapsed':True,'topologyMetrics':True,'graph/grid':False,'graph/panSpeed':2.5,'graph/zoomFactor':1.3,'preferences/startPage':7,'export/directory':str(self.root)})
        self.settings.sync();second=self.window()
        self.assertEqual(second.pages.currentIndex(),7);self.assertTrue(second.nav_buttons[7].isChecked());self.assertEqual(second.theme,'light_monochrome')
        self.assertTrue(second.sidebar_collapsed);self.assertTrue(second.topology_metrics_action.isChecked());self.assertFalse(second.graph.show_grid)
        self.assertEqual(second.graph.pan_speed,2.5);self.assertEqual(second.graph.zoom_factor,1.3);self.assertEqual(second.settings_page.fields['export/directory'].text(),str(self.root))
    def test_menu_actions_and_export_dialog_settings_are_reflected(self):
        w=self.window();w.apply_theme('monochrome');w.set_sidebar_collapsed(True);w.set_overview_height(190);w.topology_metrics_action.setChecked(True)
        self.settings.setValue('export/directory',str(self.root));w.show_page(7)
        self.assertEqual(w.settings_page.fields['theme'].currentData(),'monochrome');self.assertTrue(w.settings_page.fields['sidebarCollapsed'].isChecked())
        self.assertEqual(w.settings_page.fields['overviewHeight'].value(),190);self.assertTrue(w.settings_page.fields['topologyMetrics'].isChecked())
        self.assertEqual(w.settings_page.fields['export/directory'].text(),str(self.root));self.assertFalse(w.settings_page.palette_button.isEnabled())
    def test_reset_preserves_projects_results_and_unrelated_settings(self):
        record=storage.save('project','Keep',dict(project=preset('5g').model_dump()))
        self.settings.setValue('calibration/options','keep');self.settings.setValue('export/style','keep-style');self.settings.setValue('testbed/selected','keep-profile')
        w=self.window();run=storage.save('run','Keep run',dict(summary={'sent':5}));w.apply_preferences({'theme':'porcelain','graph/grid':False,'preferences/startPage':4});w.settings_page.reset()
        self.assertEqual(preferences.load(self.settings),preferences.defaults());self.assertEqual(storage.get('project',record['id'])['name'],'Keep');self.assertEqual(storage.get('run',run['id'])['summary'],{'sent':5})
        self.assertEqual(self.settings.value('calibration/options'),'keep');self.assertEqual(self.settings.value('export/style'),'keep-style');self.assertEqual(self.settings.value('testbed/selected'),'keep-profile')
    def test_restore_workspace_and_geometry_can_be_disabled(self):
        (self.root/'desktop-workspace.json').write_text(json.dumps(preset('5g').model_dump()),encoding='utf-8')
        self.settings.setValue('preferences/restoreWorkspace',False);self.settings.setValue('preferences/rememberWindow',False)
        self.settings.setValue('geometry','previous-geometry')
        with patch.object(MainWindow,'restoreGeometry') as restore:
            w=self.window();restore.assert_not_called()
        self.assertEqual(w.project['name'],preset().name)
    def test_fit_can_be_disabled_without_affecting_manual_fit(self):
        w=self.window();w.apply_preferences({'preferences/fitOnTopology':False})
        with patch.object(w.graph,'fit') as fit:
            w.show_page(1);w.show_page(0);self.app.processEvents();fit.assert_not_called()
        w.graph.fit();self.assertTrue(w.graph.fit_mode)
    def test_configured_scroll_speed_and_zoom_are_used(self):
        w=self.window();w.apply_preferences({'graph/panSpeed':2.,'graph/zoomFactor':1.25});position=QPointF(w.graph.viewport().rect().center())
        event=QWheelEvent(position,position,QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        with patch.object(w.graph,'pan_view') as pan:w.graph.wheelEvent(event);self.assertEqual(pan.call_args.args[0],QPointF(0,120))
        w.graph.resetTransform();event=QWheelEvent(position,position,QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.ControlModifier,Qt.ScrollPhase.NoScrollPhase,False)
        w.graph.wheelEvent(event);self.assertAlmostEqual(w.graph.transform().m11(),1.25)
    def test_success_sound_is_optional_and_errors_are_silent(self):
        w=self.window()
        with patch('native.window.QApplication.beep') as beep:
            w.notify_job_completed({});beep.assert_not_called();w.apply_preferences({'preferences/beepOnSuccess':True})
            w.notify_job_completed({'ok':False});beep.assert_not_called();w.notify_job_completed({});beep.assert_called_once()
    def test_unknown_or_invalid_settings_are_not_partially_applied(self):
        w=self.window();before=dict(w.preferences)
        with self.assertRaises(ValueError):w.apply_preferences({'theme':'porcelain','graph/panSpeed':float('inf')})
        self.assertEqual(w.preferences,before);self.assertEqual(w.theme,before['theme'])


if __name__=='__main__':unittest.main()
