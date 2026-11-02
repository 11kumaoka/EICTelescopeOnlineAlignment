"""Configured SiMAPS selection and geometry checks using the native model."""
import json
import math
from pathlib import Path


from calibration.alignment.sensors import (MODE_CONFIG, is_multi, config_name, configuration, aligned_sensors, references, labels, mapping, pose_increments, normalize_expanded_envelopes, xml_identity)















def fixture(mode):
    selected=labels(mode)
    sign=-1 if mode.endswith('_negative') else 1
    shifts={1:.080,2:-.060,3:.050}
    vectors={1:(.001,.0005,-.0005),2:(-.0012,.0006,.0006),3:(.0008,-.0004,.0004)}
    dy={layer:shifts[layer] for k in selected for layer,c in [mapping(k)] if c==1}
    dw={layer:[sign*v for v in vectors[layer]] for k in selected for layer,c in [mapping(k)] if c>=3}
    return dy,dw




def injection(mode):
    shifts,rotations=fixture(mode)
    return {k:(shifts[layer] if column==1 else rotations[layer][column-3])
            for k in labels(mode) for layer,column in [mapping(k)]}




def pose_gate(args):
    """Real-constructor simultaneous left composition and inverse per sensor."""
    import json
    import numpy as np
    from research_common import stage,execute,done,write_json
    from validation.telescope_closure.closure import displaced_xml,environment
    from validation.telescope_closure.rotation import pose_checks
    p=stage(args.result,'coupled_pose')
    base=json.loads((args.result/'data/control/channels_truth.json').read_text())['sensors']
    shifts,rotations=fixture(args.mode)
    truth=json.loads((args.result/'data/shifted/channels_truth.json').read_text())['sensors']
    pose_checks(base,truth,shifts,rotations,axis='xyz')
    parent=args.result/'geometry/fixtures/nominal.xml';previous=base
    cross_shifts={1:-.015,2:.020};cross_rotations={1:[-.0003,.0004,.0002]}
    if 2 in rotations:cross_rotations[2]=[.0002,-.0003,.0004]
    if 3 in shifts:cross_shifts[3]=-.010
    if 3 in rotations:cross_rotations[3]=[-.0002,.0003,-.0001]
    chain=[(shifts,rotations),(cross_shifts,cross_rotations),
           ({l:-v for l,v in cross_shifts.items()},{l:[-v for v in w] for l,w in cross_rotations.items()}),
           ({l:-v for l,v in shifts.items()},{l:[-v for v in w] for l,w in rotations.items()})]
    for i,(dy,dw) in enumerate(chain):
        q=stage(p,'pose_'+str(i));displaced_xml(parent,q/'geometry.xml',dy,rotations=dw,rotation_axis='xyz',allowed_layers=aligned_sensors(args.mode))
        execute([args.build/'TelescopeChannels',q/'geometry.xml',0,q/'geometry.json'],q,env=environment(args.build))
        actual=json.loads((q/'geometry.json').read_text())['sensors'];pose_checks(previous,actual,dy,dw,axis='xyz')
        xml_identity(parent,q/'geometry.xml',labels(args.mode));previous=actual;parent=q/'geometry.xml'
    for a,b in zip(base,actual):
        np.testing.assert_allclose(b['center_mm'],a['center_mm'],atol=1e-9,rtol=0)
        np.testing.assert_allclose(b['acts_rotation'],a['acts_rotation'],atol=2e-12,rtol=0)
    write_json(p/'report.json',{'passed':True,'independent_sensor_updates':True,'left_global_composition_and_inverse':True,'geometry_activated':False})
    done(p,passed=True)
