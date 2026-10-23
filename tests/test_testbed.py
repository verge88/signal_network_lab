import copy
import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import CancelledError
from pathlib import Path
from unittest.mock import patch,Mock
from lab import testbed,testbed_agent as agent,storage


class TestbedTests(unittest.TestCase):
    def setUp(self):
        self.profile=testbed.Profile();self.p=self.profile.model_dump()
    def test_profile_rejects_invalid_network_and_command_inputs(self):
        for change in (dict(distro='Ubuntu; whoami'),dict(core_bin='/usr/bin/../tmp'),dict(ran_subnet='10.84.0.0/30'),dict(ran_subnet='127.0.0.0/30'),dict(ue_subnet='0.0.0.0/0'),dict(subscribers=[dict(imsi='001010000000001',key='0'*32,opc='0'*32)])):
            with self.subTest(change=change),self.assertRaises(ValueError):testbed.Profile.model_validate(dict(self.p,**change))
    def test_default_credentials_are_unique_and_public_profile_is_redacted(self):
        other=testbed.Profile();self.assertNotEqual(self.profile.subscribers[0].key,other.subscribers[0].key)
        redacted=json.dumps(testbed.public_profile(self.profile));self.assertNotIn(self.profile.subscribers[0].key,redacted)
        self.assertNotIn(self.profile.subscribers[0].opc,redacted)
    def test_wsl_utf16_inventory_and_localized_state_parsing(self):
        sample='  NAME              STATE           VERSION\r\n* Ubuntu            Stopped         2\r\n  docker-desktop    Running         2\r\n'
        self.assertEqual(testbed.decode_output(sample.encode('utf-16-le')),sample)
        with patch.object(testbed,'execute',return_value=(0,sample,'')):
            rows=testbed.distributions();self.assertEqual([r['name'] for r in rows],['Ubuntu','docker-desktop']);self.assertEqual(rows[0]['version'],2)
    def test_transport_uses_argument_array_and_stdin_for_credentials(self):
        with patch.object(testbed,'execute',return_value=(0,'{"ok":true,"result":{"ready":true}}','')) as execute:
            self.assertTrue(testbed.request(self.profile,'preflight')['ready'])
            argv,payload=execute.call_args.args
            self.assertEqual(argv[:3],['wsl.exe','--distribution','Ubuntu'])
            self.assertNotIn(self.p['subscribers'][0]['key'],' '.join(argv))
            self.assertEqual(json.loads(payload)['profile'],self.p)
    def test_linux_error_and_unknown_action_do_not_look_like_success(self):
        with self.assertRaises(ValueError):testbed.request(self.profile,'reset_database')
        with patch.object(testbed,'execute',return_value=(0,'{"ok":false,"error":"Not ready"}','')):
            with self.assertRaisesRegex(ValueError,'Not ready'):testbed.request(self.profile,'prepare')
    def test_execute_cancels_without_shell(self):
        with self.assertRaises(CancelledError):testbed.execute([sys.executable,'-c','import time;time.sleep(5)'],cancel=lambda:True)
    def test_execute_bounds_wait(self):
        with self.assertRaises(TimeoutError):testbed.execute([sys.executable,'-c','import time;time.sleep(5)'],timeout=.01)
    def test_core_configuration_rewrite_keeps_installed_options(self):
        template=dict(amf=dict(sbi=dict(server=[dict(address='127.0.0.5')],client=dict(scp=[dict(uri='http://127.0.0.200:7777')])),custom=dict(value=123)))
        actual=agent.rewrite_config(template,'amf',self.p,Path('/var/tmp/example'))
        self.assertEqual(actual['amf']['custom'],dict(value=123))
        self.assertEqual(actual['amf']['ngap']['server'][0]['address'],'10.203.0.1')
        self.assertEqual(actual['amf']['sbi']['client']['scp'][0]['uri'],'http://127.20.0.200:7777')
        self.assertEqual(template['amf']['sbi']['server'][0]['address'],'127.0.0.5')
    def test_unsupported_core_schema_is_rejected(self):
        with self.assertRaises(ValueError):agent.rewrite_config(dict(amf=dict(sbi=dict(addr='legacy'))),'amf',self.p,Path('/tmp/example'))
    def test_core_and_ueransim_share_network_slice_and_subscriber(self):
        configs=agent.ue_configs(self.p);ue=configs['ue-'+self.p['subscribers'][0]['imsi']+'.yaml'];gnb=configs['gnb.yaml']
        self.assertEqual(ue['key'],self.p['subscribers'][0]['key'])
        self.assertEqual(ue['sessions'][0]['slice'],gnb['slices'][0])
        self.assertEqual(gnb['ngapIp'],'10.203.0.2');self.assertEqual(gnb['amfConfigs'][0]['address'],'10.203.0.1')
    def test_database_namespace_is_owned_and_isolated(self):
        config=agent.rewrite_config(dict(udr=dict(sbi=dict(server=[])),db_uri='mongodb://localhost/open5gs'),'udr',self.p,Path('/tmp/example'))
        self.assertTrue(config['db_uri'].endswith('/signal_lab_'+self.p['id']))
        self.assertNotEqual(config['db_uri'],'mongodb://localhost/open5gs')
    def test_process_identity_guards_pid_reuse(self):
        entry=dict(pid=12,start='100',exe='/usr/bin/open5gs-amfd',config='/var/tmp/example/amf.yaml')
        for actual in (None,dict(start='101',exe=entry['exe'],argv=[entry['config']]),dict(start='100',exe='/other',argv=[entry['config']]),dict(start='100',exe=entry['exe'],argv=['foreign.yaml'])):
            with patch.object(agent,'identity',return_value=actual):self.assertFalse(agent.owned(entry))
        with patch.object(agent,'identity',return_value=dict(start='100',exe=entry['exe'],argv=['-c',entry['config']])):self.assertTrue(agent.owned(entry))
    def test_unknown_workspace_id_cannot_escape_lab_directory(self):
        with self.assertRaises(ValueError):agent.workspace(dict(id='../../etc'))
    def test_prepared_profile_must_match_fingerprint(self):
        with self.assertRaises(ValueError):agent.require_prepared(self.p,dict(prepared=True,fingerprint='other'))
        agent.require_prepared(self.p,dict(prepared=True,fingerprint=agent.fingerprint(self.p)))
    def test_redaction_covers_case_variants_in_logs(self):
        key=self.p['subscribers'][0]['key']
        self.assertNotIn(key.lower(),json.dumps(agent.redact(dict(log=key.lower()),self.p)))
    def test_ping_failure_does_not_create_zero_latency(self):
        r=agent.ping_samples('5 packets transmitted, 0 received, 100% packet loss')
        self.assertEqual(r['received'],0);self.assertIsNone(r['rtt_p95_ms']);self.assertEqual(r['rtt_samples_ms'],[])
        r=agent.ping_samples('time=1.1 ms\ntime=3.2 ms\n5 packets transmitted, 2 received')
        self.assertAlmostEqual(r['rtt_mean_ms'],2.15);self.assertEqual(r['rtt_p95_ms'],3.2)
    def test_log_procedures_preserve_rejections_and_midnight(self):
        lines=['[2026-10-22 23:59:59.990] [nas] [debug] Sending Initial Registration',
               '[2026-10-23 00:00:00.010] [nas] [info] Initial Registration is successful',
               '[2026-10-23 00:00:01.000] [nas] [debug] Sending PDU Session Establishment Request',
               '[2026-10-23 00:00:01.015] [nas] [error] PDU Session Establishment Reject received [NETWORK_FAILURE]']
        result=agent.procedure_events('\n'.join(lines));self.assertEqual([e['latency_ms'] for e in result['events']],[20,15]);self.assertEqual(result['events'][1]['status'],'rejected')
    def test_incomplete_or_ambiguous_log_events_are_not_timings(self):
        line='[2026-10-22 12:00:00.000] [nas] [debug] Sending Initial Registration'
        result=agent.procedure_events(line);self.assertEqual(result['events'],[]);self.assertEqual(result['incomplete'],1)
        result=agent.procedure_events(line+'\n'+line+'\n[2026-10-22 12:00:00.100] [nas] [info] Initial Registration is successful')
        self.assertEqual(result['events'],[])
    def test_measurement_saved_as_real_time_source_without_keys(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(storage,'DATA',Path(temp)),patch.object(testbed,'request',return_value=dict(data_tests=[],procedure_samples=[])):
            record=testbed.measure(self.profile)
            self.assertEqual(record['source'],'open5gs-ueransim');self.assertEqual(record['clock'],'wall')
            self.assertNotIn(self.p['subscribers'][0]['key'],json.dumps(record))
    def test_cancelled_measurement_does_not_create_history(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(storage,'DATA',Path(temp)),patch.object(testbed,'request',return_value={}):
            with self.assertRaises(CancelledError):testbed.measure(self.profile,cancel=lambda:True)
            self.assertEqual(storage.listing('testbed_run'),[])


if __name__=='__main__':unittest.main()
