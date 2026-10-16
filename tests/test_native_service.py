import tempfile
import unittest
from concurrent.futures import CancelledError
from pathlib import Path
from unittest.mock import patch
from lab import research,storage,ml
from lab.presets import preset


class NativeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)
        self.storage_patch=patch.object(storage,'DATA',self.path);self.storage_patch.start()
        self.ml_patch=patch.object(ml,'DATA',self.path);self.ml_patch.start()
        self.project=preset('diameter');self.project.duration=20;self.project.window=2
    def tearDown(self):
        self.storage_patch.stop();self.ml_patch.stop();self.temp.cleanup()
    def test_native_run_progress_and_snapshot(self):
        progress=[]
        result=research.run(self.project,progress.append)
        self.assertEqual(progress[0],0);self.assertEqual(progress[-1],100)
        self.assertEqual(result['project'],self.project.model_dump())
        self.assertEqual(storage.get('run',result['id'])['config_sha256'],result['config_sha256'])
    def test_cancelled_simulation_does_not_create_record(self):
        with self.assertRaises(CancelledError):research.run(self.project,cancel=lambda:True)
        self.assertEqual(storage.listing('run'),[])
    def test_cancelled_dataset_does_not_create_record(self):
        with self.assertRaises(CancelledError):research.dataset(self.project,6,cancel=lambda:True)
        self.assertEqual(storage.listing('dataset'),[])
    def test_direct_ml_workflow_without_server(self):
        dataset=research.dataset(self.project,6)
        csv=research.csv_text(dataset['rows'])
        imported=research.import_csv(csv,'roundtrip')
        self.assertEqual(dataset['metadata']['rows'],imported['metadata']['rows'])
        model=research.train(imported['id'],trees=25)
        self.assertEqual(model['dataset_id'],imported['id'])
        self.assertTrue((self.path/'models'/f'{model["id"]}.joblib').exists())
        self.assertEqual(model['feature_contract'],'procedures-v2')
        from lab.simulator import simulate
        legacy=self.project.model_copy(deep=True);legacy.engine='messages'
        with self.assertRaisesRegex(ValueError,'messages-v1'):
            ml.predict(model['id'],simulate(legacy)['windows'],model['threshold'])
    def test_cancelled_training_removes_new_artifact(self):
        dataset=research.dataset(self.project,6)
        cancelled=[False]
        with self.assertRaises(CancelledError):research.train(dataset['id'],trees=25,progress=lambda v:cancelled.__setitem__(0,True),cancel=lambda:cancelled[0])
        self.assertEqual(storage.listing('model'),[])
        self.assertEqual(list((self.path/'models').glob('*.joblib')),[])
    def test_fiveg_dataset_training_and_inference(self):
        p=preset('5g');p.duration=10;p.window=1
        for f in p.flows:f.rate=.5
        data=research.dataset(p,6)
        self.assertEqual(data['metadata']['feature_contract'],'procedures-v2')
        model=research.train(data['id'],trees=25)
        from lab.simulator import simulate
        result=simulate(p)
        predictions=ml.predict(model['id'],result['windows'],model['threshold'])
        self.assertEqual(len(predictions),10)
    def test_native_sweep_validates_duplicates_and_records_repeats(self):
        with self.assertRaises(ValueError):research.sweep(self.project,[1,1])
        result=research.sweep(self.project,[.5,1],2)
        self.assertEqual(len(result['results']),4)
        self.assertEqual(result['results'][0]['seed'],result['results'][2]['seed'])
    def test_large_native_canvas_preserves_schema(self):
        p=self.project.model_dump();p['nodes'][0].update(x=12000,y=14000)
        self.assertEqual(research.checked(p).nodes[0].x,12000)

    def test_workspace_migration_preserves_source_and_never_overwrites(self):
        original=storage.save('project','Source',{'project':self.project.model_dump()})
        source=self.path
        with tempfile.TemporaryDirectory() as target:
            with patch.object(storage,'DATA',Path(target)):
                self.assertTrue(storage.migrate_workspace(source))
                self.assertEqual(storage.get('project',original['id'])['name'],'Source')
                self.assertFalse(storage.migrate_workspace(source))
        self.assertEqual(storage.get('project',original['id'])['name'],'Source')


if __name__=='__main__':unittest.main()
