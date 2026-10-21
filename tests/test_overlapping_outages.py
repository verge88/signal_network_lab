import unittest
from lab.schema import Incident
from lab.procedures import ProcedureSimulation
from test_realism import pair


class OverlappingOutageTests(unittest.TestCase):
    def run_faults(self,faults):
        project=pair();project.duration=20;project.control_plane=False;project.route_refresh_ms=60000
        for item in [*project.nodes,*project.links]:item.detection_ms=0;item.switch_ms=0;item.recovery_ms=0
        project.incidents=[Incident(**fault) for fault in faults]
        return ProcedureSimulation(project).run()['control_plane']['view_events']
    def assert_no_early_up(self,events,resource,target,direction,start,end):
        events=[row for row in events if row['observer']=='a' and row['resource']==resource and row['target']==target and row['direction']==direction]
        self.assertTrue(any(not row['up'] for row in events))
        self.assertFalse([row for row in events if row['up'] and start<=row['time']<end],events)
        self.assertTrue(any(row['up'] and abs(row['time']-end)<1e-8 for row in events),events)
    def test_node_remains_down_until_both_overlapping_faults_finish(self):
        for faults in ([dict(kind='node_down',target='b',start=2,duration=3),dict(kind='node_down',target='b',start=3,duration=4)],
                       [dict(kind='node_down',target='b',start=3,duration=4),dict(kind='node_restart',target='b',start=2,duration=3)]):
            with self.subTest(faults=faults):self.assert_no_early_up(self.run_faults(faults),'node','b','both',2,7)
    def test_nested_and_touching_faults_do_not_publish_a_recovery_between_them(self):
        faults=[dict(kind='node_down',target='b',start=2,duration=5),dict(kind='node_down',target='b',start=3,duration=1),dict(kind='node_down',target='b',start=7,duration=1)]
        self.assert_no_early_up(self.run_faults(faults),'node','b','both',2,8)
    def test_late_recovery_of_older_incident_has_a_newer_update_version(self):
        faults=[dict(kind='node_down',target='b',start=2,duration=2,recovery_ms=10000),dict(kind='node_down',target='b',start=6,duration=1)]
        self.assert_no_early_up(self.run_faults(faults),'node','b','both',2,14)
    def test_short_undetected_fault_cannot_cancel_an_existing_down_state(self):
        faults=[dict(kind='node_down',target='b',start=2,duration=5),dict(kind='node_down',target='b',start=3,duration=.1,detection_ms=500)]
        self.assert_no_early_up(self.run_faults(faults),'node','b','both',2,7)
    def test_directional_link_recovery_is_independent(self):
        faults=[dict(kind='link_down',target='ab',start=2,duration=3),dict(kind='link_down',target='ab',direction='forward',start=3,duration=4)]
        events=self.run_faults(faults)
        self.assert_no_early_up(events,'link','ab','forward',2,7)
        self.assert_no_early_up(events,'link','ab','reverse',2,5)
    def test_expired_detection_after_long_switch_cannot_leave_a_permanent_down_state(self):
        project=pair();project.control_plane=False;project.nodes[1].switch_ms=5000
        project.incidents=[Incident(kind='node_down',target='b',start=2,duration=1,detection_ms=0,recovery_ms=0)]
        result=ProcedureSimulation(project).run()
        view=next(row for row in result['control_plane']['views'] if row['observer']=='a' and row['resource']=='node' and row['target']=='b')
        self.assertTrue(view['up'])
    def test_lifecycle_does_not_label_one_fault_end_as_resource_recovery(self):
        project=pair();project.control_plane=False
        project.incidents=[Incident(kind='node_down',target='b',start=2,duration=3,detection_ms=0,recovery_ms=0),Incident(kind='node_down',target='b',start=3,duration=4,detection_ms=0,recovery_ms=0)]
        events=ProcedureSimulation(project).run()['lifecycle']
        self.assertFalse([row for row in events if row['object']=='b' and row['time']==5 and row['state'] in ('PHYSICAL_UP','AVAILABLE')])
