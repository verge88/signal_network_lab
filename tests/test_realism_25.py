import copy
import csv
import io
import unittest
from lab.schema import Project,MobilityEvent
from lab.presets import preset
from lab.procedures import ProcedureSimulation
from lab.fiveg import FiveGSimulation
from lab.simulator import simulate
from lab.resources import CapacityError
from lab.calibration import observations,evidence_report
from lab.calibration_search import calibrate


def pair():
    return Project.model_validate(dict(nodes=[dict(id='a',name='Client',kind='MME',x=0,y=0,capacity=10000,processing_ms=.1),dict(id='b',name='HSS',kind='HSS',x=400,y=0,capacity=10000,processing_ms=.1,database_ms=20,cache_entries=0)],links=[dict(id='ab',source='a',target='b',protocol='Diameter',loss=0,jitter_ms=0,latency_ms=1)],flows=[dict(id='f',source='a',target='b',protocol='Diameter',operation='Diameter ULR',rate=0,max_retries=0,timeout_ms=2000)],duration=10,window=1,control_plane=False,routing_knowledge='shared'))


def timed_engine(project,times,kind=ProcedureSimulation):
    class Timed(kind):
        def prepare(self):
            super().prepare();self.total=len(times)
            for ident,at in enumerate(times,1):self.push(at,'new',(ident,self.p.flows[0]))
    return Timed(project)


def core():
    p=preset('5g');p.duration=10;p.window=1;p.control_plane=False;p.routing_knowledge='shared';p.flows=[f for f in p.flows if f.operation=='5G PDU Session'];p.flows[0].rate=0;p.flows[0].subscribers=1
    for l in p.links:l.loss=0;l.jitter_ms=0
    return p


def mobile_core():
    p=core();f=p.flows[0];f.data_packets=80;f.data_interval_ms=100;f.session_lifetime_s=0
    for old,nid,kind in [('gnb','gnb2','gNB'),('amf','amf2','AMF'),('upf','upf2','UPF')]:
        n=next(n for n in p.nodes if n.id==old).model_copy(deep=True);n.id=nid;n.name=nid;p.nodes.append(n)
    for src,dst,proto in [('gnb2','amf2','NGAP'),('amf2','scp5g','SBI'),('smf','upf2','PFCP'),('gnb2','upf2','GTPU'),('upf2','dn','IP')]:
        l=p.links[0].model_copy(deep=True);l.id=src+'-'+dst;l.source=src;l.target=dst;l.protocol=proto;p.links.append(l)
    f.mobility=[MobilityEvent(time=3,gnb='gnb2',amf='amf2',upf='upf2',interruption_ms=100)]
    return Project.model_validate(p.model_dump())


class StorageTests(unittest.TestCase):
    def test_database_pool_serializes_independently_of_cpu_workers(self):
        p=pair();p.nodes[1].db_connections=1;e=timed_engine(p,[100,101,102]);r=e.run();s=r['resources']['database']['b']
        self.assertEqual(s['max_active'],1);self.assertGreaterEqual(s['max_queue'],2);self.assertGreater(s['wait_ms'],30)
        self.assertEqual(r['summary']['delivered'],3);self.assertLess(r['nodes']['b']['busy_ms'],5)
    def test_more_database_connections_reduce_waiting(self):
        p=pair();p.nodes[1].db_connections=1;a=timed_engine(p,[100,101,102]).run();p.nodes[1].db_connections=3;b=timed_engine(p,[100,101,102]).run()
        self.assertGreater(a['summary']['latency_mean'],b['summary']['latency_mean']+10)
    def test_database_queue_overflow_returns_explicit_result(self):
        p=pair();p.nodes[1].db_connections=1;p.nodes[1].db_queue_limit=0;r=timed_engine(p,[100,101]).run()
        self.assertEqual(r['summary']['failure_reasons'],{'database_queue_overflow':1});self.assertEqual(r['resources']['database']['b']['rejected'],1)
    def test_database_deadline_is_not_a_cpu_queue_timeout(self):
        p=pair();p.nodes[1].db_timeout_ms=5;r=timed_engine(p,[100]).run()
        self.assertEqual(r['summary']['failure_reasons'],{'database_timeout':1});self.assertEqual(r['resources']['database']['b']['timeouts'],1)
    def test_data_cache_reuses_subscriber_operation_and_expires(self):
        p=pair();p.flows[0].subscribers=1;p.nodes[1].cache_entries=2;p.nodes[1].cache_ttl_s=.1
        r=timed_engine(p,[100,150,400]).run();s=r['resources']['database']['b']
        self.assertEqual((s['hits'],s['misses']),(1,2));self.assertGreater(r['resources']['stores']['data_cache'].get('expired',0),0)
    def test_lru_eviction_is_real_and_bounded(self):
        e=ProcedureSimulation(pair());cache=e.storage.data_cache;e.nodes['b'].cache_entries=2
        for key in ('one','two'):cache[('b',key)]=True
        self.assertTrue(cache[('b','one')]);cache[('b','three')]=True
        self.assertNotIn(('b','two'),cache);self.assertEqual(len(cache),2);self.assertEqual(cache.stats['evicted'],1)
    def test_context_capacity_rejects_instead_of_evicting_live_sessions(self):
        e=ProcedureSimulation(pair());e.nodes['b'].context_limit=1;e.subscribers[('b','a',1)]='registered'
        with self.assertRaises(CapacityError):e.subscribers[('b','a',2)]='registered'
        self.assertEqual(e.subscribers[('b','a',1)],'registered');e.nodes['b'].context_ttl_s=.1
        e.subscribers[('b','a',1)]='registered';e.t=101;e.subscribers[('b','a',2)]='registered';self.assertEqual(len(e.subscribers),1)
    def test_old_expiration_cannot_delete_refreshed_context(self):
        e=ProcedureSimulation(pair());e.nodes['b'].context_ttl_s=1;key=('b','a',0);e.subscribers[key]='registered';token=e.subscribers.serial
        e.t=500;e.subscribers[key]='registered';e.t=1000;e.subscribers.expire(key,token);self.assertIn(key,e.subscribers)
    def test_restart_during_database_work_cannot_create_stale_context(self):
        from lab.schema import Incident
        p=pair();p.nodes[1].database_ms=500;p.incidents=[Incident(kind='node_restart',target='b',start=.2,duration=.2)]
        r=timed_engine(p,[100]).run();self.assertEqual(r['summary']['delivered'],0);self.assertEqual(r['subscriber_states'],{})
    def test_transaction_cache_is_finite_and_has_ttl(self):
        e=ProcedureSimulation(pair());e.nodes['b'].transaction_cache_limit=1;e.nodes['b'].transaction_cache_ttl_s=.1
        e.cache[('b',1,0)]='success';e.cache[('b',2,0)]='success';self.assertNotIn(('b',1,0),e.cache);e.t=101;self.assertNotIn(('b',2,0),e.cache)
    def test_completed_transaction_duplicate_does_not_repeat_database_work(self):
        e=ProcedureSimulation(pair());f=e.p.flows[0];e.procedures[1]=dict(id=1,flow=f,subscriber=0);e.cache[('b',1,0)]='success';seen=[]
        e.complete_node=lambda *args:seen.append(args);e.storage.submit('b',dict(procedure=1,step=0,rpc_id=None,operation=f.operation),0)
        self.assertEqual(len(seen),1);self.assertEqual(e.storage.stats['b']['deduplicated'],1);self.assertEqual(len(e.storage.tasks),0)
    def test_expiry_does_not_extend_experiment_clock_to_days(self):
        p=pair();p.nodes[1].context_ttl_s=86400;e=timed_engine(p,[100]);e.run();self.assertLess(e.t,100000)


class ServiceTests(unittest.TestCase):
    def test_upf_rule_can_block_data_without_failing_pdu(self):
        from lab.schema import UpfRule
        p=core();next(n for n in p.nodes if n.kind=='UPF').upf_rules=[UpfRule(action='drop',dnn='internet')]
        r=timed_engine(p,[100],FiveGSimulation).run();self.assertEqual(r['summary']['delivered'],1);self.assertEqual(r['user_plane']['delivered'],0);self.assertIn('upf_rule_drop',r['user_plane']['failure_reasons'])
    def test_first_matching_upf_rule_wins_by_precedence(self):
        from lab.schema import UpfRule
        p=core();n=next(n for n in p.nodes if n.kind=='UPF');n.upf_rules=[UpfRule(precedence=100,action='drop'),UpfRule(precedence=10,action='forward',flow_id=p.flows[0].id)]
        r=timed_engine(p,[100],FiveGSimulation).run();self.assertEqual(r['user_plane']['sent'],r['user_plane']['delivered'])
    def test_shaper_uses_real_queue_and_deadline_failures(self):
        p=core();f=p.flows[0];f.application_profile='bulk';f.data_interval_ms=1;f.data_timeout_ms=500
        n=next(n for n in p.nodes if n.kind=='UPF');n.upf_rate_mbps=.01;n.upf_burst_bytes=1200;n.upf_queue_limit=2
        r=timed_engine(p,[100],FiveGSimulation).run();self.assertGreater(r['user_plane']['upf_policy'].get('queue_drops',0),0);self.assertLess(r['user_plane']['delivered'],r['user_plane']['sent'])
    def test_qos_budget_is_reported_separately_from_delivery(self):
        p=core();p.flows[0].qos_budget_ms=1;r=timed_engine(p,[100],FiveGSimulation).run()
        self.assertGreater(r['user_plane']['delivered'],0);self.assertEqual(r['user_plane']['qos_met'],0)
    def test_extremely_slow_shaper_does_not_extend_clock_past_packet_deadlines(self):
        p=core();p.flows[0].data_packets=3;p.flows[0].data_timeout_ms=500
        node=next(n for n in p.nodes if n.kind=='UPF');node.upf_rate_mbps=.000001;node.upf_burst_bytes=1200
        e=timed_engine(p,[100],FiveGSimulation);r=e.run();self.assertLess(e.t,20000);self.assertEqual(r['user_plane']['sent'],3)
    def test_pdu_release_frees_session_flags_and_stops_later_packets(self):
        p=core();p.flows[0].session_lifetime_s=.15;p.flows[0].data_packets=10
        r=timed_engine(p,[100],FiveGSimulation).run();self.assertEqual(r['user_plane']['released_sessions'],1);self.assertNotIn('pdu_active',r['fiveg']['contexts']);self.assertLess(r['user_plane']['delivered'],10)
    def test_initial_registration_cannot_reappear_after_context_expiry(self):
        p=core();next(n for n in p.nodes if n.kind=='AMF').context_ttl_s=.5
        r=timed_engine(p,[100,1500],FiveGSimulation).run();self.assertEqual([t['status'] for t in r['traces']],['delivered','not_registered'])
    def test_interactive_profile_replays_with_identical_seed(self):
        p=core();p.flows[0].application_profile='interactive';a=timed_engine(p,[100],FiveGSimulation).run();b=timed_engine(p,[100],FiveGSimulation).run();self.assertEqual(a,b)
    def test_video_has_activity_gaps(self):
        p=core();f=p.flows[0];f.application_profile='video';f.video_on_s=.15;f.video_off_s=.5;f.data_packets=8
        r=timed_engine(p,[100],FiveGSimulation).run();times=[t['time'] for t in r['user_plane']['traces']];self.assertGreater(max(b-a for a,b in zip(times,times[1:])),.4)


class MobilityTests(unittest.TestCase):
    def test_handover_interruption_applies_to_shared_cohort(self):
        p=mobile_core();other=p.flows[0].model_copy(deep=True);other.id='other';other.mobility=[];p.flows.append(other);e=FiveGSimulation(p)
        e.t=3000;e.mobility.begin(p.flows[0].id,p.flows[0].mobility[0]);self.assertTrue(e.mobility.busy('other'))
    def test_gnb_amf_upf_relocation_transfers_context_and_resumes_service(self):
        p=mobile_core();e=timed_engine(p,[100],FiveGSimulation);r=e.run()
        self.assertEqual(r['mobility'][0]['status'],'completed');self.assertEqual(e.mobility.flow(p.flows[0]).source,'gnb2');self.assertTrue(any(t['upf']=='upf2' and t['status']=='delivered' for t in r['user_plane']['traces']))
        self.assertGreater(sum(l['bytes'] for lid,l in r['links'].items() if 'amf2' in lid),0)
    def test_handover_requires_delivered_control_exchange(self):
        p=mobile_core();next(l for l in p.links if l.id=='amf2-scp5g').loss=1;r=timed_engine(p,[100],FiveGSimulation).run()
        self.assertEqual(r['mobility'][0]['status'],'handover_timeout');self.assertFalse(r['mobility'][0]['transferred'])
    def test_handover_rejects_upf_without_required_dnn(self):
        p=mobile_core();next(n for n in p.nodes if n.id=='upf2').dnns=['private'];e=timed_engine(p,[100],FiveGSimulation);r=e.run()
        self.assertEqual(r['mobility'][0]['status'],'profile_not_supported');self.assertEqual(e.mobility.flow(p.flows[0]).source,'gnb')
    def test_relocation_can_run_out_of_context_capacity(self):
        p=mobile_core();e=timed_engine(p,[100],FiveGSimulation);n=e.nodes['amf2'];n.context_limit=1;e.contexts[('amf2','other',0,'00101','1-010203','internet')]={'registered':True}
        r=e.run();self.assertEqual(r['mobility'][0]['status'],'context_limit');self.assertEqual(e.mobility.flow(p.flows[0]).source,'gnb')
    def test_no_transfer_requires_registration_for_following_pdu(self):
        p=mobile_core();p.flows[0].mobility[0].transfer_context=False;r=timed_engine(p,[100,6000],FiveGSimulation).run()
        self.assertEqual(r['mobility'][0]['status'],'completed');self.assertEqual(r['traces'][-1]['status'],'not_registered')
    def test_wrong_target_kind_is_rejected(self):
        p=core().model_dump();p['flows'][0]['mobility']=[dict(time=3,gnb='upf')]
        with self.assertRaises(ValueError):Project.model_validate(p)


class PeerLifecycleTests(unittest.TestCase):
    def test_diameter_rejects_no_common_application(self):
        p=pair();p.control_plane=True;p.nodes[0].applications=[1];p.nodes[1].applications=[2];r=simulate(p)
        self.assertEqual(r['control_plane']['associations']['ab'],'REJECTED');self.assertEqual(r['control_plane']['counts']['no_common_application'],1)
    def test_relay_capability_allows_common_peer(self):
        p=pair();p.control_plane=True;p.nodes[0].applications=[4294967295];p.nodes[1].applications=[2]
        self.assertEqual(simulate(p)['control_plane']['associations']['ab'],'OPEN')
    def test_m3ua_rejects_peer_routing_context(self):
        p=preset('ss7');p.duration=10
        for f in p.flows:f.rate=0
        link=next(l for l in p.links if l.protocol=='M3UA');link.peer_routing_context=999
        self.assertEqual(simulate(p)['control_plane']['associations'][link.id],'REJECTED')
    def test_graceful_diameter_close_has_dpr_dpa_and_stays_closed(self):
        p=pair();p.control_plane=True;p.links[0].shutdown_at_s=2;r=simulate(p)
        self.assertEqual(r['control_plane']['associations']['ab'],'CLOSED');self.assertEqual(r['control_plane']['counts']['DPR'],1);self.assertEqual(r['control_plane']['counts']['DPA'],1)
    def test_graceful_m3ua_close_is_two_phase(self):
        p=preset('ss7');p.duration=10
        for f in p.flows:f.rate=0
        link=next(l for l in p.links if l.protocol=='M3UA');link.shutdown_at_s=2;r=simulate(p)
        self.assertEqual(r['control_plane']['associations'][link.id],'CLOSED')
        for op in ('ASPIA','ASPIA_ACK','ASPDN','ASPDN_ACK'):self.assertGreater(r['control_plane']['counts'].get(op,0),0)


def measurements(source='lab',same_id=False):
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=['run_id','offered_rate','latency_mean','source_kind','measurement_id','device','firmware','measured_at','raw_sha256']);w.writeheader()
    for i in range(4):w.writerow(dict(run_id='r'+str(i),offered_rate=1,latency_mean=10,source_kind=source,measurement_id='one' if same_id else str(i),device='fixture',firmware='fixture-1',measured_at='2026-10-18T10:00:00+05:00',raw_sha256='a'*64))
    return out.getvalue()


class EvidenceTests(unittest.TestCase):
    def test_synthetic_fixture_is_never_labelled_hardware_validated(self):
        text=measurements('synthetic');rows=observations(text);evidence=evidence_report(text,rows,['r0','r1'],['r2','r3'])
        self.assertFalse(evidence['hardware_accuracy_proven']);self.assertEqual(evidence['status'],'synthetic_or_unverified')
    def test_claimed_lab_source_requires_full_metadata(self):
        with self.assertRaises(ValueError):observations(measurements().replace('fixture-1',''))
    def test_duplicate_measurement_ids_cannot_be_split_as_independent_runs(self):
        with self.assertRaisesRegex(ValueError,'четыре'):observations(measurements(same_id=True))
    def test_calibration_preserves_provenance_hash_and_independent_groups(self):
        text=measurements();p=pair();p.flows[0].rate=1
        r=calibrate(p,text,options=dict(max_evaluations=5,passes=1,seeds=1));e=r['evidence']
        self.assertEqual(e['status'],'declared_lab_holdout');self.assertFalse(e['raw_artifacts_verified']);self.assertEqual(len(e['input_csv_sha256']),64)
        self.assertFalse(set(r['split']['train'])&set(r['split']['test']));self.assertEqual(e['independent_groups'],4)


if __name__=='__main__':unittest.main()
