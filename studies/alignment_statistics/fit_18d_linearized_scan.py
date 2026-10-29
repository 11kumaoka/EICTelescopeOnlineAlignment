#!/usr/bin/env python3
"""Offline 18-DoF linearized alignment statistics from native ACTS mille.json.

Uses measured SiMAPS L0..L4, fixes L0/L4 as survey reference planes,
fits six ACTS global pose derivatives for each of L1,L2,L3, while profiling
four per-track local parameters. No Pede, Geometry updates, or simulations.

The output is a *linearized least-squares diagnostic*, not a validated
Geometry correction/closure. Formal errors assume the input covariance model.
"""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path
import sys

import numpy as np

NS=(100,180,320,580,1050,1900,3420,6160,11100,20000)
LAYERS=(1,2,3)
AXES=('X','Y','Z','Rx','Ry','Rz')
SCALE=np.tile(np.array([.1,.1,.1,.001,.001,.001],float),len(LAYERS))
LABELS=[f'L{lay}_{ax}' for lay in LAYERS for ax in AXES]


def contribution(event):
    equations=event['equations']
    layers=sorted(int(eq['layer']) for eq in equations)
    if layers!=list(range(5)):
        raise ValueError(f'Expected 5 SiMAPS measurements (L0..L4); got {layers}')
    a=np.zeros((10,4));g=np.zeros((10,18));r=np.zeros(10)
    for eq in equations:
        layer=int(eq['layer']);j=2*layer
        w=np.asarray(eq['whitening'],float).reshape((2,2))
        a[j:j+2]=w@np.asarray(eq['local'],float).reshape((2,4))
        if layer in LAYERS:
            k=6*(layer-1)
            g[j:j+2,k:k+6]=(w@np.asarray(eq['global'],float).reshape((2,6)))*SCALE[k:k+6]
        if 'residual' not in eq:
            raise ValueError('Missing residual field: input must be a real first-iteration mille.json')
        r[j:j+2]=w@np.asarray(eq['residual'],float).reshape(2)
    if not all(np.isfinite(x).all() for x in (a,g,r)):
        raise ValueError('Non-finite input values')
    q,rmat=np.linalg.qr(a,mode='reduced')
    if np.linalg.matrix_rank(rmat)!=4:
        raise ValueError('Track-local derivative rank != 4')
    pg=g-q@(q.T@g)
    pr=r-q@(q.T@r)
    return pg.T@pg,pg.T@pr, float(pr@pr)


def checked_input(path):
    """Read one immutable byte snapshot; never silently discard rejected rows.

    A declared accepted-track subset may retain its parent producer's counters
    and failed status. Its declared parent hash is provenance, not an independently
    verified producer binding. Every selected row must still pass explicitly.
    """
    path = Path(path)
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError('Input changed while reading the JSON snapshot')
    data = json.loads(raw)
    events = data.get('events')
    if not isinstance(events, list) or not events:
        raise ValueError('Nonempty native events list required')
    model = data.get('model_sha256')
    if not isinstance(model, str) or not re.fullmatch(r'[0-9a-f]{64}', model):
        raise ValueError('Native model SHA256 required')
    identities = []
    for event in events:
        if not isinstance(event, dict) or event.get('accepted') is not True or event.get('passed') is not True:
            raise ValueError('Every selected event must be explicitly accepted and passed')
        key = [event.get('run_number'), event.get('event_number')]
        if any(type(value) is not int for value in key):
            raise ValueError('Complete integer run/event identities required')
        if event.get('model_sha256') != model:
            raise ValueError('Event and parent model SHA256 disagree')
        equations = event.get('equations')
        if (not isinstance(equations, list) or len(equations) != 5
                or any(not isinstance(eq, dict) or type(eq.get('layer')) is not int for eq in equations)
                or sorted(eq['layer'] for eq in equations) != list(range(5))):
            raise ValueError('Exactly one measured equation on each SiMAPS layer L0..L4 required')
        identities.append(key)
    if len({tuple(key) for key in identities}) != len(identities):
        raise ValueError('Duplicate run/event identity in selected input')
    counts = {key: data.get(key) for key in ('processed', 'accepted', 'records', 'rejected')}
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError('Nonnegative integer native producer counts required')
    if (counts['accepted'] + counts['rejected'] != counts['processed']
            or counts['records'] != counts['accepted']):
        raise ValueError('Inconsistent native producer counts')
    if data.get('passed') is not (counts['rejected'] == 0):
        raise ValueError('Native producer passed flag disagrees with its rejected count')
    filtered = data.get('track_only_selection') is True
    if filtered:
        parent = data.get('source_mille_json_sha256')
        if (type(data.get('requested_tracks')) is not int or data['requested_tracks'] != len(events)
                or counts['accepted'] < len(events)
                or not isinstance(parent, str) or not re.fullmatch(r'[0-9a-f]{64}', parent)
                or type(data.get('passed')) is not bool):
            raise ValueError('Explicit accepted-subset provenance is incomplete or inconsistent')
    elif (data.get('passed') is not True or counts['processed'] != len(events)
            or counts['accepted'] != len(events) or counts['rejected'] != 0):
        raise ValueError('Unfiltered input must be a complete passed all-accepted native report')
    return data, {'input_sha256': hashlib.sha256(raw).hexdigest(),
        'selected_events': len(events), 'event_identities': identities,
        'selection': 'explicit_accepted_track_subset' if filtered else 'complete_all_accepted_native_report',
        'parent_producer_counts': counts, 'parent_producer_passed': data['passed'],
        'declared_parent_report_sha256': data.get('source_mille_json_sha256') if filtered else None,
        'parent_report_binding_independently_verified': False,
        'interpretation': 'Conditional selected-sample diagnostic; not a Controller or producer-integrity certification'}


def truth_snapshot(path):
    """Require all 18 physical components explicitly; no implicit zero Truth."""
    if path is None:
        return None, None
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    if (data.get('units') != 'mm_rad' or not isinstance(data.get('values'), dict)
            or set(data['values']) != set(LABELS)):
        raise ValueError('Truth JSON requires mm_rad and exactly all 18 physical parameter keys')
    values = [data['values'][key] for key in LABELS]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError('Truth values must be finite numbers in mm and rad')
    return np.asarray(values, dtype=float), {'path': str(Path(path).resolve()),
        'sha256': hashlib.sha256(raw).hexdigest(),
        'binding_to_initial_and_truth_geometry': 'Not independently verified by this offline tool'}


def expected_truth(path):
    return truth_snapshot(path)[0]


def solve(info,rhs,cut):
    eig,U=np.linalg.eigh((info+info.T)*.5)
    largest=max(float(eig[-1]),0.)
    active=eig>max(cut*largest,1e-13)
    rank=int(np.count_nonzero(active))
    if rank!=18:
        return {'rank':rank,'eigenvalues':eig.tolist(),'condition':None,
                'estimates':None,'formal_sigma':None},None,None
    condition=float(eig[-1]/eig[0])
    u=U@((U.T@rhs)/eig)
    var=np.maximum(np.sum((U*U)/eig[None,:],axis=1),0.)
    return {'rank':18,'eigenvalues':eig.tolist(),'condition':condition,
            'estimates':(u*SCALE).tolist(),'formal_sigma':(np.sqrt(var)*SCALE).tolist()},u*SCALE,np.sqrt(var)*SCALE


def create_graphs(output,summary):
    try:
        import ROOT
    except ImportError:
        print('PyROOT is absent; no ROOT graphs generated',file=sys.stderr)
        return
    ROOT.gROOT.SetBatch(True)
    f=ROOT.TFile(str(output/'fit_18d_linearized.root'),'RECREATE')
    if f.IsZombie():raise RuntimeError('Failed to create ROOT file')
    ROOT.TNamed('analysis_scope',summary['scope']).Write()
    ROOT.TNamed('truth_available','true' if summary['truth'] is not None else 'false').Write()
    ROOT.TNamed('input_sha256',summary['input_sha256']).Write()
    ROOT.TObjString(json.dumps({'scope':summary['scope'],
        'truth_available':summary['truth'] is not None,
        'input_sha256':summary['input_sha256'],
        'input_provenance':summary.get('input_provenance'),
        'truth_provenance':summary.get('truth_provenance'),
        'uncertainties':'Unscaled first-linearized data-only curvature; no coverage or operational calibration claim'},
        sort_keys=True,allow_nan=False)).Write('metadata_json')
    source={}
    for key in LABELS:
        layer=int(key[1]);axis=key.split('_',1)[1]
        unit_factor=1000.0 # mm to um and rad to mrad
        pts=[(int(n),r) for n,r in summary['statistics'].items()
             if r.get('cohorts_successful',0)>0 and key in r.get('parameters',{})]
        repeated=[(n,row) for n,row in pts if row['cohorts_successful'] > 1]
        single=[(n,row) for n,row in pts if row['cohorts_successful'] == 1]
        gr=ROOT.TGraphErrors();gr.SetName(f'mean_error_{key}' if summary['truth'] is not None else f'mean_estimate_{key}')
        gr.SetTitle(f'{key} mean estimated minus injected;Accepted events per cohort;Mean error ({"um" if axis in ("X","Y","Z") else "mrad"})' if summary['truth'] is not None else f'{key} mean linearized update;Accepted events per cohort;Estimated update ({"um" if axis in ("X","Y","Z") else "mrad"})')
        g2=ROOT.TGraphErrors();g2.SetName(f'rms_error_{key}')
        g2.SetTitle(f'{key} RMS estimated minus injected;Accepted events per cohort;RMS error ({"um" if axis in ("X","Y","Z") else "mrad"})')
        g3=ROOT.TGraphErrors();g3.SetName(f'formal_sigma_{key}')
        g3.SetTitle(f'{key} nominal uncertainty;Accepted events per cohort;Formal sigma ({"um" if axis in ("X","Y","Z") else "mrad"})')
        singleton=ROOT.TGraph();singleton.SetName('single_cohort_'+key)
        singleton.SetTitle('One cohort: between-cohort SD is undefined')
        for k,(n,row) in enumerate(repeated):
            d=row['parameters'][key]
            gr.SetPoint(k,n,(d['mean_error'] if summary['truth'] is not None else d['mean_estimate'])*unit_factor)
            gr.SetPointError(k,0,d['sample_sd']*unit_factor)
        for k,(n,row) in enumerate(single):
            d=row['parameters'][key]
            singleton.SetPoint(k,n,(d['mean_error'] if summary['truth'] is not None else d['mean_estimate'])*unit_factor)
        singleton.SetMarkerStyle(21);singleton.Write()
        for k,(n,row) in enumerate(pts):
            d=row['parameters'][key]
            if summary['truth'] is not None:
                g2.SetPoint(k,n,d['rms_error']*unit_factor)
                g2.SetPointError(k,0,0)
            g3.SetPoint(k,n,d['mean_formal_sigma']*unit_factor)
            g3.SetPointError(k,0,0)
        for g in ((gr,g2,g3) if summary['truth'] is not None else (gr,g3)):
            g.SetMarkerStyle(20);g.SetLineWidth(2);g.Write()
        source[key]=(gr,g2,g3)
    if summary['truth'] is not None:
        can=ROOT.TCanvas('l2_6d_rms','L2 six-DOF residual (linearized)',1200,800)
        can.Divide(3,2)
        for j,key in enumerate(f'L2_{ax}' for ax in AXES):
            can.cd(j+1);ROOT.gPad.SetLogx();ROOT.gPad.SetGrid()
            gr=source[key][1]
            if gr.GetN():gr.Draw('ALP')
        can.Write();can.SaveAs(str(output/'l2_6d_rms.png'))
        can.SaveAs(str(output/'l2_6d_rms.pdf'))
    can=ROOT.TCanvas('l2_6d_formal_sigma','L2 six-DOF formal precision (linearized)',1200,800)
    can.Divide(3,2)
    for j,key in enumerate(f'L2_{ax}' for ax in AXES):
        can.cd(j+1);ROOT.gPad.SetLogx();ROOT.gPad.SetGrid()
        gr=source[key][2]
        if gr.GetN():gr.Draw('ALP')
    can.Write();can.SaveAs(str(output/'l2_6d_formal_sigma.png'))
    can.SaveAs(str(output/'l2_6d_formal_sigma.pdf'))
    f.Write();f.Close()


def run(args):
    inp=Path(args.input).resolve(); out=Path(args.output).resolve()
    if not inp.is_file():raise FileNotFoundError(inp)
    if out.exists():raise FileExistsError(f'Output must be a new directory: {out}')
    if type(args.eigen_cut) not in (int,float) or not math.isfinite(args.eigen_cut) or not 0 < args.eigen_cut < 1:
        raise ValueError('Eigenvalue cutoff must be finite and between zero and one')
    data, provenance=checked_input(inp);events=data['events'];N=len(events)
    counts=sorted(set(int(s) for s in args.counts.split(',')))
    if not counts or min(counts)<1:raise ValueError('All counts must be positive')
    info={n:[(np.zeros((18,18)),np.zeros(18),0.) for _ in range(N//n)] for n in counts}
    if not any(info.values()):raise ValueError('No complete cohort for requested N')
    for i,event in enumerate(events):
        H,b,z=contribution(event)
        for n,cohorts in info.items():
            idx=i//n
            if idx<len(cohorts):
                h0,b0,z0=cohorts[idx]
                h0+=H;b0+=b
                cohorts[idx]=(h0,b0,z0+z)
        if (i+1)%2000==0:print(f'Processed {i+1}/{N} events',flush=True)
    truth, truth_provenance=truth_snapshot(args.truth)
    result={'scope':'OFFLINE_FIRST_ITERATION_LINEARIZED_18D_NOT_PEDE_OR_GEOMETRY_CORRECTION',
            'input':str(inp),'input_sha256':provenance['input_sha256'],
            'input_provenance':provenance, 'truth_provenance':truth_provenance,
            'available_events':N,'observed_layers':[0,1,2,3,4],
            'fixed_reference_layers':[0,4],'fitted_layers':[1,2,3],
            'unmeasured_layers':[5,6],'axes':list(AXES),
            'physical_units':'mm for translation; rad for rotation',
            'parameter_scales':SCALE.tolist(),'relative_eigen_cut':args.eigen_cut,
            'truth':dict(zip(LABELS,truth.tolist())) if truth is not None else None,
            'notes':['The fit is a first-iteration linearized diagnostic using native ACTS derivatives and weighted residuals, not a Pede fit or Geometry update.',
                     'L0 and L4 held fixed, so this is 18 DoF, NOT all 42 DoF; L5 and L6 have no hits in current fit.',
                     'Truth values, if specified, MUST be independently verified for the input Truth and Initial Geometry. All 18 Truth components must be supplied explicitly; the tool does not independently bind them to the producer.',
                     'Covariance uses the supplied hit uncertainties, without chi2 rescaling; it may underestimate empirical scatter.',
                     'Cohorts disjoint within each N, overlapping between N, from one shared Geant4 campaign.'],
            'statistics':{},'cohorts':[]}
    for n,cohorts in info.items():
        rows=[];success=[]
        for j,(H,b,z) in enumerate(cohorts):
            sol,p,sig=solve(H,b,args.eigen_cut)
            entry={'N':n,'cohort':j,'start_ordinal':j*n,'stop_ordinal_exclusive':(j+1)*n,
                   'event_identities_sha256':hashlib.sha256(json.dumps(provenance['event_identities'][j*n:(j+1)*n],separators=(',',':')).encode()).hexdigest(),'rank':sol['rank'],'condition':sol['condition'],
                   'estimates':sol['estimates'],'formal_sigma':sol['formal_sigma']}
            if p is not None:
                entry['estimated_update_mm_rad']=dict(zip(LABELS,p.tolist()))
                if truth is not None:
                    entry['error_mm_rad']=dict(zip(LABELS,(p-truth).tolist()))
                success.append((p,sig,sol['condition']))
            rows.append(entry);result['cohorts'].append(entry)
        if not cohorts:
            result['statistics'][str(n)]={'replicas':0,'reason':f'Only {N} measured events available'}
            print(f'N={n}: insufficient events ({N})');continue
        report={'replicas':len(rows),'cohorts_successful':len(success),
                'rank_min':min(r['rank'] for r in rows),
                'rank_max':max(r['rank'] for r in rows),
                'condition_max':max((r['condition'] for r in rows if r['condition'] is not None),default=None)}
        if success:
            ps=np.array([q[0] for q in success]);sigmas=np.array([q[1] for q in success]);ref=truth if truth is not None else np.zeros(18)
            errs=ps-ref
            report['parameters']={key:{'mean_estimate':float(np.mean(ps[:,k])),
                'mean_error':float(np.mean(errs[:,k])),
                'rms_error':float(np.sqrt(np.mean(errs[:,k]**2))) if truth is not None else None,
                'sample_sd':float(np.std(errs[:,k],ddof=1)) if len(success)>1 else None,
                'mean_formal_sigma':float(np.mean(sigmas[:,k]))}
                for k,key in enumerate(LABELS)}
        result['statistics'][str(n)]=report
        print(f'N={n:<6} cohorts={len(rows):<3} full-rank 18D fits={len(success):<3} rank {report["rank_min"]}..{report["rank_max"]}',flush=True)
    out.mkdir(parents=True,exist_ok=False)
    (out/'fit_18d_linearized_summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    create_graphs(out,result)
    print(f'SAVED: {out}')
    print('CAUTION: first-iteration 18D linearized fit with L0/L4 fixed, not a 42D/Pede Geometry correction')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--counts',default=','.join(map(str,NS)))
    ap.add_argument('--truth',help='Optional verified injected physical truth, JSON with mm and rad')
    ap.add_argument('--eigen-cut',type=float,default=1e-10)
    args=ap.parse_args()
    if not 0<args.eigen_cut<1:ap.error('--eigen-cut must be in (0,1)')
    run(args)

if __name__=='__main__':main()
