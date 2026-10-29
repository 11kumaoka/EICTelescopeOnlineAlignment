#!/usr/bin/env python3
"""Compare final aligned telescope geometry with Geant4 truth vs track count.

Read-only analysis of completed native-controller runs. The only output is a
new ROOT file under the supplied study's analysis/ directory. Truth is not
provided to tracking, alignment, or Pede.

Docker usage (from any directory):
    python3 -B studies/alignment_statistics/plot_geometry_accuracy.py \
      --study result/statistics_study_12000
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

# Reuse the repository's tested SO(3) convention when called as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
from geometry.transforms import rotation_log
from studies.alignment_statistics.scan_statistics import verified_final


RUNS = (
    (100, "accuracy_N100"),
    (500, "accuracy_N500"),
    (1000, "accuracy_N1000_filtered"),
    (5000, "accuracy_N5000_filtered"),
    (10000, "accuracy_N10000_no5730"),
)

# label, graph name, title, unit; y translations: mm -> um, rotations: rad -> mrad.
PARAMETERS = (
    (700102, "L1_Y", "L1 Y", "um"),
    (700104, "L1_Rx", "L1 Rx", "mrad"),
    (700105, "L1_Ry", "L1 Ry", "mrad"),
    (700106, "L1_Rz", "L1 Rz", "mrad"),
    (700202, "L2_Y", "L2 Y", "um"),
)


def read_json(path):
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def sensors_by_layer(path):
    return {int(sensor["layer"]): sensor
            for sensor in read_json(path)["sensors"]}


def collect(study):
    truth_path = study / "preparation/truth_audit/geometry.json"
    truth = sensors_by_layer(truth_path)
    if not all(layer in truth for layer in (1, 2)):
        raise ValueError("Truth audit is missing L1 or L2")

    points = {label: [] for label, _, _, _ in PARAMETERS}
    sources = {"truth_audit": {"path": str(truth_path),
                                "sha256": sha256(truth_path)}}
    for n, name in RUNS:
        run = study / name
        final_path = run / "final/report.json"
        info_path = run / "jobs/attempt_000/iteration_000/parameter_information.json"
        metrics_path = run / "jobs/attempt_000/iteration_000/metrics.json"

        report = verified_final(run)
        info, metrics = (read_json(p) for p in (info_path, metrics_path))
        if not (report.get("engineering_success") is True and
                report.get("status") in ("converged", "iteration_limit") and
                report.get("applied_corrections") == 1):
            raise ValueError(f"N={n}: no verified one-correction final report")
        if metrics["tracks"] != n or metrics["rejected"] != 0:
            raise ValueError(f"N={n}: expected {n} accepted and zero rejected tracks")

        poses = {int(item["layer"]): item for item in report["cumulative_poses"]}
        for layer in (1, 2):
            if (poses[layer]["volume_id"] != truth[layer]["volume_id"] or
                    poses[layer]["geometry_id"] != truth[layer]["geometry_id"]):
                raise ValueError(f"N={n}: L{layer} geometry identity mismatch")

        # Physical left-global rotation of the corrected pose relative to Truth.
        corrected_rotation = np.asarray(poses[1]["acts_rotation"], dtype=float)
        true_rotation = np.asarray(truth[1]["acts_rotation"], dtype=float)
        difference = rotation_log(corrected_rotation @ true_rotation.T)
        values = {
            700102: 1000.0 * (poses[1]["center_mm"][1] - truth[1]["center_mm"][1]),
            700104: 1000.0 * difference[0],
            700105: 1000.0 * difference[1],
            700106: 1000.0 * difference[2],
            700202: 1000.0 * (poses[2]["center_mm"][1] - truth[2]["center_mm"][1]),
        }
        sigma = dict(zip((int(k) for k in info["selected_labels"]),
                         info["data_only_formal_sigma"]))
        if set(sigma) != set(values):
            raise ValueError(f"N={n}: unexpected fitted parameter labels: {list(sigma)}")
        for label in values:
            value, error = float(values[label]), 1000.0 * float(sigma[label])
            if not np.isfinite(value) or not np.isfinite(error) or error <= 0:
                raise ValueError(f"N={n}: invalid closure or nominal formal error")
            points[label].append((n, value, error))

        sources[name] = {"report_sha256": sha256(final_path),
                         "information_sha256": sha256(info_path),
                         "first_fit_tracks": metrics["tracks"],
                         "first_fit_rejected": metrics["rejected"]}
    return points, sources


def make_root(output, points, sources):
    import ROOT
    ROOT.gROOT.SetBatch(True)
    if output.exists():
        raise FileExistsError(f"Output already exists (will not overwrite): {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    root = ROOT.TFile(str(output), "CREATE")
    if not root or root.IsZombie():
        raise OSError(f"Cannot create ROOT output: {output}")
    try:
        for label, tag, title, unit in PARAMETERS:
            graph = ROOT.TGraphErrors(len(points[label]))
            graph.SetName(f"error_{tag}")
            graph.SetTitle(f"{title}: aligned - truth;Accepted tracks;Error [{unit}]")
            graph.SetMarkerStyle(20)
            graph.SetMarkerSize(1.1)
            graph.SetLineWidth(2)
            for i, (n, value, sigma) in enumerate(points[label]):
                graph.SetPoint(i, float(n), value)
                graph.SetPointError(i, 0.0, sigma)
            canvas = ROOT.TCanvas(f"canvas_{tag}", title, 850, 550)
            canvas.SetLogx()
            canvas.SetGrid()
            graph.Draw("AP")
            graph.Write()
            canvas.Write()

        metadata = {"definition": "aligned pose minus simulated Truth pose",
                    "rotation": "SO(3) log(R_aligned @ R_truth.T), global frame",
                    "translation": "global Y of sensor center",
                    "errors": "first-fit data-only curvature 1-sigma; not Pede-reported or stabilized covariance; NOT validated coverage",
                    "sample_construction": "nested prefix cohorts; N=10000 omits event 5730",
                    "runs": [name for _, name in RUNS], "sources": sources}
        ROOT.TObjString(json.dumps(metadata, indent=2)).Write("analysis_metadata_json")
        root.Write()
    finally:
        root.Close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True,
                        help="Existing master study directory, e.g. result/statistics_study_12000")
    parser.add_argument("--output", type=Path,
                        help="New ROOT output path (default: STUDY/analysis/geometry_accuracy.root)")
    args = parser.parse_args()
    study = args.study.resolve()
    output = (args.output.resolve() if args.output else
              study / "analysis/geometry_accuracy.root")
    points, sources = collect(study)
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    make_root(output, points, sources)
    print("N     L1 Y [um]       L1 Rx [mrad]   L1 Ry [mrad]   L1 Rz [mrad]   L2 Y [um]")
    for i, (n, _) in enumerate(RUNS):
        entries = [points[label][i][1] for label, _, _, _ in PARAMETERS]
        print(f"{n:5d} " + " ".join(f"{v:+13.6f}" for v in entries))
    print("ROOT:", output)
    print("Y error bars = nominal first-fit 1 sigma (not externally validated)")


if __name__ == "__main__":
    main()
