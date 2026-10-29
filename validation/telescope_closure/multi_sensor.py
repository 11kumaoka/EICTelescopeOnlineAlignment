"""Configured SiMAPS selection and geometry checks using the native model."""
import json
import math
from pathlib import Path


MODE_CONFIG = {name: name for name in (
    'l1_y_rx_ry_rz_l2_y', 'l1_l2_y_rx_ry_rz',
    'l1_l2_y_rx_ry_rz_l3_y', 'l1_l2_l3_y_rx_ry_rz')}
MODE_CONFIG['l1_y'] = 'l1_global_y'


def is_multi(mode):
    return mode.removesuffix('_negative') in MODE_CONFIG


def config_name(mode):
    if not is_multi(mode):raise ValueError('Unsupported multi-sensor scope')
    return MODE_CONFIG[mode.removesuffix('_negative')]


def configuration(mode):
    path=Path(__file__).resolve().parents[1]/'telescope_alignment'/(config_name(mode)+'.json')
    config=json.loads(path.read_text())
    parameters=config['parameters']
    selected=[p['label'] for p in parameters]
    if not selected or len(set(selected))!=len(selected):raise ValueError('Empty or duplicate selected parameter')
    for p in parameters:
        sensor,column=mapping(p['label'])
        expected={1:'global_y',3:'global_rx',4:'global_ry',5:'global_rz'}[column]
        if p['layer']!=sensor or p['system']!=31 or p['axis']!=expected:
            raise ValueError('Parameter identity disagrees with physical label')
        if p['unit']!=('mm' if column==1 else 'rad'):
            raise ValueError('Parameter unit disagrees with physical component')
    sensors=sorted({p['layer'] for p in parameters})
    if 3 in sensors and 'aligned_sensors' not in config:
        raise ValueError('L3 requires an explicit sensor configuration')
    if 'aligned_sensors' in config:
        from geometry.native_safety import AIR_ENVELOPE_CONVENTION
        conventions={'fixed_components':['global_x','global_z'],
            'correction_pivot':'sensitive_surface_center',
            'rotation_composition':'left_global_exp',
            'translation_convention':'world_translation',
            'containment':{'module_envelope':AIR_ENVELOPE_CONVENTION,
                           'layer_envelope':AIR_ENVELOPE_CONVENTION,'sensitive_corners':8}}
        if any(config[key]!=value for key,value in conventions.items()):
            raise ValueError('Unsupported correction or containment convention')
        declared=config['aligned_sensors']
        if sorted(p['layer'] for p in declared)!=sensors:
            raise ValueError('Configured aligned sensors disagree with parameters')
        for sensor in declared:
            rows=[p for p in parameters if p['layer']==sensor['layer']]
            if set(sensor['components'])!={p['axis'] for p in rows}:
                raise ValueError('Configured components disagree with parameters')
            if any(any(p[key]!=sensor[key] for key in ('system','volume_id','geometry_id')) for p in rows):
                raise ValueError('Configured detector identities disagree with parameters')
            for p in rows:
                if p['pivot']!='sensitive_surface_center' or p['composition']!=('world_translation' if p['axis']=='global_y' else 'left_global_exp'):
                    raise ValueError('Unsupported physical correction convention')
        if config['fixed_reference_sensors']!=[i for i in range(5) if i not in sensors]:
            raise ValueError('Selected/reference sensor partition disagrees')
    return config


def aligned_sensors(mode):
    return sorted({p['layer'] for p in configuration(mode)['parameters']})


def references(mode):
    return [i for i in range(5) if i not in aligned_sensors(mode)]


def labels(mode):
    return [p['label'] for p in configuration(mode)['parameters']]


def mapping(label):
    if not isinstance(label,int):raise ValueError('Expected an integer physical label')
    layer,component=divmod(label-700000,100)
    if layer not in (1,2,3) or component not in (2,4,5,6):
        raise ValueError('Unsupported physical parameter label')
    return layer,component-1


def fixture(mode):
    selected=labels(mode)
    sign=-1 if mode.endswith('_negative') else 1
    shifts={1:.080,2:-.060,3:.050}
    vectors={1:(.001,.0005,-.0005),2:(-.0012,.0006,.0006),3:(.0008,-.0004,.0004)}
    dy={layer:shifts[layer] for k in selected for layer,c in [mapping(k)] if c==1}
    dw={layer:[sign*v for v in vectors[layer]] for k in selected for layer,c in [mapping(k)] if c>=3}
    return dy,dw


def pose_increments(values):
    shifts={};rotations={}
    for label,value in values.items():
        layer,column=mapping(int(label))
        if not math.isfinite(value):raise ValueError('Nonfinite physical update')
        if column==1:shifts[layer]=value
        else:rotations.setdefault(layer,[None,None,None])[column-3]=value
    if any(any(v is None for v in w) for w in rotations.values()):
        raise ValueError('A selected SO(3) update requires all three components')
    return shifts,rotations


def injection(mode):
    shifts,rotations=fixture(mode)
    return {k:(shifts[layer] if column==1 else rotations[layer][column-3])
            for k in labels(mode) for layer,column in [mapping(k)]}

def normalize_expanded_envelopes(
    original_module, candidate_module,
    original_layer, candidate_layer
):
    """Independently require the exact approved preserved-clearance growth."""
    from geometry.native_safety import normalize_air_envelopes
    try:
        normalize_air_envelopes(original_module, candidate_module, original_layer, candidate_layer)
    except ValueError as error:
        raise AssertionError(str(error)) from error

def xml_identity(parent,candidate,selected):
    """Whitelist placed component position/orientation; preserve every other node."""
    import copy
    import xml.etree.ElementTree as ET
    def structure(n):return n.tag,dict(n.attrib),(n.text or '').strip(),tuple(structure(c) for c in n)
    a=ET.parse(parent).getroot();b=ET.parse(candidate).getroot()
    da=a.find("./detectors/detector[@name='SiMAPS']");db=b.find("./detectors/detector[@name='SiMAPS']")
    for layer in sorted({mapping(k)[0] for k in selected}):
        la=da.find(f"./layer[@id='{layer}']");lb=db.find(f"./layer[@id='{layer}']")
        # A selected sensor may have no increment in an isolation test. The
        # final whole-XML comparison still rejects in-place/shared mutations.
        if la.get('module')==lb.get('module'):continue
        reference=da.find(f"./module[@name='{la.get('module')}']")
        module=db.find(f"./module[@name='{lb.get('module')}']")
        normalized=copy.deepcopy(module);normalized.set('name',reference.get('name'))
        normalize_expanded_envelopes(reference, normalized, la, lb)
        normalized.find('./module_component/position').set('x',reference.find('./module_component/position').get('x'))
        if any(mapping(k)==(layer,3) for k in selected):
            for key in 'xyz':normalized.find('./module_component/rotation').set(key,reference.find('./module_component/rotation').get(key))
        assert structure(normalized)==structure(reference)
        db.remove(module);lb.set('module',la.get('module'))
    assert structure(a)==structure(b)


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
