"""Finite reference checks for the proposed KIE evidence method.

This is NOT a 5G parser, a distributed implementation, or a performance benchmark.
Run with the project Python; --output writes a reproducible JSON report.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import itertools
import json
import platform
from pathlib import Path

OUTCOMES = ("success", "error", "redirect", "pending")


def histogram(values):
    return tuple(values.count(item) for item in OUTCOMES)


def feasible_vectors(domains):
    return {histogram(list(values)) for values in itertools.product(*domains)}


def add_vectors(left, right):
    return {tuple(a + b for a, b in zip(x, y)) for x in left for y in right}


def diagnose(vectors, report, index=None):
    if not vectors:
        return "evidence_or_model_conflict"
    values = vectors if index is None else {value[index] for value in vectors}
    return "not_refuted" if report in values else "report_inconsistent"


def interval_from_visible(actual, visible):
    successes = sum(actual[i] == "success" for i in visible)
    return successes, successes + len(actual) - len(visible)


def minimum_revelations(actual, visible, report):
    """Optimal offline certificate size for a fixed known binary realization.

    The auditor does NOT know the hidden realization before fetching records.
    This function is an oracle lower bound, not an online selection policy.
    """
    low, high = interval_from_visible(actual, visible)
    if not low <= report <= high:
        return 0
    hidden = [actual[i] for i in range(len(actual)) if i not in visible]
    success_need = report - low + 1
    failure_need = high - report + 1
    possible = []
    if hidden.count("success") >= success_need:
        possible.append(success_need)
    if len(hidden) - hidden.count("success") >= failure_need:
        possible.append(failure_need)
    return min(possible) if possible else None


def brute_certificate(actual, visible, report):
    hidden = [i for i in range(len(actual)) if i not in visible]
    for count in range(len(hidden) + 1):
        for selected in itertools.combinations(hidden, count):
            low, high = interval_from_visible(actual, visible | set(selected))
            if not low <= report <= high:
                return count
    return None


def encode_demo(payload):
    # Restricted demo format; NOT an implementation of RFC 8785.
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def mining_demo():
    import numpy as np
    import sklearn
    from sklearn.ensemble import IsolationForest

    rng = np.random.default_rng(20261025)
    scores = {}
    for role, point in (("amf", [0.2, 0.1, -0.1]),
                        ("smf", [5.0, 6.0, 4.0])):
        train = rng.normal(size=(256, 3))
        calibration = rng.normal(size=(128, 3))
        model = IsolationForest(n_estimators=64, random_state=42).fit(train)
        reference = -model.score_samples(calibration)
        anomaly = float(-model.score_samples([point])[0])
        # A finite calibration rank, not P(compromise | data).
        scores[role] = float((1 + (reference <= anomaly).sum()) / 129)
    # Synthetic gain and byte cost for ordering only. Mandatory audit is separate.
    tasks = [{"role": "amf", "gain": 0.5, "cost": 2},
             {"role": "smf", "gain": 0.5, "cost": 2}]
    for task in tasks:
        task["priority"] = scores[task["role"]] * task["gain"] / task["cost"]
    return {"status": "illustration_only", "sklearn": sklearn.__version__,
            "numpy": np.__version__, "seed": 20261025,
            "calibration_ranks": scores,
            "query_order": [t["role"] for t in sorted(
                tasks, key=lambda t: -t["priority"])]}


def run():
    report_cases = honest_cases = full_false = hierarchy_cases = 0
    rejected = ambiguous_false = zero_fill_false = 0
    for actual in itertools.product(OUTCOMES, repeat=3):
        truth = histogram(list(actual))
        for mask in range(8):
            visible = {i for i in range(3) if mask & (1 << i)}
            domains = [(actual[i],) if i in visible else OUTCOMES for i in range(3)]
            feasible = feasible_vectors(domains)
            assert diagnose(feasible, truth) == "not_refuted"
            honest_cases += 1
            zonal = add_vectors(feasible_vectors(domains[:1]),
                                feasible_vectors(domains[1:]))
            assert zonal == feasible
            hierarchy_cases += 1
            low, high = interval_from_visible(actual, visible)
            assert {v[0] for v in feasible} == set(range(low, high + 1))
            if low != truth[0]:
                zero_fill_false += 1
            for claim in range(4):
                report_cases += 1
                result = diagnose(feasible, claim, 0)
                if result == "report_inconsistent":
                    assert claim != truth[0]
                    rejected += 1
                elif claim != truth[0]:
                    ambiguous_false += 1
                if mask == 7 and claim != truth[0]:
                    assert result == "report_inconsistent"
                    full_false += 1

    certificate_cases = 0
    for actual in itertools.product(("success", "error"), repeat=4):
        for mask in range(16):
            visible = {i for i in range(4) if mask & (1 << i)}
            for claim in range(5):
                assert minimum_revelations(actual, visible, claim) == brute_certificate(
                    actual, visible, claim)
                certificate_cases += 1

    # Exact shared boundary relation retains dependence x + y = 1.
    left = {(0, 0), (1, 1)}  # (boundary, x)
    right = {(0, 1), (1, 0)}  # (boundary, y)
    joined = {(x, y) for b, x in left for c, y in right if b == c}
    boxed = set(itertools.product((0, 1), repeat=2))
    assert joined == {(0, 1), (1, 0)}
    assert (0, 0) in boxed and (0, 0) not in joined

    # Same HTTP/2 stream id, different connection epoch: distinct attempts.
    attempt_a = ("witness-1", "conn-epoch-1", 3)
    attempt_b = ("witness-1", "conn-epoch-2", 3)
    assert attempt_a != attempt_b
    assert diagnose(set(), 0, 0) == "evidence_or_model_conflict"
    assert diagnose(feasible_vectors([OUTCOMES]), 1, 0) == "not_refuted"
    assert diagnose(feasible_vectors([("error",)]), 1, 0) == "report_inconsistent"

    # A compromised sender can authenticate a false report using its own key.
    demo_key = b"public-demo-key-never-use-in-production"
    lie = {"scope": "demo-1", "success_count": 1, "sequence": 1}
    tag = hmac.new(demo_key, encode_demo(lie), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(tag, hmac.new(
        demo_key, encode_demo(lie), hashlib.sha256).hexdigest())
    assert diagnose(feasible_vectors([("error",)]), lie["success_count"], 0
                    ) == "report_inconsistent"

    return {
        "scope": "finite mathematical reference; no real 5G traffic",
        "python": platform.python_version(),
        "exhaustive_success_count_claims": report_cases,
        "honest_full_vector_checks": honest_cases,
        "false_rejections_of_honest_vectors": 0,
        "correctly_rejected_scalar_claims": rejected,
        "false_scalar_claims_left_ambiguous": ambiguous_false,
        "full_visibility_false_claims_rejected": full_false,
        "central_vs_hierarchy_checks": hierarchy_cases,
        "minimum_certificate_vs_bruteforce_checks": certificate_cases,
        "naive_zero_fill_honest_false_rejections": zero_fill_false,
        "dependence_preservation_counterexample": {
            "exact": sorted(joined), "independent_intervals": sorted(boxed)},
        "authenticated_lie_detected": True,
        "empty_evidence_set_separated_from_report_fault": True,
        "missing_distinguished_from_observed_failure": True,
        "stream_identity_includes_connection_epoch": True,
        "data_mining_demo": mining_demo(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = run()
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
