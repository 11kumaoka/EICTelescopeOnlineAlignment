"""Validated sensor selections, physical parameter labels and XML identity gates."""
import json
import math
from pathlib import Path
from research_common import REPO

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
    path=REPO/'calibration/alignment/config'/(config_name(mode)+'.json')
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
        from geometry.safety import AIR_ENVELOPE_CONVENTION
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


def normalize_expanded_envelopes(
    original_module, candidate_module,
    original_layer, candidate_layer
):
    """Independently require the exact approved preserved-clearance growth."""
    from geometry.safety import normalize_air_envelopes
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

