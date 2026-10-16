import io
import csv
import unittest
from copy import deepcopy
from lab.schema import Project, Incident
from lab.simulator import simulate
from lab.procedures import ProcedureSimulation
from lab.calibration import calibrate, observations, measure


def network(**overrides):
    data=dict(name='Analytic procedure fixture',duration=10,window=1,seed=42,control_plane=False,routing_knowledge='shared',
        nodes=[dict(id='a',name='Access',kind='MME',x=0,y=0,processing_ms=0,capacity=100000),dict(id='b',name='Database',kind='HSS',x=400,y=0,processing_ms=0,capacity=100000)],
        links=[dict(id='ab',source='a',target='b',protocol='Diameter',latency_ms=10,jitter_ms=0,loss=0,bandwidth_mbps=1000)],
        flows=[dict(id='f',source='a',target='b',protocol='Diameter',operation='Diameter ULR',rate=5,max_retries=0)])
    data.update(overrides)
    return Project.model_validate(data)


class ProcedureTests(unittest.TestCase):
    def test_sender_exhaustion_does_not_withdraw_delivered_or_on_wire_data(self):
        p=network();p.flows[0].rate=0;p.flows[0].timeout_ms=1000
        relay=p.nodes[0].model_copy(deep=True);relay.id='relay';relay.name='Relay';relay.kind='DRA';p.nodes.append(relay)
        p.links[0].target='relay';p.links[0].latency_ms=1;p.links[0].reverse_latency_ms=80
        p.links[0].rto_ms=10;p.links[0].rto_min_ms=10;p.links[0].rto_max_ms=10;p.links[0].transport_retries=0
        second=p.links[0].model_copy(deep=True);second.id='rb';second.source='relay';second.target='b';second.reverse_latency_ms=1;second.rto_ms=200;second.rto_max_ms=200;p.links.append(second)
        p.nodes[1].processing_ms=30
        class OneArrival(ProcedureSimulation):
            def prepare(self):super().prepare();self.total=1;self.push(1,'new',(1,self.p.flows[0]))
        engine=OneArrival(p);r=engine.run()
        self.assertEqual(r['summary']['delivered'],1)
        self.assertEqual(sum(l['dropped'] for l in r['links'].values()),0)
        self.assertEqual({m['role'] for m in r['message_traces']},{'request','answer'})
        self.assertTrue(any(tr['sender_failed'] and tr['delivered'] for tr in engine.transfers.values()))
        self.assertTrue(all(not a['completed'] for a in engine.associations.values()))

    def test_data_arrives_after_sender_exhausts_its_timer(self):
        p=network();p.links[0].latency_ms=80;p.links[0].reverse_latency_ms=80
        p.links[0].rto_ms=10;p.links[0].rto_min_ms=10;p.links[0].rto_max_ms=10;p.links[0].transport_retries=0
        r=simulate(p)
        self.assertEqual(r['summary']['delivery_ratio'],1)
        self.assertGreater(r['nodes']['b']['processed'],0)

    def test_late_retransmitted_data_is_not_delivered_twice(self):
        p=network();p.links[0].reverse_latency_ms=100;p.links[0].rto_ms=10;p.links[0].rto_min_ms=10;p.links[0].rto_max_ms=10;p.links[0].transport_retries=2
        engine=ProcedureSimulation(p);r=engine.run()
        self.assertEqual(r['summary']['delivery_ratio'],1)
        self.assertEqual(sum(m['role']=='request' for m in r['message_traces']),r['summary']['sent'])
        self.assertGreater(r['links']['ab']['duplicates'],0)
        self.assertTrue(all(not a['completed'] for a in engine.associations.values()))

    def test_incomplete_fragmented_receive_is_retired_after_pending_data_drains(self):
        p=network();p.flows[0].rate=1;p.flows[0].message_bytes=4000;p.links[0].cwnd_packets=1
        p.links[0].latency_ms=80;p.links[0].rto_ms=10;p.links[0].rto_min_ms=10;p.links[0].rto_max_ms=10;p.links[0].transport_retries=0
        engine=ProcedureSimulation(p);r=engine.run()
        self.assertGreater(r['summary']['sent'],0);self.assertEqual(r['summary']['delivered'],0)
        self.assertTrue(all(tr['failed'] and not tr['pending_arrivals'] for tr in engine.transfers.values()))
        self.assertTrue(all(not stream for a in engine.associations.values() for stream in a['streams'].values()))

    def gtt_network(self):
        p=network();p.nodes[0].kind='MSC';p.nodes[0].point_code='2-1-1';p.nodes[1].kind='HLR';p.nodes[1].point_code='2-1-2'
        for nid,kind,pc in [('stp','STP','2-1-3'),('other','HLR','2-1-4'),('isolated','STP','2-1-5')]:
            node=p.nodes[1].model_copy(deep=True);node.id=nid;node.name=nid;node.kind=kind;node.point_code=pc;p.nodes.append(node)
        p.links=[]
        from lab.schema import Link
        for lid,src,dst in [('as','a','stp'),('sb','stp','b'),('so','stp','other')]:p.links.append(Link(id=lid,source=src,target=dst,protocol='M3UA',latency_ms=1,jitter_ms=0,loss=0))
        f=p.flows[0];f.protocol='M3UA';f.operation='MAP UpdateLocation';f.global_title='123456'
        return p

    def test_gtt_only_uses_table_of_node_actually_reached(self):
        p=self.gtt_network();p.nodes[2].gtt={'12':'2-1-2'};p.nodes[4].gtt={'12345':'2-1-4'}
        r=simulate(p)
        self.assertEqual(r['summary']['delivery_ratio'],1)
        self.assertTrue(all(t['target']=='b' and t['route']==['a','stp','b'] for t in r['traces']))
        self.assertEqual(r['nodes']['isolated']['processed'],0);self.assertEqual(r['nodes']['other']['processed'],0)

    def test_disabled_or_disconnected_gtt_does_not_make_network_work(self):
        for enabled in (False,True):
            with self.subTest(enabled=enabled):
                p=self.gtt_network();p.nodes[4].enabled=enabled;p.nodes[4].gtt={'123':'2-1-2'}
                r=simulate(p);self.assertEqual(r['summary']['delivered'],0)

    def test_gtt_source_translation_is_not_overridden_by_remote_longer_prefix(self):
        p=self.gtt_network();p.nodes[0].gtt={'1':'2-1-4'};p.nodes[2].gtt={'123':'2-1-2'}
        r=simulate(p)
        self.assertEqual(r['summary']['delivery_ratio'],1);self.assertTrue(all(t['target']=='other' for t in r['traces']))

    def test_gtt_relay_uses_local_next_hop_and_charges_processing(self):
        p=self.gtt_network();p.nodes[0].routing_table={'GT:123':['stp']};p.nodes[2].gtt={'12':'2-1-4','123':'2-1-2'}
        p.nodes[2].processing_ms=20
        r=simulate(p)
        self.assertEqual(r['summary']['delivery_ratio'],1);self.assertGreater(r['summary']['latency_mean'],40)
        self.assertTrue(all(t['target']=='b' for t in r['traces']))
        p.nodes[0].routing_table={'*':['other']}
        self.assertEqual(simulate(p)['summary']['delivered'],0)

    def test_translated_address_with_no_route_does_not_fall_back_to_remote_gtt(self):
        p=self.gtt_network();p.nodes[2].gtt={'1':'2-1-4'};p.nodes[4].gtt={'12345':'2-1-2'};p.links[2].enabled=False
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertTrue(all(t['cause']=='no_route' for t in r['traces']))

    def test_untranslated_gtt_loop_is_bounded(self):
        p=self.gtt_network();p.nodes[0].routing_table={'*':['stp']};p.nodes[2].routing_table={'*':['a']}
        r=simulate(p);self.assertEqual(r['summary']['delivered'],0)
        self.assertTrue(all(t['cause']=='gtt_not_found' and t['route']==['a','stp'] for t in r['traces']))

    def test_round_trip_and_reverse_wire_load(self):
        r=simulate(network())
        self.assertEqual(r['summary']['sent'],r['summary']['delivered'])
        self.assertAlmostEqual(r['summary']['latency_mean'],20,delta=.1)
        self.assertEqual({m['role'] for m in r['message_traces']},{'request','answer'})
        self.assertGreater(r['links']['ab']['directions']['reverse']['bytes'],0)
        self.assertEqual(r['summary']['wire_messages'],r['summary']['sent']*2)

    def test_registration_depends_on_authentication_answer(self):
        p=network();p.flows[0].procedure='registration'
        r=simulate(p)
        self.assertEqual(r['summary']['sent'],r['summary']['delivered'])
        self.assertEqual(r['summary']['wire_messages'],r['summary']['sent']*4)
        self.assertTrue(all(t['steps_completed']==2 for t in r['traces']))
        self.assertIn('registered',r['subscriber_states'])
        for ident in {m['procedure_id'] for m in r['message_traces']}:
            messages=[m for m in r['message_traces'] if m['procedure_id']==ident]
            auth=next(m for m in messages if m['operation']=='Diameter AIA')
            update=next(m for m in messages if m['operation']=='Diameter ULR')
            self.assertGreaterEqual(update['time'],auth['end'])

    def test_protocol_error_is_an_answer_not_transport_success(self):
        p=network();p.nodes[1].error_rate=1
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertEqual(r['summary']['failure_reasons']['protocol_error'],r['summary']['sent'])
        self.assertEqual(r['links']['ab']['dropped'],0)

    def test_timeout_event_and_retry_deadlines_are_exact(self):
        p=network();f=p.flows[0];f.timeout_ms=5;f.max_retries=2;f.retry_backoff_ms=7
        p.links[0].loss=1
        r=simulate(p)
        # 3 attempts * 5 ms + 7 + 14 ms backoff.
        self.assertTrue(all(t['latency_ms']==36 for t in r['traces']))
        self.assertTrue(all(t['attempts']==3 and t['status']=='timeout' for t in r['traces']))

    def test_client_timeout_does_not_erase_inflight_server_load(self):
        p=network();p.flows[0].timeout_ms=1
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertTrue(all(t['latency_ms']==1 for t in r['traces']))
        self.assertGreater(r['nodes']['b']['processed'],0)
        self.assertEqual(r['summary']['wire_messages'],2*r['summary']['sent'])

    def test_transport_recovers_losses_and_counts_fragments(self):
        p=network();p.links[0].loss=.08;p.links[0].transport_retries=6;p.flows[0].timeout_ms=10000;p.flows[0].message_bytes=7000
        r=simulate(p)
        self.assertGreater(r['summary']['delivery_ratio'],.9)
        self.assertGreater(r['summary']['transport_retries'],0)
        self.assertGreater(r['links']['ab']['packets'],r['summary']['sent']*6)
        self.assertGreater(r['links']['ab']['acks'],r['summary']['sent'])

    def test_response_loss_and_duplicate_request_deduplication(self):
        p=network();p.links[0].reverse_loss=1;p.flows[0].max_retries=1;p.flows[0].timeout_ms=100;p.links[0].rto_ms=1000
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertGreater(r['nodes']['b']['duplicates'],0)
        self.assertTrue(all(t['status']=='timeout' for t in r['traces']))

    def test_incident_on_unused_link_is_not_affected_label(self):
        p=network();d=p.model_dump();d['nodes'].append(dict(id='c',name='Unused',kind='DRA',x=0,y=400))
        d['links'].append(dict(id='ac',source='a',target='c',protocol='Diameter'))
        d['incidents']=[dict(kind='link_down',target='ac',start=2,duration=4)]
        r=simulate(Project.model_validate(d))
        self.assertEqual(sum(w['scheduled_event'] for w in r['windows']),4)
        self.assertEqual(sum(w['label'] for w in r['windows']),0)

    def test_tail_is_separate_from_observation_throughput(self):
        p=network();p.links[0].latency_ms=800;p.flows[0].timeout_ms=5000
        p.links[0].rto_ms=2000
        r=simulate(p);s=r['summary']
        self.assertGreater(s['completed_in_tail'],0)
        self.assertEqual(s['completed_in_observation']+s['completed_in_tail'],s['delivered'])
        self.assertEqual(s['throughput'],s['completed_in_observation']/p.duration)
        self.assertLess(s['throughput'],s['delivered']/p.duration)

    def test_trace_reservoir_has_no_physics_side_effect(self):
        p=network();p.flows[0].rate=40;p.trace_limit=100
        a=simulate(p);p.trace_limit=500;b=simulate(p)
        self.assertEqual(a['summary'],b['summary'])
        self.assertEqual(a['windows'],b['windows'])
        self.assertEqual(len(a['traces']),100)
        self.assertGreater(max(t['time'] for t in a['traces']),9)

    def test_route_tables_realm_application_and_context(self):
        p=network();p.flows[0].destination_realm='unknown.example'
        self.assertEqual(simulate(p)['summary']['delivered'],0)
        p.flows[0].destination_realm='';p.flows[0].application_id=123
        self.assertEqual(simulate(p)['summary']['delivered'],0)
        p=network();p.nodes[0].routing_table={'lab.example':['b']}
        self.assertEqual(simulate(p)['summary']['delivery_ratio'],1)
        p.links[0].protocol='M3UA';p.flows[0].protocol='M3UA';p.flows[0].operation='MAP UpdateLocation';p.flows[0].routing_context=2
        self.assertEqual(simulate(p)['summary']['delivered'],0)

    def test_failure_detection_has_a_blind_interval(self):
        p=network();p.incidents=[Incident(kind='link_down',target='ab',start=2,duration=2,detection_ms=500)]
        engine=ProcedureSimulation(p);f=p.flows[0]
        engine.t=2100
        self.assertIsNotNone(engine.route('a','b',f))
        self.assertFalse(engine.link_up('ab','a',engine.t))
        engine.t=2600;self.assertIsNone(engine.route('a','b',f))
        engine.t=4050;self.assertIsNone(engine.route('a','b',f))
        engine.t=4300;self.assertIsNotNone(engine.route('a','b',f))

    def test_directional_failure_preserves_other_direction(self):
        p=network();p.incidents=[Incident(kind='link_down',target='ab',start=2,duration=2,direction='forward')]
        engine=ProcedureSimulation(p)
        self.assertFalse(engine.link_up('ab','a',2500))
        self.assertTrue(engine.link_up('ab','b',2500))

    def test_pc_and_longest_prefix_gtt(self):
        p=network();p.nodes[0].kind='MSC';p.nodes[0].point_code='2-1-1';p.nodes[1].kind='HLR';p.nodes[1].point_code='2-1-2'
        p.links[0].protocol='M3UA';f=p.flows[0];f.protocol='M3UA';f.operation='MAP UpdateLocation';f.global_title='123456'
        p.nodes[0].gtt={'12':'2-1-1','123':'2-1-2'}
        self.assertEqual(simulate(p)['summary']['delivery_ratio'],1)
        p.nodes[0].gtt={'999':'2-1-2'}
        self.assertEqual(simulate(p)['summary']['delivered'],0)
        f.global_title='';f.destination_pc='2-1-2'
        self.assertEqual(simulate(p)['summary']['delivery_ratio'],1)

    def test_parallel_path_recovers_transport_without_application_retry(self):
        p=network();p.flows[0].rate=0;p.flows[0].timeout_ms=2000
        p.links[0].loss=1;p.links[0].path_group='association';p.links[0].detection_ms=0
        alternate=p.links[0].model_copy(deep=True);alternate.id='alternate';alternate.loss=0;alternate.latency_ms=20;p.links.append(alternate)
        p.incidents=[Incident(kind='link_down',target='ab',start=.001,duration=5,detection_ms=0)]
        class OneArrival(ProcedureSimulation):
            def prepare(self):
                super().prepare();self.total=1;self.push(.5,'new',(1,self.p.flows[0]))
        r=OneArrival(p).run()
        self.assertEqual(r['summary']['delivered'],1)
        self.assertEqual(r['summary']['procedure_retries'],0)
        self.assertGreater(r['summary']['transport_retries'],0)
        self.assertGreater(r['links']['alternate']['packets'],0)

    def test_finite_channel_buffer_drops_excess_fragments(self):
        p=network();p.flows[0].rate=1;p.flows[0].message_bytes=6000;p.links[0].buffer_bytes=1500;p.links[0].bandwidth_mbps=.01
        r=simulate(p)
        self.assertGreater(r['links']['ab']['buffer_drops'],0)

    def test_worker_parallelism_and_finite_queue(self):
        p=network();p.flows[0].rate=60;p.nodes[1].processing_ms=20;p.nodes[1].workers=1;p.nodes[1].queue_limit=2
        slow=simulate(p);p.nodes[1].workers=8;fast=simulate(p)
        self.assertGreater(slow['nodes']['b']['dropped'],0)
        self.assertLessEqual(slow['nodes']['b']['max_queue'],2)
        self.assertGreater(fast['summary']['delivery_ratio'],slow['summary']['delivery_ratio'])

    def test_restart_erases_subscriber_and_duplicate_state(self):
        engine=ProcedureSimulation(network());engine.t=500
        engine.cache[('b',1,0)]='success';engine.subscribers[('b','a',1)]='authenticated'
        engine.node_fault('b','node_restart')
        self.assertFalse(engine.cache);self.assertFalse(engine.subscribers)

    def test_seed_repeatability_and_conservation(self):
        p=network();p.flows[0].traffic_profile='bursty';p.flows[0].profile_amplitude=.5;p.links[0].loss=.02
        a,b=simulate(p),simulate(p)
        self.assertEqual(a,b)
        self.assertEqual(sum(w['sent'] for w in a['windows']),a['summary']['sent'])
        self.assertEqual(sum(w['delivered'] for w in a['windows']),a['summary']['delivered'])
        self.assertEqual(a['summary']['max_outstanding']>=a['summary']['outstanding_end'],True)


class CalibrationTests(unittest.TestCase):
    def test_grouped_holdout_and_parameter_search(self):
        p=network();p.flows[0].rate=1
        text=io.StringIO();writer=csv.DictWriter(text,fieldnames=['run_id','offered_rate','latency_mean','delivery_ratio'])
        writer.writeheader()
        for i in range(4):writer.writerow(dict(run_id=f'measured-{i}',offered_rate=1+i*.2,latency_mean=40,delivery_ratio=1))
        r=calibrate(p,text.getvalue())
        self.assertFalse(set(r['split']['train'])&set(r['split']['test']))
        self.assertLess(r['after']['test']['objective'],r['before']['test']['objective'])
        self.assertAlmostEqual(r['scales']['latency'],2,delta=.05)
        self.assertGreater(len(r['search']),9)
    def test_measurements_reject_nonfinite(self):
        with self.assertRaises(ValueError):observations('run_id,offered_rate,latency_mean\na,1,nan\n')


if __name__=='__main__':unittest.main()
