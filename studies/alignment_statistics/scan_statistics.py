#!/usr/bin/env python3
"""Repeat native alignment on disjoint track cohorts, then summarize truth closure.

Keep shared reconstruction and calibration software unchanged. Use the existing
accepted Master ROOT; only create study-specific ROOT files for non-prefix cohorts.

Usage inside the fixed Docker environment, from any current directory:
  python3 -B /work/studies/alignment_statistics/scan_statistics.py prepare --study /work/result/statistics_study_12000
  python3 -B /work/studies/alignment_statistics/scan_statistics.py run --study /work/result/statistics_study_12000 --max-jobs 2
  python3 -B /work/studies/alignment_statistics/scan_statistics.py run --study /work/result/statistics_study_12000
  python3 -B /work/studies/alignment_statistics/scan_statistics.py report --study /work/result/statistics_study_12000
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# 10 statistics points. Repetitions at the same N use disjoint input intervals.
COUNTS = ((100, 10), (200, 5), (300, 3), (400, 2), (500, 2),
          (700, 2), (1000, 1), (1250, 1), (1500, 1), (2000, 1))
BASELINES = {100: "accuracy_N100", 500: "accuracy_N500",
             1000: "accuracy_N1000_filtered"}
PARAMETERS = ((700102, "L1_Y", "L1 Global Y", "um"),
              (700104, "L1_Rx", "L1 Global Rx", "mrad"),
              (700105, "L1_Ry", "L1 Global Ry", "mrad"),
              (700106, "L1_Rz", "L1 Global Rz", "mrad"),
              (700202, "L2_Y", "L2 Global Y", "um"))


def read(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    with Path(path).open("x") as stream:  # Never overwrite existing artifacts.
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def paths(study):
    master = study / "raw/track_accepted/channels.root"
    provenance = study / "raw/track_accepted/provenance.json"
    root = study / "analysis/scan_2000"
    return master, provenance, root


def cases():
    for n, count in COUNTS:
        for repeat in range(count):
            yield n, repeat, f"n{n:04d}_r{repeat:02d}"


def sample_paths(study, n, repeat, name):
    master, parent_provenance, root = paths(study)
    if repeat == 0:
        return master, parent_provenance
    folder = root / "inputs" / name
    return folder / "channels.root", folder / "provenance.json"


def baseline_for(n, repeat, fresh_calibrations=False):
    """Reuse only the three explicitly named historical prefix calibrations."""
    return BASELINES.get(n) if repeat == 0 and not fresh_calibrations else None


def case_directory(study, entry):
    """Resolve the fixed study layout without following stale absolute run paths.

    Historical plans contain the producer's absolute paths. Analysis may read
    those same immutable bytes through a read-only mount at a different path;
    this does not authorize resuming a relocated Controller checkpoint.
    """
    if entry["name"] not in {name for _, _, name in cases()}:
        raise ValueError("Unknown statistics-study cohort")
    if entry["baseline_run"]:
        if entry["baseline_run"] != baseline_for(entry["n"], entry["repeat"]):
            raise ValueError("Unknown reused calibration baseline")
        return study / entry["baseline_run"]
    return paths(study)[2] / "runs" / entry["name"]


def verified_final(path):
    """Read a sealed one-correction result without asserting physics validation."""
    from calibration.controller import verify
    verify(path / "final")
    value = read(path / "final/report.json")
    if not (value.get("engineering_success") is True and
            value.get("status") in ("converged", "iteration_limit") and
            value.get("applied_corrections") == 1):
        raise ValueError(f"Not a completed one-update calibration: {path}")
    return value


def replicate_summary(values):
    """Separate physical-error RMS from between-cohort sample variation."""
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError("Finite, nonempty cohort values are required")
    spread = statistics.stdev(values) if len(values) > 1 else None
    return {"mean_error": statistics.mean(values),
            "rms_error": math.sqrt(statistics.mean([v*v for v in values])),
            "sample_sd": spread,
            "standard_error_of_mean": spread / math.sqrt(len(values)) if spread is not None else None,
            "repetitions": len(values)}


def selection_indices(identities, ledger, source_sha256, payload_sha256):
    """Replay explicit event exclusions; never infer an acceptance criterion."""
    if (ledger.get("schema") != "explicit-event-selection-v1" or
            ledger.get("source_raw_root_sha256") != source_sha256 or
            ledger.get("source_channel_payload_sha256") != payload_sha256):
        raise ValueError("Selection ledger does not bind this exact immutable source")
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate source run/event identity")
    exclusions = {}
    for item in ledger["exclude"]:
        identity = (item["run_number"], item["event_number"])
        if (any(type(v) is not int for v in identity) or
                not isinstance(item["reason"], str) or not item["reason"].strip()):
            raise ValueError("Every exclusion needs integer event identity and an explicit reason")
        if identity in exclusions or identity not in identities:
            raise ValueError("Duplicate or absent excluded event")
        exclusions[identity] = item["reason"]
    kept = [i for i, identity in enumerate(identities) if identity not in exclusions]
    rejected = [{"source_event_index": i, "run_number": identity[0],
                 "event_number": identity[1], "reason": exclusions[identity]}
                for i, identity in enumerate(identities) if identity in exclusions]
    return kept, rejected


def header_values(header):
    return (int(header.getRunNumber()), int(header.getEventNumber()), int(header.getTimeStamp()),
            float(header.getWeight()), tuple(float(value) for value in header.getWeights()))


def select(study, ledger_path, source=None, output=None):
    """Experimental offline replay of a researcher-supplied selection ledger.

    The unchanged Controller still rejects failed tracks/local fits. This helper
    does not decide which failures may be excluded, certify a selection policy,
    read Simulation Truth or modify the source ROOT file.
    """
    from podio import reading
    from simulation.digitization import write_channels
    from calibration.reconstruction.operations import channel_values, channel_digest
    source = study / "raw/projection/channels.root" if source is None else source
    output = study / "raw/track_accepted" if output is None else output
    if output.exists():
        raise FileExistsError(output)
    if not output.resolve().is_relative_to(REPO / "result"):
        raise ValueError("Selection output must be a new child of this checkout's result/")
    source_provenance = (source.parent.parent if source.parent.name == "projection" else source.parent) / "provenance.json"
    source_sha = sha(source)
    provenance_sha = sha(source_provenance)
    original = channel_values(source)
    payload_sha = channel_digest(original)
    metadata = read(source_provenance)
    if metadata["raw_root_sha256"] != source_sha or metadata["channel_payload_sha256"] != payload_sha:
        raise ValueError("Source ROOT/provenance mismatch")
    ledger_sha = sha(ledger_path)
    ledger = read(ledger_path)
    events = []
    reader = reading.get_reader(str(source))
    for frame in reader.get("events"):
        if set(frame.getAvailableCollections()) != {"EventHeader", "TelescopeChannels"}:
            raise ValueError("Selection input must contain only EventHeader and TelescopeChannels")
        if len(frame.get("EventHeader")) != 1 or len(frame.get("TelescopeChannels")) != 5:
            raise ValueError("Selection input requires one EventHeader and five RawHits")
        header = frame.get("EventHeader")[0]
        events.append((header.clone(), [hit.clone() for hit in frame.get("TelescopeChannels")], None))
    identities = [header_values(event[0])[:2] for event in events]
    kept, rejected = selection_indices(identities, ledger, source_sha, payload_sha)
    if not kept:
        raise ValueError("Selection ledger excludes every event")
    # PODIO transfers cloned-object ownership to the written collections. Cache
    # plain header values before writing rather than dereferencing those handles
    # after their collections have been released.
    expected_headers = [header_values(events[i][0]) for i in kept]
    output.mkdir(parents=True)
    target = output / "channels.root"
    write_channels([events[i] for i in kept], target)
    actual = channel_values(target)
    if actual != [original[i] for i in kept]:
        raise RuntimeError("RawHit payload changed during explicit event selection")
    written_reader = reading.get_reader(str(target))
    written_headers = [header_values(frame.get("EventHeader")[0])
                       for frame in written_reader.get("events")]
    if written_headers != expected_headers:
        raise RuntimeError("Complete EventHeader changed during selection")
    if (sha(source) != source_sha or sha(source_provenance) != provenance_sha or
            sha(ledger_path) != ledger_sha):
        raise RuntimeError("Immutable selection input changed")
    save(output / "provenance.json", {
        "schema": "native-geant4-input-v1", "input_origin": metadata["input_origin"],
        "raw_root_sha256": sha(target), "channel_payload_sha256": channel_digest(actual),
        "source_root_sha256": source_sha, "source_provenance_sha256": provenance_sha,
        "selection_ledger_sha256": ledger_sha, "selection_ledger": ledger,
        "selection": "explicit researcher-supplied event ledger; no automatic acceptance or Truth selection",
        "source_events": len(events), "accepted_events": len(kept),
        "excluded_events": rejected, "kept_source_indices": kept,
        "complete_event_headers_preserved": True, "raw_values_preserved": True,
        "truth_used_for_selection": False, "automatic_fit_based_selection": False,
        "scientific_selection_policy_validated": False})
    print(f"SELECTED {len(kept)}/{len(events)} events; explicit exclusions={len(rejected)}; {output}")


def prepare(study, fresh_calibrations=False):
    from podio import reading
    from simulation.digitization import write_channels
    from calibration.reconstruction.operations import channel_values, channel_digest
    master, provenance, root = paths(study)
    if root.exists():
        raise FileExistsError(f"Study already prepared: {root}; never overwrite. Use 'run'.")
    metadata = read(provenance)
    original = channel_values(master)
    if metadata["raw_root_sha256"] != sha(master) or metadata["channel_payload_sha256"] != channel_digest(original):
        raise ValueError("Master ROOT/provenance SHA256 mismatch")
    if len(original) < max(n * repeat_count for n, repeat_count in COUNTS):
        raise ValueError("Not enough accepted input events for disjoint cohorts")
    if len(set(k for k, _ in original)) != len(original):
        raise ValueError("Duplicate event identities in Master ROOT")

    # Verify previously produced prefix cohorts. They must remain unchanged.
    baseline_sources = {}
    for n, existing in (() if fresh_calibrations else BASELINES.items()):
        snapshot = study / existing / "input/channels.root"
        final = study / existing / "final/report.json"
        verified_final(study / existing)
        if channel_values(snapshot)[:n] != original[:n]:
            raise ValueError(f"Old N={n} input differs from accepted Master prefix")
        baseline_sources[existing] = {"path": str(final.resolve()), "sha256": sha(final)}

    root.mkdir(parents=True)
    (root / "inputs").mkdir()
    (root / "runs").mkdir()
    (root / "logs").mkdir()
    rows = []
    for n, repeat, name in cases():
        first, stop = repeat * n, (repeat + 1) * n
        indices = list(range(first, stop))
        expected = original[first:stop]
        assert len(expected) == n
        input_root, receipt = sample_paths(study, n, repeat, name)
        if repeat:
            # Preserve EventHeader run/event identity and clone exactly 5 RawHits.
            selected = []
            for index, frame in enumerate(reading.get_reader(str(master)).get("events")):
                if index < first:
                    continue
                if index >= stop:
                    break
                head = frame.get("EventHeader")[0]
                selected.append((head.clone(),
                                 [hit.clone() for hit in frame.get("TelescopeChannels")], None))
            if len(selected) != n:
                raise RuntimeError(f"Cannot extract input cohort {name}")
            input_root.parent.mkdir()
            write_channels(selected, input_root)
            actual = channel_values(input_root)
            if actual != expected:
                raise RuntimeError(f"RawHit payload or EventHeader changed in {name}")
            # Do not copy the parent's full selection ledger: it describes a
            # different ROOT and would be misleading for this N-event cohort.
            fields = {
                "schema": "native-geant4-input-v1",
                "input_origin": "geant4_digitized",
                "raw_root": str(input_root.resolve()),
                "raw_root_sha256": sha(input_root),
                "channel_payload_sha256": channel_digest(actual),
                "parent_raw_root_sha256": sha(master),
                "parent_provenance_sha256": sha(provenance),
                "accepted_events": n,
                "selection": {
                    "method": "disjoint contiguous accepted-event blocks; no Truth selection",
                    "first_inclusive": first,
                    "last_exclusive": stop,
                    "master_event_numbers": [event for event, _ in expected],
                },
            }
            save(receipt, fields)
        rows.append({"name": name, "n": n, "repeat": repeat,
                     "master_start_inclusive": first, "master_stop_exclusive": stop,
                     "event_numbers": [event for event, _ in expected],
                     "input_root": str(input_root.resolve()), "input_sha256": sha(input_root),
                     "input_provenance": str(receipt.resolve()),
                     "provenance_sha256": sha(receipt),
                     "baseline_run": baseline_for(n, repeat, fresh_calibrations),
                     "new_run": str((root / "runs" / name).resolve())})
        print(f"Prepared N={n:4d} repeat={repeat+1}/{dict(COUNTS)[n]} "
              f"events={first}:{stop} "
              f"{'(reuse calibration)' if baseline_for(n, repeat, fresh_calibrations) else '(Master prefix)' if repeat == 0 else '(PODIO clone)'}", flush=True)
    save(root / "plan.json", {
        "purpose": "Physical sensor-pose truth closure versus selected track count",
        "sampling": "Within each N, disjoint contiguous accepted-event blocks; cross-N overlap is expected",
        "master_sha256": sha(master), "master_provenance_sha256": sha(provenance),
        "master_events": len(original), "initial_xml_sha256": sha(study / "preparation/initial.xml"),
        "truth_geometry_sha256": sha(study / "preparation/truth_audit/geometry.json"),
        "alignment_config_sha256": sha(study / "configuration/alignment.json"),
        "existing_baselines": baseline_sources,
        "calibration_reuse": "none-explicit" if fresh_calibrations else "three-historical-prefix-baselines",
        "cases": rows})
    print(f"PREPARED: {len(rows)} cases; {len(rows)-len(baseline_sources)} new fits needed; at {root}")


def checked_plan(study):
    master, prov, root = paths(study)
    plan = read(root / "plan.json")
    if [(entry["n"], entry["repeat"], entry["name"]) for entry in plan["cases"]] != list(cases()):
        raise ValueError("Study plan does not contain the declared 28 cohorts in order")
    if sha(master) != plan["master_sha256"] or sha(prov) != plan["master_provenance_sha256"]:
        raise ValueError("Master data/provenance changed since plan creation")
    if sha(study / "preparation/initial.xml") != plan["initial_xml_sha256"]:
        raise ValueError("Initial geometry changed")
    if sha(study / "preparation/truth_audit/geometry.json") != plan["truth_geometry_sha256"]:
        raise ValueError("Truth geometry changed")
    if sha(study / "configuration/alignment.json") != plan["alignment_config_sha256"]:
        raise ValueError("Alignment config changed")
    for name, item in plan["existing_baselines"].items():
        if name not in BASELINES.values() or sha(study / name / "final/report.json") != item["sha256"]:
            raise ValueError("Reused baseline report changed since plan preparation")
    return plan, root


def run(study, max_jobs):
    plan, root = checked_plan(study)
    completed = 0
    for entry in plan["cases"]:
        name, n = entry["name"], entry["n"]
        destination = case_directory(study, entry)
        final = destination / "final/report.json"
        if final.exists():
            verified_final(destination)
            print(f"SKIP {name}: already successful", flush=True)
            continue
        if destination.exists():
            raise RuntimeError(f"Previously failed/incomplete run; inspect before proceeding: {destination}")
        if sha(entry["input_root"]) != entry["input_sha256"] or sha(entry["input_provenance"]) != entry["provenance_sha256"]:
            raise ValueError(f"Input modified for {name}")
        if max_jobs is not None and completed >= max_jobs:
            print(f"PAUSED after {completed} new jobs; invoke run again to continue")
            return
        command = [sys.executable, "-B", "-m", "calibration.controller", "start",
                   "--run", str(destination),
                   "--raw", entry["input_root"], "--input-provenance", entry["input_provenance"],
                   "--geometry", str((study / "preparation/initial.xml").resolve()),
                   "--config", str((study / "configuration/alignment.json").resolve()),
                   "--build", str((REPO / "build/native_telescope").resolve()),
                   "--events", str(n), "--chi2-cut", "1000", "--validation-mode", "diagnostic",
                   "--geometry-aware-holes", "--minimum-corrections", "1", "--maximum-corrections", "1"]
        log_path = root / "logs" / (name + ".log")
        print(f"RUN {name}: N={n}, first accepted-master event={entry['master_start_inclusive']}", flush=True)
        with log_path.open("x") as log:
            log.write("Command: " + " ".join(command) + "\n\n")
            log.flush()
            process = subprocess.run(command, cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=False)
        completed += 1
        if process.returncode or not final.exists() or read(final).get("engineering_success") is not True:
            print(f"STOP {name}: exit={process.returncode}; inspect {log_path}", flush=True)
            raise SystemExit(process.returncode or 1)
        print(f"OK {name}: engineering_success=true", flush=True)
    print("COMPLETE: all planned alignment runs are successful")


def closure(study, path, truth):
    import numpy as np
    from geometry.transforms import rotation_log
    report = verified_final(path)
    info = read(path / "jobs/attempt_000/iteration_000/parameter_information.json")
    m = read(path / "jobs/attempt_000/iteration_000/metrics.json")
    poses = {int(p["layer"]): p for p in report["cumulative_poses"]}
    for layer in (1, 2):
        if ((poses[layer]["volume_id"], poses[layer]["geometry_id"]) !=
                (truth[layer]["volume_id"], truth[layer]["geometry_id"])):
            raise ValueError("Detector identities do not match Truth")
    rotation = rotation_log(np.array(poses[1]["acts_rotation"]) @
                            np.array(truth[1]["acts_rotation"]).T)
    errors = {700102: 1000 * (poses[1]["center_mm"][1] - truth[1]["center_mm"][1]),
              700104: 1000 * rotation[0], 700105: 1000 * rotation[1],
              700106: 1000 * rotation[2],
              700202: 1000 * (poses[2]["center_mm"][1] - truth[2]["center_mm"][1])}
    sigmas = {int(k): 1000 * v for k, v in zip(info["selected_labels"], info["data_only_formal_sigma"])}
    if set(errors) != set(sigmas):
        raise ValueError(f"Mismatch in fitted alignment parameters: {path}")
    return errors, sigmas, m


def report(study, output_dir=None):
    import ROOT
    plan, root = checked_plan(study)
    output_dir = root if output_dir is None else output_dir
    target = output_dir / "alignment_scan_2000.root"
    summary_target = output_dir / "alignment_scan_summary.json"
    for existing in (target, summary_target):
        if existing.exists():
            raise FileExistsError(f"Cannot overwrite existing report: {existing}")
    truth = {int(s["layer"]): s for s in read(study / "preparation/truth_audit/geometry.json")["sensors"]}
    records = {}
    for entry in plan["cases"]:
        dest = case_directory(study, entry)
        values, sigmas, metrics = closure(study, dest, truth)
        if metrics["tracks"] != entry["n"] or metrics["rejected"]:
            raise ValueError(f"Track count mismatch: {entry['name']}")
        records[entry["name"]] = {"n": entry["n"], "repeat": entry["repeat"],
                                   "error": values, "formal_sigma": sigmas,
                                   "report_sha256": sha(dest / "final/report.json")}
    output_dir.mkdir(parents=True, exist_ok=True)
    ROOT.gROOT.SetBatch(True)
    f = ROOT.TFile(str(target), "CREATE")
    if not f or f.IsZombie():
        raise OSError(target)
    try:
        for label, tag, title, unit in PARAMETERS:
            points = [v for v in records.values()]
            individual = ROOT.TGraphErrors(len(points))
            individual.SetName("individual_" + tag)
            individual.SetTitle(f"{title}: corrected - simulated Truth;Accepted tracks;Error [{unit}]")
            individual.SetMarkerStyle(24)
            individual.SetMarkerSize(.8)
            for i, p in enumerate(points):
                individual.SetPoint(i, p["n"], p["error"][label])
                individual.SetPointError(i, 0, p["formal_sigma"][label])
            individual.Write()
            repeated = [(n, replicate_summary([p["error"][label] for p in points if p["n"] == n]))
                        for n, count in COUNTS if count > 1]
            single = [(n, replicate_summary([p["error"][label] for p in points if p["n"] == n]))
                      for n, count in COUNTS if count == 1]
            mean = ROOT.TGraphErrors(len(repeated))
            mean.SetName("mean_" + tag)
            mean.SetTitle(f"{title}: mean over repetitions;Accepted tracks;Error [{unit}]")
            mean.SetMarkerStyle(20)
            mean.SetLineWidth(2)
            for i, (n, value) in enumerate(repeated):
                mean.SetPoint(i, n, value["mean_error"])
                mean.SetPointError(i, 0, value["sample_sd"])
            mean.Write()
            singleton = ROOT.TGraph(len(single))
            singleton.SetName("single_cohort_" + tag)
            singleton.SetTitle("Single cohort: between-cohort SD is not estimated")
            singleton.SetMarkerStyle(21)
            for i, (n, value) in enumerate(single):
                singleton.SetPoint(i, n, value["mean_error"])
            singleton.Write()
            rms = ROOT.TGraph(len(COUNTS))
            rms.SetName("rms_error_" + tag)
            rms.SetTitle(f"{title}: RMS physical error over repetitions;Accepted tracks;RMS error [{unit}]")
            rms.SetMarkerStyle(20)
            for i, (n, _) in enumerate(COUNTS):
                values = [p["error"][label] for p in points if p["n"] == n]
                rms.SetPoint(i, n, math.sqrt(sum(v*v for v in values) / len(values)))
            rms.Write()
            canvas = ROOT.TCanvas("canvas_" + tag, tag, 850, 550)
            canvas.SetLogx()
            canvas.SetGrid()
            individual.Draw("AP")
            mean.Draw("P SAME")
            singleton.Draw("P SAME")
            canvas.BuildLegend(.45, .71, .89, .89,
                               "Mean +/- cohort SD where measured; individual +/- data-only formal 1sigma")
            canvas.Write()
        ROOT.TObjString(json.dumps({
            "source_plan_sha256": sha(root / "plan.json"),
            "source_runs": records,
            "mean_y_error": "between-cohort sample SD only where >=2 disjoint cohorts; singleton markers have no measured between-cohort error bar",
            "individual_y_error": "initial-fit data-only curvature 1 sigma; not Pede-reported or stabilized covariance; coverage not validated",
            "reference": "physical pose (corrected minus simulated truth), global SO(3) difference",
            "selection": "Within-N disjoint cohorts; cross-N samples overlap; baseline runs reused",
        }, indent=2)).Write("analysis_metadata_json")
        f.Write()
    finally:
        f.Close()
    save(summary_target, {
        "root": str(target.resolve()), "root_sha256": sha(target),
        "npoints": len(COUNTS), "fits": len(records),
        "summaries": {str(n): {str(label): replicate_summary(
            [v["error"][label] for v in records.values() if v["n"] == n])
            for label, _, _, _ in PARAMETERS} for n, _ in COUNTS}
    })
    print(f"ROOT: {target}")
    print("Mean error bars are replica SD when repetitions >=2, not SEM or formal fit uncertainties")
    print("For N with one cohort, between-cohort SD is UNDEFINED (separate markers, no SD error bar)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=("select", "prepare", "run", "report"))
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--max-jobs", type=int, help="Only with run: perform at most this many new fits")
    parser.add_argument("--fresh-calibrations", action="store_true",
                        help="Only with prepare: explicitly run all 28 calibrations without reusing the three historical baselines")
    parser.add_argument("--output-dir", type=Path,
                        help="With report/select: new output location; report inputs may be a read-only historical study")
    parser.add_argument("--selection-ledger", type=Path,
                        help="Only with select: exact-source event exclusions and reasons; never inferred automatically")
    parser.add_argument("--source-root", type=Path,
                        help="Only with select: immutable channels.root (default: STUDY/raw/projection/channels.root)")
    args = parser.parse_args()
    if args.step != "run" and args.max_jobs is not None:
        parser.error("--max-jobs is only supported by 'run'")
    if args.max_jobs is not None and args.max_jobs < 1:
        parser.error("--max-jobs must be >=1")
    if args.step != "prepare" and args.fresh_calibrations:
        parser.error("--fresh-calibrations is only supported by 'prepare'")
    if args.step not in ("report", "select") and args.output_dir is not None:
        parser.error("--output-dir is only supported by 'report' or 'select'")
    if args.step == "select" and args.selection_ledger is None:
        parser.error("select requires --selection-ledger")
    if args.step != "select" and (args.selection_ledger is not None or args.source_root is not None):
        parser.error("--selection-ledger and --source-root are only supported by 'select'")
    study = args.study.resolve()
    if args.step == "select":
        select(study, args.selection_ledger.resolve(), args.source_root.resolve() if args.source_root else None,
               args.output_dir.resolve() if args.output_dir else None)
    elif args.step == "prepare":
        prepare(study, args.fresh_calibrations)
    elif args.step == "run":
        run(study, args.max_jobs)
    else:
        report(study, args.output_dir.resolve() if args.output_dir else None)


if __name__ == "__main__":
    main()
