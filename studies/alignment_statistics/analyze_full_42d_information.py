#!/usr/bin/env python3
"""Offline 42-DoF ACTS derivative information scan; does NOT fit/apply corrections.

Input is an existing TelescopeMille mille.json with all six ACTS global
alignment derivatives for each of the five measured SiMAPS layers (L0--L4).
MPGD L5 and AC-LGAD L6 have no measured equations in the current workflow;
their information columns are correctly zero. No source files are changed.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from studies.alignment_statistics.fit_18d_linearized_scan import checked_input

DEFAULT_N = (100, 180, 320, 580, 1050, 1900, 3420, 6160, 11100, 20000)
AXES = ('X', 'Y', 'Z', 'Rx', 'Ry', 'Rz')


def single_information(event):
    rows = event['equations']
    if len(rows) != 5 or sorted(int(e['layer']) for e in rows) != list(range(5)):
        raise ValueError('Expected exactly five measured SiMAPS layers 0..4')
    local = np.zeros((10, 4), dtype=np.float64)
    global_ = np.zeros((10, 42), dtype=np.float64)
    for eq in rows:
        layer = int(eq['layer'])
        w = np.asarray(eq['whitening'], dtype=np.float64).reshape(2, 2)
        a = np.asarray(eq['local'], dtype=np.float64).reshape(2, 4)
        g = np.asarray(eq['global'], dtype=np.float64).reshape(2, 6)
        local[2*layer:2*layer+2] = w @ a
        # Parameter coordinates are mm (X/Y/Z) and rad (Rx/Ry/Rz).
        global_[2*layer:2*layer+2, 6*layer:6*layer+6] = w @ g
    if not (np.isfinite(local).all() and np.isfinite(global_).all()):
        raise ValueError('Nonfinite ACTS linearized derivatives')
    q, r = np.linalg.qr(local, mode='reduced')
    if np.linalg.matrix_rank(r) != 4:
        raise ValueError('Local four-parameter track model is rank deficient')
    pg = global_ - q @ (q.T @ global_)
    h = pg.T @ pg
    return (h + h.T) / 2


def effective_rank(h, tolerance):
    scale = np.sqrt(np.maximum(np.diag(h), 0.))
    active = scale > 0
    normalized = np.zeros_like(h)
    normalized[np.ix_(active, active)] = (h[np.ix_(active, active)] /
                                              np.outer(scale[active], scale[active]))
    eigen = np.linalg.eigvalsh((normalized + normalized.T) / 2)
    ceiling = max(float(np.max(eigen)), 0.)
    rank = int(np.count_nonzero(eigen > tolerance * ceiling)) if ceiling else 0
    return rank, eigen, active


def run(args):
    root = Path(args.input).resolve()
    output = Path(args.output).resolve()
    if not root.is_file():
        raise FileNotFoundError(root)
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite: {output}')
    if (type(args.relative_eigen_cut) not in (int,float) or not math.isfinite(args.relative_eigen_cut)
            or not 0 < args.relative_eigen_cut < 1):
        raise ValueError('Relative eigenvalue cutoff must be finite and between zero and one')
    groups, provenance = checked_input(root)
    events = groups['events']
    total = len(events)
    if total == 0:
        raise ValueError('Empty events list')
    counts = sorted({int(x) for x in args.counts.split(',')})
    if not counts or counts[0] < 1:
        raise ValueError('Positive event counts required')
    entries = {n: [np.zeros((42, 42), dtype=np.float64) for _ in range(total // n)]
               for n in counts}
    if not any(entries.values()):
        raise ValueError(f'No requested cohorts fit within {total} measured events')
    for i, ev in enumerate(events):
        h = single_information(ev)
        for n, cohorts in entries.items():
            j = i // n
            if j < len(cohorts):
                cohorts[j] += h
        if (i + 1) % 2000 == 0:
            print(f'Processed {i+1}/{total} event derivatives', flush=True)
    for n, cohorts in entries.items():
        if cohorts:
            print(f'N={n:<6} cohorts={len(cohorts)}', flush=True)
        else:
            print(f'N={n:<6} NOT_AVAILABLE (requires {n} measured events, available {total})')

    stats = {}
    all_rows = []
    for n, cohorts in entries.items():
        rows = []
        for i, h in enumerate(cohorts):
            rank, eig, active = effective_rank(h, args.relative_eigen_cut)
            zero = [f'L{j//6}_{AXES[j%6]}' for j in range(42) if not active[j]]
            row = {'N': n, 'replica': i, 'start_ordinal':i*n, 'stop_ordinal_exclusive':(i+1)*n,
                   'event_identities_sha256':hashlib.sha256(json.dumps(provenance['event_identities'][i*n:(i+1)*n],separators=(',',':')).encode()).hexdigest(), 'rank': rank,
                   'nullity': 42-rank, 'zero_derivative_parameters': zero,
                   'minimum_positive_eigenvalue': float(np.min(eig[eig > args.relative_eigen_cut * max(float(np.max(eig)),0.)])) if rank else None,
                   'maximum_eigenvalue': float(np.max(eig)),
                   'relative_eigen_cut': args.relative_eigen_cut}
            rows.append(row)
            all_rows.append(row)
        if rows:
            rank_values = [r['rank'] for r in rows]
            stats[str(n)] = {'replicas': len(rows), 'rank_min': min(rank_values),
                             'rank_max': max(rank_values),
                             'rank_mean': float(np.mean(rank_values)),
                             'rank_sample_sd': float(np.std(rank_values, ddof=1)) if len(rows)>1 else None,
                             'rank_first': rank_values[0]}
        else:
            stats[str(n)] = {'replicas': 0, 'reason': f'Available {total} events < requested {n}'}
    output.mkdir(parents=True, exist_ok=False)
    result = {'scope': 'OFFLINE_42D_INFORMATION_ONLY_NOT_A_PEDE_FIT',
              'input': str(root), 'input_sha256': provenance['input_sha256'],
              'input_provenance': provenance,
              'actual_equation_layers': [0,1,2,3,4],
              'unmeasured_layers': [5,6],
              'parameter_axes_per_layer': AXES,
              'total_parameters': 42,
              'measured_events': total,
              'relative_eigen_cut': args.relative_eigen_cut,
              'notes': [
                  'No new Geant4 simulation, tracking reconstruction, Pede solution or Geometry Correction was performed.',
                  'Only the five SiMAPS planes have measured ACTS equations. L5/L6 contribute zero information.',
                  'Global gauge modes may be unobservable; a matrix rank result does not imply physical accuracy or parameter identifiability under real material effects.',
                  'The derivatives are at the existing reconstruction Geometry, NOT at an all-42-DOF-perturbed Truth Geometry.',
                  'Replicas are disjoint within each N, but overlap across N and originate from one simulation campaign.',
              ], 'statistics': stats, 'cohorts': all_rows}
    (output/'information_42d_summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    try:
        import ROOT
        ROOT.gROOT.SetBatch(True)
        f = ROOT.TFile(str(output/'information_42d.root'), 'RECREATE')
        if f.IsZombie():
            raise RuntimeError('Unable to create ROOT output')
        gr = ROOT.TGraph()
        gr.SetName('first_cohort_rank')
        gr.SetTitle('Full 42-DoF information rank;Accepted SiMAPS tracks per cohort;Observable linearized modes / 42')
        gmean = ROOT.TGraphErrors()
        singleton = ROOT.TGraph()
        singleton.SetName('single_cohort_rank')
        singleton.SetTitle('Single-cohort rank; no between-cohort SD is estimated')
        gmean.SetName('mean_cohort_rank')
        gmean.SetTitle('Full 42-DoF information rank;Accepted SiMAPS tracks per cohort;Mean rank (SD)')
        mean_index=0; single_index=0
        for k,n in enumerate(n for n in counts if stats[str(n)]['replicas']):
            d = stats[str(n)]
            gr.SetPoint(k, n, d['rank_first'])
            if d['replicas'] > 1:
                gmean.SetPoint(mean_index,n,d['rank_mean'])
                gmean.SetPointError(mean_index, 0, d['rank_sample_sd'])
                mean_index += 1
            else:
                singleton.SetPoint(single_index,n,d['rank_mean'])
                single_index += 1
        for g in (gr,gmean,singleton):
            g.SetMarkerStyle(20)
            g.Write()
        canvas = ROOT.TCanvas('information_rank_canvas', '42D information rank', 900,600)
        canvas.SetLogx()
        canvas.SetGrid()
        gr.Draw('AP')
        gr.GetYaxis().SetRangeUser(0,42)
        if gmean.GetN() > 0:
            gmean.Draw('P SAME')
        singleton.SetMarkerStyle(21)
        if singleton.GetN() > 0:
            singleton.Draw('P SAME')
        canvas.Write()
        canvas.SaveAs(str(output/'information_42d_rank.png'))
        f.Close()
    except ImportError:
        print('WARNING: PyROOT not installed; JSON created without ROOT plot', file=sys.stderr)
    print(f'SAVED: {output}')
    print('IMPORTANT: This is a 42-DoF INFORMATION/RANK study, not an all-DOF alignment fit.')
    for n in counts:
        s = stats[str(n)]
        if s['replicas']:
            print(f"N={n:6d}: {s['replicas']:3d} cohorts, rank {s['rank_min']}..{s['rank_max']} / 42")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Existing native first-iteration mille.json')
    parser.add_argument('--output', required=True, help='Brand-new output directory')
    parser.add_argument('--counts', default=','.join(map(str,DEFAULT_N)))
    parser.add_argument('--relative-eigen-cut', type=float, default=1e-8)
    args=parser.parse_args()
    if not (0 < args.relative_eigen_cut < 1):
        parser.error('relative eigen cut must be between 0 and 1')
    run(args)

if __name__=='__main__':
    main()
