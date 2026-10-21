import unittest
from lab.fiveg import FiveGSimulation
from lab.schema import Incident,Project
from test_fiveg import core


def scenario(arrivals,setup=None,engine=FiveGSimulation):
    p=core('5G PDU Session');f=p.flows[0];f.rate=0;f.subscribers=1;f.pdu_session_count=2
    f.data_packets=40;f.data_interval_ms=100;f.session_lifetime_s=0
    if setup:setup(p)
    class Scheduled(engine):
        def prepare(self):
            super().prepare();self.total=len(arrivals)
            for ident,(time,flow) in enumerate(arrivals,1):self.push(time,'new',(ident,self.p.flows[flow]))
    e=Scheduled(p);return e,e.run()


class SessionLifecycleTests(unittest.TestCase):
    def test_two_ids_same_ue_do_not_overwrite_each_other(self):
        e,r=scenario([(100,0),(300,0)])
        self.assertEqual([t['status'] for t in r['traces']],['delivered','delivered'])
        self.assertEqual([t['pdu_session_id'] for t in r['traces']],[1,2])
        self.assertEqual(r['session_lifecycle']['states'],{'ACTIVE':2})
        self.assertTrue(all(len(k)==7 for k in e.session_lifecycle.store.data))
        for owner in (1,2):self.assertTrue(any(t['procedure_id']==owner and t['time']>1 and t['status']=='delivered' for t in r['user_plane']['traces']))
    def test_id_collision_preserves_first_session(self):
        e,r=scenario([(100,0),(300,0)],lambda p:setattr(p.flows[0],'pdu_session_count',1))
        self.assertEqual(r['traces'][1]['status'],'pdu_session_in_use')
        self.assertEqual(e.session_lifecycle.context(1,'upf')['owner'],1)
    def test_finite_pdu_pool_does_not_clear_other_session(self):
        def setup(p):
            for n in p.nodes:n.pdu_session_limit=1
        e,r=scenario([(100,0),(300,0)],setup)
        self.assertEqual(r['traces'][0]['status'],'delivered');self.assertEqual(r['traces'][1]['status'],'pdu_context_limit')
        self.assertEqual(e.session_lifecycle.context(1,'amf')['owner'],1)
        self.assertEqual(e.session_lifecycle.records[2]['state'],'RELEASED')
    def test_lifetime_release_uses_messages_even_without_user_data(self):
        def setup(p):p.flows[0].session_lifetime_s=.3;p.flows[0].user_plane_enabled=False
        e,r=scenario([(100,0)],setup)
        self.assertEqual(r['session_lifecycle']['states'],{'RELEASED':1});self.assertEqual(r['session_lifecycle']['remaining_contexts'],0)
        self.assertTrue({'PFCP Session Deletion','Npcf SMPolicyDelete','Nsmf ReleaseSMContext','NGAP PDUResourceRelease'}<=set(r['fiveg']['services']))
        self.assertTrue(e.contexts[e.context_key(e.procedures[1],'amf')]['registered'])
    def test_explicit_release_only_removes_requested_id(self):
        def setup(p):
            release=p.flows[0].model_copy(deep=True);release.id='release';release.operation='5G PDU Session Release';release.pdu_session_count=1;p.flows.append(release)
        e,r=scenario([(100,0),(300,0),(1000,1)],setup)
        self.assertEqual(r['traces'][-1]['status'],'delivered')
        self.assertEqual(e.session_lifecycle.records[1]['state'],'RELEASED');self.assertEqual(e.session_lifecycle.records[2]['state'],'ACTIVE')
        self.assertTrue(any(t['procedure_id']==2 and t['time']>1.5 and t['status']=='delivered' for t in r['user_plane']['traces']))
    def test_deregistration_clears_all_ids_and_blocks_initial_reseeding(self):
        def setup(p):
            dereg=p.flows[0].model_copy(deep=True);dereg.id='dereg';dereg.operation='5G Deregistration';p.flows.append(dereg)
        e,r=scenario([(100,0),(300,0),(1000,1),(2000,0)],setup)
        self.assertEqual([t['status'] for t in r['traces']],['delivered','delivered','delivered','not_registered'])
        self.assertEqual(r['session_lifecycle']['remaining_contexts'],0);self.assertFalse(e.contexts.data)
        self.assertIn('Nudm UECMDeregistration',r['fiveg']['services']);self.assertIn('Nausf AuthenticationContextRelease',r['fiveg']['services'])
    def test_registration_after_deregistration_restores_service(self):
        def setup(p):
            for operation in ('5G Deregistration','5G Registration'):
                f=p.flows[0].model_copy(deep=True);f.id=operation;f.operation=operation;p.flows.append(f)
        e,r=scenario([(100,0),(1000,1),(2000,2),(3000,0)],setup)
        self.assertTrue(all(t['status']=='delivered' for t in r['traces']))
        self.assertEqual(e.session_lifecycle.records[4]['state'],'ACTIVE')
    def test_scheduled_deregistration_works_without_user_data(self):
        def setup(p):p.flows[0].deregister_after_s=.5;p.flows[0].user_plane_enabled=False
        e,r=scenario([(100,0),(2000,0)],setup)
        self.assertEqual(r['traces'][1]['status'],'not_registered');self.assertEqual(r['session_lifecycle']['states'],{'RELEASED':1})
    def test_partial_setup_error_reclaims_policy_context(self):
        def setup(p):next(n for n in p.nodes if n.kind=='UPF').error_rate=1
        e,r=scenario([(100,0)],setup)
        self.assertEqual(r['summary']['delivered'],0);self.assertEqual(r['session_lifecycle']['remaining_contexts'],0)
        self.assertEqual(e.session_lifecycle.records[1]['state'],'RELEASED');self.assertIn('Npcf SMPolicyDelete',r['fiveg']['services'])
    def test_late_setup_answer_cannot_recreate_released_context(self):
        class Delayed(FiveGSimulation):
            def reply_wire(self,frame,result):
                if frame['spec']['operation']=='PFCP Session Establishment':self.push(self.t+600,'rpc_held',(frame['id'],result));return
                super().reply_wire(frame,result)
            def rpc_event(self,kind,data):
                if kind=='rpc_held':super().reply_wire(self.rpcs[data[0]],data[1]);return
                super().rpc_event(kind,data)
        def setup(p):
            f=p.flows[0];f.max_retries=0;f.service_timeout_ms=120;f.timeout_ms=200;f.session_cleanup_retry_s=.2
        e,r=scenario([(100,0)],setup,Delayed)
        self.assertEqual(r['summary']['delivered'],0);self.assertEqual(r['session_lifecycle']['remaining_contexts'],0)
        self.assertEqual(e.session_lifecycle.records[1]['state'],'RELEASED')
    def test_cleanup_recovers_after_upf_outage(self):
        def setup(p):
            f=p.flows[0];f.session_lifetime_s=1.5;f.max_retries=0;f.service_timeout_ms=150;f.timeout_ms=500;f.session_cleanup_retry_s=.3;f.session_cleanup_retries=10
            p.incidents=[Incident(kind='node_down',target='upf',start=1.5,duration=2,detection_ms=0,recovery_ms=0)]
        e,r=scenario([(100,0)],setup)
        self.assertEqual(e.session_lifecycle.records[1]['state'],'RELEASED');self.assertEqual(r['session_lifecycle']['remaining_contexts'],0)
        self.assertGreater(e.session_lifecycle.records[1]['cleanup_attempts'],1)
    def test_unreachable_peer_remains_explicitly_orphaned(self):
        def setup(p):
            f=p.flows[0];f.session_lifetime_s=1;f.max_retries=0;f.service_timeout_ms=100;f.timeout_ms=400;f.session_cleanup_retry_s=.1;f.session_cleanup_retries=1
            p.incidents=[Incident(kind='node_down',target='upf',start=.8,duration=9,detection_ms=0,recovery_ms=0)]
        e,r=scenario([(100,0)],setup)
        self.assertEqual(e.session_lifecycle.records[1]['state'],'ORPHANED')
        self.assertEqual([k[0] for k in e.session_lifecycle.owned_keys(1)],['upf'])
        self.assertEqual(r['user_plane']['released_sessions'],0)
    def test_old_cleanup_cannot_delete_reused_id(self):
        def setup(p):p.flows[0].session_lifetime_s=.4;p.flows[0].pdu_session_count=1
        e,r=scenario([(100,0),(2000,0)],setup)
        pr=e.procedures[2];key=e.session_lifecycle.context_key(2,'upf')
        e.session_lifecycle.store[key]={'owner':2,'n4_session':True};e.session_lifecycle.delete(1,'upf')
        self.assertEqual(e.session_lifecycle.store.data[key][0]['owner'],2)
    def test_journal_records_session_context_and_lifecycle(self):
        e,r=scenario([(100,0)],lambda p:setattr(p.flows[0],'session_lifetime_s',.2))
        events=r['procedure_journal']['sessions'][0]['events']
        self.assertTrue(any(v['kind']=='pdu_state' for v in events));self.assertTrue(any(v.get('store')=='pdu_contexts' for v in events))
    def test_schema_rejects_id_overflow_and_accepts_new_operations(self):
        p=core().model_dump();p['flows'][0].update(pdu_session_id=15,pdu_session_count=2)
        with self.assertRaises(ValueError):Project.model_validate(p)
        p['flows'][0].update(pdu_session_count=1,operation='5G Deregistration');self.assertEqual(Project.model_validate(p).flows[0].operation,'5G Deregistration')

    def test_deregister_releases_sessions_with_different_dnns(self):
        def setup(p):
            second=p.flows[0].model_copy(deep=True);second.id='second';second.dnn='private';second.pdu_session_id=2;second.pdu_session_count=1;p.flows.append(second)
            for n in p.nodes:n.dnns=['internet','private']
            dereg=p.flows[0].model_copy(deep=True);dereg.id='dereg';dereg.operation='5G Deregistration';p.flows.append(dereg)
        e,r=scenario([(100,0),(300,1),(1000,2)],setup)
        self.assertEqual([t['status'] for t in r['traces']],['delivered']*3)
        self.assertEqual(r['session_lifecycle']['states'],{'RELEASED':2});self.assertEqual(r['session_lifecycle']['remaining_contexts'],0)

    def test_failed_automatic_deregister_reconciles_and_recovers(self):
        def setup(p):
            f=p.flows[0];f.deregister_after_s=1;f.max_retries=0;f.service_timeout_ms=150;f.timeout_ms=500;f.session_cleanup_retry_s=.3;f.session_cleanup_retries=10
            p.incidents=[Incident(kind='node_down',target='upf',start=.8,duration=2,detection_ms=0,recovery_ms=0)]
        e,r=scenario([(100,0)],setup)
        self.assertEqual(r['session_lifecycle']['states'],{'RELEASED':1})
        self.assertEqual(r['session_lifecycle']['pending_deregistrations'],0);self.assertFalse(e.contexts.data)

    def test_context_ttl_reclaims_other_peers_and_allows_id_reuse(self):
        def setup(p):
            p.flows[0].pdu_session_count=1;next(n for n in p.nodes if n.id=='upf').context_ttl_s=.3
        e,r=scenario([(100,0),(2000,0)],setup)
        self.assertEqual([t['status'] for t in r['traces']],['delivered','delivered'])
        self.assertEqual(r['session_lifecycle']['states'],{'RELEASED':2});self.assertEqual(r['session_lifecycle']['remaining_contexts'],0)

    def test_mobility_transfers_all_pdu_ids(self):
        from test_realism_25 import mobile_core
        template=mobile_core()
        def setup(p):p.nodes=template.nodes;p.links=template.links;p.flows[0].mobility=template.flows[0].mobility;p.flows[0].data_packets=80
        e,r=scenario([(100,0),(300,0)],setup)
        self.assertEqual(r['mobility'][0]['status'],'completed')
        self.assertEqual([record['ue'][0] for record in e.session_lifecycle.records.values()],['gnb2','gnb2'])
        self.assertTrue(all(e.session_lifecycle.context(owner,'upf2')['owner']==owner for owner in (1,2)))
        self.assertTrue(all(any(t['procedure_id']==owner and t['upf']=='upf2' and t['time']>4 and t['status']=='delivered' for t in r['user_plane']['traces']) for owner in (1,2)))

    def test_report_and_traces_identify_pdu_owner(self):
        e,r=scenario([(100,0),(300,0)])
        self.assertEqual({t['pdu_session_id'] for t in r['user_plane']['traces']},{1,2})
        self.assertEqual({t['pdu_session_id'] for t in r['message_traces']},{1,2})
        self.assertEqual({(c['owner'],c['pdu_session_id']) for c in r['session_lifecycle']['contexts']},{(1,1),(2,2)})
        self.assertEqual(r['session_lifecycle']['remaining_contexts'],r['resources']['stores']['pdu_contexts']['entries'])

    def test_lifecycle_journal_does_not_change_physics(self):
        def setup(p):p.flows[0].session_lifetime_s=.4
        _,on=scenario([(100,0),(300,0)],setup)
        def off(p):setup(p);p.journal_sessions=0
        _,without=scenario([(100,0),(300,0)],off)
        for key in ('summary','windows','user_plane','fiveg'):self.assertEqual(on[key],without[key],key)
        events=on['procedure_journal']['sessions'][0]['events']
        self.assertTrue(any(v['kind']=='timer_expired' and v.get('timer_type')=='session_release' for v in events))

    def test_stale_deregistration_cannot_erase_new_registration(self):
        def setup(p):
            for operation in ('5G Deregistration','5G Registration'):
                f=p.flows[0].model_copy(deep=True);f.id=operation;f.operation=operation;p.flows.append(f)
        e,r=scenario([(100,0),(1000,1),(2000,2),(3000,0)],setup)
        old=e.procedures[2];self.assertEqual(e.session_lifecycle.begin_deregister(old),'stale_deregistration')
        e.session_lifecycle.failed_deregister(old)
        self.assertTrue(e.registration_context(e.procedures[4],'amf')['registered'])
        self.assertEqual(e.session_lifecycle.context(4,'upf')['owner'],4)
        self.assertEqual(e.session_lifecycle.records[4]['state'],'ACTIVE')
