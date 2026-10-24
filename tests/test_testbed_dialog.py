import os
import tempfile
import unittest
from concurrent.futures import CancelledError
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication,QDialog
from shiboken6 import delete
from lab import storage,testbed
from native.testbed_dialog import TestbedDialog


class Owner(QDialog):
    def __init__(self,path):
        super().__init__();self.settings=QSettings(str(path/'settings.ini'),QSettings.Format.IniFormat)
        self.compute_buttons=[];self.active_job=None;self.jobs=[];self.errors=[]
    def call(self,fn,*args):return fn(*args)
    def cancel_job(self):pass
    def show_help(self,*args):pass
    def error(self,message):self.errors.append(message)
    def start_job(self,title,fn,done,result_on_cancel=False):self.jobs.append((fn,done,result_on_cancel))


class TestbedDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.data=patch.object(storage,'DATA',self.root);self.data.start()
        self.owner=Owner(self.root);self.dialog=TestbedDialog(self.owner)
    def tearDown(self):
        delete(self.owner);self.app.processEvents();self.data.stop();self.temp.cleanup()
    def absent(self,p):return dict(profile_id=p.id,prepared=False,resource_state='absent',processes_present=False,network=dict(absent=True,complete=False))
    def present(self,p,**kwargs):return dict(profile_id=p.id,prepared=True,resource_state='present',**kwargs)
    def finish(self,response=None,side_effect=None):
        fn,callback,_=self.owner.jobs[-1]
        with patch.object(testbed,'request',return_value=response,side_effect=side_effect) as rpc:
            outcome=fn(lambda v:None,lambda:False);callback(outcome)
        return rpc
    def prepare(self):
        p=self.dialog.values();self.dialog.action('prepare');self.finish(self.present(p));return p

    def test_prepare_saves_settings_and_pending_intent_without_claiming_control(self):
        p=self.dialog.values();self.dialog.action('prepare');saved=storage.get('testbed_profile',p.id)
        self.assertEqual(saved['profile'],p.model_dump());self.assertEqual(saved['pending_profile'],p.model_dump())
        self.assertIsNone(saved['control_profile']);self.assertIsNone(self.dialog.control_profile)
        self.finish(self.present(p));self.assertEqual(self.dialog.control_profile,p);self.assertIsNone(self.dialog.pending_profile)
        self.assertIn(p.id,self.dialog.state_note.text())
    def test_storage_failure_prevents_a_remote_start(self):
        with patch.object(storage,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.dialog.action('start_core')
        self.assertEqual(self.owner.jobs,[]);self.assertIsNone(self.dialog.pending_profile)
    def test_first_measure_failure_with_confirmed_absence_allows_correcting_the_path(self):
        p=self.dialog.values();self.dialog.measure()
        def rpc(profile,action,*args,**kwargs):
            if action=='measure':raise testbed.AgentError('Сначала подготовьте профиль')
            self.assertEqual(action,'status');return self.absent(p)
        calls=self.finish(side_effect=rpc)
        self.assertEqual([c.args[1] for c in calls.call_args_list],['measure','status'])
        self.assertIsNone(self.dialog.control_profile);self.assertIsNone(self.dialog.pending_profile)
        saved=storage.get('testbed_profile',p.id);self.assertIsNone(saved['control_profile']);self.assertIsNone(saved['pending_profile'])
        self.dialog.paths['ran_bin'].setText('/opt/fixed/build');self.dialog.action('prepare')
        self.finish(self.present(p));self.assertEqual(self.dialog.control_profile.ran_bin,'/opt/fixed/build')
    def test_timeout_keeps_pending_intent_and_requires_status_before_any_retry(self):
        p=self.dialog.values();self.dialog.action('prepare');calls=self.finish(side_effect=TimeoutError('timeout'))
        self.assertEqual(calls.call_count,1);self.assertIsNone(self.dialog.control_profile);self.assertEqual(self.dialog.pending_profile,p)
        self.dialog.paths['ran_bin'].setText('/opt/fixed/build')
        for action in ('prepare','start_core','cleanup'):
            with self.subTest(action=action),self.assertRaisesRegex(ValueError,'Обновить'):self.dialog.action(action)
        self.dialog.action('status');calls=self.finish(self.absent(p))
        self.assertEqual(calls.call_args.args[0],p);self.assertIsNone(self.dialog.pending_profile)
        self.dialog.action('prepare');self.finish(self.present(p));self.assertEqual(self.dialog.control_profile.ran_bin,'/opt/fixed/build')
    def test_unknown_intent_survives_reload_and_status_uses_the_original_connection(self):
        p=self.dialog.values();self.dialog.action('prepare');self.finish(side_effect=TimeoutError('timeout'))
        self.dialog.distro.setText('DifferentLinux');self.dialog.save_profile();self.dialog.load_profile()
        self.assertEqual(self.dialog.pending_profile.distro,p.distro);self.assertIsNone(self.dialog.control_profile)
        self.dialog.action('status');rpc=self.finish(self.present(p))
        self.assertEqual(rpc.call_args.args[0].distro,p.distro);self.assertEqual(self.dialog.control_profile,p)
        self.assertIsNone(self.dialog.pending_profile)
    def test_remote_error_with_an_unavailable_status_does_not_unlock(self):
        p=self.dialog.values();self.dialog.action('prepare')
        self.finish(side_effect=[testbed.AgentError('failed'),TimeoutError('status timeout')])
        self.assertEqual(self.dialog.pending_profile,p);self.assertIsNone(self.dialog.control_profile)
    def test_not_prepared_alone_is_not_proof_of_absence(self):
        p=self.dialog.values();self.dialog.action('prepare')
        self.finish(side_effect=[testbed.AgentError('failed'),dict(profile_id=p.id,prepared=False)])
        self.assertEqual(self.dialog.pending_profile,p);self.assertIn('не подтверждено',self.dialog.state_note.text())
    def test_busy_status_keeps_intent_and_does_not_retry_the_original_operation(self):
        p=self.dialog.values();self.dialog.action('prepare')
        calls=self.finish(side_effect=[testbed.AgentError('busy'),testbed.AgentError('lock busy')])
        self.assertEqual([c.args[1] for c in calls.call_args_list],['prepare','status'])
        self.assertEqual(self.dialog.pending_profile,p)
    def test_partial_resources_after_failure_are_confirmed_as_controlled(self):
        p=self.dialog.values();self.dialog.action('prepare')
        evidence=dict(profile_id=p.id,prepared=False,resource_state='present',processes_present=False,network=dict(absent=False,complete=False))
        self.finish(side_effect=[testbed.AgentError('partial preparation'),evidence])
        self.assertEqual(self.dialog.control_profile,p);self.assertIsNone(self.dialog.pending_profile)
        self.dialog.paths['ran_bin'].setText('/opt/fixed/build')
        with self.assertRaisesRegex(ValueError,'изменены'):self.dialog.action('prepare')
    def test_cancellation_preserves_unknown_state_and_delivers_the_outcome(self):
        p=self.dialog.values();self.dialog.action('prepare');self.assertTrue(self.owner.jobs[-1][2])
        self.finish(side_effect=CancelledError());self.assertEqual(self.dialog.pending_profile,p)
        self.assertIsNone(self.dialog.control_profile)
    def test_new_profile_ignores_previous_callback_but_retains_its_control_record(self):
        old=self.dialog.values();self.dialog.action('prepare');_,callback,_=self.owner.jobs[-1]
        self.dialog.new_profile();new=self.dialog.profile
        callback(dict(ok=True,result=self.present(old,logs={'old.log':'old profile'})))
        self.assertIsNone(self.dialog.last_state);self.assertEqual(self.dialog.core.rowCount(),0)
        self.assertEqual(self.dialog.history.currentIndex(),-1);self.assertIn(new.id,self.dialog.state_note.text())
        self.dialog.history.setCurrentIndex(self.dialog.history.findData(old.id));self.dialog.load_profile()
        self.assertEqual(self.dialog.control_profile,old);self.assertIsNone(self.dialog.pending_profile)
    def test_stop_uses_control_connection_after_form_changes_and_reload(self):
        old=self.prepare();self.dialog.distro.setText('DifferentLinux');self.dialog.save_profile();self.dialog.load_profile()
        self.dialog.fields['mcc'].setText('invalid');self.dialog.action('stop')
        rpc=self.finish(self.present(old,stopped=True))
        self.assertEqual(rpc.call_args.args[0].distro,old.distro)
    def test_confirmed_absence_releases_a_previously_pinned_legacy_profile(self):
        old=self.dialog.values();self.dialog.set_profile(old,control_profile=old);self.dialog.save_profile()
        self.dialog.action('status');self.finish(self.absent(old))
        self.assertIsNone(self.dialog.control_profile);self.assertIsNone(storage.get('testbed_profile',old.id)['control_profile'])
        self.dialog.paths['ran_bin'].setText('/opt/fixed/build');self.dialog.action('prepare')
    def test_absence_with_a_live_process_cannot_release_control(self):
        old=self.prepare();self.dialog.action('status');invalid=dict(self.absent(old),processes_present=True)
        self.finish(invalid);self.assertEqual(self.dialog.control_profile,old)
        self.assertIn('не подтверждено',self.dialog.state_note.text())
    def test_cleanup_updates_only_the_old_profile_and_names_the_actual_target(self):
        old=self.prepare();self.dialog.distro.setText('DifferentLinux');self.dialog.save_profile();self.dialog.action('cleanup')
        self.finish(dict(self.absent(old),cleaned=True));self.assertIsNone(self.dialog.control_profile)
        self.assertIn(old.distro,self.dialog.state_note.text());self.assertNotIn('DifferentLinux',self.dialog.state_note.text())
    def test_cleanup_callback_after_switch_clears_only_its_own_intent(self):
        old=self.prepare();self.dialog.action('cleanup');_,callback,_=self.owner.jobs[-1]
        self.dialog.new_profile();new=self.dialog.profile
        callback(dict(ok=True,result=dict(self.absent(old),cleaned=True)))
        saved=storage.get('testbed_profile',old.id);self.assertIsNone(saved['control_profile']);self.assertIsNone(saved['pending_profile'])
        self.assertEqual(self.dialog.profile,new);self.assertIsNone(self.dialog.last_state)
    def test_measurement_saves_history_but_does_not_replace_another_profiles_view(self):
        old=self.dialog.values();self.dialog.measure();fn,callback,_=self.owner.jobs[-1];self.dialog.new_profile()
        with patch.object(testbed,'request',return_value=dict(source='open5gs-ueransim',clock='wall',profile_id=old.id,resource_state='present',data_tests=[])):
            outcome=fn(lambda v:None,lambda:False)
        callback(outcome);self.assertIsNone(self.dialog.report)
        self.assertEqual(storage.get('testbed_run',outcome['result']['id'])['profile_id'],old.id)


if __name__=='__main__':unittest.main()
