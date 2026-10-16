import unittest
from lab.schema import Project,Incident
from lab.presets import preset
from lab.simulator import simulate
from lab.procedures import ProcedureSimulation
from lab.fiveg import FiveGSimulation


def pair(protocol='Diameter'):
    return Project(name='Management fixture',duration=10,window=1,nodes=[dict(id='a',name='A',kind='MME',x=0,y=0),dict(id='b',name='B',kind='HSS',x=300,y=0)],links=[dict(id='ab',source='a',target='b',protocol=protocol,latency_ms=5,jitter_ms=0,loss=0)],flows=[dict(id='f',source='a',target='b',protocol='Diameter',operation='Diameter ULR',rate=0)])


class ManagementTests(unittest.TestCase):
    def test_capabilities_and_watchdog_use_real_queues_and_bandwidth(self):
        p=pair();result=simulate(p);control=result['control_plane']
        self.assertEqual(control['associations']['ab'],'OPEN');self.assertEqual(control['counts']['CER'],1);self.assertGreater(control['counts']['DWR'],1)
        self.assertGreater(result['links']['ab']['bytes'],1000);self.assertGreater(result['nodes']['b']['processed'],0)
        self.assertGreater(result['nodes']['a']['busy_ms'],0)
        p.control_plane=False;p.routing_knowledge='shared';baseline=simulate(p)
        self.assertEqual(baseline['links']['ab']['bytes'],0)

    def test_m3ua_waits_for_aspup_then_aspac(self):
        result=simulate(pair('M3UA'));messages=result['control_plane']['messages']
        operations={m['operation']:m for m in messages}
        self.assertLessEqual(operations['ASPUP_ACK']['end'],operations['ASPAC']['time'])
        self.assertEqual(result['control_plane']['associations']['ab'],'OPEN')

    def test_failed_handshake_does_not_open_peer(self):
        p=pair();p.links[0].reverse_loss=1;p.links[0].control_timeout_ms=30;p.links[0].control_retries=0
        result=simulate(p)
        self.assertNotEqual(result['control_plane']['associations']['ab'],'OPEN');self.assertGreater(result['control_plane']['counts']['timeouts'],0)

    def test_watchdog_failure_and_recovery_are_observable(self):
        p=pair();p.incidents=[Incident(kind='link_down',target='ab',start=2,duration=3,detection_ms=0,recovery_ms=0)]
        result=simulate(p);states=[event['state'] for event in result['lifecycle']]
        self.assertIn('CONTROL_DOWN',states);self.assertGreaterEqual(states.count('CONTROL_OPEN'),2)

    def test_each_observer_can_have_a_different_network_view(self):
        p=pair();third=p.nodes[1].model_copy(deep=True);third.id='c';third.name='C';p.nodes.append(third)
        p.nodes[1].kind='DRA';p.nodes[1].advertisement_delay_ms=500
        second=p.links[0].model_copy(deep=True);second.id='bc';second.source='b';second.target='c';p.links.append(second)
        p.control_plane=False;p.incidents=[Incident(kind='node_down',target='c',start=2,duration=3,detection_ms=0,recovery_ms=0)]
        e=ProcedureSimulation(p);result=e.run();events=[x for x in result['control_plane']['view_events'] if x['resource']=='node' and x['target']=='c' and not x['up']]
        times={x['observer']:x['time'] for x in events}
        self.assertGreater(times['a']-times['b'],.49)

    def test_old_update_does_not_overwrite_newer_state(self):
        e=ProcedureSimulation(pair());key=('node','b','both')
        e.control.record_view('a',key,True,10,'b');e.control.record_view('a',key,False,9,'b')
        self.assertTrue(e.control.known('a','node','b'))

    def chain(self):
        p=pair();p.control_plane=False;p.route_refresh_ms=60000;p.nodes[1].kind='DRA'
        third=p.nodes[1].model_copy(deep=True);third.id='c';third.name='C';third.kind='HSS';p.nodes.append(third)
        second=p.links[0].model_copy(deep=True);second.id='bc';second.source='b';second.target='c';p.links.append(second)
        p.flows[0].target='c'
        return p

    def test_periodic_refresh_converges_after_a_lost_advertisement(self):
        p=self.chain();p.incidents=[Incident(kind='node_down',target='c',start=2,duration=3,detection_ms=0,recovery_ms=0),Incident(kind='link_down',target='ab',direction='reverse',start=1.9,duration=1.5,detection_ms=0,recovery_ms=0)]
        p.links[0].transport_retries=0
        without=simulate(p)
        missed=lambda r:[x for x in r['control_plane']['view_events'] if x['observer']=='a' and x['resource']=='node' and x['target']=='c' and not x['up']]
        self.assertFalse([event for event in missed(without) if event['time']<5])
        p.route_refresh_ms=300;refreshed=simulate(p);updates=missed(refreshed)
        self.assertTrue(updates);self.assertGreater(updates[0]['time'],3.4);self.assertLess(updates[0]['time'],4)

    def test_forwarder_uses_its_own_view_and_trims_unvisited_route(self):
        p=self.chain();p.nodes[1].advertisement_delay_ms=5000
        p.incidents=[Incident(kind='node_down',target='c',start=2,duration=3,detection_ms=0,recovery_ms=0)]
        p.flows[0].timeout_ms=500;p.flows[0].max_retries=0
        class One(ProcedureSimulation):
            def prepare(self):super().prepare();self.total=1;self.push(2100,'new',(1,self.p.flows[0]))
        e=One(p);r=e.run()
        self.assertEqual(r['traces'][0]['cause'],'no_route');self.assertEqual(r['traces'][0]['route'],['a','b'])
        self.assertFalse([tr for tr in e.transfers.values() if tr['msg'].get('category')=='signalling' and tr['dst']=='c'])

    def test_management_does_not_execute_database_application_work(self):
        p=pair();p.routing_knowledge='shared';p.nodes[1].database_ms=1000;p.nodes[1].cache_hit_ratio=0
        p.links[0].control_timeout_ms=100;p.links[0].control_retries=0
        result=simulate(p)
        self.assertEqual(result['control_plane']['associations']['ab'],'OPEN')
        self.assertEqual(result['control_plane']['counts'].get('timeouts',0),0)


class UserPlaneTests(unittest.TestCase):
    def config(self):
        p=preset('5g');p.duration=10;p.window=1;p.flows=[f for f in p.flows if f.operation=='5G PDU Session'];p.flows[0].rate=0
        for link in p.links:link.loss=0;link.jitter_ms=0
        return p

    def run_one(self,p):
        class One(FiveGSimulation):
            def prepare(self):super().prepare();self.total=1;self.push(1000,'new',(1,self.p.flows[0]))
        return One(p).run()

    def test_successful_pdu_is_verified_by_bidirectional_n3_n6_data(self):
        p=self.config();r=self.run_one(p)
        self.assertEqual(r['summary']['delivered'],1);self.assertEqual(r['user_plane']['sent'],p.flows[0].data_packets)
        self.assertEqual(r['user_plane']['delivery_ratio'],1);self.assertGreater(r['links']['n3']['directions']['reverse']['bytes'],0)
        self.assertGreater(r['links']['n6']['directions']['forward']['bytes'],0);self.assertEqual(r['links']['n3']['acks'],0)
        self.assertEqual(sum(row['user_delivered'] for row in r['windows']),r['user_plane']['delivered'])

    def test_pdu_success_does_not_hide_missing_data_path(self):
        p=self.config();next(link for link in p.links if link.id=='n3').enabled=False;r=self.run_one(p)
        self.assertEqual(r['summary']['delivered'],1);self.assertEqual(r['user_plane']['delivered'],0)
        self.assertEqual(r['user_plane']['failure_reasons'],{'no_user_plane_route':p.flows[0].data_packets})

    def test_n6_loss_is_distinct_from_control_success(self):
        p=self.config();next(link for link in p.links if link.id=='n6').loss=1;r=self.run_one(p)
        self.assertEqual(r['summary']['delivered'],1);self.assertEqual(r['user_plane']['delivered'],0)
        self.assertGreater(r['links']['n6']['dropped'],0)

    def test_upf_restart_invalidates_data_session(self):
        p=self.config();p.flows[0].data_packets=40;p.flows[0].data_interval_ms=200
        p.incidents=[Incident(kind='node_restart',target='upf',start=5,duration=1,detection_ms=0,recovery_ms=0)]
        r=self.run_one(p)
        self.assertEqual(r['summary']['delivered'],1);self.assertGreater(r['user_plane']['delivered'],0)
        self.assertIn('user_plane_context_lost',r['user_plane']['failure_reasons'])

    def test_reproducibility_includes_control_and_data(self):
        p=self.config();self.assertEqual(self.run_one(p),self.run_one(p))

    def test_data_path_startup_and_explicit_next_hop_work_without_management(self):
        p=self.config();p.control_plane=False;p.routing_knowledge='shared'
        next(link for link in p.links if link.id=='n3').startup_ms=10
        gnb=next(node for node in p.nodes if node.id=='gnb');upf=next(node for node in p.nodes if node.id=='upf');amf=next(node for node in p.nodes if node.id=='amf')
        gnb.routing_table={upf.host or upf.id:['upf'],amf.host or amf.kind:['amf']}
        result=self.run_one(p)
        self.assertEqual(result['summary']['delivered'],1);self.assertEqual(result['user_plane']['delivery_ratio'],1)
        gnb.routing_table[upf.host or upf.id]=['amf'];blocked=self.run_one(p)
        self.assertEqual(blocked['summary']['delivered'],1);self.assertEqual(blocked['user_plane']['delivered'],0)
