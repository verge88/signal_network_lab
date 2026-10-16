import unittest
from lab.presets import preset
from lab.schema import Project, Incident
from lab.simulator import simulate
from lab.fiveg import FiveGSimulation


def core(operation='5G Registration + PDU Session'):
    p=preset('5g');p.duration=10;p.window=1;p.trace_limit=20000;p.control_plane=False;p.routing_knowledge='shared'
    p.flows=[next(f for f in p.flows if f.operation==operation)]
    p.flows[0].rate=1
    for link in p.links:link.loss=0;link.jitter_ms=0
    return p


class FiveGTests(unittest.TestCase):
    def test_initial_registration_is_not_reseeded_after_amf_restart(self):
        p=core('5G PDU Session');p.flows[0].rate=0;p.flows[0].subscribers=1
        p.incidents=[Incident(kind='node_restart',target='amf',start=2,duration=1,detection_ms=0,recovery_ms=0)]
        class Arrivals(FiveGSimulation):
            def prepare(self):
                super().prepare();self.total=2
                for ident,t in enumerate((100,4000),1):self.push(t,'new',(ident,self.p.flows[0]))
        r=Arrivals(p).run();traces={t['procedure_id']:t for t in r['traces']}
        self.assertEqual(traces[1]['status'],'delivered');self.assertEqual(traces[2]['status'],'not_registered')

    def test_restart_before_first_pdu_invalidates_unmaterialized_initial_state(self):
        p=core('5G PDU Session');p.flows[0].rate=0
        p.incidents=[Incident(kind='node_restart',target='amf',start=0,duration=1,detection_ms=0,recovery_ms=0)]
        class Arrival(FiveGSimulation):
            def prepare(self):super().prepare();self.total=1;self.push(2000,'new',(1,self.p.flows[0]))
        r=Arrival(p).run();self.assertEqual(r['summary']['failure_reasons'],{'not_registered':1})

    def test_explicit_registration_restores_pdu_after_restart(self):
        p=core('5G PDU Session');p.flows[0].rate=0;p.flows[0].subscribers=1
        registration=p.flows[0].model_copy(deep=True);registration.id='reg';registration.operation='5G Registration';registration.initial_registered=False;p.flows.append(registration)
        p.incidents=[Incident(kind='node_restart',target='amf',start=1,duration=1,detection_ms=0,recovery_ms=0)]
        class Arrivals(FiveGSimulation):
            def prepare(self):
                super().prepare();self.total=3
                for ident,t,flow in [(1,2500,0),(2,4000,1),(3,6000,0)]:self.push(t,'new',(ident,self.p.flows[flow]))
        r=Arrivals(p).run();traces={t['procedure_id']:t for t in r['traces']}
        self.assertEqual([traces[i]['status'] for i in (1,2,3)],['not_registered','delivered','delivered'])

    def test_temporary_amf_outage_preserves_initial_registration(self):
        p=core('5G PDU Session');p.flows[0].rate=0
        p.incidents=[Incident(kind='node_down',target='amf',start=0,duration=1,detection_ms=0,recovery_ms=0)]
        class Arrival(FiveGSimulation):
            def prepare(self):super().prepare();self.total=1;self.push(2000,'new',(1,self.p.flows[0]))
        self.assertEqual(Arrival(p).run()['summary']['delivered'],1)

    def test_all_three_procedures_and_real_interface_load(self):
        p=preset('5g');p.duration=10
        for f in p.flows:f.rate=.5
        for l in p.links:l.loss=0
        r=simulate(p)
        self.assertEqual(r['summary']['delivery_ratio'],1)
        self.assertEqual(len(r['procedures']),3)
        self.assertEqual({m['protocol'] for m in r['message_traces']},{'NGAP','SBI','PFCP'})
        for link in p.links:self.assertGreater(r['links'][link.id]['bytes'],0)
        self.assertGreater(r['fiveg']['contexts']['registered'],0)
        self.assertGreater(r['fiveg']['contexts']['pdu_active'],0)

    def test_registration_accept_follows_backend_and_pdu_follows_registration(self):
        r=simulate(core());self.assertEqual(r['summary']['delivery_ratio'],1)
        for ident in {m['procedure_id'] for m in r['message_traces']}:
            messages=[m for m in r['message_traces'] if m['procedure_id']==ident]
            accepted=next(m for m in messages if m['operation']=='5G Registration Accept')
            sdm=min(m['end'] for m in messages if m['operation']=='Nudm SDMGet Response')
            pdu=next(m for m in messages if m['operation']=='5G PDU Session')
            self.assertGreaterEqual(accepted['time'],sdm)
            self.assertGreaterEqual(pdu['time'],accepted['end'])
        self.assertTrue(all(t['steps_completed']==2 and t['route']==['gnb','amf'] for t in r['traces']))

    def test_pdu_requires_registration_unless_explicitly_seeded(self):
        p=core('5G PDU Session');p.flows[0].initial_registered=False
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertEqual(r['summary']['failure_reasons'],{'not_registered':r['summary']['sent']})
        self.assertNotIn('PFCP Session Establishment',r['fiveg']['services'])

    def test_slice_and_dnn_selection_rejections(self):
        p=core();p.flows[0].snssai='2-010203'
        self.assertTrue(all(t['status']=='slice_not_supported' for t in simulate(p)['traces']))
        p=core('5G PDU Session');p.flows[0].dnn='private'
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertNotIn('PFCP Session Establishment',r['fiveg']['services'])

    def test_backend_error_propagates_and_stops_subsequent_phases(self):
        p=core();next(n for n in p.nodes if n.kind=='AUSF').error_rate=1
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertNotIn('5G PDU Session',r['fiveg']['services'])
        self.assertNotIn('Nudm SDMGet',r['fiveg']['services'])
        self.assertTrue(all(t['status']=='protocol_error' for t in r['traces']))

    def test_nrf_is_optional_and_suspended_nf_is_not_discovered(self):
        p=core('5G Registration');next(n for n in p.nodes if n.kind=='NRF').nf_status='SUSPENDED'
        self.assertEqual(simulate(p)['summary']['delivered'],0)
        p.flows[0].use_nrf=False
        r=simulate(p)
        self.assertEqual(r['summary']['delivery_ratio'],1)
        self.assertNotIn('Nnrf NFDiscovery',r['fiveg']['services'])

    def test_ngap_cannot_route_over_sbi(self):
        p=core('5G Registration');next(l for l in p.links if l.id=='n2').protocol='SBI'
        p.flows[0].max_retries=0;p.flows[0].timeout_ms=10
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertTrue(all(t['status']=='timeout' and t['latency_ms']==10 for t in r['traces']))

    def test_service_timeout_reaches_root_as_explicit_failure(self):
        p=core('5G Registration');next(n for n in p.nodes if n.kind=='UDR').enabled=False
        p.flows[0].max_retries=0;p.flows[0].service_timeout_ms=20
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertTrue(any(t['status']=='service_timeout' for t in r['traces']))

    def test_pfcp_uses_datagrams_and_rpc_retries_not_transport_acks(self):
        p=core('5G PDU Session');r=simulate(p)
        self.assertEqual(r['links']['n4']['acks'],0)
        self.assertEqual(r['links']['n4']['retransmissions'],0)
        next(l for l in p.links if l.id=='n4').loss=1
        r=simulate(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertGreater(r['summary']['procedure_retries'],0)
        self.assertEqual(r['links']['n4']['retransmissions'],0)

    def test_request_retries_do_not_duplicate_the_server_workflow(self):
        p=core('5G Registration');p.flows[0].timeout_ms=70;p.flows[0].retry_backoff_ms=1
        r=simulate(p)
        self.assertGreater(r['nodes']['amf']['duplicates'],0)
        self.assertEqual(r['fiveg']['services']['Nausf UEAuthentication']['requests'],r['summary']['sent'])

    def test_restart_removes_contexts_and_cached_rpc_answers(self):
        engine=FiveGSimulation(core())
        engine.contexts[('amf','gnb',1,'00101','1-010203','internet')]={'registered':True}
        engine.cache[('5g','amf',1,1)]='success'
        engine.node_fault('amf','node_restart')
        self.assertFalse(engine.contexts);self.assertFalse(engine.cache)

    def test_nf_discovery_selects_available_replica(self):
        p=core('5G Registration');original=next(n for n in p.nodes if n.kind=='AUSF')
        replica=original.model_copy(deep=True);replica.id='ausf-b';replica.name='Replica';replica.host='ausfb.5gc.lab';replica.x+=200;p.nodes.append(replica)
        original.nf_status='SUSPENDED'
        link=next(l for l in p.links if l.source=='ausf').model_copy(deep=True);link.id='sbi-ausfb';link.source='ausf-b';p.links.append(link)
        r=simulate(Project.model_validate(p.model_dump()))
        self.assertEqual(r['summary']['delivery_ratio'],1)
        self.assertTrue(all(t['nf_selected']['AUSF']=='ausf-b' for t in r['traces']))

    def test_discovery_dependency_failure_is_labelled_but_unused_nf_is_not(self):
        p=core('5G Registration');p.flows[0].rate=4
        p.incidents=[Incident(kind='node_down',target='ausf',start=2,duration=5,detection_ms=0)]
        r=simulate(p)
        self.assertTrue(all(r['windows'][i]['affected']==1 for i in (3,4,5)))
        self.assertGreater(r['summary']['loss_ratio'],0)
        p.incidents[0].target='pcf'
        r=simulate(p)
        self.assertEqual(sum(w['affected'] for w in r['windows']),0)
        self.assertEqual(r['summary']['delivery_ratio'],1)

    def test_legacy_and_5g_can_coexist_without_protocol_bridging(self):
        p=core('5G Registration');legacy=preset('diameter')
        p.nodes.extend(legacy.nodes);p.links.extend(legacy.links);p.flows.extend(legacy.flows)
        p.flows[-1].rate=1
        for l in p.links:l.loss=0
        r=simulate(Project.model_validate(p.model_dump()))
        self.assertEqual(r['summary']['delivery_ratio'],1)
        self.assertEqual(len(r['procedures']),2)

    def test_seed_reproducibility_and_validation(self):
        p=core();self.assertEqual(simulate(p),simulate(p))
        data=p.model_dump();data['engine']='messages'
        with self.assertRaises(ValueError):Project.model_validate(data)
        data=p.model_dump();data['flows'][0]['nf_bindings']={'SMF':'udr'}
        with self.assertRaises(ValueError):Project.model_validate(data)


if __name__=='__main__':unittest.main()
