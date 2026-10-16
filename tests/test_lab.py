import csv
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import numpy as np
from fastapi.testclient import TestClient
from lab.schema import Project
from lab.presets import preset
from lab.simulator import simulate, FEATURES
from lab import storage, ml
from server import app


def simple(**overrides):
    d=dict(name='Test',nodes=[dict(id='a',name='A',kind='MME',x=100,y=100,capacity=10000,processing_ms=0),dict(id='b',name='B',kind='HSS',x=500,y=100,capacity=10000,processing_ms=0)],links=[dict(id='ab',source='a',target='b',protocol='Diameter',latency_ms=10,jitter_ms=0,bandwidth_mbps=100,loss=0)],flows=[dict(id='f',source='a',target='b',protocol='Diameter',operation='Diameter ULR',rate=20)],duration=20,window=5,seed=42)
    d['engine']='messages'
    d.update(overrides)
    return Project.model_validate(d)


class SimulationTests(unittest.TestCase):
    def test_seed_determinism_and_conservation(self):
        p=simple()
        a,b=simulate(p),simulate(p)
        self.assertEqual(a,b)
        self.assertEqual(a['summary']['sent'],a['summary']['delivered'])
        self.assertEqual(sum(r['sent'] for r in a['windows']),a['summary']['sent'])
        self.assertGreater(a['summary']['latency_mean'],10)
        self.assertLess(a['summary']['latency_mean'],11)

    def test_disconnected_and_protocol_domain(self):
        p=simple()
        p.links[0].protocol='M3UA'
        a=simulate(p)
        self.assertEqual(a['summary']['delivered'],0)
        self.assertTrue(all(t['status']=='no_route' for t in a['traces']))

    def test_total_link_loss(self):
        p=simple()
        p.links[0].loss=1
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertEqual(r['links']['ab']['dropped'],r['summary']['sent'])

    def test_timeout(self):
        p=simple()
        p.flows[0].timeout_ms=5
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertTrue(all(t['status']=='timeout' for t in r['traces']))

    def test_queue_overflow_and_utilization(self):
        p=simple()
        p.nodes[1].capacity=5
        p.nodes[1].queue_limit=2
        r=simulate(p)
        self.assertGreater(r['nodes']['b']['dropped'],100)
        self.assertLess(r['summary']['delivery_ratio'],.5)
        self.assertTrue(any(w['utilization']>.95 for w in r['windows']))
        self.assertLessEqual(r['nodes']['b']['max_queue'],2)

    def test_redundant_route_survives_failure(self):
        p=preset('ss7')
        p.engine='messages'
        p.duration=30
        p.incidents=[]
        for l in p.links:l.loss=0
        from lab.schema import Incident
        p.incidents=[Incident(kind='node_down',target='stp-a',start=10,duration=10)]
        r=simulate(p)
        self.assertGreater(r['summary']['delivery_ratio'],.97)
        self.assertGreater(sum(w['route_changes'] for w in r['windows']),0)
        self.assertEqual([w['label'] for w in r['windows']],[0,0,1,1,0,0])

    def test_higher_latency_increases_measured_latency(self):
        p=simple()
        baseline=simulate(p)
        p.links[0].latency_ms=100
        slow=simulate(p)
        self.assertGreater(slow['summary']['latency_mean'],baseline['summary']['latency_mean']+85)

    def test_fractional_incident_boundaries(self):
        from lab.schema import Incident
        p=simple()
        p.incidents=[Incident(kind='surge',target='f',start=2.25,duration=.5,magnitude=20)]
        r=simulate(p)
        self.assertEqual([w['label'] for w in r['windows']],[1,0,0,0])
        self.assertGreater(r['windows'][0]['rate'],30)


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.path=Path(cls.temp.name)
        cls.patch=patch.object(storage,'DATA',cls.path)
        cls.patch.start()
        cls.mlpatch=patch.object(ml,'DATA',cls.path)
        cls.mlpatch.start()
        cls.client=TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.patch.stop()
        cls.mlpatch.stop()
        cls.temp.cleanup()

    def test_parallel_library_initialization(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(lambda k:storage.listing(k),['run','model','project','dataset','sweep']))
        self.assertEqual(len(results),5)

    def test_validation_and_origin(self):
        d=simple().model_dump()
        d['links'][0]['target']='missing'
        self.assertEqual(self.client.post('/api/simulate',json=d).status_code,422)
        self.assertEqual(self.client.post('/api/simulate',json=simple().model_dump(),headers={'Origin':'https://example.com'}).status_code,403)
        self.assertEqual(self.client.get('/api/health',headers={'Host':'example.com'}).status_code,403)

    def test_complete_research_workflow(self):
        c=self.client
        p=simple().model_dump()
        saved=c.post('/api/projects',json=p)
        self.assertEqual(saved.status_code,200,saved.text)
        run=c.post('/api/simulate',json=p)
        self.assertEqual(run.status_code,200,run.text)
        run=run.json()
        d=c.post('/api/datasets/generate',json=dict(project=p,runs=12,name='QA dataset'))
        self.assertEqual(d.status_code,200,d.text)
        d=d.json()
        self.assertEqual(d['metadata']['runs'],12)
        for algorithm in ['logistic','random_forest','isolation_forest']:
            response=c.post('/api/train',json=dict(dataset_id=d['id'],algorithm=algorithm,trees=25))
            self.assertEqual(response.status_code,200,response.text)
            model=response.json()
            splits=[set(model['split'][s]['runs']) for s in ['train','validation','test']]
            self.assertFalse(splits[0]&splits[1] or splits[0]&splits[2] or splits[1]&splits[2])
            self.assertGreaterEqual(model['metrics']['f1'],0)
            self.assertLessEqual(model['metrics']['f1'],1)
            pred=c.post('/api/predict',json=dict(model_id=model['id'],run_id=run['id']))
            self.assertEqual(pred.status_code,200,pred.text)
            self.assertEqual(len(pred.json()['windows']),4)
            ev=c.post('/api/models/'+model['id']+'/evaluate',json={'threshold':1})
            self.assertEqual(ev.status_code,200)
            self.assertEqual(ev.json()['confusion'][0][1],0)
        exported=c.get('/api/export/dataset/'+d['id']+'?format=csv')
        self.assertEqual(exported.status_code,200)
        imported=c.post('/api/datasets/import',json=dict(name='roundtrip',csv=exported.text))
        self.assertEqual(imported.status_code,200,imported.text)
        self.assertEqual(imported.json()['metadata']['rows'],d['metadata']['rows'])
        sweep=c.post('/api/sweep',json=dict(project=p,values=[.5,1],repetitions=2))
        self.assertEqual(sweep.status_code,200,sweep.text)
        self.assertEqual(len(sweep.json()['results']),4)
        self.assertEqual(sweep.json()['results'][0]['seed'],sweep.json()['results'][2]['seed'])

    def test_csv_rejects_nonfinite_and_nonbinary_labels(self):
        output=io.StringIO()
        writer=csv.DictWriter(output,fieldnames=['run_id','label',*FEATURES])
        writer.writeheader()
        for i in range(35):writer.writerow(dict(run_id='run1',label=.5,**{f:0 for f in FEATURES}))
        res=self.client.post('/api/datasets/import',json={'csv':output.getvalue()})
        self.assertEqual(res.status_code,422)

    def test_isolation_contamination_sets_threshold(self):
        rng=np.random.default_rng(123)
        rows=[]
        for run in range(12):
            for i in range(12):
                label=int(run%2 and i>=8)
                values=rng.normal(label*4,1,len(FEATURES))
                rows.append(dict(run_id=str(run),time=i,label=label,**dict(zip(FEATURES,values))))
        low=ml.train(rows,'isolation_forest',trees=25,contamination=.05,threshold_mode='contamination')
        high=ml.train(rows,'isolation_forest',trees=25,contamination=.4,threshold_mode='contamination')
        self.assertGreater(low['threshold'],high['threshold'])
        self.assertEqual(low['test_scores'],high['test_scores'])
        self.assertEqual(low['training_config']['threshold_mode'],'contamination')

    def test_rejects_too_large_batch(self):
        p=simple().model_dump()
        r=self.client.post('/api/sweep',json=dict(project=p,values=[-1,1]))
        self.assertEqual(r.status_code,422)


if __name__=='__main__':unittest.main()
