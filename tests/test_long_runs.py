import json
import sqlite3
import tempfile
import unittest
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from lab import checkpoints as cp,long_runs as lr,storage
from lab.presets import preset
from lab.schema import Project,Incident


class LongRunTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.patch=patch.object(storage,'DATA',self.root);self.patch.start()
        self.p=preset('diameter');self.p.duration=10;self.p.window=1;self.p.flows[0].rate=2
    def tearDown(self):self.patch.stop();self.temp.cleanup()
    def paused(self,p=None,**kwargs):
        return lr.start(p or self.p,lr.Options(pause_at_s=3,checkpoint_sim_s=1,batch_events=32,**kwargs))
    def assert_physics(self,a,b):
        for key in ('summary','windows','nodes','links','traces','message_traces','control_plane','resources','clusters','procedure_journal','fiveg','user_plane','session_lifecycle','mobility'):
            if key in a:self.assertEqual(a[key],b[key],key)
    def test_checkpoint_and_continuation_match_uninterrupted_engine(self):
        expected=cp.engine_for(self.p).run();paused=self.paused()
        self.assertEqual(paused['status'],'paused');self.assertIsNone(paused['result_id']);self.assertGreaterEqual(paused['checkpoint_s'],3)
        result=lr.resume(paused['id']);self.assertEqual(result['status'],'completed')
        actual=storage.get('run',result['result_id']);self.assert_physics(expected,actual)
        self.assertGreater(sum(result['compaction'].values()),0)
    def test_fiveg_resources_mobility_and_sessions_survive_resume(self):
        p=preset('5g-mobile');p.duration=20;p.window=2
        for f in p.flows:f.rate=.2
        for f in p.flows:
            for move in f.mobility:move.time=5
        p=Project.model_validate(p.model_dump());expected=cp.engine_for(p).run()
        paused=self.paused(p);result=lr.resume(paused['id']);self.assert_physics(expected,storage.get('run',result['result_id']))
    def test_cluster_state_and_replication_continue_exactly(self):
        p=preset('5g-ha');p.duration=10;p.window=1;p.incidents=[Incident(kind='node_restart',target='amf',start=4,duration=2)]
        p.flows[0].rate=.2
        expected=cp.engine_for(p).run();paused=self.paused(p);result=lr.resume(paused['id'])
        self.assert_physics(expected,storage.get('run',result['result_id']))
    def test_pause_after_every_event_does_not_change_rng_or_sampling(self):
        class Pause(Exception):pass
        e=cp.engine_for(self.p)
        def hook(engine):
            if engine.steps>30:raise Pause()
        with self.assertRaises(Pause):e.run(hook)
        blob,digest=cp.dumps(e);restored=cp.loads(blob,digest)
        self.assertIs(restored.storage.e,restored);self.assertIs(restored.cache,restored.storage.stores['transactions'])
        self.assert_physics(e.run(),restored.run())
    def test_multiple_rng_streams_are_distinct_and_preserved(self):
        e=cp.engine_for(self.p);e.prepare();e.prepared=True
        e.rng.random(7);e.sample_rng.random(3);e.journal.rng.random(4)
        blob,digest=cp.dumps(e);other=cp.loads(blob,digest)
        for name in ('rng','sample_rng','metric_rng'):self.assertEqual(getattr(e,name).random(),getattr(other,name).random())
        self.assertEqual(e.journal.rng.random(),other.journal.rng.random())
    def test_checksum_and_version_mismatch_are_rejected(self):
        e=cp.engine_for(self.p);blob,digest=cp.dumps(e)
        with self.assertRaisesRegex(ValueError,'Повреждена'):cp.loads(blob+b'x',digest)
        with patch.object(cp,'ENGINE_VERSION','incompatible'):
            with self.assertRaisesRegex(ValueError,'Версии'):cp.loads(blob,digest)
    def test_graph_refuses_executable_objects(self):
        encoder=cp.GraphEncoder({})
        with self.assertRaises(ValueError):encoder.encode(lambda:None)
        decoder=cp.GraphDecoder({},[['model','__import__',{}]])
        with self.assertRaises(KeyError):decoder.decode({'ref':0})
    def test_lock_prevents_two_writers_and_releases(self):
        ident='a'*32;a=lr.Journal(ident,True)
        try:
            with self.assertRaisesRegex(ValueError,'другим процессом'):lr.Journal(ident)
        finally:a.close()
        b=lr.Journal(ident);b.close()
    def test_stream_written_before_completion_and_rewound_to_checkpoint(self):
        paused=self.paused();rows=lr.stream_rows(paused['id'],10000);self.assertGreater(len(rows),0)
        journal=lr.Journal(paused['id'])
        try:
            engine=journal.restore();cursor=journal.seq
            journal.append('test_suffix',engine,dict(test=True),1);journal.flush(engine)
        finally:journal.close()
        result=lr.resume(paused['id']);rows=lr.stream_rows(paused['id'],10000)
        self.assertFalse(any(r['kind']=='test_suffix' for r in rows));self.assertEqual([r['seq'] for r in rows],list(range(1,len(rows)+1)))
        self.assertEqual(result['records'],len(rows))
    def test_stream_exports_incrementally_and_final_windows_saved(self):
        result=lr.start(self.p);rows=lr.stream_rows(result['id'],10000)
        for fmt in ('csv','jsonl'):
            path=self.root/('events.'+fmt);self.assertEqual(lr.export_stream(result['id'],path,fmt),len(rows));self.assertGreater(path.stat().st_size,0)
        with closing(sqlite3.connect(lr.path_for(result['id']))) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM windows').fetchone()[0],10)
        with self.assertRaises(ValueError):lr.export_stream(result['id'],lr.path_for(result['id']))
    def test_finished_run_cannot_be_resumed_or_overwrite_another_job(self):
        result=lr.start(self.p)
        with self.assertRaisesRegex(ValueError,'завершён'):lr.resume(result['id'])
        for ident in ('../secrets','x'*32,''):
            with self.assertRaises(ValueError):lr.get(ident)
    def test_long_duration_and_profile_resolution_have_budgets(self):
        data=self.p.model_dump();data.update(duration=86400,window=30);data['flows'][0].update(rate=.0001,profile_period_s=86400,profile_step_s=10,profile_amplitude=.5)
        p=Project.model_validate(data);p.control_plane=False;p.routing_knowledge='shared';p.route_refresh_ms=60000
        result=lr.start(p,lr.Options(checkpoint_sim_s=3600,batch_events=20000));self.assertEqual(result['status'],'completed');self.assertEqual(len(storage.get('run',result['result_id'])['windows']),2880)
        data['window']=1
        with self.assertRaisesRegex(ValueError,'10 000'):Project.model_validate(data)
        data.update(window=30,engine='messages')
        with self.assertRaisesRegex(ValueError,'движок процедур'):Project.model_validate(data)
    def test_preparation_cancel_is_explicit_and_does_not_fake_checkpoint(self):
        from concurrent.futures import CancelledError
        with self.assertRaises(CancelledError):lr.start(self.p,cancel=lambda:True)
        jobs=lr.listing();self.assertEqual(len(jobs),1);self.assertEqual(jobs[0]['status'],'cancelled');self.assertIsNone(jobs[0]['checkpoint_s'])
    def test_cancellation_after_preparation_saves_current_state(self):
        stop=[False]
        def progress(value):
            if value>=30:stop[0]=True
        paused=lr.start(self.p,lr.Options(checkpoint_sim_s=1,batch_events=32),progress=progress,cancel=lambda:stop[0])
        self.assertEqual(paused['status'],'paused');self.assertGreaterEqual(paused['checkpoint_s'],3)
        result=lr.resume(paused['id']);self.assert_physics(cp.engine_for(self.p).run(),storage.get('run',result['result_id']))
    def test_continuation_in_another_process_preserves_output(self):
        class Stop(Exception):pass
        engine=cp.engine_for(self.p)
        def hook(e):
            if e.t>=3000:raise Stop()
        with self.assertRaises(Stop):engine.run(hook)
        blob,digest=cp.dumps(engine);point=self.root/'state.bin';point.write_bytes(blob);output=self.root/'result.json'
        code="from pathlib import Path; import sys,json; from lab.checkpoints import loads; e=loads(Path(sys.argv[1]).read_bytes(),sys.argv[2]); Path(sys.argv[3]).write_text(json.dumps(e.run()),encoding='utf-8')"
        process=subprocess.run([sys.executable,'-c',code,str(point),digest,str(output)],cwd=str(Path(__file__).resolve().parents[1]),capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,0,process.stderr);self.assert_physics(json.loads(json.dumps(engine.run())),json.loads(output.read_text(encoding='utf-8')))
    def test_cancelled_stream_export_preserves_existing_destination(self):
        from concurrent.futures import CancelledError
        result=lr.start(self.p);target=self.root/'existing.csv';target.write_text('original',encoding='utf-8')
        with self.assertRaises(CancelledError):lr.export_stream(result['id'],target,'csv',cancel=lambda:True)
        self.assertEqual(target.read_text(encoding='utf-8'),'original');self.assertEqual(list(self.root.glob('*.partial-*')),[])
    def test_late_reverse_ack_and_retries_remain_exact_under_compaction(self):
        p=self.p.model_copy(deep=True);p.flows[0].max_retries=2
        for link in p.links:link.reverse_latency_ms=250;link.rto_ms=60;link.rto_min_ms=30;link.loss=.02
        expected=cp.engine_for(p).run();paused=self.paused(p);result=lr.resume(paused['id'])
        self.assert_physics(expected,storage.get('run',result['result_id']))
    def test_late_incidents_and_mobility_can_be_scheduled(self):
        data=self.p.model_dump();data.update(duration=7200,window=30);data['flows'][0]['rate']=.001
        data['incidents']=[dict(kind='node_restart',target='hss',start=3600,duration=1800)]
        self.assertEqual(Project.model_validate(data).incidents[0].start,3600)
    def test_many_profile_ticks_are_rejected_independently_of_rate(self):
        data=preset().model_dump();data.update(duration=86400,window=30)
        for f in data['flows']:f.update(rate=0,profile_amplitude=.5,profile_step_s=.1)
        with self.assertRaisesRegex(ValueError,'интервалов профиля'):Project.model_validate(data)
    def test_workspace_migration_backs_up_paused_job_and_allows_continuation(self):
        storage.save('project','Source',dict(project=self.p.model_dump()));paused=self.paused();source=storage.DATA
        with tempfile.TemporaryDirectory() as target:
            with patch.object(storage,'DATA',Path(target)):
                self.assertTrue(storage.migrate_workspace(source));self.assertEqual(lr.get(paused['id'])['status'],'paused')
                completed=lr.resume(paused['id']);self.assertEqual(completed['status'],'completed')
        self.assertEqual(lr.get(paused['id'])['status'],'paused')
    def test_long_independent_runs_also_retire_completed_transport_objects(self):
        p=self.p.model_copy(deep=True);p.duration=601;p.window=30;p.flows[0].rate=.5
        engine=cp.engine_for(p);actual=engine.run()
        self.assertLess(len(engine.messages),engine.message_seq//2)
        expected=cp.engine_for(p).run(lambda engine:None)
        self.assert_physics(expected,actual)


if __name__=='__main__':unittest.main()
