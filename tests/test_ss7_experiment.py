import unittest
from scripts.ss7_experiment import build_project, experiment


class SS7ReproducibilityTests(unittest.TestCase):
    def test_surge_is_only_change(self):
        baseline = build_project(42, False).model_dump()
        surge = build_project(42, True).model_dump()
        self.assertEqual(baseline.pop("incidents"), [])
        self.assertEqual(len(surge.pop("incidents")), 1)
        self.assertEqual(baseline, surge)

    def test_paired_runs_reproducible(self):
        a = experiment(2)
        b = experiment(2)
        self.assertEqual(a["runs"], b["runs"])
        self.assertEqual(len(a["runs"]), 4)
        self.assertEqual([r["seed"] for r in a["runs"]], [20261025, 20261025, 20261026, 20261026])
        self.assertTrue(all(r["sent"] > 0 for r in a["runs"]))


if __name__ == "__main__":
    unittest.main()
