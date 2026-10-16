import csv
import io
import hashlib
import unittest
from concurrent.futures import CancelledError
from lab.calibration import calibrate,observations,measure
from lab.calibration_search import Options
from lab.simulator import simulate
from test_procedures import network


def measurements(rows):
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=sorted({key for row in rows for key in row}));writer.writeheader();writer.writerows(rows);return stream.getvalue()


class CalibrationSearchTests(unittest.TestCase):
    def test_absolute_link_parameter_can_calibrate_from_zero_loss(self):
        p=network();p.flows[0].rate=10;p.flows[0].timeout_ms=300;p.links[0].transport_retries=0;rows=[]
        for index in range(6):
            rid=f'loss-{index}';target=p.model_copy(deep=True);target.links[0].loss=.2
            target.seed=(p.seed+int.from_bytes(hashlib.sha256(rid.encode()).digest()[:4],'big'))%2147483647
            rows.append(dict(run_id=rid,offered_rate=10,delivery_ratio=measure(simulate(target))['delivery_ratio']))
        options=dict(parameters=[dict(path='link:ab:loss',lower=0,upper=.5)],passes=4,max_evaluations=20,seeds=1)
        result=calibrate(p,measurements(rows),options=options)
        self.assertGreater(result['parameters']['link:ab:loss'],.05);self.assertEqual(p.links[0].loss,0)
        self.assertLess(result['after']['test']['objective'],result['before']['test']['objective'])

    def test_test_measurements_cannot_change_selected_parameters(self):
        p=network();p.flows[0].rate=1;rows=[dict(run_id=f'run-{index}',offered_rate=1,latency_mean=37) for index in range(6)]
        options=dict(parameters=[dict(path='global:latency',lower=.5,upper=3,logarithmic=True)],passes=3,max_evaluations=15,seeds=2)
        first=calibrate(p,measurements(rows),options=options)
        for row in rows:
            if row['run_id'] in first['split']['test']:row['latency_mean']=5000
        second=calibrate(p,measurements(rows),options=options)
        self.assertEqual(first['parameters'],second['parameters']);self.assertEqual(first['search'],second['search'])
        self.assertNotEqual(first['after']['test']['objective'],second['after']['test']['objective'])
        self.assertEqual(first['test_error_ci95']['latency_mean']['independent_groups'],2)
        self.assertEqual(len(first['after']['test']['experiments'][0]['seed_predictions']),2)

    def test_search_budget_bounds_and_cancellation(self):
        p=network();p.flows[0].rate=1;text=measurements([dict(run_id=str(i),offered_rate=1,latency_mean=33) for i in range(4)])
        result=calibrate(p,text,options=dict(max_evaluations=7,passes=12,seeds=1))
        self.assertLessEqual(len(result['search']),7)
        for spec in result['parameter_bounds']:self.assertLessEqual(spec['lower'],result['parameters'][spec['path']]);self.assertGreaterEqual(spec['upper'],result['parameters'][spec['path']])
        with self.assertRaises(CancelledError):calibrate(p,text,cancel=lambda:True)
        with self.assertRaises(ValueError):Options(parameters=[dict(path='global:latency',lower=2,upper=1)])
        with self.assertRaises(ValueError):calibrate(p,text,options=dict(parameters=[dict(path='node:missing:processing_ms',lower=0,upper=10)]))

    def test_csv_supports_independent_failure_scenarios(self):
        import json
        text=measurements([dict(run_id=str(i),offered_rate=1,latency_mean=30,scenario='outage',incidents_json=json.dumps([dict(kind='link_down',target='ab',start=2,duration=2)])) for i in range(4)])
        rows=observations(text);self.assertEqual(rows[0]['incidents'][0]['target'],'ab')
        p=network();result=calibrate(p,text,options=dict(max_evaluations=5,seeds=1))
        self.assertTrue(all(x['scenario']=='outage' for x in result['after']['test']['experiments']))


if __name__=='__main__':unittest.main()
