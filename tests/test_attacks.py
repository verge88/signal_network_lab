import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lab.schema import Project, AttackScenario, AttackStep
from lab.attacks import compile_attacks,PRESETS,candidates,from_preset,encode_template,decode_template
from lab.procedures import ProcedureSimulation
from lab.simulator import simulate, FEATURES
from lab.presets import preset
from lab import research,storage


def network(**updates):
    data=dict(name='Attack fixture',nodes=[dict(id='a',name='Access',kind='MME',x=0,y=0,processing_ms=1,capacity=1000),dict(id='b',name='Service',kind='HSS',x=400,y=0,processing_ms=1,capacity=1000)],links=[dict(id='ab',source='a',target='b',protocol='Diameter',latency_ms=2,jitter_ms=0,loss=0)],flows=[dict(id='f',source='a',target='b',operation='Diameter ULR',protocol='Diameter',rate=0,max_retries=0,timeout_ms=1000)],duration=10,window=1,control_plane=False)
    data.update(updates);return Project.model_validate(data)


def attack(effect='node_error',target='b',magnitude=1,**kwargs):
    step=dict(id='s',target=target,effect=effect,magnitude=magnitude,duration=4)
    step.update(kwargs.pop('step',{}));return AttackScenario(id='attack',name='Test attack',start=2,steps=[step],**kwargs)


class ThreeArrivals(ProcedureSimulation):
    def prepare(self):
        super().prepare();self.total=3
        for ident,t in enumerate((1000,3000,7000),1):self.push(t,'new',(ident,self.p.flows[0]))


class AttackSchemaTests(unittest.TestCase):
    def test_old_projects_default_to_no_attacks(self):self.assertEqual(network().attacks,[])
    def test_profile_schedule_and_repeat_expansion(self):
        a=attack('surge','f',4,repeats=2,interval=4,step=dict(duration=2,profile='pulse',period=1,duty=.25))
        p=network(attacks=[a]);events,origins=compile_attacks(p)
        self.assertEqual([e.start for e in events],[2,3,6,7]);self.assertEqual([e.duration for e in events],[.25]*4)
        self.assertEqual([x['repeat'] for x in origins],[1,1,2,2])
    def test_ramp_has_twelve_bounded_intervals(self):
        p=network(attacks=[attack('node_capacity','b',.1,step=dict(profile='ramp'))]);events,_=compile_attacks(p)
        self.assertEqual(len(events),12);self.assertAlmostEqual(events[-1].magnitude,.1);self.assertGreater(events[0].magnitude,events[-1].magnitude)
        self.assertAlmostEqual(sum(e.duration for e in events),4)
    def test_repeat_may_not_silently_exceed_experiment(self):
        with self.assertRaisesRegex(ValueError,'выходит'):network(attacks=[attack(repeats=3,interval=3)])
    def test_invalid_or_wrong_resource_target_is_rejected(self):
        for effect,target in [('surge','b'),('node_error','ab'),('latency','f')]:
            with self.subTest(effect=effect),self.assertRaisesRegex(ValueError,'не найдена цель'):network(attacks=[attack(effect,target,3 if effect!='node_error' else .5)])
    def test_legacy_engine_cannot_silently_ignore_attacks(self):
        with self.assertRaisesRegex(ValueError,'движок процедур'):network(attacks=[attack()],engine='messages')
    def test_legacy_engine_rejects_unsupported_manual_effects(self):
        with self.assertRaisesRegex(ValueError,'движок процедур'):network(engine='messages',incidents=[dict(kind='node_capacity',target='b',start=1,duration=2,magnitude=.1)])
    def test_invalid_effect_values_and_nonfinite_are_rejected(self):
        for effect,value in [('node_error',1.1),('node_capacity',0),('node_capacity',1.1),('surge',.5),('node_delay',float('nan'))]:
            with self.subTest(effect=effect,value=value),self.assertRaises(ValueError):attack(effect,'b',value)
    def test_direction_and_profile_semantics_are_validated(self):
        with self.assertRaisesRegex(ValueError,'Направление'):attack(step=dict(direction='reverse'))
        with self.assertRaisesRegex(ValueError,'постоянный'):attack('node_restart',step=dict(profile='ramp'))
    def test_expansion_is_bounded_before_simulation(self):
        with self.assertRaisesRegex(ValueError,'512'):
            network(duration=600,attacks=[attack('surge','f',2,repeats=10,interval=40,step=dict(duration=40,profile='pulse',period=.5))])
    def test_workload_guard_includes_attacks(self):
        p=network().model_dump();p['flows'][0]['rate']=3000;p['attacks']=[attack('surge','f',100).model_dump()]
        with self.assertRaisesRegex(ValueError,'Слишком большой'):Project.model_validate(p)
    def test_disabled_attacks_do_not_expand_or_label(self):
        a=attack();a.enabled=False;r=ThreeArrivals(network(attacks=[a])).run()
        self.assertEqual(r['summary']['delivered'],3);self.assertEqual(r['attacks'][0]['affected_procedures'],0)
        self.assertFalse(any(w['label'] for w in r['windows']));self.assertEqual(r['incidents'],[])
    def test_all_applicable_presets_validate_in_all_domains(self):
        count=0
        for domain in ('ss7','diameter','hybrid','5g'):
            p=preset(domain)
            for spec in PRESETS:
                if not candidates(p,spec):continue
                a=from_preset(p,spec['id']);d=p.model_dump();d['attacks']=[a.model_dump()];Project.model_validate(d);count+=1
        self.assertGreater(count,25)
    def test_unavailable_preset_is_not_retargeted_to_another_protocol(self):
        with self.assertRaisesRegex(ValueError,'Нет подходящей'):from_preset(preset('ss7'),'5g_registration')


class AttackRuntimeTests(unittest.TestCase):
    def test_errors_only_within_interval_and_configuration_immutable(self):
        p=network(attacks=[attack()]);before=p.model_dump();r=ThreeArrivals(p).run()
        self.assertEqual([t['status'] for t in r['traces']],['delivered','protocol_error','delivered'])
        self.assertEqual(r['attacks'][0]['affected_procedures'],1);self.assertEqual(r['attacks'][0]['failed'],1)
        self.assertEqual(r['traces'][1]['affected_attacks'],['attack']);self.assertEqual(p.model_dump(),before)
        self.assertEqual(r['config_sha256'],hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest())
    def test_absolute_loss_works_with_zero_baseline_and_recovers(self):
        r=ThreeArrivals(network(attacks=[attack('loss_absolute','ab',1)])).run()
        self.assertEqual(r['summary']['delivered'],2);self.assertEqual(r['attacks'][0]['failed'],1)
        self.assertGreater(r['links']['ab']['dropped'],0)
    def test_reverse_loss_leaves_forward_direction_intact(self):
        r=ThreeArrivals(network(attacks=[attack('loss_absolute','ab',1,step=dict(direction='reverse'))])).run()
        self.assertEqual(r['links']['ab']['directions']['forward']['dropped'],0)
        self.assertGreater(r['links']['ab']['directions']['reverse']['dropped'],0)
    def test_node_service_delay_applies_and_restores(self):
        r=ThreeArrivals(network(attacks=[attack('node_delay','b',20)])).run();traces=r['traces']
        self.assertGreater(traces[1]['latency_ms'],traces[0]['latency_ms']+20)
        self.assertAlmostEqual(traces[0]['latency_ms'],traces[2]['latency_ms'],places=5)
    def test_capacity_reduction_produces_real_queues(self):
        p=network().model_dump();p['flows'][0]['rate']=80;p['attacks']=[attack('node_capacity','b',.01).model_dump()]
        r=simulate(Project.model_validate(p));self.assertGreater(r['nodes']['b']['max_queue'],10)
        self.assertGreater(r['summary']['loss_ratio'],0);self.assertGreater(r['attacks'][0]['affected_procedures'],0)
    def test_zero_resource_effects_do_not_change_physics(self):
        p=network().model_dump();p['flows'][0]['rate']=80;baseline=simulate(Project.model_validate(p))
        for effect,value in [('node_error',0),('node_delay',1),('node_capacity',1)]:
            p['attacks']=[attack(effect,'b',value).model_dump()];r=simulate(Project.model_validate(p))
            self.assertEqual(r['summary'],baseline['summary']);self.assertEqual(r['nodes'],baseline['nodes'])
    def test_overlapping_node_and_link_ids_do_not_misattribute_attacks(self):
        p=network().model_dump();p['links'][0]['id']='b';p['attacks']=[attack('loss_absolute','b',1).model_dump()]
        e=ProcedureSimulation(Project.model_validate(p));e.t=3000;pr=dict(id=1,objects=set(),affected=set())
        e.touch(pr,'b');self.assertEqual(pr['affected'],set())
        e.touch(pr,'b','forward');self.assertEqual(pr['affected'],{0})
    def test_overlap_is_maximum_severity_and_report_counts_unique_procedures(self):
        a=attack();s=a.steps[0].model_copy(update={'id':'s2','magnitude':.5});a.steps.append(s)
        e=ThreeArrivals(network(attacks=[a]));self.assertEqual(e.effect_value('node_error','b',3000,0),1)
        r=e.run();self.assertEqual(r['attacks'][0]['affected_procedures'],1)
        self.assertEqual(sum(s['affected_procedures'] for s in r['attacks'][0]['steps']),2)
    def test_surge_reproducibility_and_trace_annotations(self):
        d=network().model_dump();d['flows'][0]['rate']=15;baseline=simulate(Project.model_validate(d));d['attacks']=[attack('surge','f',4).model_dump()];p=Project.model_validate(d)
        r=simulate(p);self.assertEqual(r,simulate(p));self.assertGreater(r['summary']['sent'],baseline['summary']['sent']*1.5)
        self.assertTrue(r['windows'][2]['scheduled_attacks']);self.assertTrue(any(t['affected_attacks'] for t in r['traces']))
        self.assertEqual(r['features'],FEATURES)
    def test_fiveg_application_errors_use_same_effects(self):
        from lab.fiveg import FiveGSimulation
        p=preset('5g').model_dump();p['duration']=10;p['control_plane']=False;p['flows']=p['flows'][:1];p['flows'][0]['rate']=0;p['attacks']=[attack('node_error','amf',1).model_dump()]
        class One(FiveGSimulation):
            def prepare(self):super().prepare();self.total=1;self.push(3000,'new',(1,self.p.flows[0]))
        r=One(Project.model_validate(p)).run();self.assertEqual(r['summary']['failure_reasons'],{'protocol_error':1});self.assertEqual(r['attacks'][0]['affected_procedures'],1)


class AttackTemplateTests(unittest.TestCase):
    def test_roundtrip_assigns_new_scenario_identity(self):
        a=attack();b=decode_template(encode_template(a));self.assertNotEqual(a.id,b.id)
        self.assertEqual(a.steps,b.steps);self.assertEqual(a.name,b.name)
    def test_unknown_fields_cannot_smuggle_executable_actions(self):
        payload=json.loads(encode_template(attack()));payload['scenario']['command']='external tool'
        with self.assertRaises(ValueError):decode_template(json.dumps(payload))
    def test_unsupported_versions_and_oversize_templates(self):
        with self.assertRaises(ValueError):decode_template('{"format":"other","version":1,"scenario":{}}')
        with self.assertRaisesRegex(ValueError,'256'):decode_template(' '*262145)
    def test_dataset_controls_clear_attacks_and_provenance_preserves_positive_scenarios(self):
        p=network().model_dump();p['flows'][0]['rate']=3;p['attacks']=[attack('surge','f',3).model_dump()]
        with tempfile.TemporaryDirectory() as directory,patch.object(storage,'DATA',Path(directory)):
            r=research.dataset(p,6)
            for i,item in enumerate(r['provenance']):self.assertEqual(bool(item['project']['attacks']),bool(i%2))
            self.assertTrue(r['metadata']['positives']>0);self.assertEqual(r['metadata']['features'],FEATURES)
            storage.save('attack_template','Fixture',dict(template=encode_template(attack())),record_id='fixture')
            saved=storage.get('attack_template','fixture');self.assertEqual(decode_template(saved['template']).name,'Test attack')
            storage.delete('attack_template','fixture');self.assertEqual(storage.listing('attack_template'),[])


if __name__=='__main__':unittest.main()
