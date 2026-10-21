import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lab.simulator import simulate
from lab.procedures import ProcedureSimulation
from lab.procedure_trace import snapshot,causal_chain
from lab import research,storage
from test_procedures import network
from test_fiveg import core


class JournalTests(unittest.TestCase):
    def test_fiveg_nested_rpcs_and_mutable_context_diffs_are_captured(self):
        p=core();p.journal_procedure_id=1
        session=simulate(p)['procedure_journal']['sessions'][0];events=session['events']
        self.assertEqual(session['status'],'delivered')
        self.assertTrue(any(event.get('parent_rpc') is not None for event in events))
        changes=[event for event in events if event['kind']=='context_changed' and isinstance(event.get('after'),dict)]
        self.assertTrue(any(event['after'].get('registered') and not (event['before'] or {}).get('registered') for event in changes))
        state=snapshot(session,len(events)-1)
        self.assertFalse([timer for timer in state['timers'].values() if timer['state']=='active' and timer.get('timer_type') in ('rpc','procedure')])
    def test_journal_is_physics_neutral_and_has_independent_sampling(self):
        p=network();p.flows[0].rate=15
        baseline=simulate(p.model_copy(update=dict(journal_sessions=0)));recorded=simulate(p)
        for key in ('summary','windows','nodes','links','traces','message_traces','control_plane','resources'):self.assertEqual(baseline[key],recorded[key],key)
        self.assertEqual(baseline['procedure_journal']['sessions'],[]);self.assertEqual(len(recorded['procedure_journal']['sessions']),20)
        again=simulate(p);self.assertEqual(recorded['procedure_journal'],again['procedure_journal'])
    def test_exact_target_is_recorded_from_start_and_all_causes_precede_their_events(self):
        p=network();p.journal_sessions=1;p.journal_procedure_id=7
        session=simulate(p)['procedure_journal']['sessions'][0];self.assertEqual(session['procedure_id'],7)
        self.assertEqual(session['events'][0]['kind'],'procedure_start');self.assertEqual(session['status'],'delivered')
        times=[event['time_ms'] for event in session['events']];self.assertEqual(times,sorted(times))
        for event in session['events']:
            if event['cause_event'] is not None:self.assertLess(event['cause_event'],event['id'])
        self.assertTrue(any(event['kind']=='ack_received' for event in session['events']))
    def test_late_duplicates_do_not_replace_database_or_cpu_causal_parents(self):
        p=network();p.journal_procedure_id=1;p.links[0].reverse_latency_ms=100;p.links[0].rto_ms=10;p.links[0].rto_min_ms=10;p.links[0].rto_max_ms=10
        p.nodes[1].storage_model='queued';p.nodes[1].database_ms=150;p.nodes[1].cache_entries=0
        session=simulate(p)['procedure_journal']['sessions'][0];events={event['id']:event for event in session['events']}
        for event in events.values():
            if event['kind']=='database_finished' and event['reason']=='success':self.assertEqual(events[event['cause_event']]['kind'],'database_started')
            if event['kind']=='cpu_finished':self.assertEqual(events[event['cause_event']]['kind'],'cpu_started')
            if event['kind']=='ack_sent':self.assertEqual(events[event['cause_event']]['kind'],'packet_received')
    def test_database_timeout_has_its_timer_as_a_cause_and_no_phantom_timers_on_overflow(self):
        p=network();p.journal_sessions=100;p.nodes[1].storage_model='queued';p.nodes[1].database_ms=500;p.nodes[1].db_timeout_ms=30;p.nodes[1].db_queue_limit=0;p.nodes[1].cache_entries=0
        sessions=simulate(p)['procedure_journal']['sessions']
        for session in sessions:
            events={event['id']:event for event in session['events']}
            for event in events.values():
                if event['kind']=='database_finished' and event['reason']=='database_timeout':self.assertEqual(events[event['cause_event']]['kind'],'timer_expired')
                if event['kind']=='timer_cancelled' and event.get('timer_type')=='database':self.assertIsNotNone(event['cause_event'])
    def test_cpu_database_channel_waiting_and_actual_routing_rules_are_observed(self):
        p=network();p.flows[0].rate=15;p.flows[0].subscribers=1;p.nodes[1].processing_ms=20;p.nodes[1].workers=1
        p.nodes[1].storage_model='queued';p.nodes[1].database_ms=120;p.nodes[1].db_connections=1;p.nodes[1].cache_entries=0;p.nodes[1].db_timeout_ms=5000
        p.nodes[0].routing_table={p.nodes[1].realm:['b']};p.journal_sessions=100
        sessions=simulate(p)['procedure_journal']['sessions'];events=[event for session in sessions for event in session['events']]
        self.assertTrue(any(event['kind']=='cpu_started' and event['wait_ms']>0 for event in events))
        self.assertTrue(any(event['kind']=='database_started' and event['wait_ms']>0 for event in events))
        self.assertTrue(any(event['kind']=='channel_transmit' and event['serialization_ms']>0 for event in events))
        route=next(event for event in events if event['kind']=='route_selected' and event['hops'])
        self.assertEqual(route['hops'][0]['configured_next_hops'],['b']);self.assertEqual(route['hops'][0]['rank'],0)
    def test_retry_has_an_explicit_timeout_cause_and_late_ack_cannot_change_success(self):
        p=network();p.journal_procedure_id=1;p.links[0].reverse_latency_ms=100;p.links[0].rto_ms=10;p.links[0].rto_min_ms=10;p.links[0].rto_max_ms=10;p.links[0].transport_retries=2
        session=simulate(p)['procedure_journal']['sessions'][0]
        retry=next(event for event in session['events'] if event['kind']=='transport_retry')
        self.assertEqual(causal_chain(session,retry['id'])[-2]['kind'],'timer_expired')
        self.assertEqual(session['status'],'delivered');self.assertTrue(any(event['kind']=='ack_received' and not event['accepted'] for event in session['events']))
    def test_context_before_after_ttl_and_snapshot_do_not_reveal_future_events(self):
        p=network();p.journal_procedure_id=1;p.nodes[1].context_ttl_s=.5
        session=simulate(p)['procedure_journal']['sessions'][0]
        change=next(event for event in session['events'] if event['kind']=='context_changed' and event['after']=='registered')
        self.assertIsNone(change['before']);self.assertFalse(snapshot(session,0)['contexts'])
        self.assertTrue(any(item['value']=='registered' for item in snapshot(session,change['id']-1)['contexts']))
        self.assertTrue(any(event['kind']=='context_changed' and event['reason']=='expired' for event in session['events']))
    def test_limits_are_explicit_and_target_replay_reproduces_metrics(self):
        p=network();p.journal_sessions=2;p.journal_events=100;p.links[0].mtu=256;p.flows[0].message_bytes=10000;p.flows[0].response_bytes=10000
        result=simulate(p);self.assertLessEqual(len(result['procedure_journal']['sessions']),2)
        for session in result['procedure_journal']['sessions']:
            self.assertLessEqual(len(session['events']),100);self.assertGreater(session['dropped_events'],0);self.assertEqual(session['event_count'],len(session['events'])+session['dropped_events'])
        with tempfile.TemporaryDirectory() as directory,patch.object(storage,'DATA',Path(directory)):
            record=research.run(network().model_dump());replay=research.replay_procedure(record,1)
            self.assertTrue(replay['replay']['same_summary']);self.assertTrue(replay['replay']['same_windows']);self.assertNotEqual(replay['id'],record['id'])
            self.assertEqual(replay['procedure_journal']['sessions'][0]['procedure_id'],1)
