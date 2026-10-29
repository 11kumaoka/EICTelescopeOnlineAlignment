#!/usr/bin/env python3
"""Plot EACH single-cohort 18-DoF estimate and its formal uncertainty.

Input: fit_18d_linearized_summary.json from fit_18d_linearized_scan.py.
No re-fit, no Pede call, no geometry update. PyROOT required inside ePIC Docker.
The x coordinate is N = number of accepted tracks PER FIRST-LINEARIZED FIT.

Gray individual point: (estimated injected shift - true injected shift) +/- formal 1sigma.
Blue mean point: average signed error +/- between-cohort sample SD.
Display jitter in x is only for visibility; saved individual_ graphs have exact x=N.

Usage:
  python3 -B studies/alignment_statistics/plot_18d_individual_cohorts.py \
    --summary result/linearized18/fit_18d_linearized_summary.json \
    --output result/linearized18_individual
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

AXES = ('X', 'Y', 'Z', 'Rx', 'Ry', 'Rz')
LAYERS = (1, 2, 3)
KEYS = [f'L{layer}_{axis}' for layer in LAYERS for axis in AXES]


def check_and_collect(data):
    if data.get('scope') != 'OFFLINE_FIRST_ITERATION_LINEARIZED_18D_NOT_PEDE_OR_GEOMETRY_CORRECTION':
        raise ValueError('Unexpected input scope: need actual first-iteration linearized 18D summary')
    if data.get('truth') is None:
        raise ValueError('Injected truth absent; signed closure errors cannot be plotted')
    if not isinstance(data.get('cohorts'), list):
        raise ValueError('Missing individual cohort results')
    observations = defaultdict(list)
    seen = set()
    for cohort in data['cohorts']:
        n = cohort['N']
        index = cohort['cohort']
        if type(n) is not int or n <= 0 or type(index) is not int or index < 0:
            raise ValueError('Positive integer N and nonnegative integer cohort index required')
        if (n, index) in seen:
            raise ValueError('Duplicate N/cohort identity')
        seen.add((n, index))
        if cohort['rank'] != 18:
            continue
        errors = cohort.get('error_mm_rad')
        sigmas = cohort.get('formal_sigma')
        if not isinstance(errors, dict) or not isinstance(sigmas, list) or len(sigmas) != 18:
            raise ValueError(f'N={n}, cohort={cohort.get("cohort")}: missing estimated error or formal sigma')
        if any(not math.isfinite(float(errors[key])) or not math.isfinite(float(sigmas[i])) or float(sigmas[i]) <= 0
               for i, key in enumerate(KEYS)):
            raise ValueError('Invalid individual closure errors or formal uncertainties')
        observations[n].append((index, errors, sigmas))
    for n, values in observations.items():
        values.sort(key=lambda item: item[0])
        expected = int(data['statistics'][str(n)]['cohorts_successful'])
        if len(values) != expected:
            raise ValueError(f'N={n}: {len(values)} entries, expected {expected}')
    for n, row in data['statistics'].items():
        if len(observations.get(int(n), [])) != row.get('cohorts_successful', 0):
            raise ValueError('Successful-cohort count disagrees with individual records')
    if not observations:
        raise ValueError('No successful individual fits')
    return observations


def plot_rows(data, observations, key):
    """Preserve exact points; separate measured SD from a singleton marker."""
    idx = KEYS.index(key)
    rows, displayed, means, singletons = [], [], [], []
    for n, group in sorted(observations.items()):
        total = len(group)
        for j, (_, errors, sigmas) in enumerate(group):
            error, sigma = float(errors[key])*1000., float(sigmas[idx])*1000.
            rows.append((float(n), error, sigma))
            offset = 0 if total == 1 else 0.14*(j/(total-1)-0.5)
            displayed.append((float(n)*math.exp(offset), error, sigma))
        stat = data['statistics'][str(n)]['parameters'][key]
        mean = float(stat['mean_error'])*1000.
        if not math.isfinite(mean):
            raise ValueError('Nonfinite mean error')
        if total == 1:
            if stat['sample_sd'] is not None:
                raise ValueError('Singleton must not claim a measured sample SD')
            singletons.append((float(n), mean))
        else:
            if stat['sample_sd'] is None:
                raise ValueError('Repeated cohorts require their measured sample SD')
            sd = float(stat['sample_sd'])*1000.
            if not math.isfinite(sd) or sd < 0:
                raise ValueError('Invalid between-cohort sample SD')
            means.append((float(n), mean, sd))
    return rows, displayed, means, singletons


def create_plots(summary, output):
    import ROOT
    ROOT.gROOT.SetBatch(True)
    ROOT.gStyle.SetOptStat(0)
    source = summary.read_bytes()
    source_sha = hashlib.sha256(source).hexdigest()
    data = json.loads(source)
    obs = check_and_collect(data)
    if output.exists():
        raise FileExistsError(f'Output already exists; choose a NEW directory: {output}')
    output.mkdir(parents=True, exist_ok=False)
    rootpath = output / 'individual_18d_linearized.root'
    rootfile = ROOT.TFile(str(rootpath), 'CREATE')
    if not rootfile or rootfile.IsZombie():
        raise IOError(f'Cannot create {rootpath}')
    alive = []
    try:
        for layer in LAYERS:
            canvas = ROOT.TCanvas(f'canvas_L{layer}_individual',
                                  f'Layer {layer}: individual cohorts, first linearized fit', 1550, 960)
            canvas.Divide(3, 2, 0.009, 0.013)
            alive.append(canvas)
            for axisnum, axis in enumerate(AXES):
                key = f'L{layer}_{axis}'
                unit = '#mum' if axisnum < 3 else 'mrad'
                rows, display_rows, means, singleton_rows = plot_rows(data, obs, key)
                limits = [0.0, 0.0]
                for _, value, error in rows + means:
                    limits[0] = min(limits[0], value-error)
                    limits[1] = max(limits[1], value+error)
                def make_graph(name, points, title):
                    g = ROOT.TGraphErrors()
                    g.SetName(name)
                    g.SetTitle(title)
                    for i, (x, y, dy) in enumerate(points):
                        g.SetPoint(i, x, y)
                        g.SetPointError(i, 0.0, dy)
                    alive.append(g)
                    g.Write()
                    return g
                title = f'{key};Number of tracks per fit;Estimated minus injected ({unit})'
                individual = make_graph(f'individual_{key}', rows, title)
                display = make_graph(f'individual_display_{key}', display_rows, title)
                mean = make_graph(f'mean_{key}', means, title)
                singleton = ROOT.TGraph()
                singleton.SetName(f'single_cohort_{key}')
                singleton.SetTitle('One cohort: between-cohort SD is undefined')
                for i, (x, y) in enumerate(singleton_rows):
                    singleton.SetPoint(i, x, y)
                singleton.SetMarkerStyle(21)
                singleton.SetMarkerColor(ROOT.kBlue+1)
                singleton.Write()
                alive.append(singleton)
                display.SetMarkerStyle(24)
                display.SetMarkerSize(0.39)
                display.SetMarkerColor(ROOT.kGray+2)
                display.SetLineColor(ROOT.kGray+1)
                display.SetLineWidth(1)
                mean.SetMarkerStyle(20)
                mean.SetMarkerSize(0.88)
                mean.SetMarkerColor(ROOT.kBlue+1)
                mean.SetLineColor(ROOT.kBlue+1)
                mean.SetLineWidth(2)
                canvas.cd(axisnum+1)
                pad = ROOT.gPad
                pad.SetLeftMargin(0.16)
                pad.SetRightMargin(0.04)
                pad.SetBottomMargin(0.15)
                pad.SetTopMargin(0.09)
                pad.SetGrid(1, 1)
                pad.SetLogx(True)
                spread = max(limits[1]-limits[0], 0.01)
                xmin, xmax = 0.8*min(obs), 1.25*max(obs)
                h = ROOT.TH1F(f'axes_{key}', title, 100, xmin, xmax)
                h.SetDirectory(0)
                h.SetMinimum(limits[0]-0.13*spread)
                h.SetMaximum(limits[1]+0.13*spread)
                h.GetXaxis().SetMoreLogLabels(True)
                h.GetXaxis().SetNoExponent(True)
                h.GetXaxis().SetTitleSize(0.043)
                h.GetYaxis().SetTitleSize(0.043)
                h.GetXaxis().SetLabelSize(0.034)
                h.GetYaxis().SetLabelSize(0.037)
                h.GetYaxis().SetTitleOffset(1.47)
                h.Draw()
                line = ROOT.TLine(xmin, 0.0, xmax, 0.0)
                line.SetLineColor(ROOT.kGray+2)
                line.SetLineStyle(2)
                line.Draw('SAME')
                display.Draw('PZ SAME')
                if mean.GetN() > 0:
                    mean.Draw('PZ SAME')
                if singleton.GetN() > 0:
                    singleton.Draw('P SAME')
                alive.extend((h,line))
                if axisnum == 0:
                    legend = ROOT.TLegend(0.36, 0.69, 0.94, 0.89)
                    legend.SetFillStyle(0)
                    legend.SetBorderSize(0)
                    legend.SetTextSize(0.030)
                    legend.AddEntry(display, 'Individual #pm formal 1#sigma', 'pe')
                    legend.AddEntry(mean, 'Mean #pm across-cohort SD', 'pe')
                    legend.AddEntry(singleton, 'One cohort: SD not estimated', 'p')
                    legend.Draw()
                    alive.append(legend)
            rootfile.cd()
            canvas.Write()
            canvas.SaveAs(str(output/f'L{layer}_individual_signed_error.pdf'))
            canvas.SaveAs(str(output/f'L{layer}_individual_signed_error.png'))
        metadata = ROOT.TObjString(json.dumps({
            'source_json': str(summary),
            'source_json_sha256': source_sha,
            'scope': data['scope'],
            'x': 'accepted tracks per single linearized fit; not iteration count',
            'individual_y': 'estimated alignment shift minus injected truth shift',
            'individual_error_bar': 'formal 1sigma (unscaled), not validated uncertainty coverage',
            'mean_error_bar': 'sample SD only for >=2 full-rank cohorts at fixed N; singleton markers have no measured SD',
            'display_only_x_jitter': 'log-x offset +/-0.07 (roughly 7 percent); exact x in individual_* graphs',
            'selection': 'Only rank-18 cohorts are displayed; unavailable or rank-deficient cohorts remain recorded in source statistics',
            'independence': 'Cohorts are disjoint within N, overlap across N and may come from one simulation campaign; not independent-seed samples',
            'not': 'not the corrected-geometry minus true-geometry closure; not official Millepede-II'
        }, indent=2))
        metadata.Write('metadata_json')
        rootfile.Write()
    finally:
        rootfile.Close()
    for layer in LAYERS:
        for extension in ('pdf', 'png'):
            image = output/f'L{layer}_individual_signed_error.{extension}'
            if not image.is_file() or image.stat().st_size == 0:
                raise OSError('ROOT did not save all requested images; partial outputs retained')
    if summary.read_bytes() != source:
        raise RuntimeError('Input summary changed during plotting; partial outputs retained')
    print('ROOT:', rootpath)
    for layer in LAYERS:
        print('PDF:', output/f'L{layer}_individual_signed_error.pdf')
    print('NOTE: Each gray point is one full-rank cohort FIRST linearized fit; no independent-seed, iteration or coverage claim.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summary = args.summary.resolve()
    if not summary.is_file():
        parser.error(f'Missing summary: {summary}')
    create_plots(summary, args.output.resolve())


if __name__ == '__main__':
    main()
