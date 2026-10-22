import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from lab import research, storage
from lab.presets import preset
from lab.procedures import ProcedureSimulation


class DatasetScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(storage, 'DATA', Path(self.temp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def project(self, kind='diameter'):
        p = preset(kind)
        p.duration = 10
        p.window = 1
        p.journal_sessions = 0
        for flow in p.flows:
            flow.rate = .5
        return p

    def plans(self, p, **options):
        return [run['project']['incidents'] for run in research.dataset(p, 12, **options)['provenance']]

    def test_latency_diversity_independent_of_initial_associations(self):
        for kind in ('diameter', 'ss7', '5g'):
            with self.subTest(protocol=kind):
                p = self.project(kind)
                before = p.model_dump()
                probe = ProcedureSimulation(p)
                flow = p.flows[0]
                self.assertIsNone(probe.route(flow.source, probe.destination(flow), flow))
                active = research.dataset(p, 12)
                counts = Counter(event['kind'] for run in active['provenance'] for event in run['project']['incidents'])
                self.assertEqual(counts, {'surge': 2, 'latency': 2, 'node_down': 2})
                self.assertEqual(p.model_dump(), before)
                self.assertTrue(any('latency' in row['fault_types'] for row in active['rows']))
                active_plans = [run['project']['incidents'] for run in active['provenance']]
                p.control_plane = False
                self.assertEqual(active_plans, self.plans(p))

    def test_topology_variants_keep_the_same_plans_with_control_on_and_off(self):
        p = self.project()
        plans = self.plans(p, topology_variants=True)
        self.assertTrue(any(event['kind'] == 'latency' for run in plans for event in run))
        p.control_plane = False
        self.assertEqual(plans, self.plans(p, topology_variants=True))

    def test_disabled_link_is_skipped_in_favour_of_configured_alternate(self):
        p = self.project()
        direct = next(link for link in p.links if {link.source, link.target} == {'mme', 'hss'})
        direct.enabled = False
        latency = [event for run in self.plans(p) for event in run if event['kind'] == 'latency']
        self.assertEqual(len(latency), 2)
        self.assertTrue(all(event['target'] != direct.id for event in latency))
        self.assertTrue(all(next(link for link in p.links if link.id == event['target']).enabled for event in latency))

    def test_unusable_topologies_do_not_select_unrelated_links(self):
        for reason in ('disabled_source', 'disabled_target', 'disabled_links', 'incompatible_protocol', 'routing_rule', 'disconnected'):
            with self.subTest(reason=reason):
                p = self.project()
                if reason == 'disabled_source':
                    p.nodes[0].enabled = False
                elif reason == 'disabled_target':
                    p.nodes[-1].enabled = False
                elif reason == 'disabled_links':
                    for link in p.links:
                        link.enabled = False
                elif reason == 'incompatible_protocol':
                    for link in p.links:
                        link.protocol = 'SS7'
                elif reason == 'routing_rule':
                    p.nodes[0].routing_table['*'] = ['dra']
                    p.nodes[1].routing_table['*'] = ['mme']
                else:
                    p.links = [link for link in p.links if 'mme' not in (link.source, link.target)]
                counts = Counter(event['kind'] for run in self.plans(p) for event in run)
                self.assertEqual(counts, {'surge': 4, 'node_down': 2})

    def test_m3ua_routing_context_is_respected(self):
        p = self.project('ss7')
        for link in p.links:
            if link.protocol == 'M3UA':
                link.routing_context = 999
        self.assertFalse(any(event['kind'] == 'latency' for run in self.plans(p) for event in run))

    def test_existing_project_incidents_are_preserved(self):
        p = self.project()
        p = research.checked(dict(p.model_dump(), incidents=[dict(kind='latency', target=p.links[0].id, start=2, duration=4, magnitude=3)]))
        expected = [event.model_dump() for event in p.incidents]
        plans = self.plans(p)
        self.assertTrue(all(events == (expected if i % 2 else []) for i, events in enumerate(plans)))


if __name__ == '__main__':
    unittest.main()
