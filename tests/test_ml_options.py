import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from shiboken6 import delete
from lab.ml_algorithms import ALGORITHMS,parameters
from native.ml_options import ModelOptions,ModelOptionsDialog


class ModelOptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_all_model_editors_roundtrip_valid_defaults(self):
        editor=ModelOptions()
        try:
            for key,meta in ALGORITHMS.items():
                with self.subTest(algorithm=key):
                    editor.set_algorithm(key)
                    self.assertEqual(parameters(key,editor.values()),parameters(key))
                    self.assertEqual(set(editor.controls),set(meta['parameters']))
        finally:delete(editor);self.app.processEvents()
    def test_switching_restores_each_models_edited_parameters(self):
        editor=ModelOptions()
        try:
            editor.set_algorithm('svm');editor.controls['C'][0].setValue(4.)
            editor.set_algorithm('mlp');editor.controls['hidden_layer_sizes'][0].setText('20, 10')
            editor.set_algorithm('svm');self.assertEqual(editor.values()['C'],4.)
            editor.set_algorithm('mlp');self.assertEqual(parameters('mlp',editor.values())['hidden_layer_sizes'],[20,10])
        finally:delete(editor);self.app.processEvents()
    def test_dialog_maintains_distinct_configs_and_shared_tree_default(self):
        dialog=ModelOptionsDialog(None,['svm','random_forest'],trees=25)
        try:
            dialog.options.controls['C'][0].setValue(3.)
            dialog.selector.setCurrentIndex(1);self.assertEqual(dialog.options.values()['n_estimators'],25)
            dialog.options.controls['max_depth'][0].setValue(7)
            values=dialog.result_config();self.assertEqual(values['svm']['C'],3.);self.assertEqual(values['random_forest']['max_depth'],7)
            dialog.reset();self.assertEqual(dialog.options.values()['max_depth'],10)
        finally:delete(dialog);self.app.processEvents()
    def test_switch_hides_previous_fields_before_deferred_deletion(self):
        editor=ModelOptions();editor.set_algorithm('svm');editor.show();self.app.processEvents()
        try:
            old=[w for w,_ in editor.controls.values()];self.assertTrue(any(w.isVisible() for w in old))
            editor.set_algorithm('mlp');self.assertTrue(all(w.isHidden() for w in old))
            self.assertEqual(editor.controls['alpha'][0].text().replace(',','.'),'0.0001')
        finally:delete(editor);self.app.processEvents()


if __name__=='__main__':unittest.main()
