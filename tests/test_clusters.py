import unittest
from lab.schema import Project,StateCluster,Incident
from lab.fiveg import FiveGSimulation
from lab.procedures import ProcedureSimulation
from test_fiveg import core


def ha_core(kind='AMF',**options):
    p=core('5G PDU Session');f=p.flows[0];f.rate=0;f.subscribers=1;f.session_lifetime_s=0;f.data_packets=80;f.data_interval_ms=100
    old=next(n for n in p.nodes if n.kind==kind);clone=old.model_copy(deep=True);clone.id=old.id+'2';clone.name=kind+' Reserve';clone.host=clone.id+'.lab';clone.y+=250;p.nodes.append(clone)
    for link in list(p.links):
        if old.id in (link.source,link.target):
            new=link.model_copy(deep=True);new.id=link.id+'-reserve';new.source=clone.id if new.source==old.id else new.source;new.target=clone.id if new.target==old.id else new.target;p.links.append(new)
    p.clusters=[StateCluster(id='ha',members=[old.id,clone.id],heartbeat_ms=100,heartbeat_timeout_ms=300,failover_ms=200,sync_ms=200,**options)]
    return Project.model_validate(p.model_dump())


def run_at(p,times=(100,),base=FiveGSimulation):
    class Scheduled(base):
        def prepare(self):
            super().prepare();self.total=len(times)
            for ident,t in enumerate(times,1):self.push(t,'new',(ident,self.p.flows[0]))
    e=Scheduled(p);return e,e.run()


class ClusterTests(unittest.TestCase):
    def test_replicates_separate_pdu_and_registration_contexts(self):
        e,r=run_at(ha_core())
        self.assertEqual(e.contexts[e.context_key(e.procedures[1],'amf2')]['registered'],True)
        self.assertEqual(e.session_lifecycle.context(1,'amf2')['owner'],1)
        self.assertGreater(r['clusters']['groups'][0]['applied_updates'],0)
        self.assertTrue(any(m['operation']=='STATE_REPLICATE' for m in e.messages.values()))

    def test_amf_restart_preserves_replicated_active_session(self):
        p=ha_core();p.incidents=[Incident(kind='node_restart',target='amf',start=2,duration=5,detection_ms=0,recovery_ms=0)]
        e,r=run_at(p)
        self.assertEqual(e.session_lifecycle.records[1]['state'],'ACTIVE')
        self.assertTrue(any(t['time']>3 and t['status']=='delivered' for t in r['user_plane']['traces']))
        self.assertTrue(any(v['event']=='session_rebound' for v in r['clusters']['events']))
        self.assertEqual(e.procedures[1]['flow'].target,'amf2')

    def test_smf_restart_preserves_replicated_active_session(self):
        p=ha_core('SMF');p.incidents=[Incident(kind='node_restart',target='smf',start=2,duration=5,detection_ms=0,recovery_ms=0)]
        e,r=run_at(p)
        self.assertEqual(e.session_lifecycle.records[1]['state'],'ACTIVE');self.assertEqual(e.procedures[1]['nf_selected']['SMF'],'smf2')
        self.assertTrue(any(t['time']>3 and t['status']=='delivered' for t in r['user_plane']['traces']))

    def test_missing_replica_is_not_recreated_by_election(self):
        p=ha_core(replication_delay_ms=5000);p.incidents=[Incident(kind='node_restart',target='amf',start=2,duration=7,detection_ms=0,recovery_ms=0)]
        e,r=run_at(p)
        self.assertTrue(any(v['event']=='session_state_lost' for v in r['clusters']['events']))
        self.assertNotEqual(e.session_lifecycle.records[1]['state'],'ACTIVE')
        self.assertGreater(r['clusters']['groups'][0]['unreplicated_entries_lost'],0)
        self.assertFalse(any(t['time']>3 and t['status']=='delivered' for t in r['user_plane']['traces']))

    def test_two_node_quorum_blocks_service_after_one_failure(self):
        p=ha_core(partition_policy='quorum');p.incidents=[Incident(kind='node_down',target='amf',start=2,duration=7,detection_ms=0,recovery_ms=0)]
        e,r=run_at(p)
        self.assertFalse(any(t['time']>3 and t['status']=='delivered' for t in r['user_plane']['traces']))
        self.assertFalse(e.clusters.can_serve('amf2'))

    def test_partition_creates_local_leaders_and_healing_converges(self):
        p=ha_core();lid=next(l.id for l in p.links if l.source=='amf' and l.protocol=='SBI')
        p.incidents=[Incident(kind='link_down',target=lid,start=2,duration=3,detection_ms=0,recovery_ms=0)]
        e,r=run_at(p)
        self.assertGreater(r['clusters']['groups'][0].get('split_brain_ticks',0),0)
        self.assertEqual(sum(e.clusters.can_serve(n) for n in ('amf','amf2')),1)
        self.assertEqual(e.clusters.views['amf']['leader'],e.clusters.views['amf2']['leader'])

    def test_replica_ttl_is_not_refreshed_by_copy(self):
        p=ha_core(replication_delay_ms=100);next(n for n in p.nodes if n.id=='amf').context_ttl_s=2
        e,r=run_at(p)
        self.assertFalse(e.session_lifecycle.context(1,'amf2'))
        self.assertEqual(r['session_lifecycle']['remaining_contexts'],0)

    def test_release_tombstone_removes_replica(self):
        p=ha_core();p.flows[0].session_lifetime_s=.5;e,r=run_at(p)
        self.assertEqual(r['session_lifecycle']['states'],{'RELEASED':1});self.assertEqual(r['session_lifecycle']['remaining_contexts'],0)
        self.assertEqual(r['clusters']['groups'][0]['divergent_keys'],0)

    def test_replica_capacity_is_enforced(self):
        p=ha_core();p.flows[0].pdu_session_count=2;next(n for n in p.nodes if n.id=='amf2').pdu_session_limit=1
        e,r=run_at(p,(100,500))
        self.assertEqual(len(e.session_lifecycle.store.orders['amf2']),1)
        self.assertGreater(r['clusters']['groups'][0]['capacity_rejections'],0)

    def test_schema_rejects_mixed_kinds_and_duplicate_membership(self):
        p=ha_core().model_dump();p['clusters'][0]['members']=['amf','smf']
        with self.assertRaises(ValueError):Project.model_validate(p)
        p=ha_core().model_dump();p['clusters'].append(dict(p['clusters'][0],id='another'))
        with self.assertRaises(ValueError):Project.model_validate(p)

    def test_old_project_has_no_cluster_traffic(self):
        p=core('5G PDU Session');p.flows[0].rate=0;e,r=run_at(p)
        self.assertEqual(r['clusters']['groups'],[]);self.assertFalse(any(m.get('category')=='replication' for m in e.messages.values()))

    def test_deterministic_results(self):
        p=ha_core();self.assertEqual(run_at(p)[1],run_at(p)[1])

    def test_udr_replicates_update_and_exposes_read_versions(self):
        p=ha_core('UDR');p.flows[0].operation='5G Registration + PDU Session';e,r=run_at(p)
        self.assertEqual(r['summary']['delivered'],1)
        self.assertTrue(e.repository.get(e.context_key(e.procedures[1],'udr2'))['updated'])
        self.assertTrue(any(v['event']=='repository_read' and v['read_version'] is not None for v in r['clusters']['events']))

    def test_hss_pair_copies_registration_and_switches_endpoint(self):
        from lab.presets import preset
        p=preset('diameter');p.duration=10;p.window=1;p.control_plane=False;p.routing_knowledge='shared';p.flows=p.flows[:1];p.flows[0].rate=0;p.flows[0].subscribers=1
        old=next(n for n in p.nodes if n.kind=='HSS');reserve=old.model_copy(deep=True);reserve.id='hss2';reserve.host='hss2';p.nodes.append(reserve)
        for link in list(p.links):
            link.loss=0;link.jitter_ms=0
            if old.id in (link.source,link.target):
                copy=link.model_copy(deep=True);copy.id=link.id+'-reserve';copy.source='hss2' if copy.source==old.id else copy.source;copy.target='hss2' if copy.target==old.id else copy.target;p.links.append(copy)
        p.clusters=[StateCluster(id='hss-ha',members=[old.id,'hss2'],heartbeat_ms=100,heartbeat_timeout_ms=300,failover_ms=100,sync_ms=200)]
        p.incidents=[Incident(kind='node_restart',target=old.id,start=2,duration=7,detection_ms=0,recovery_ms=0)]
        e,r=run_at(p,(100,4000),ProcedureSimulation)
        self.assertEqual(r['summary']['delivered'],2);self.assertEqual(e.procedures[2]['destination'],'hss2')
        self.assertEqual(e.subscribers[('hss2',p.flows[0].source,0)],'registered')

    def test_reordering_cannot_overwrite_a_newer_replica(self):
        e,_=run_at(ha_core());manager=e.clusters;pr=e.procedures[1];key=e.context_key(pr,'amf2');canonical=('fiveg_contexts',tuple(key[1:]));current=manager.entries['amf2'][canonical]
        old=dict(current,value={'registered':False},stamp=[0,0,'amf'])
        ident=manager.send('amf','amf2','update',old);manager.apply(ident,e.node_epoch['amf2'])
        self.assertTrue(e.contexts[key]['registered']);self.assertEqual(manager.entries['amf2'][canonical]['stamp'],current['stamp'])

    def test_three_node_quorum_preserves_majority_after_one_failure(self):
        p=ha_core(partition_policy='quorum');old=next(n for n in p.nodes if n.id=='amf2');third=old.model_copy(deep=True);third.id='amf3';third.host='amf3';p.nodes.append(third)
        for link in list(p.links):
            if 'amf2' in (link.source,link.target):
                copy=link.model_copy(deep=True);copy.id=link.id+'-third';copy.source='amf3' if copy.source=='amf2' else copy.source;copy.target='amf3' if copy.target=='amf2' else copy.target;p.links.append(copy)
        p.clusters[0].members.append('amf3');p.incidents=[Incident(kind='node_restart',target='amf',start=2,duration=7,detection_ms=0,recovery_ms=0)]
        e,r=run_at(Project.model_validate(p.model_dump()))
        self.assertTrue(any(t['time']>3 and t['status']=='delivered' for t in r['user_plane']['traces']))
        self.assertTrue(any(v['node']=='amf2' and v['role']=='LEADER' and len(v['live'])>=2 for v in r['clusters']['views']))

    def test_ha_preset_is_valid_and_computes(self):
        from lab.presets import preset
        p=preset('5g-ha');p.incidents=[];p.duration=10;p.window=1
        for f in p.flows:f.rate=0
        e,r=run_at(p,(6000,))
        self.assertEqual(len(r['clusters']['groups']),3);self.assertEqual(r['summary']['delivered'],1,r['summary'])

    def test_database_timeout_does_not_apply_replica(self):
        p=ha_core();replica=next(n for n in p.nodes if n.id=='amf2');replica.database_ms=100;replica.db_timeout_ms=1
        e,r=run_at(p)
        self.assertFalse(e.session_lifecycle.context(1,'amf2'));self.assertGreater(r['clusters']['groups'][0]['database_rejections'],0)

    def test_replication_writes_do_not_bypass_database_through_cache(self):
        p=ha_core();replica=next(n for n in p.nodes if n.id=='amf2');replica.database_ms=20;replica.db_connections=1;replica.cache_entries=100
        e,r=run_at(p)
        tasks=[v for v in e.storage.tasks.values() if v['node']=='amf2' and v['msg']['operation']=='STATE_REPLICATE']
        self.assertGreaterEqual(len(tasks),2);self.assertTrue(all(t['started'] is not None for t in tasks))
        self.assertFalse(any(k[0]=='amf2' and k[-1]=='STATE_REPLICATE' for k in e.storage.data_cache.data))
        self.assertGreaterEqual(r['clusters']['groups'][0]['lag_max_ms'],40)

    def test_repository_read_your_write_rejects_missing_version(self):
        e,r=run_at(ha_core('UDR'));pr=e.procedures[1];key=e.context_key(pr,'udr2')
        pr['repository_tokens']={tuple(key[1:]):[e.t+1,999,'udr']}
        self.assertFalse(e.clusters.repository_read(pr,'udr2',key))
        self.assertEqual(e.clusters.stats['ha']['stale_reads_rejected'],1)

    def test_quorum_requires_delivered_heartbeats_at_startup(self):
        p=ha_core(partition_policy='quorum')
        for link in p.links:
            if link.protocol=='SBI' and 'amf2' in (link.source,link.target):link.enabled=False
        e,r=run_at(p)
        self.assertEqual(r['summary']['delivered'],0)
        self.assertTrue(all(v['role']=='NO_QUORUM' for v in r['clusters']['views']))

    def test_delete_on_empty_reserve_prevents_old_primary_resurrection(self):
        p=ha_core(replication_delay_ms=5000);f=p.flows[0];f.max_retries=0;f.service_timeout_ms=150;f.timeout_ms=400;f.session_cleanup_retry_s=.1;f.session_cleanup_retries=3
        p.incidents=[Incident(kind='node_down',target='amf',start=2,duration=3,detection_ms=0,recovery_ms=0)]
        e,r=run_at(p)
        self.assertEqual(r['session_lifecycle']['states'],{'RELEASED':1});self.assertEqual(r['session_lifecycle']['remaining_contexts'],0)

    def test_deregister_on_empty_reserve_revokes_old_primary_registration(self):
        p=ha_core(replication_delay_ms=5000);f=p.flows[0];f.deregister_after_s=3;f.max_retries=0;f.service_timeout_ms=150;f.timeout_ms=400;f.session_cleanup_retry_s=.1;f.session_cleanup_retries=3
        p.incidents=[Incident(kind='node_down',target='amf',start=2,duration=3,detection_ms=0,recovery_ms=0)]
        e,r=run_at(p)
        self.assertEqual(r['session_lifecycle']['pending_deregistrations'],0)
        self.assertFalse(any(k[0] in ('amf','amf2') for k in e.contexts.data))
