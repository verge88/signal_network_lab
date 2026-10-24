import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import joblib
import numpy as np
from sklearn.metrics import f1_score
from lab import ml,storage,research,monitor_study
from lab.features import LEGACY
from lab.ml_algorithms import ALGORITHMS,parameters
from lab.schema import Monitoring
from lab.monitoring import schema_spec


def sample_rows():
    rng=np.random.default_rng(81);rows=[]
    for run in range(12):
        for tick in range(20):
            label=int(tick>=10);values=rng.normal(size=len(LEGACY))+label*np.linspace(1.,3.,len(LEGACY));values[-1]=0.
            rows.append(dict(zip(LEGACY,values),run_id=str(run),time=tick,label=label,window_seconds=1,fault_intervals=[[10,20]],feature_contract='procedures-v2'))
    return rows


class AlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        for module in (ml,storage):
            p=patch.object(module,'DATA',self.root);p.start();self.addCleanup(p.stop)
        self.rows=sample_rows()
    def train(self,algorithm,**kwargs):return ml.train(self.rows,algorithm,trees=20,**kwargs)
    def test_all_25_models_train_reload_and_predict_in_original_units(self):
        self.assertEqual(len(ALGORITHMS),25)
        for key,meta in ALGORITHMS.items():
            with self.subTest(algorithm=key):
                result=self.train(key);test=[r for r in self.rows if r['run_id'] in result['split']['test']['runs']]
                predictions=ml.predict(result['model_id'],test,result['threshold'])
                self.assertTrue(np.isfinite(result['test_scores']).all());self.assertTrue(np.isfinite(result['threshold']))
                np.testing.assert_allclose([r['score'] for r in predictions],result['test_scores'],rtol=1e-10,atol=1e-10)
                self.assertEqual([int(r['anomaly']) for r in predictions],list(np.asarray(result['test_scores'])>=result['threshold']))
                self.assertEqual(result['training_config']['parameters'],parameters(key,trees=20))
                self.assertEqual(result['normal_only'],meta['normal_only'])
                self.assertEqual(result['fit']['samples'],sum(r['run_id'] in result['split']['train']['runs'] and (not meta['normal_only'] or r['label']==0) for r in self.rows))
                self.assertFalse(set(result['split']['train']['runs'])&set(result['split']['test']['runs']))
                artifact=joblib.load(self.root/'models'/f'{result["model_id"]}.joblib')
                if meta['scaled']:
                    fitted=[r for r in self.rows if r['run_id'] in result['split']['train']['runs'] and (not meta['normal_only'] or r['label']==0)]
                    np.testing.assert_allclose(artifact['model']['scale'].mean_,np.mean([[r[f] for f in LEGACY] for r in fitted],axis=0))
    def test_all_novelty_thresholds_use_only_normal_train(self):
        for key,meta in ALGORITHMS.items():
            if not meta['normal_only']:continue
            with self.subTest(algorithm=key):
                result=self.train(key,threshold_mode='contamination');self.assertTrue(np.isfinite(result['threshold']))
                self.assertEqual(result['training_config']['threshold_mode'],'contamination')
                artifact=joblib.load(self.root/'models'/f'{result["model_id"]}.joblib')
                if key=='local_outlier_factor':self.assertEqual(result['threshold'],-artifact['model'][-1].offset_)
    def test_test_changes_do_not_change_training_or_validation_threshold(self):
        split=dict(train=[str(i) for i in range(6)],validation=['6','7','8'],test=['9','10','11'])
        for key in ('svm','kmeans','local_outlier_factor','mlp'):
            with self.subTest(algorithm=key):
                before=self.train(key,fixed_split=split);changed=copy.deepcopy(self.rows)
                for r in changed:
                    if r['run_id'] in split['test']:
                        for f in LEGACY:r[f]+=1000.
                after=ml.train(changed,key,trees=20,fixed_split=split)
                self.assertEqual(before['threshold'],after['threshold'])
    def test_fast_threshold_matches_exhaustive_search_for_negative_and_large_scores(self):
        rng=np.random.default_rng(14)
        for count in (10,200):
            values=np.round(rng.normal(size=count)*20,1);labels=rng.integers(0,2,count)
            expected=max(np.unique(values),key=lambda v:(f1_score(labels,values>=v,zero_division=0),v))
            self.assertEqual(ml.best_threshold(labels,values),expected)
    def test_parameter_bounds_types_and_unknown_names(self):
        for key,values in [('svm',{'C':0}),('mlp',{'hidden_layer_sizes':'64, bad'}),('mlp',{'hidden_layer_sizes':[1024]}),('knn',{'n_neighbors':True}),('gaussian_nb',{'var_smoothing':float('nan')}),('qda',{'wrong':1})]:
            with self.subTest(algorithm=key),self.assertRaises(ValueError):parameters(key,values)
        self.assertEqual(parameters('mlp',{'hidden_layer_sizes':'32, 16'})['hidden_layer_sizes'],[32,16])
    def test_infeasible_model_does_not_create_artifact(self):
        with self.assertRaisesRegex(ValueError,'соседей'):self.train('knn',parameters=dict(n_neighbors=1000))
        self.assertFalse(list((self.root/'models').glob('*')) if (self.root/'models').exists() else [])
    def test_supervised_contamination_mode_rejected(self):
        with self.assertRaisesRegex(ValueError,'порога'):self.train('svm',threshold_mode='contamination')
    def test_research_saves_selected_model_parameters(self):
        data=storage.save('dataset','Sample',dict(rows=self.rows,metadata=dict(feature_set='legacy-v1')))
        result=research.train(data['id'],'svm',parameters=dict(C=2.,kernel='linear'))
        self.assertEqual(storage.get('model',result['id'])['training_config']['parameters']['C'],2.)
        self.assertEqual(result['algorithm_title'],'SVM · ядро')
    def test_storage_failure_removes_new_model(self):
        data=storage.save('dataset','Sample',dict(rows=self.rows,metadata={}))
        with patch.object(storage,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):research.train(data['id'],'decision_tree')
        self.assertEqual(list((self.root/'models').glob('*.joblib')),[])
    def test_qda_handles_fewer_class_samples_than_features(self):
        rows=copy.deepcopy(self.rows)
        for r in rows:r['label']=int(r['time']==19)
        result=ml.train(rows,'qda')
        self.assertTrue(np.isfinite(result['test_scores']).all())
        self.assertEqual(result['training_config']['parameters']['shrinkage'],'auto')
    def test_legacy_model_artifacts_without_new_metadata_still_load(self):
        for key in ('random_forest','logistic','isolation_forest'):
            result=self.train(key);path=self.root/'models'/f'{result["model_id"]}.joblib';artifact=joblib.load(path);artifact.pop('parameters');joblib.dump(artifact,path)
            self.assertEqual(len(ml.predict(result['model_id'],self.rows,result['threshold'])),len(self.rows))
    def test_score_ranges_include_negative_margins_and_large_distances(self):
        for key,values in [('linear_svm',[-9.,20.]),('gaussian_mixture',[-100.,500.])]:
            lo,hi=ml.threshold_limits(dict(algorithm=key,score_kind='margin',test_scores=values,threshold=-3.))
            self.assertLess(lo,min(values));self.assertGreater(hi,max(values))
    def test_new_models_work_with_custom_schema_paired_ablation_and_inference(self):
        config=Monitoring(poll_s=5.,decision_s=5.,ttl_s=15.,features=[dict(name='a',expression='reported_processed_s',group='load'),dict(name='b',expression='reported_cpu_ms',group='queue')])
        spec=schema_spec(config);rows=[dict(r,a=r['rate'],b=r['latency_mean'],entity_id='node-a',schema_id=spec['id'],feature_contract='monitoring-v1') for r in self.rows]
        data=storage.save('monitor_dataset','Custom',dict(rows=rows,schema=spec,metadata={}))
        result=monitor_study.compare(data,('linear_svm','gaussian_nb','kmeans'),groups=['queue'],parameters={'kmeans':dict(n_clusters=1)})
        self.assertEqual(len(result['models']),6)
        for m in result['models']:
            self.assertEqual({k:v['runs'] for k,v in m['split'].items()},result['split'])
            self.assertEqual(len(ml.predict(m['model_id'],rows,m['threshold'])),len(rows))
    def test_study_failure_removes_prior_model_files(self):
        config=Monitoring(poll_s=5.,decision_s=5.,ttl_s=15.,features=[dict(name='a',expression='reported_processed_s',group='load')]);spec=schema_spec(config)
        rows=[dict(r,a=r['rate'],entity_id='n',schema_id=spec['id'],feature_contract='monitoring-v1') for r in self.rows]
        data=storage.save('monitor_dataset','Custom',dict(rows=rows,schema=spec,metadata={}))
        with self.assertRaises(ValueError):monitor_study.compare(data,('decision_tree','knn'),groups=[],parameters={'knn':dict(n_neighbors=1000)})
        self.assertEqual(list((self.root/'models').glob('*.joblib')),[])


if __name__=='__main__':unittest.main()
