#!/usr/bin/env python3
"""Offline Global XYZ RecHit-minus-linked-SimHit comparison, with exact Raw matching.

No nearest-neighbour, fitted-track or MCParticle matching is used. A cell with
several official SimHit contributors remains several explicitly counted links;
the single-contributor distributions are additional, separately named diagnostics.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4194304), b""):
            value.update(block)
    return value.hexdigest()


def raw_values(hit):
    return int(hit.getCellID()), int(hit.getCharge()), int(hit.getTimeStamp())


def object_id(value):
    identifier = value.getObjectID()
    return int(identifier.collectionID), int(identifier.index)


def header_values(header):
    return (int(header.getRunNumber()), int(header.getEventNumber()), int(header.getTimeStamp()),
            float(header.getWeight()), tuple(float(value) for value in header.getWeights()))


def position(hit):
    point = hit.getPosition()
    result = tuple(float(getattr(point, axis)) for axis in "xyz")
    if not all(math.isfinite(value) for value in result):
        raise ValueError("Nonfinite Global hit position")
    return result


def bridge_raw(reconstructed, digitized):
    """Exact integer bridge between independent PODIO clones, without Truth."""
    by_cell = {}
    for value in digitized:
        if value[0] in by_cell:
            raise ValueError("Ambiguous digitized Raw cell")
        by_cell[value[0]] = value
    if len(set(value[0] for value in reconstructed)) != len(reconstructed):
        raise ValueError("Duplicate reconstructed Raw cell")
    for value in reconstructed:
        if by_cell.get(value[0]) != value:
            raise ValueError("Raw CellID/charge/time differs from digitization")
    return by_cell


def reconstruction(path):
    from podio import reading
    result = {}
    reader = reading.get_reader(str(path))
    for frame in reader.get("events"):
        headers = frame.get("EventHeader")
        if len(headers) != 1:
            raise ValueError("Exactly one EventHeader is required")
        header = header_values(headers[0]); identity = header[:2]
        if identity in result:
            raise ValueError("Duplicate reconstructed run/event identity")
        channels = frame.get("TelescopeChannels")
        raw_ids = {object_id(hit): raw_values(hit) for hit in channels}
        if len(raw_ids) != 5:
            raise ValueError("Expected five unique saved RawHits per event")
        hits = {}
        for hit in frame.get("TelescopeHits"):
            raw = hit.getRawHit()
            if not raw.isAvailable() or raw_ids.get(object_id(raw)) != raw_values(raw):
                raise ValueError("RecHit RawHit relation does not identify a saved input channel")
            values = raw_values(raw)
            if values[0] in hits:
                raise ValueError("Duplicate reconstructed hit for one Raw cell")
            hits[values[0]] = {"raw": values, "position": position(hit)}
        if len(hits) != 5:
            raise ValueError("Expected five RecHits; no silent hit-level selection")
        result[identity] = {"header": header, "hits": hits}
    if not result:
        raise ValueError("No reconstructed events")
    return result


def compare(digitized, before, after, provenance, output):
    import ROOT
    from podio import reading
    ROOT.gROOT.SetBatch(True)
    if output.exists():
        raise FileExistsError(output)
    inputs = {str(path): sha(path) for path in (digitized, before, after, provenance)}
    metadata = json.loads(provenance.read_text())
    if metadata["digitized_root"]["sha256"] != inputs[str(digitized)]:
        raise ValueError("Digitization ROOT/provenance mismatch")
    first, second = reconstruction(before), reconstruction(after)
    if first.keys() != second.keys():
        raise ValueError("Before/after event identities differ")
    for identity, value in first.items():
        other = second[identity]
        if value["header"] != other["header"] or {
                cell: hit["raw"] for cell, hit in value["hits"].items()} != {
                cell: hit["raw"] for cell, hit in other["hits"].items()}:
            raise ValueError("Before/after complete EventHeader or RawHit values differ")
    contract = metadata["readout_contract"]
    field = contract["fields"]["layer"]
    volumes = {sensor["layer"]: sensor["volume_id"] for sensor in contract["sensors"]}
    residuals = []; contributor_counts = Counter(); matched = set(); scanned = 0
    reader = reading.get_reader(str(digitized))
    for frame in reader.get("events"):
        scanned += 1
        headers = frame.get("EventHeader")
        if len(headers) != 1:
            raise ValueError("Digitization needs exactly one EventHeader")
        header = header_values(headers[0]); identity = header[:2]
        if identity not in first:
            continue
        if identity in matched or first[identity]["header"] != header:
            raise ValueError("Duplicate or mismatched digitized event identity/header")
        raw_hits = frame.get("TelescopeSiMAPSRawHits")
        bridge_raw([hit["raw"] for hit in first[identity]["hits"].values()],
                   [raw_values(hit) for hit in raw_hits])
        by_id = {object_id(hit): raw_values(hit) for hit in raw_hits}
        links = {}
        for link in frame.get("TelescopeSiMAPSRawHitLinks"):
            raw, sim = link.getFrom(), link.getTo()
            if not raw.isAvailable() or not sim.isAvailable() or object_id(raw) not in by_id:
                raise ValueError("Unavailable or unknown official RawHit-to-SimHit relation")
            values = by_id[object_id(raw)]
            if values != raw_values(raw) or int(sim.getCellID()) != values[0]:
                raise ValueError("Official RawHit-to-SimHit link CellID mismatch")
            links.setdefault(values[0], []).append((position(sim), float(link.getWeight())))
        for cell, hit in first[identity]["hits"].items():
            layer = (cell >> field["offset"]) & ((1 << field["width"]) - 1)
            if layer not in volumes or cell & contract["volume_mask"] != volumes[layer]:
                raise ValueError("CellID does not identify an audited SiMAPS sensor")
            contributors = links.get(cell, [])
            if not contributors:
                raise ValueError("Selected RawHit has no official SimHit contributor")
            contributor_counts[(layer, len(contributors))] += 1
            for sim, weight in contributors:
                if not math.isfinite(weight) or weight <= 0:
                    raise ValueError("Invalid official association weight")
                residuals.append({"run": identity[0], "event": identity[1], "cell_id": cell,
                    "layer": layer, "contributors": len(contributors), "official_link_weight": weight,
                    "before_mm": [a-b for a, b in zip(hit["position"], sim)],
                    "after_mm": [a-b for a, b in zip(second[identity]["hits"][cell]["position"], sim)]})
        matched.add(identity)
        if len(matched) == len(first):
            break
    if matched != first.keys():
        raise ValueError("Some reconstructed events are absent from digitization")
    output.mkdir(parents=True)
    file = ROOT.TFile(str(output / "hit_residuals.root"), "CREATE")
    if not file or file.IsZombie():
        raise OSError("Cannot create ROOT output")
    histograms = {}; canvases = []
    try:
        for layer in range(5):
            for kind in ("all_links", "single_contributor"):
                for stage in ("before", "after"):
                    for axis in "xyz":
                        name = f"residual_{kind}_{stage}_L{layer}_{axis}"
                        histograms[(layer, kind, stage, axis)] = ROOT.TH1D(name,
                            f"L{layer} {stage}, {kind};Global {axis.upper()} RecHit - linked SimHit [mm];Links", 200, -.5, .5)
        for row in residuals:
            for stage in ("before", "after"):
                for index, axis in enumerate("xyz"):
                    histograms[(row["layer"], "all_links", stage, axis)].Fill(row[stage + "_mm"][index])
                    if row["contributors"] == 1:
                        histograms[(row["layer"], "single_contributor", stage, axis)].Fill(row[stage + "_mm"][index])
        for histogram in histograms.values():
            histogram.Write()
        for layer in range(5):
            canvas = ROOT.TCanvas(f"canvas_L{layer}", f"L{layer}: exact linked hit residuals", 1200, 400)
            canvas.Divide(3, 1)
            for index, axis in enumerate("xyz", 1):
                canvas.cd(index)
                old = histograms[(layer, "all_links", "before", axis)]
                new = histograms[(layer, "all_links", "after", axis)]
                old.SetLineColor(ROOT.kRed); new.SetLineColor(ROOT.kBlue)
                old.Draw("HIST"); new.Draw("HIST SAME")
                ROOT.gPad.BuildLegend(.55, .7, .9, .9)
            canvas.Write(); canvases.append(canvas)
        report = {"passed": True, "events": len(first), "raw_cells": 5*len(first),
            "simhit_links": len(residuals), "scanned_digitized_events": scanned,
            "input_sha256": inputs, "analysis_source_sha256": sha(Path(__file__)),
            "digitizer_conditions": metadata["conditions"],
            "matching": "exact run/event + full RawHit CellID/charge/time; actual PODIO RawHit-to-SimHit links",
            "coordinates": "Global XYZ millimetres for RecHit and SimHit; RecHit - SimHit",
            "histogram_weighting": "one entry per actual official link; stored official weights recorded, not reinterpreted as probabilities",
            "multiple_contributors": [{"layer": layer, "contributors": count, "raw_cells": number}
                for (layer, count), number in sorted(contributor_counts.items())],
            "histogram_range_mm": [-.5, .5], "overflow_included_in_entries": True,
            "truth_used_for_offline_validation": True, "truth_used_for_alignment_inference": False,
            "physics_validation": "not_validated", "note": "Finite sensor depth, multiple Geant4 deposits and digitization affect these distributions; they are not track-fit residuals or an uncertainty-coverage test"}
        ROOT.TObjString(json.dumps(report, indent=2)).Write("analysis_metadata_json")
        file.Write()
    finally:
        file.Close()
    if inputs != {name: sha(Path(name)) for name in inputs}:
        raise RuntimeError("An immutable analysis input changed")
    with (output / "report.json").open("x") as stream:
        json.dump(report, stream, indent=2); stream.write("\n")
    print(f"EXACT_HIT_COMPARISON: {len(first)} events, {len(residuals)} actual SimHit links; {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digitized", type=Path, required=True)
    parser.add_argument("--digitization-provenance", type=Path)
    parser.add_argument("--before-reco", type=Path, required=True)
    parser.add_argument("--after-reco", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    compare(args.digitized.resolve(), args.before_reco.resolve(), args.after_reco.resolve(),
            (args.digitization_provenance or args.digitized.parent / "provenance.json").resolve(),
            args.output_dir.resolve())


if __name__ == "__main__":
    main()
