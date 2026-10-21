import copy
import json
import math
import tempfile
import unittest
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from lab import experiments as e, storage
from lab.presets import preset
from lab.schema import Project


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.storage=patch.object(storage,'DATA',Path(self.temp.name));self.storage.start()
        self.p=preset('diameter');self.p.duration=10;self.p.window=1
        for flow in self.p.flows:flow.rate=.8
    def tearDown(self):
        self.storage.stop();self.temp.cleanup()
    def options(self,**kwargs):
        return e.Options(axes=[dict(path='load',values=[.5,1])],repetitions=3,**kwargs)
    def fake(self,project,warmup_s,*args,**kwargs):
        load=project.flows[0].rate/.8
        return dict(seed=project.seed,run_id=None,config_sha256='fake',environment={},warmup_s=warmup_s,
            observation_s=project.duration-warmup_s,sent=10,delivered=10,completed_in_observation=10,completed_in_tail=0,
            latency_p95=100*load,delivery_ratio=1.,throughput=load,unfinished=0,policy='test')
    def test_catalog_fields_exist_and_targets_are_whitelisted(self):
        p=preset('5g-ha')
        for path,_ in e.parameter_catalog(p):
            if path=='load':continue
            kind,ident,field=path.split(':');obj=next(o for o in getattr(p,e.COLLECTIONS[kind]) if o.id==ident)
            self.assertIn(field,type(obj).model_fields)
        for path in ['__class__','node:hss:enabled','node:missing:workers','flow:ulr:source']:
            with self.assertRaises(ValueError):e.configuration(self.p,{path:2})
    def test_cartesian_and_paired_designs(self):
        axes=[dict(path='load',values=[.5,1]),dict(path='node:hss:workers',values=[1,2,3])]
        o=e.Options(axes=axes,repetitions=2);design=e.plan(self.p,o)
        self.assertEqual(len(design['points']),6);self.assertEqual(design['runs_max'],12)
        o.design='paired'
        with self.assertRaises(ValueError):e.plan(self.p,o)
        o.axes[1].values=[1,2];self.assertEqual(e.plan(self.p,o)['points'][1],{'load':1.,'node:hss:workers':2.})
    def test_preflight_rejects_invalid_integer_and_keeps_project(self):
        before=self.p.model_dump();o=e.Options(axes=[dict(path='node:hss:workers',values=[1,1.5])])
        with self.assertRaises(ValueError):e.run_series(self.p,o)
        self.assertEqual(self.p.model_dump(),before);self.assertEqual(storage.listing('sweep'),[])
    def test_duplicate_paths_values_and_ambiguous_load_are_rejected(self):
        for axes in [[dict(path='load',values=[1,1])],[dict(path='load',values=[1,2])]*2,
                     [dict(path='load',values=[1,2]),dict(path='flow:ulr:rate',values=[1,2])]]:
            with self.assertRaises(ValueError):e.Options(axes=axes)
    def test_nonfinite_values_and_impossible_precision_are_rejected(self):
        for value in [float('nan'),float('inf')]:
            with self.assertRaises(ValueError):e.Options(axes=[dict(path='load',values=[1,value])])
        with self.assertRaises(ValueError):e.Options(adaptive=True,absolute_tolerance=0,relative_tolerance=0)
        with self.assertRaises(ValueError):e.Options(adaptive=True,repetitions=3,min_repetitions=10)
    def test_budgets_and_warmup_validate_before_running(self):
        with self.assertRaisesRegex(ValueError,'Прогрев'):e.plan(self.p,self.options(warmup_s=10))
        self.p.engine='messages'
        with self.assertRaisesRegex(ValueError,'процедур'):e.plan(self.p,self.options())
        self.p.engine='procedures';self.p.flows[0].rate=500
        with self.assertRaisesRegex(ValueError,'2 млн'):e.plan(self.p,e.Options(repetitions=1000))
        with self.assertRaisesRegex(ValueError,'64'):e.plan(self.p,e.Options(axes=[dict(path='node:hss:workers',values=list(range(1,10))),dict(path='node:mme:workers',values=list(range(1,10)))]))
    def test_exact_cohort_excludes_control_and_counts_tail_without_sampling(self):
        pr={1:dict(start=1000,status='delivered',elapsed=100),2:dict(start=5000,status='delivered',elapsed=100),
            3:dict(start=9000,status='delivered',elapsed=2000),4:dict(start=6000,status='timeout',elapsed=300),
            5:dict(start=7000),6:dict(start=6000,control=True),7:dict(start=10000,status='delivered',elapsed=1)}
        m=e.measurement(SimpleNamespace(p=self.p,procedures=pr),5)
        self.assertEqual((m['sent'],m['delivered'],m['completed_in_observation'],m['completed_in_tail'],m['unfinished']),(4,2,1,1,1))
        self.assertEqual(m['throughput'],.2);self.assertEqual(m['delivery_ratio'],.5);self.assertEqual(m['latency_p95'],1905.)
    def test_missing_cohort_and_no_success_do_not_create_fake_latency(self):
        m=e.measurement(SimpleNamespace(p=self.p,procedures={}),0)
        self.assertIsNone(m['latency_p95']);self.assertIsNone(m['delivery_ratio']);self.assertIsNone(m['throughput'])
        m=e.measurement(SimpleNamespace(p=self.p,procedures={1:dict(start=5,status='timeout',elapsed=30)}),0)
        self.assertIsNone(m['latency_p95']);self.assertEqual(m['delivery_ratio'],0);self.assertEqual(m['throughput'],0)
    def test_student_interval_matches_known_three_sample_example(self):
        stats=e.interval([1,2,3],'latency_p95')
        self.assertAlmostEqual(stats['mean'],2);self.assertAlmostEqual(stats['high'],4.4841377,places=6)
        self.assertEqual(stats['low'],0);self.assertGreater(stats['half_width'],1.96/math.sqrt(3))
    def test_success_interval_does_not_claim_zero_uncertainty(self):
        small=e.interval([1.]*10,'delivery_ratio');large=e.interval([1.]*500,'delivery_ratio')
        self.assertGreater(small['half_width'],0);self.assertLess(large['half_width'],small['half_width']);self.assertEqual(large['high'],1.)
    def test_missing_observation_blocks_interval_and_precision(self):
        self.assertIsNone(e.interval([10,None,20],'latency_p95')['half_width'])
        rows=[dict(latency_p95=None,delivery_ratio=None,throughput=None,sent=0,delivered=0)]*5
        group=e.summarize(rows,{'load':1},self.options(),.05)
        self.assertFalse(group['precision_reached']);self.assertEqual(group['verdict'],'unavailable')
    def test_more_than_five_repeats_and_matched_seeds_are_persisted(self):
        before=copy.deepcopy(self.p.model_dump())
        with patch.object(e,'observed_run',self.fake):result=e.run_series(self.p,e.Options(axes=[dict(path='load',values=[.5,1])],repetitions=7))
        self.assertEqual(len(result['results']),14)
        self.assertEqual([r['seed'] for r in result['results'][:7]],[r['seed'] for r in result['results'][7:]])
        self.assertEqual(result['metadata']['actual_runs'],14);self.assertEqual(self.p.model_dump(),before)
        self.assertEqual(storage.get('sweep',result['id'])['contract'],'experiments-v1');json.dumps(result,allow_nan=False)
    def test_adaptive_stops_only_at_scheduled_checks_and_corrects_looks(self):
        o=e.Options(axes=[dict(path='load',values=[.5,1])],repetitions=20,adaptive=True,min_repetitions=5,batch=3)
        with patch.object(e,'observed_run',self.fake):r=e.run_series(self.p,o)
        self.assertEqual([g['n'] for g in r['groups']],[5,5]);self.assertTrue(all(g['stop_reason']=='precision' for g in r['groups']))
        self.assertAlmostEqual(r['inference']['alpha'],.05/(2*6*3))
    def test_unavailable_data_cannot_stop_adaptively(self):
        def empty(*args,**kwargs):
            result=self.fake(*args,**kwargs);result.update(sent=0,delivered=0,latency_p95=None,delivery_ratio=None,throughput=None);return result
        o=e.Options(axes=[dict(path='load',values=[.5,1])],repetitions=8,adaptive=True,min_repetitions=3)
        with patch.object(e,'observed_run',empty):r=e.run_series(self.p,o)
        self.assertTrue(all(g['n']==8 and g['stop_reason']=='budget' for g in r['groups']))
    def test_capacity_reports_best_tested_load_and_upper_censoring(self):
        o=e.Options(mode='capacity',load_min=1,load_max=3,search_points=3,repetitions=300,p95_limit_ms=210,success_min=.9)
        with patch.object(e,'observed_run',self.fake):r=e.run_series(self.p,o)
        self.assertEqual([g['verdict'] for g in r['groups']],['pass','pass','fail']);self.assertEqual(r['capacity']['best_tested_load'],2)
        self.assertFalse(r['capacity']['right_censored'])
        o.p95_limit_ms=500
        with patch.object(e,'observed_run',self.fake):r=e.run_series(self.p,o)
        self.assertTrue(r['capacity']['right_censored']);self.assertEqual(r['capacity']['best_tested_load'],3)
    def test_capacity_refuses_to_confirm_on_point_estimate_alone(self):
        with patch.object(e,'observed_run',self.fake):r=e.run_series(self.p,e.Options(mode='capacity',search_points=2,repetitions=3,p95_limit_ms=500))
        self.assertIsNone(r['capacity']['best_tested_load']);self.assertEqual(r['capacity']['status'],'insufficient_evidence')
    def test_capacity_detects_non_monotone_results(self):
        groups=[dict(parameters=dict(load=i),verdict=v) for i,v in enumerate(['pass','fail','pass'],1)]
        report=e.capacity_report(groups);self.assertTrue(report['non_monotone']);self.assertTrue(report['right_censored'])
    def test_capacity_adaptive_waits_for_both_requirements(self):
        o=e.Options(mode='capacity',load_min=.5,load_max=1,search_points=2,repetitions=250,adaptive=True,
            min_repetitions=10,batch=30,p95_limit_ms=200,success_min=.8)
        with patch.object(e,'observed_run',self.fake):r=e.run_series(self.p,o)
        self.assertTrue(all(g['n']>10 and g['n']<250 and g['stop_reason']=='requirements' for g in r['groups']))
        self.assertTrue(all(g['verdict']=='pass' for g in r['groups']))
    def test_confident_failure_is_reported_even_without_successful_latency(self):
        rows=[dict(latency_p95=None,delivery_ratio=0,throughput=0,sent=10,delivered=0)]*300
        self.assertEqual(e.summarize(rows,dict(load=1),self.options(),.005)['verdict'],'fail')
    def test_float_resolution_cannot_create_duplicate_capacity_points(self):
        with self.assertRaisesRegex(ValueError,'совпадают'):e.plan(self.p,e.Options(mode='capacity',load_min=1,load_max=1+2**-52))
    def test_cancellation_does_not_save_series(self):
        with self.assertRaises(CancelledError):e.run_series(self.p,self.options(),cancel=lambda:True)
        self.assertEqual(storage.listing('sweep'),[]);self.assertEqual(storage.listing('run'),[])
    def test_real_runs_measurement_is_reproducible_and_trace_limit_independent(self):
        a=e.observed_run(self.p,3)
        self.p.trace_limit=20000;b=e.observed_run(self.p,3)
        for key in [*e.METRICS,'sent','delivered','completed_in_tail']:self.assertEqual(a[key],b[key])
        self.assertGreater(a['sent'],0)
        self.assertEqual(storage.listing('run'),[])
    def test_full_run_can_be_saved_with_measurement_and_replayed(self):
        a=e.observed_run(self.p,3,save=True);saved=storage.get('run',a['run_id'])
        self.assertEqual(saved['measurement']['latency_p95'],a['latency_p95']);self.assertEqual(saved['project'],self.p.model_dump())
        from lab.procedures import ProcedureSimulation
        engine=ProcedureSimulation(Project.model_validate(saved['project']));engine.run()
        self.assertEqual(e.measurement(engine,3),saved['measurement'])
    def test_fiveg_series_uses_full_procedure_engine(self):
        p=preset('5g');p.duration=10;p.window=1
        for flow in p.flows:flow.rate=.2
        r=e.observed_run(p,2)
        self.assertGreater(r['sent'],0);self.assertEqual(r['policy'],'exact-start-cohort-v1');self.assertEqual(r['observation_s'],8)


if __name__=='__main__':unittest.main()
