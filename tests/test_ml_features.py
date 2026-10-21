import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lab import ml,research,storage
from lab.features import SETS,matrix,topology_id,operational_metrics
from lab.presets import preset
from lab.simulator import simulate


class ExpandedMLTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.a=patch.object(storage,'DATA',self.directory);self.a.start()
        self.b=patch.object(ml,'DATA',self.directory);self.b.start()
    def tearDown(self):self.a.stop();self.b.stop();self.temp.cleanup()
    def project(self,kind='diameter'):
        p=preset(kind);p.duration=10;p.window=1;p.journal_sessions=0;p.incidents=[]
        for f in p.flows:f.rate=2
        return p
    def rows(self):
        result=[]
        for run in range(12):
            for t in range(8):
                label=int(2<=t<5)
                row={f:float(label*2+.01*run) for f in SETS['combined-v1']['features']}
                row.update(run_id=str(run),time=t,label=label,window_seconds=1,feature_contract='procedures-v2',telemetry_contract='events-v1',
                    topology_id=f'topology-{run%3}',fault_types=[f'fault-{run%3}'],fault_intervals=[[2,5]])
                result.append(row)
        return result
    def test_all_sets_available_and_finite_on_non_5g(self):
        result=simulate(self.project())
        for key in SETS:self.assertEqual(len(matrix(result['windows'],key)),10)
        self.assertTrue(all(r['data_sent_s']==0 for r in result['windows']))
        self.assertTrue(all(0<=r['db_active_ratio']<=1 for r in result['windows']))
    def test_no_tail_leak_and_fractional_last_window(self):
        p=self.project();p.window=3
        for f in p.flows:f.timeout_ms=500
        result=simulate(p);rows=result['windows']
        self.assertEqual([r['window_seconds'] for r in rows],[3,3,3,1])
        self.assertAlmostEqual(sum(r['signal_arrivals_s']*r['window_seconds'] for r in rows),result['summary']['sent'])
        self.assertAlmostEqual(sum(r['signal_completed_s']*r['window_seconds'] for r in rows),sum(t['end']<10 for t in result['traces']))
    def test_resource_waiting_overflow_expiry(self):
        p=self.project();p.duration=5
        for f in p.flows:f.rate=35
        for n in p.nodes:
            n.storage_model='queued';n.db_connections=1;n.db_queue_limit=1;n.database_ms=500;n.db_timeout_ms=200
            n.cache_entries=1;n.context_ttl_s=1
        result=simulate(p)
        self.assertGreater(sum(r['db_requests_s'] for r in result['windows']),0)
        self.assertGreater(sum(r['db_overflow_s']+r['db_timeouts_s'] for r in result['windows']),0)
        self.assertGreater(sum(r['db_queue_mean'] for r in result['windows']),0)
    def test_user_data_completion_and_qos(self):
        p=self.project('5g');p.duration=10
        for f in p.flows:
            f.user_plane_enabled=True;f.data_packets=3;f.qos_budget_ms=1
        result=simulate(p);rows=result['windows']
        self.assertGreater(sum(r['data_sent_s'] for r in rows),0)
        self.assertGreater(sum(r['data_goodput_mbps'] for r in rows),0)
        self.assertGreater(sum(r['data_qos_violation_ratio'] for r in rows),0)
        delivered_bytes=sum(t['bytes'] for t in result['user_plane']['traces'] if t['status']=='delivered' and t['end']<10)
        self.assertAlmostEqual(sum(r['data_goodput_mbps']*r['window_seconds']*1e6/8 for r in rows),delivered_bytes)
    def test_missing_features_never_imputed(self):
        row=self.rows()[0];row.pop('db_wait_mean_ms')
        with self.assertRaises(ValueError):matrix([row],'combined-v1')
        row=self.rows()[0];row.pop('telemetry_contract')
        with self.assertRaisesRegex(ValueError,'новый расчёт'):matrix([row],'combined-v1')
    def test_telemetry_does_not_change_network_physics(self):
        class ObserverOff:
            def __init__(self,*args):pass
            def __getattr__(self,key):return lambda *args,**kwargs:None
        p=self.project('5g');observed=simulate(p)
        with patch('lab.telemetry.Telemetry',ObserverOff):reference=simulate(p)
        for key in ('summary','nodes','links','traces','resources','user_plane','control_plane'):
            self.assertEqual(observed[key],reference[key],key)
        for a,b in zip(observed['windows'],reference['windows']):
            self.assertEqual({k:a[k] for k in b},b)
    def test_topology_fingerprint_ignores_cosmetics_and_renaming(self):
        p=self.project().model_dump();q=copy.deepcopy(p)
        mapping={n['id']:'renamed-'+str(i) for i,n in enumerate(q['nodes'])}
        for n in q['nodes']:n.update(id=mapping[n['id']],name='x',x=777,y=500)
        for l in q['links']:l.update(source=mapping[l['source']],target=mapping[l['target']])
        self.assertEqual(topology_id(p),topology_id(q))
        q['links'].append(copy.deepcopy(q['links'][0]));self.assertNotEqual(topology_id(p),topology_id(q))
    def test_topology_and_fault_holdouts_are_disjoint(self):
        rows=self.rows()
        for mode in ('topology','fault'):
            model=ml.train(rows,'random_forest',trees=20,feature_set='combined-v1',evaluation_mode=mode)
            held=model['evaluation']['holdout'];split=model['split']
            self.assertFalse(set(split['test']['runs'])&(set(split['train']['runs'])|set(split['validation']['runs'])))
            for row in rows:
                match=row['topology_id']==held if mode=='topology' else held in row['fault_types']
                self.assertEqual(match,row['run_id'] in split['test']['runs'])
            self.assertEqual(len(ml.predict(model['model_id'],rows,model['threshold'])),len(rows))
    def test_no_silent_fallback_for_single_topology(self):
        rows=self.rows()
        for r in rows:r['topology_id']='same'
        with self.assertRaisesRegex(ValueError,'другие топологии'):ml.train(rows,'logistic',feature_set='signaling-v1',evaluation_mode='topology')
    def test_operational_delays_exposure_and_missed(self):
        rows=[dict(run_id='r',time=i,window_seconds=1,label=int(2<=i<4),fault_intervals=[[2,4],[2.5,3]]) for i in range(6)]
        op=operational_metrics(rows,[1,1,0,1,0,0],.5)
        self.assertEqual(op['episodes'],1);self.assertEqual(op['delays_s'],[2]);self.assertEqual(op['normal_exposure_s'],4)
        self.assertEqual(op['false_alarm_episodes'],1);self.assertEqual(op['false_alarms_per_hour'],900)
        missed=operational_metrics(rows,[0]*6,.5)
        self.assertEqual(missed['missed'],1);self.assertIsNone(missed['delay_mean_s'])
    def test_operational_missing_metadata_not_zero(self):
        op=operational_metrics([dict(run_id='r',time=0,label=0)],[1],.5)
        self.assertFalse(op['available']);self.assertIsNone(op['false_alarms_per_hour'])
    def test_overlapping_windows_cannot_inflate_exposure(self):
        rows=[dict(run_id='r',time=i,window_seconds=2,label=0,fault_intervals=[]) for i in range(2)]
        with self.assertRaisesRegex(ValueError,'перекрываться'):operational_metrics(rows,[1,1],.5)
    def test_rich_dataset_csv_training_and_inference(self):
        data=research.dataset(self.project(),12,feature_set='combined-v1',topology_variants=True)
        self.assertEqual(data['metadata']['topologies'],3)
        imported=research.import_csv(research.csv_text(data['rows']),'rich roundtrip')
        self.assertEqual(imported['metadata']['feature_set'],'combined-v1')
        self.assertEqual(imported['rows'][1]['fault_intervals'],data['rows'][1]['fault_intervals'])
        model=research.train(imported['id'],trees=20,evaluation_mode='topology')
        self.assertEqual(len(model['features']),30);self.assertTrue(model['operational']['available'])
        self.assertEqual(len(ml.predict(model['id'],simulate(self.project())['windows'],model['threshold'])),10)
    def test_rich_csv_rejects_nonfinite_and_mixed_versions(self):
        rows=self.rows()[:32]
        for r in rows:r['feature_set']='combined-v1'
        rows[0]['db_wait_mean_ms']=float('nan')
        with self.assertRaises(ValueError):research.import_csv(research.csv_text(rows),'nan')
        rows[0]['db_wait_mean_ms']=0;rows[0]['feature_set']='resources-v1'
        with self.assertRaises(ValueError):research.import_csv(research.csv_text(rows),'mixed')
