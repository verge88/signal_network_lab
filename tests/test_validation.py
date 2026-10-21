import copy
import csv
import io
import json
import unittest
from concurrent.futures import CancelledError
from unittest.mock import patch
from lab.validation import (Options,TEMPLATE,METRICS,validate,parse,distribution,error,
    synthetic_example,report_html,digest)
from test_procedures import network


def csv_text(rows):
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=sorted({key for row in rows for key in row}));writer.writeheader();writer.writerows(rows);return stream.getvalue()


def measured_rows():
    return [dict(run_id=f'm-{index}',offered_rate=5,delivery_ratio=1,latency_mean=30,latency_p95=30,latency_p99=30,throughput=5,
        scenario='baseline',source_kind='synthetic',evaluation_role='validation',duration_s=10,window_s=1,
        latency_samples_ms='|'.join(['30']*60),time_series_json=json.dumps([dict(time=i,width_s=1,delivery_ratio=1,latency_mean=30,latency_p95=30,throughput=5) for i in range(10)])) for index in range(3)]


def simulation_fixture(config,cancel=None):
    return dict(summary=dict(delivery_ratio=1,latency_mean=30,latency_p95=30,latency_p99=30,throughput=5,transport_retries=0),
        links={'ab':dict(packets=100,acks=50)},traces=[dict(status='delivered',latency_ms=30) for _ in range(60)],
        windows=[dict(time=i,delivered=5,delivery_ratio=1,latency_mean=30,latency_p95=30,completion_rate=5) for i in range(config.duration)],incidents=[])


def fixture_report(rows=None,options=None,**kwargs):
    with patch('lab.validation.simulate',side_effect=simulation_fixture):return validate(network(),csv_text(measured_rows() if rows is None else rows),options=options or dict(seeds=2),**kwargs)


class ValidationTests(unittest.TestCase):
    def test_matches_pass_per_condition_without_claiming_hardware_proof(self):
        result=fixture_report();self.assertEqual(result['summary'],dict(conditions=1,passed=1,failed=0,insufficient=0))
        self.assertEqual(result['simulations'],6);self.assertEqual(result['conditions'][0]['support'],'synthetic_only')
        self.assertFalse(result['hardware_accuracy_proven']);self.assertFalse(result['raw_artifacts_verified'])
        self.assertEqual(result['policy_sha256'],digest(result['policy']))
        self.assertTrue(all(e['time_series']['complete'] for e in result['experiments']))
    def test_single_bad_experiment_is_not_hidden_by_condition_averaging(self):
        rows=measured_rows();rows[1]['latency_p95']=100
        result=fixture_report(rows);self.assertEqual(result['conditions'][0]['status'],'failed')
        error_=next(x for x in result['experiments'][1]['errors'] if x['metric']=='latency_p95')
        self.assertEqual(error_['absolute_error'],70);self.assertEqual(error_['allowed'],10)
    def test_missing_required_measurements_and_unseen_conditions_are_explicit(self):
        rows=measured_rows()
        for row in rows:row.pop('latency_samples_ms');row.pop('time_series_json');row.pop('latency_p95')
        result=fixture_report(rows,dict(seeds=1,expected_conditions=[dict(scenario='new-outage',offered_rate=25)]))
        self.assertEqual(result['summary']['insufficient'],2)
        self.assertIn('Измерения этого условия отсутствуют',result['conditions'][1]['missing'])
        self.assertIn('Выборка задержек',result['conditions'][0]['missing'])
    def test_unknown_evaluation_role_and_calibration_reuse_cannot_be_independent(self):
        rows=measured_rows();rows[0]['evaluation_role']='calibration';rows[1].pop('evaluation_role')
        result=fixture_report(rows,calibration_groups={'m-2'});self.assertEqual(result['conditions'][0]['independent_runs'],0)
        self.assertEqual(result['conditions'][0]['status'],'insufficient')
    def test_same_lab_file_is_one_independent_group_and_hash_reuse_is_detected(self):
        rows=measured_rows()
        for index,row in enumerate(rows):row.update(source_kind='lab',measurement_id=str(index),device='A',firmware='1',measured_at='2026-10-21T10:00:00Z',raw_sha256='a'*64)
        result=fixture_report(rows);self.assertEqual(result['conditions'][0]['independent_runs'],1)
        result=fixture_report(rows,calibration_hashes={'a'*64});self.assertEqual(result['conditions'][0]['independent_runs'],0)
        for index,row in enumerate(rows):row['raw_sha256']=str(index+1)*64
        result=fixture_report(rows);self.assertEqual(result['conditions'][0]['support'],'declared_lab_data');self.assertFalse(result['hardware_accuracy_proven'])
    def test_time_series_alignment_requires_exact_windows_and_complete_coverage(self):
        rows=measured_rows();points=json.loads(rows[0]['time_series_json']);points[0]['width_s']=2;rows[0]['time_series_json']=json.dumps(points)
        result=fixture_report(rows);self.assertFalse(result['experiments'][0]['time_series']['complete']);self.assertEqual(result['experiments'][0]['time_series']['missing_times'],[0])
        rows=measured_rows();points=json.loads(rows[0]['time_series_json']);points[0]['latency_p95']=200;rows[0]['time_series_json']=json.dumps(points)
        result=fixture_report(rows);self.assertEqual(result['conditions'][0]['status'],'failed')
        self.assertTrue(any(e['scope']=='window' and e['status']=='failed' for e in result['experiments'][0]['time_series']['errors']))
    def test_one_measurement_identity_cannot_be_split_into_independent_files(self):
        rows=measured_rows()
        for index,row in enumerate(rows):row.update(source_kind='lab',measurement_id='one-experiment',device='A',firmware='1',measured_at='2026-10-21T10:00:00Z',raw_sha256=str(index+1)*64)
        result=fixture_report(rows);self.assertEqual(result['conditions'][0]['independent_runs'],1)
    def test_empty_latency_windows_are_missing_not_zero(self):
        def empty(config,cancel=None):
            result=simulation_fixture(config);result['windows'][0]['delivered']=0;return result
        with patch('lab.validation.simulate',side_effect=empty):result=validate(network(),csv_text(measured_rows()),options=dict(seeds=1))
        self.assertFalse(result['experiments'][0]['time_series']['complete'])
        self.assertEqual(next(x for x in result['experiments'][0]['time_series']['errors'] if x['metric']=='latency_p95' and x['time']==0)['status'],'insufficient')
    def test_identical_labels_do_not_merge_different_failure_configurations(self):
        rows=measured_rows();rows[0]['incidents_json']='[]';rows[1]['incidents_json']=json.dumps([dict(kind='node_down',target='b',start=2,duration=2)])
        result=fixture_report(rows);self.assertEqual(len(result['conditions']),2)
        self.assertEqual(sorted(c['independent_runs'] for c in result['conditions']),[1,2])
    def test_policy_and_project_are_not_mutated_and_changing_tolerance_does_not_change_predictions(self):
        project=network();before=project.model_dump();rows=measured_rows();rows[0]['latency_p95']=40
        with patch('lab.validation.simulate',side_effect=simulation_fixture):
            strict=validate(project,csv_text(rows),options=dict(seeds=1));loose_policy=Options(seeds=1);loose_policy.tolerances['latency_p95'].absolute=100
            loose=validate(project,csv_text(rows),options=loose_policy.model_dump())
        self.assertEqual(project.model_dump(),before);self.assertNotEqual(strict['policy_sha256'],loose['policy_sha256'])
        self.assertEqual([x['predicted'] for x in strict['experiments']],[x['predicted'] for x in loose['experiments']])
    def test_distance_and_absolute_relative_zero_value_rules(self):
        policy=Options(min_samples=20);result=distribution([0]*20,[5]*20,policy)
        self.assertEqual(result['ks'],1);self.assertEqual(result['wasserstein_ms'],5);self.assertEqual(result['status'],'failed')
        self.assertEqual(distribution([],[],policy)['status'],'insufficient')
        error_=error('delivery_ratio',0,.005,policy);self.assertEqual(error_['status'],'passed');self.assertIsNone(error_['relative_error'])
    def test_malformed_and_over_budget_data_are_rejected_before_simulation(self):
        for invalid in ('[NaN]','{}','[{"time":0,"width_s":0,"latency_p95":1}]','[{"time":0,"width_s":1,"latency_p95":1},{"time":0,"width_s":1,"latency_p95":2}]'):
            rows=measured_rows();rows[0]['time_series_json']=invalid
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):parse(csv_text(rows))
        rows=measured_rows()
        for row in rows:row['duration_s']=600;row['offered_rate']=3000
        with patch('lab.validation.simulate') as engine:
            with self.assertRaises(ValueError):validate(network(),csv_text(rows))
            engine.assert_not_called()
        with self.assertRaises(CancelledError):fixture_report(cancel=lambda:True)
        with self.assertRaises(ValueError):Options(tolerances={})
    def test_real_simulator_example_is_labelled_synthetic_and_round_trips(self):
        project=network();project.flows[0].rate=8
        example=synthetic_example(project);rows=parse(example['text']);self.assertEqual(len(rows),3)
        self.assertTrue(all(row['evidence']['source_kind']=='synthetic' for row in rows))
        self.assertTrue(all('throughput' in row['time_series'][0] for row in rows));self.assertIn('time_series_json',TEMPLATE)
        result=validate(project,example['text'],options=dict(seeds=1));self.assertFalse(result['hardware_accuracy_proven'])
        html=report_html(result);self.assertIn('Внешняя точность',html);self.assertIn(result['policy_sha256'],html)
    def test_report_escapes_imported_names(self):
        rows=measured_rows();rows[0]['scenario']='<script>alert(1)</script>'
        html=report_html(fixture_report(rows));self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
