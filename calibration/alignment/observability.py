#!/usr/bin/env python3
"""Read-only tilt sensitivity from real native ACTS rows, without enabling a fit mode."""
import argparse
import json
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research_common import write_json,digest


def analyze(native):
    data=json.loads(native.read_text())
    columns=[1,2,3,4,5]
    names=['Global Y mm','Global Z mm','Global Rx rad','Global Ry rad','Global Rz rad']
    contributions=[];responses=[];slopes=[];positions=[];residuals=[];extended=[]
    for event in data['events']:
        a=np.concatenate([np.array(e['whitening'])@e['local'] for e in event['equations']])
        g=np.concatenate([np.array(e['whitening'])@np.array(e['global'])[:,columns]
                          if e['layer']==1 else np.zeros((2,5)) for e in event['equations']])
        r=np.concatenate([np.array(e['whitening'])@(np.array(e['measurement']).ravel()-np.array(e['prediction']).ravel()) for e in event['equations']])
        assert np.linalg.matrix_rank(a)==4
        for e in event['equations']:
            w=np.array(e['whitening']);c=np.array(e['covariance'])
            assert np.allclose(w@c@w.T,np.eye(2),atol=1e-12)
        q,_=np.linalg.qr(a);p=np.eye(10)-q@q.T
        contributions.append(g.T@p@g);responses.append(g.T@p);residuals.append(r)
        all_columns=np.concatenate([np.array(e['whitening'])@np.array(e['global'])
            if e['layer']==1 else np.zeros((2,6)) for e in event['equations']])
        extended.append(all_columns.T@p@all_columns)
        direction=np.array(event['reference_direction']);slopes.append(direction[1:]/direction[0])
        positions.append(next(e['measurement'] for e in event['equations'] if e['layer']==1))
    result={}
    for count in sorted(set([min(len(contributions),k) for k in [16,32,64,128,256,512,1024,2048,4096]])):
        h=sum(contributions[:count]);cov=np.linalg.inv(h);sigma=np.sqrt(cov.diagonal())
        scaled=h/np.sqrt(np.outer(h.diagonal(),h.diagonal()))
        joint=h[np.ix_([3,4],[3,4])];joint_cov=np.linalg.inv(joint);joint_sigma=np.sqrt(joint_cov.diagonal())
        joint_report={'rank':int(np.linalg.matrix_rank(joint)),'sigma_rad':joint_sigma.tolist(),
            'information':joint.tolist(),'covariance_rad2':joint_cov.tolist(),
            'correlation':(joint_cov/np.outer(joint_sigma,joint_sigma)).tolist(),
            'diagonal_scaled_condition':float(np.linalg.cond(joint/np.sqrt(np.outer(joint.diagonal(),joint.diagonal()))))}
        rotation_h=h[2:,2:];rotation_cov=np.linalg.inv(rotation_h);rotation_sigma=np.sqrt(rotation_cov.diagonal())
        rotation_report={'rank':int(np.linalg.matrix_rank(rotation_h)),'sigma_rad':rotation_sigma.tolist(),
            'information':rotation_h.tolist(),'covariance_rad2':rotation_cov.tolist(),
            'correlation':(rotation_cov/np.outer(rotation_sigma,rotation_sigma)).tolist(),
            'diagonal_scaled_condition':float(np.linalg.cond(rotation_h/np.sqrt(np.outer(rotation_h.diagonal(),rotation_h.diagonal()))))}
        indices=[0,2,3,4];four_h=h[np.ix_(indices,indices)];four_cov=np.linalg.inv(four_h);four_sigma=np.sqrt(four_cov.diagonal())
        four_report={'parameters':['Global Y mm','Global Rx rad','Global Ry rad','Global Rz rad'],
            'rank':int(np.linalg.matrix_rank(four_h)),'sigma':four_sigma.tolist(),'information':four_h.tolist(),
            'covariance_mixed_units':four_cov.tolist(),'correlation':(four_cov/np.outer(four_sigma,four_sigma)).tolist(),
            'diagonal_scaled_condition':float(np.linalg.cond(four_h/np.sqrt(np.outer(four_h.diagonal(),four_h.diagonal()))))}
        result[str(count)]={'selected_Y_Rx_Ry_Rz':four_report,'selected_Rx_Ry_Rz':rotation_report,'selected_Ry_Rz':joint_report,'rank':int(np.linalg.matrix_rank(h)),'diagonal_scaled_condition':float(np.linalg.cond(scaled)),
            'sigma':sigma.tolist(),'single_parameter_sigma':(1/np.sqrt(h.diagonal())).tolist(),
            'correlation':(cov/np.outer(sigma,sigma)).tolist(),'information':h.tolist(),
            'worst_case_independent_pixel_bound':(sum(np.abs(cov@b).sum(axis=1) for b in responses[:count])*np.sqrt(3)).tolist(),
            'linearized_five_column_displacement':(cov@sum(b@r for b,r in zip(responses[:count],residuals[:count]))).tolist()}
    slopes=np.array(slopes);positions=np.array(positions).reshape(-1,2)
    subsets={}
    for name,quantity in [('slope_magnitude',np.linalg.norm(slopes,axis=1)),
                          ('impact_radius',np.linalg.norm(positions,axis=1))]:
        median=float(np.median(quantity))
        for side,mask in [('lower',quantity<=median),('upper',quantity>median)]:
            h=sum(v for v,take in zip(contributions,mask) if take)
            sigma=np.sqrt(np.diag(np.linalg.inv(h)))
            subsets[name+'_'+side]={'tracks':int(mask.sum()),'median_split':median,
                'sigma':sigma.tolist(),'Ry_information_per_track':float(h[3,3]/mask.sum()),'Rz_information_per_track':float(h[4,4]/mask.sum())}
    full=sum(extended);full_cov=np.linalg.inv(full);full_sigma=np.sqrt(full_cov.diagonal())
    extended_report={'parameters':['Global X mm','Global Y mm','Global Z mm','Global Rx rad','Global Ry rad','Global Rz rad'],
        'rank':int(np.linalg.matrix_rank(full)),'sigma':full_sigma.tolist(),
        'diagonal_scaled_condition':float(np.linalg.cond(full/np.sqrt(np.outer(full.diagonal(),full.diagonal())))),
        'correlation':(full_cov/np.outer(full_sigma,full_sigma)).tolist(),
        'scope':'Additional longitudinal-translation correlation diagnostic only; no six-DOF Pede mode enabled'}
    return {'source':str(native),'source_sha256':digest(native),'model_sha256':data['model_sha256'],
        'individual_Y_Rx_Ry_Rz_information_ranks':{str(k):sum(int(np.linalg.matrix_rank(h[np.ix_([0,2,3,4],[0,2,3,4])],tol=1e-9*max(1.,np.linalg.norm(h)))==k) for h in contributions) for k in (0,1,2,3,4)},
        'individual_Rx_Ry_Rz_information_ranks':{str(k):sum(int(np.linalg.matrix_rank(h[2:,2:],tol=1e-9*max(1.,np.linalg.norm(h)))==k) for h in contributions) for k in (0,1,2,3)},
        'individual_Ry_Rz_information_ranks':{str(k):sum(int(np.linalg.matrix_rank(h[np.ix_([3,4],[3,4])],tol=1e-9*max(1.,np.linalg.norm(h)))==k) for h in contributions) for k in (0,1,2)},
        'parameters':names,'accepted_tracks':len(contributions),'prefix_designs':result,'slope_position_subsets':subsets,
        'slopes_min':slopes.min(axis=0).tolist(),'slopes_max':slopes.max(axis=0).tolist(),
        'slopes_rms':np.sqrt((slopes**2).mean(axis=0)).tolist(),
        'L1_local_position_min_mm':positions.min(axis=0).tolist(),'L1_local_position_max_mm':positions.max(axis=0).tolist(),
        'method':'Original calibrated covariance; actual ACTS global columns; QR elimination of four local nuisance parameters per track. Five L1 global columns compared, not enabled for Pede.',
        'uncertainty_coverage_validated':False,'truth_used_for_inference':False,'all_translation_diagnostic':extended_report}


def frame_mapping(legacy_path,native_path):
    """Physical pose mapping, distinct from the Writer's comparison-only world roll."""
    from geometry.transforms import rotation_exp
    old=json.loads(legacy_path.read_text())['layers']['1']
    new=json.loads(native_path.read_text())['sensors'][1]
    d_old=np.array(old['sensor_basis_columns']).T
    d_new=np.array(new['dd4hep_rotation'])
    q=d_new@d_old.T
    assert np.allclose(q.T@q,np.eye(3),atol=1e-12) and abs(np.linalg.det(q)-1)<1e-12
    expected=np.array([[0,0,1],[-1,0,0],[0,-1,0]])
    assert np.allclose(q,expected,atol=1e-12)
    assert np.allclose(q@old['center_mm']+[250,0,0],new['center_mm'],atol=1e-10)
    errors=[]
    for axis in np.eye(3):
        for angle in (-.01,.003):
            lhs=q@np.array(rotation_exp(axis*angle))@q.T
            rhs=np.array(rotation_exp(q@axis*angle))
            errors.append(float(np.max(np.abs(lhs-rhs))))
    assert max(errors)<1e-12
    return {'legacy_source_sha256':digest(legacy_path),'native_source_sha256':digest(native_path),
        'legacy_DD4hep_to_global':d_old.tolist(),'native_DD4hep_to_global':d_new.tolist(),
        'legacy_global_to_native_global':q.tolist(),'origin_shift_mm':[250,0,0],
        'native_ACTS_to_global':new['acts_rotation'],
        'native_ACTS_to_DD4hep':(d_new.T@new['acts_rotation']).tolist(),
        'rotation_conjugation_max_error':max(errors),
        'physical_mapping':'legacy +Rx -> native -Ry; legacy +Ry -> native -Rz; legacy +Rz -> native +Rx',
        'writer_comparison_only_mapping':[[0,0,1],[0,1,0],[-1,0,0]],
        'pivot':'corresponding current sensitive center; active right-handed left-Global rotation, radians'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--legacy-geometry',type=Path);p.add_argument('--native-geometry',type=Path)
    a=p.parse_args()
    if bool(a.legacy_geometry)!=bool(a.native_geometry):p.error('Both actual pose reports are required')
    if a.output.exists():p.error('Output must be new')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    report=analyze(a.native)
    if a.legacy_geometry:report['frames']=frame_mapping(a.legacy_geometry,a.native_geometry)
    write_json(a.output,report)

if __name__=='__main__':main()
