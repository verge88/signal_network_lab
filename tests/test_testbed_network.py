import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from lab import testbed,testbed_agent as agent


class TestbedNetworkTests(unittest.TestCase):
    def setUp(self):self.p=testbed.Profile(root=True).model_dump()

    def test_failed_environment_check_cannot_create_an_empty_workspace(self):
        with patch.object(agent,'preflight',return_value=dict(ready=False,checks=[dict(check='root',ok=False)])),patch.object(agent,'workspace') as workspace:
            with self.assertRaisesRegex(ValueError,'root'):agent.dispatch(dict(profile=self.p,action='prepare'))
            workspace.assert_not_called()
    def test_running_prepare_before_workspace_creation_prevents_false_absence(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(agent,'workspace',return_value=Path(temp)/'not-created'),patch.object(agent,'network_resources',return_value=dict(absent=True,complete=False)),patch.object(agent,'other_operation_active',return_value=True):
            result=agent.dispatch(dict(profile=self.p,action='status'))
            self.assertEqual(result['resource_state'],'unknown')
            with self.assertRaises(ValueError):agent.dispatch(dict(profile=self.p,action='cleanup'))
    def test_operation_finishing_during_status_cannot_make_old_network_observation_authoritative(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(agent,'network_resources',return_value=dict(absent=True,complete=False)),patch.object(agent,'other_operation_active',side_effect=[True,False]) as probes:
            result=agent.snapshot(self.p,Path(temp),dict(processes={},prepared=False,resources=False))
        self.assertEqual(probes.call_count,2);self.assertEqual(result['resource_state'],'unknown')
    def test_agent_process_marker_identifies_only_other_mutating_operations(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);process=root/'99999999';process.mkdir();cmdline=process/'cmdline'
            for action,ident,expected in [('prepare',self.p['id'],True),('status',self.p['id'],False),('preflight',self.p['id'],False),('prepare','a'*12,False)]:
                cmdline.write_bytes(('python3\0-c\0code\0--signal-lab-profile\0'+ident+'\0'+action+'\0').encode())
                self.assertEqual(agent.other_operation_active(self.p,root),expected)

    def test_status_and_cleanup_of_a_never_prepared_profile_confirm_absence(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(agent,'workspace',return_value=Path(temp)/'not-created'),patch.object(agent,'network_resources',return_value=dict(absent=True,complete=False)):
            for action in ('status','cleanup'):
                result=agent.dispatch(dict(profile=self.p,action=action))
                self.assertEqual(result['resource_state'],'absent');self.assertFalse(result['processes_present'])
            self.assertTrue(result['cleaned'])
    def test_missing_manifest_with_existing_network_is_unknown_and_not_cleaned(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(agent,'workspace',return_value=Path(temp)/'not-created'),patch.object(agent,'network_resources',return_value=dict(absent=False,complete=False)),patch.object(agent,'run') as commands:
            result=agent.dispatch(dict(profile=self.p,action='status'));self.assertEqual(result['resource_state'],'unknown')
            with self.assertRaisesRegex(ValueError,'Не удалось подтвердить'):agent.dispatch(dict(profile=self.p,action='cleanup'))
            commands.assert_not_called()
    def test_live_process_outside_current_subscriber_list_prevents_absence(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(agent,'network_resources',return_value=dict(absent=True,complete=False)),patch.object(agent,'owned',return_value=True):
            result=agent.snapshot(self.p,Path(temp),dict(processes={'old-ue':dict(pid=123)},prepared=False,resources=True))
        self.assertTrue(result['processes_present']);self.assertEqual(result['resource_state'],'present')

    def test_kernel_inventory_detects_missing_partial_and_misconfigured_resources(self):
        names=agent.names(self.p);host,ran,gateway=agent.addresses(self.p)
        interfaces={names['host_if']:dict(linkinfo=dict(info_kind='veth'),flags=['UP'],addr_info=[dict(local=host,prefixlen=30)]),
                    names['ran_if']:dict(linkinfo=dict(info_kind='veth'),flags=['UP'],addr_info=[dict(local=ran,prefixlen=30)]),
                    names['upf_if']:dict(linkinfo=dict(info_kind='tun'),flags=['UP'],addr_info=[dict(local=gateway,prefixlen=24)])}
        namespace=True
        def run(argv,**kwargs):
            if argv==['ip','netns','list']:return Mock(stdout=names['namespace'] if namespace else '')
            value=interfaces.get(argv[-1]);return Mock(returncode=0 if value else 1,stdout=json.dumps([value]) if value else '',stderr='' if value else 'Device does not exist')
        with patch.object(agent,'run',side_effect=run):
            self.assertTrue(agent.network_resources(self.p)['complete'])
            interfaces[names['upf_if']]['addr_info'][0]['local']='10.84.0.99'
            self.assertFalse(agent.network_resources(self.p)['complete'])
            interfaces.clear();self.assertFalse(agent.network_resources(self.p)['absent'])
            namespace=False;self.assertTrue(agent.network_resources(self.p)['absent'])

    def fixture(self,root):
        p=dict(self.p,config_dir=str(root))
        for name in agent.CORE:
            nf=dict(pfcp=dict(server=[])) if name=='upf' else dict(sbi=dict(server=[]))
            (root/(name+'.yaml')).write_text(json.dumps({name:nf}),encoding='utf-8')
        return p

    def fake_run(self,argv,**kwargs):
        if 'route' in argv:return Mock(stdout='[]',returncode=0)
        return Mock(stdout='',returncode=1 if argv[:3]==['ip','link','show'] else 0)

    def test_prepare_recreates_a_network_lost_since_the_manifest_was_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=self.fixture(root);state=dict(processes={},resources=True,prepared=True,fingerprint=agent.fingerprint(p),pfcp_pair=['stale'])
            inventories=[dict(absent=True,complete=False),dict(absent=False,complete=True)]
            with patch.dict(sys.modules,{'yaml':SimpleNamespace(safe_load=json.loads)}),patch.object(agent.os,'geteuid',return_value=0,create=True),patch.object(agent,'network_resources',side_effect=inventories),patch.object(agent,'run',side_effect=self.fake_run) as commands,patch.object(agent.socket,'socket'):
                result=agent.prepare(p,root,state)
            self.assertTrue(result['prepared']);self.assertTrue(state['prepared']);self.assertNotIn('pfcp_pair',state)
            self.assertIn(['ip','netns','add',agent.names(p)['namespace']],[c.args[0] for c in commands.call_args_list])
            self.assertTrue(json.loads((root/'state.json').read_text())['prepared'])

    def test_partial_network_cannot_be_reported_as_prepared(self):
        with tempfile.TemporaryDirectory() as temp:
            state=dict(processes={},resources=True,prepared=True)
            with patch.dict(sys.modules,{'yaml':SimpleNamespace()}),patch.object(agent.os,'geteuid',return_value=0,create=True),patch.object(agent,'network_resources',return_value=dict(absent=False,complete=False)),patch.object(agent,'run') as commands:
                with self.assertRaisesRegex(ValueError,'неполная'):agent.prepare(self.p,Path(temp),state)
            self.assertFalse(state['prepared']);commands.assert_not_called()

    def test_failed_post_creation_check_does_not_commit_a_prepared_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=self.fixture(root);state=dict(processes={},resources=False,prepared=False)
            inventories=[dict(absent=True,complete=False),dict(absent=False,complete=False)]
            with patch.dict(sys.modules,{'yaml':SimpleNamespace(safe_load=json.loads)}),patch.object(agent.os,'geteuid',return_value=0,create=True),patch.object(agent,'network_resources',side_effect=inventories),patch.object(agent,'run',side_effect=self.fake_run),patch.object(agent.socket,'socket'):
                with self.assertRaisesRegex(ValueError,'не подтверждена'):agent.prepare(p,root,state)
            self.assertFalse(state['prepared']);self.assertTrue(state['resources'])
            self.assertFalse(json.loads((root/'state.json').read_text())['prepared'])


if __name__=='__main__':unittest.main()
