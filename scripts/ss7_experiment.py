"""Offline, reproducible SS7/M3UA load experiment; no network sockets."""
import argparse
import json
import platform
from pathlib import Path

import numpy as np

from lab.experiments import interval, observed_run
from lab.presets import preset
from lab.schema import Project


def build_project(seed, attack):
    p = preset("ss7").model_dump()
    p.update(seed=seed, duration=20, window=2, name="SS7 repeatable offline study")
    for f in p["flows"]:
        f["rate"] = 1.5 if f["id"] == "map" else 0.6
    p["incidents"] = ([dict(kind="surge", target="map", start=6, duration=8, magnitude=3)]
                      if attack else [])
    return Project.model_validate(p)


def experiment(repetitions=6):
    if not 2 <= repetitions <= 30:
        raise ValueError("repetitions must be in [2,30]")
    runs = []
    for index in range(repetitions):
        seed = 20261025 + index
        for scenario in ("baseline", "surge"):
            project = build_project(seed, scenario == "surge")
            metric = observed_run(project, warmup_s=2, save=False)
            runs.append(dict(scenario=scenario, seed=seed, **metric))
    groups = {}
    for scenario in ("baseline", "surge"):
        cohort = [row for row in runs if row["scenario"] == scenario]
        groups[scenario] = {
            key: interval([r[key] for r in cohort], key)
            for key in ("delivery_ratio", "latency_p95", "throughput")
        }
    return {
        "protocol": "SS7/M3UA (abstract offline procedure simulation)",
        "experiment": "paired seed baseline vs synthetic MAP request surge",
        "attack_interval_s": [6, 14],
        "warmup_s": 2,
        "repetitions": repetitions,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "groups": groups,
        "runs": runs,
        "caution": "Synthetic load test, not a validation on operator traffic or a live SS7 network.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=6)
    parser.add_argument("--output", type=Path, default=Path("artifacts/ss7-results.json"))
    args = parser.parse_args()
    result = experiment(args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    for name, metrics in result["groups"].items():
        print(name, {k: v["mean"] for k, v in metrics.items()})
    print("Saved", args.output)


if __name__ == "__main__":
    main()
