import json
import tempfile
import unittest
from concurrent.futures import CancelledError
from pathlib import Path
from unittest.mock import patch
from lab import ml,storage,monitor_study,checkpoints
from lab.presets import preset
from lab.schema import Project,Monitoring
from lab.monitoring import default_features,schema_spec
from lab.monitor_expressions import evaluate,parse
from lab.simulator import simulate


def monitored(kind='diameter'):
    p=preset(kind).model_dump();p.update(duration=30,window=2,journal_sessions=0)
    for f in p['flows']:f['rate']=2
    if kind=='diameter':
        p['links']=[l for l in p['links'] if {l['source'],l['target']}!={'mme','hss'}]
        node,collector,witness='mme','dra','hss'
    elif kind=='ss7':node,collector,witness='msc','stp-a','hlr'
    else:node,collector,witness='gnb','amf','amf'
    p['monitoring']=dict(enabled=True,members=[dict(node=node,collector=collector,witness=witness,zone='A')],features=default_features(),poll_s=2,decision_s=2,timeout_s=1,ttl_s=5,faults=[dict(node=node,start=10,duration=10,mode='scale',factor=.1)])
    return Project.model_validate(p)


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for module in (storage,ml):
            p=patch.object(module,'DATA',Path(self.temp.name));p.start();self.addCleanup(p.stop)
    def test_expression_language_cannot_access_labels_or_execute_code(self):
        for expression in ('label','node_down','__import__("os")','reported_processed_s.real','[1][0]','2**500','max(*[1])'):
            with self.subTest(expression=expression),self.assertRaises(ValueError):parse(expression)
        self.assertEqual(evaluate('safe_div(abs(reported_processed_s-2),2)',{'reported_processed_s':6}),2)
        with self.assertRaises(ValueError):evaluate('1/0',{})
    def test_invalid_members_and_limits_are_rejected(self):
        p=monitored().model_dump()
        for update in (dict(collector='absent'),dict(collector=p['monitoring']['members'][0]['node'])):
            d=json.loads(json.dumps(p));d['monitoring']['members'][0].update(update)
            with self.assertRaises(ValueError):Project.model_validate(d)
        p['monitoring']['features'].append(dict(p['monitoring']['features'][0]))
        with self.assertRaises(ValueError):Project.model_validate(p)
    def test_fractional_last_window_and_large_decision_period(self):
        p=monitored();p.monitoring.decision_s=7
        rows=simulate(p)['monitoring']['rows']
        self.assertEqual([r['window_seconds'] for r in rows],[7,7,7,7,2])
        p.monitoring.decision_s=60;rows=simulate(p)['monitoring']['rows']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['window_seconds'],30)
    def test_monitoring_does_not_depend_on_control_being_enabled(self):
        p=monitored();p.control_plane=False
        self.assertGreater(simulate(p)['monitoring']['counts']['reports'],0)
    def test_monitoring_works_for_multiple_protocol_families(self):
        for kind in ('diameter','ss7','5g'):
            with self.subTest(kind=kind):
                result=simulate(monitored(kind));m=result['monitoring']
                self.assertEqual(len(m['rows']),15);self.assertGreater(m['counts']['reports'],0)
                self.assertGreater(m['wire_bytes'],0);self.assertTrue(any(r['label'] for r in m['rows']))
                self.assertTrue(all(r['feature_contract']=='monitoring-v1' for r in m['rows']))
    def test_report_scaling_changes_observations_without_changing_report_schema(self):
        p=monitored();normal=p.model_copy(deep=True);normal.monitoring.faults=[]
        a=simulate(normal)['monitoring'];b=simulate(p)['monitoring']
        self.assertEqual(a['schema'],b['schema'])
        before=[r['reported_rate'] for r in a['rows'] if 12<=r['time']<18]
        after=[r['reported_rate'] for r in b['rows'] if 12<=r['time']<18]
        self.assertTrue(all(abs(y-x*.1)<1e-8 for x,y in zip(before,after)))
    def test_missing_reports_and_stale_values_are_explicit(self):
        p=monitored();p.monitoring.faults[0].mode='suppress'
        m=simulate(p)['monitoring'];self.assertGreater(m['counts']['timeouts'],0)
        self.assertTrue(any(r['missing']==1 and r['reported_rate']==0 for r in m['rows'] if 14<=r['time']<20))
    def test_late_reports_are_not_retroactively_applied(self):
        p=monitored();p.monitoring.faults[0].mode='delay';p.monitoring.faults[0].delay_ms=3000
        m=simulate(p)['monitoring'];self.assertGreater(m['counts']['late_reports'],0)
        self.assertGreater(m['counts']['timeouts'],0)
    def test_observer_outside_message_path_does_not_receive_oracle_data(self):
        from lab.schema import Link
        p=monitored();p.links.append(Link(id='bypass',source='mme',target='hss',protocol='Diameter',latency_ms=1))
        p.monitoring.members[0].witness='dra'
        m=simulate(p)['monitoring']
        self.assertTrue(any(r['reported_rate']>0 for r in m['rows']))
        self.assertTrue(all(r['witness_rate']==0 for r in m['rows']))
    def test_failed_collector_does_not_make_decisions(self):
        from lab.schema import Incident
        p=monitored();p.incidents=[Incident(kind='node_down',target='dra',start=8,duration=6)]
        m=simulate(p)['monitoring']
        self.assertFalse(any(8<=r['time']+r['window_seconds']<14 for r in m['rows']))
        self.assertGreater(m['counts']['unavailable_decisions'],0)
    def test_zone_context_requires_delivered_synchronization(self):
        p=monitored();p.monitoring.members.append(p.monitoring.members[0].model_copy(update=dict(node='hss',collector='mme',zone='A',witness='dra')))
        a=simulate(p)['monitoring'];p.monitoring.sync=False;b=simulate(p)['monitoring']
        self.assertTrue(any(r['coverage']>0 for r in a['rows']))
        self.assertTrue(all(r['coverage']==0 for r in b['rows']))
    def test_checkpoint_preserves_pending_reports_and_features(self):
        p=monitored();e=checkpoints.engine_for(p)
        class Stop(Exception):pass
        def hook(engine):
            if engine.t>11000:raise Stop()
        with self.assertRaises(Stop):e.run(hook)
        blob,digest=checkpoints.dumps(e);other=checkpoints.loads(blob,digest)
        a=e.run();b=other.run();self.assertEqual(a['monitoring'],b['monitoring']);self.assertEqual(a['summary'],b['summary'])
    def test_disabled_overlay_does_not_change_existing_simulation(self):
        p=monitored();p.monitoring.enabled=False
        a=simulate(p);p.monitoring=Monitoring();b=simulate(p)
        for key in ('summary','windows','nodes','links','traces'):self.assertEqual(a[key],b[key])
    def test_dataset_controls_remove_all_report_faults(self):
        data=monitor_study.generate(monitored(),6)
        for i,run in enumerate(data['provenance']):self.assertEqual(bool(run['project']['monitoring']['faults']),bool(i%2))
        self.assertEqual(len({r['schema_id'] for r in data['rows']}),1)
        self.assertTrue(all(not r['label'] for r in data['rows'] if r['run_id']==data['provenance'][0]['run_id']))
    def test_dataset_budget_counts_fractional_final_decisions(self):
        p=monitored();p.duration=1250;p.monitoring.decision_s=.19999
        p.monitoring.members.append(p.monitoring.members[0].model_copy(update=dict(node='hss')))
        with self.assertRaisesRegex(ValueError,'300 000'):monitor_study.generate(p,24)
    def test_ablation_retrains_on_identical_run_splits_and_predicts_custom_schema(self):
        data=monitor_study.generate(monitored(),6);study=monitor_study.compare(data,trees=20)
        self.assertEqual(len(study['models']),8)
        for m in study['models']:
            self.assertEqual({k:v['runs'] for k,v in m['split'].items()},study['split'])
            self.assertEqual(len(ml.predict(m['model_id'],data['rows'],m['threshold'])),len(data['rows']))
        altered=json.loads(json.dumps(data));altered['rows'][0]['schema_id']='other'
        with self.assertRaises(ValueError):ml.predict(study['models'][0]['model_id'],altered['rows'],.5)
    def test_import_validates_schema_and_preserves_rows(self):
        data=monitor_study.generate(monitored(),6);imported=monitor_study.import_data(json.dumps(data))
        self.assertEqual(data['rows'],imported['rows'])
        data['schema']['definitions'][0]['expression']='label'
        with self.assertRaises(ValueError):monitor_study.import_data(json.dumps(data))
    def test_topology_holdout_and_ablations_use_the_same_unseen_graph(self):
        data=monitor_study.generate(monitored(),12,topology_variants=True)
        topologies=sorted({r['topology_id'] for r in data['rows']})
        self.assertEqual(len(topologies),3)
        study=monitor_study.compare(data,('random_forest',),trees=20,evaluation_mode='topology',holdout_value=topologies[-1])
        for m in study['models']:
            parts=m['evaluation']['conditions']
            self.assertEqual(parts['test']['topologies'],[topologies[-1]])
            self.assertNotIn(topologies[-1],parts['train']['topologies'])
    def test_cancelled_comparison_removes_partial_models(self):
        data=monitor_study.generate(monitored(),6);stop=[False]
        with self.assertRaises(CancelledError):monitor_study.compare(data,trees=20,progress=lambda _:stop.__setitem__(0,True),cancel=lambda:stop[0])
        self.assertEqual(list((ml.DATA/'models').glob('*.joblib')),[])
        self.assertEqual(storage.listing('monitor_study'),[])


if __name__=='__main__':unittest.main()
