"""Telescope Geometry correction and mandatory physical safety gates."""
import copy
import itertools
import json
import math
import xml.etree.ElementTree as ET
import numpy as np
from research_common import REPO, execute, stage, done, require, write_json, digest
from geometry.transforms import matvec, transpose
from calibration.reconstruction.operations import environment
LABELS=[700102,700202]
MODULE_TO_GLOBAL=((0.,0.,1.),(-1.,0.,0.),(0.,-1.,0.))

class GeometrySafetyError(RuntimeError):
    """A rejected physical candidate with retained diagnostics."""

    def __init__(self,message,diagnostics):
        super().__init__(message)
        self.diagnostics=diagnostics


def validate_geometry(sensors, config=None, *, geometry_xml):
    """Check actual seven-sensor poses against the approved Air-envelope contract."""
    from geometry.safety import layer_specs, values, LENGTH_TOLERANCE_MM
    from geometry.placements import xml_number
    diagnostics={'passed':False,'sensor_count':len(sensors),'sensors':{},'failures':[],
                 'envelope_convention':'preserve_clearance_air_growth'}
    def fail(message):diagnostics['failures'].append(message)
    try:
        root=ET.parse(geometry_xml).getroot();specs=layer_specs(root)
        if set(specs)!=set(range(7)):raise ValueError('Expected the seven real telescope layers')
        constants={c.get('name'):c.get('value') for c in root.findall('./define/constant')}
        world_node=root.find('./world')
        if world_node is None or world_node.get('material')!='Air':
            raise ValueError('Air-envelope growth requires the unchanged Air world')
        world=world_node.find('shape')
        if world is None or world.get('type')!='Box':raise ValueError('Expected the telescope Box world')
        world_half=np.array([xml_number(world.get(k),constants) for k in ('dx','dy','dz')])
        if not np.isfinite(world_half).all() or np.any(world_half<=0):raise ValueError('Invalid world bounds')
        diagnostics['world_half_mm']=world_half.tolist()
    except (OSError,ET.ParseError,TypeError,ValueError,KeyError,AttributeError) as error:
        fail('Geometry XML: '+str(error))
        raise GeometrySafetyError('; '.join(diagnostics['failures']),diagnostics) from error
    layers=[s.get('layer') for s in sensors]
    if len(sensors)!=7 or layers!=list(range(7)) or any(type(v) is not int for v in layers):
        fail('sensor identity: expected seven unique ordered layers 0..6')
    for key in ('volume_id','geometry_id'):
        ids=[s.get(key) for s in sensors]
        if any(type(v) is not int or v<=0 for v in ids) or len(set(ids))!=7:
            fail('sensor identity: missing or duplicate '+key)
    by_layer={s.get('layer'):s for s in sensors}
    if config is not None:
        for parameter in config['parameters']:
            sensor=by_layer.get(parameter['layer'])
            if sensor is None or any(sensor.get(k)!=parameter[k] for k in ('volume_id','geometry_id')):
                fail('sensor identity: configuration mismatch on layer '+str(parameter['layer']))
    parent=np.asarray(MODULE_TO_GLOBAL)
    sensor_from_acts=np.array(((0.,0.,-1.),(1.,0.,0.),(0.,-1.,0.)))
    previous_outer=None;diagnostics['layer_radial_gaps_mm']=[]
    for layer in range(7):
        spec=specs[layer];inner,outer,length=spec['tube']
        if previous_outer is not None:
            gap=inner-previous_outer;diagnostics['layer_radial_gaps_mm'].append(float(gap))
            if gap < -LENGTH_TOLERANCE_MM:fail('layer Air envelopes overlap before layer '+str(layer))
        previous_outer=outer
    for sensor in sensors:
        layer=sensor.get('layer')
        if layer not in specs:
            fail('sensor identity: unknown layer '+str(layer));continue
        spec=specs[layer]
        try:
            center=np.asarray(sensor['center_mm'],dtype=float)
            dd=np.asarray(sensor['dd4hep_rotation'],dtype=float)
            acts=np.asarray(sensor['acts_rotation'],dtype=float)
            if center.shape!=(3,) or dd.shape!=(3,3) or acts.shape!=(3,3):raise ValueError('invalid pose dimensions')
            if not all(np.isfinite(v).all() for v in (center,dd,acts)):raise ValueError('nonfinite pose')
            for matrix in (dd,acts):
                if not np.allclose(matrix.T@matrix,np.eye(3),atol=2e-12,rtol=0) or abs(np.linalg.det(matrix)-1.)>2e-12:
                    raise ValueError('pose is not a proper rotation')
            module_center=np.array([spec['radius'],0.,0.])
            if not np.allclose(center,module_center+parent@spec['position'],atol=1e-9,rtol=0):
                raise ValueError('actual DD4hep center disagrees with canonical XML placement')
            if not np.allclose(dd,parent@spec['rotation'],atol=2e-12,rtol=0):
                raise ValueError('actual DD4hep rotation disagrees with XML component chart')
            if not np.allclose(dd,acts@sensor_from_acts,atol=2e-12,rtol=0):
                raise ValueError('DD4hep and ACTS sensor axes disagree')
        except (KeyError,TypeError,ValueError) as error:
            fail('sensor pose: layer '+str(layer)+': '+str(error));continue
        local_center=parent.T@(center-module_center)
        sensor_extent=np.abs(parent.T@dd)@spec['sensor_half']
        module_clearance=spec['module_half']-np.abs(local_center)-sensor_extent
        half_world=np.abs(parent)@spec['module_half']
        inner,outer,length=spec['tube'];radius=spec['radius']
        layer_clearance=np.array([radius-half_world[0]-inner,
            outer-math.hypot(radius+half_world[0],half_world[1]),length/2.-half_world[2]])
        world_clearance=world_half-np.abs(module_center)-half_world
        module_ok=bool(np.all(module_clearance>=-LENGTH_TOLERANCE_MM))
        layer_ok=bool(np.all(layer_clearance>=-LENGTH_TOLERANCE_MM))
        world_ok=bool(np.all(world_clearance>=-LENGTH_TOLERANCE_MM))
        corners=[(center+dd@(np.array(signs)*spec['sensor_half'])).tolist()
                 for signs in itertools.product((-1,1),repeat=3)]
        diagnostics['sensors'][str(layer)]={'module_half_local_mm':spec['module_half'].tolist(),
            'sensor_half_local_mm':spec['sensor_half'].tolist(),'module_clearance_local_mm':module_clearance.tolist(),
            'layer_clearance_inner_outer_z_mm':layer_clearance.tolist(),'world_clearance_mm':world_clearance.tolist(),
            'global_corners_mm':corners,'module_contained':module_ok,'layer_contained':layer_ok,'world_contained':world_ok}
        if not module_ok:fail('module containment failure on layer '+str(layer))
        if not layer_ok:fail('layer containment failure on layer '+str(layer))
        if not world_ok:fail('world containment failure on layer '+str(layer))
    diagnostics['passed']=not diagnostics['failures']
    if not diagnostics['passed']:raise GeometrySafetyError('; '.join(diagnostics['failures']),diagnostics)
    return diagnostics


def geometry_audit(build,output,xml,config=None):
    """Reload real DD4hep/ACTS Geometry without generating any events."""
    p=stage(output.parent,output.name)
    execute([build/'TelescopeChannels',xml,0,p/'geometry.json'],p,env=environment(build))
    report=json.loads((p/'geometry.json').read_text())
    try:
        if not report.get('passed') or report.get('events')!=[]:
            raise GeometrySafetyError('Geometry audit did not return a zero-event success',
                                      {'passed':False,'failures':['invalid Geometry audit output']})
        diagnostics = validate_geometry(
            report['sensors'], config, geometry_xml=xml
        )
        physical=stage(p,'physical')
        execute([build/'TelescopePhysicalGeometryAudit',xml,physical/'geometry.json'],physical,env=environment(build))
        if not json.loads((physical/'geometry.json').read_text()).get('passed'):
            raise GeometrySafetyError('Actual DD4hep/ACTS physical Geometry audit failed',
                                      {'passed':False,'failures':['physical Geometry audit failed']})
        done(physical,passed=True,geometry_sha256=digest(xml))
    except GeometrySafetyError as error:
        write_json(p/'geometry_safety.json',error.diagnostics)
        raise
    write_json(p/'geometry_safety.json',diagnostics)
    done(p,passed=True,geometry_sha256=digest(xml))
    return report


def _xml_identity(parent,candidate,selected,mode):
    """Retain the selected-node whitelist and full XML comparison."""
    from calibration.alignment import sensors as multi
    from calibration.alignment.modes import axis
    if multi.is_multi(mode):
        return multi.xml_identity(parent,candidate,selected)
    def structure(n):return n.tag,dict(n.attrib),(n.text or '').strip(),tuple(structure(c) for c in n)
    original_xml=ET.parse(parent).getroot();candidate_xml=ET.parse(candidate).getroot()
    original=original_xml.find("./detectors/detector[@name='SiMAPS']")
    detector=candidate_xml.find("./detectors/detector[@name='SiMAPS']")
    for layer in ([1,2] if mode in ('l1_l2_y','l1_y_rx_l2_y') else [1]):
        a=original.find(f"./layer[@id='{layer}']");b=detector.find(f"./layer[@id='{layer}']")
        module=detector.find(f"./module[@name='{b.get('module')}']")
        reference=original.find(f"./module[@name='{a.get('module')}']")
        normalized=copy.deepcopy(module);normalized.set('name',reference.get('name'))
        multi.normalize_expanded_envelopes(reference, normalized, a, b)
        normalized.find('./module_component/position').set('x',reference.find('./module_component/position').get('x'))
        if mode=='l1_l2_y':
            from geometry.placements import xml_number
            position=normalized.find('./module_component/position')
            original_position=reference.find('./module_component/position')
            for attribute in ('y','z'):
                # The unchanged translation serializer spells 0 as 0*mm.
                # Require exact physical equality before normalizing units.
                assert xml_number(position.get(attribute),{})==xml_number(original_position.get(attribute),{}), 'Nonselected component position changed'
                position.set(attribute,original_position.get(attribute))
        if layer==1 and mode!='l1_l2_y':
            attributes='xyz' if axis(mode) in ('z','yz','xyz') else ('x' if axis(mode)=='y' else 'y')
            for attribute in attributes:
                normalized.find('./module_component/rotation').set(attribute,reference.find('./module_component/rotation').get(attribute))

        assert structure(normalized)==structure(reference)
        detector.remove(module);b.set('module',a.get('module'))
    assert structure(candidate_xml)==structure(original_xml)


def correct(build,output,current_xml,current_sensors,parameters,mode):
    """Write and validate one candidate XML; the caller seals its version.

    The output is the new XML filename in a fresh correction directory.
    """
    from calibration.alignment import sensors as multi
    from calibration.alignment.modes import axis, is_coupled, labels, rotation_label, rotation_labels
    from geometry.rotations import pose_checks
    selected=LABELS if mode=='l1_l2_y' else labels(mode)
    if set(parameters)!=set(selected):
        raise ValueError('Pede parameters do not match the selected alignment configuration')
    config=multi.configuration(mode) if multi.is_multi(mode) else None
    validate_geometry(
        current_sensors, config, geometry_xml=current_xml
    )
    shifts={l:parameters[k] for l,k in [(1,700102),(2,700202)] if k in parameters}
    if mode=='l1_l2_y':rotations=None
    elif multi.is_multi(mode):shifts,rotations=multi.pose_increments(parameters)
    else:rotations={1:[parameters[k] for k in rotation_labels(mode)] if is_coupled(mode) else parameters[rotation_label(mode)]}
    write_json(output.parent/'parameters.json',{'parameters':parameters,'selected_labels':selected})
    displaced_xml(current_xml,output,shifts,rotations=rotations,rotation_axis=axis(mode),
                  allowed_layers=multi.aligned_sensors(mode) if multi.is_multi(mode) else (1,2))
    _xml_identity(current_xml,output,selected,mode)
    report=geometry_audit(build,output.parent/'geometry_audit',output,config=config)
    pose_checks(current_sensors,report['sensors'],shifts,rotations or {},axis=axis(mode))
    return {'shifts':shifts,'rotations':rotations,'rotation_axis':axis(mode),'sensors':report['sensors']}


def displaced_xml(nominal,output,shifts,rotations=None,rotation_axis='x',allowed_layers=(1,2)):
    """Apply selected physical Y shifts and center-preserving rotations."""
    if output.exists():raise ValueError('Geometry output must be new')
    root=ET.parse(nominal).getroot()
    detector=root.find("./detectors/detector[@name='SiMAPS']")
    for layer in sorted(set(shifts)|set(rotations or {})):
        delta=shifts.get(layer,0.)
        if int(layer) not in allowed_layers or int(layer) not in (1,2,3) or not math.isfinite(delta):
            raise ValueError('Only finite explicitly selected SiMAPS Global Y supported')
        placement=detector.find(f"./layer[@id='{int(layer)}']")
        original=detector.find(f"./module[@name='{placement.get('module')}']")
        module=copy.deepcopy(original);module.set('name',original.get('name')+f'_L{layer}')
        component=module.find('./module_component')
        position=component.find('position')
        offset=matvec(transpose(MODULE_TO_GLOBAL),(0.,delta,0.))
        for axis,value in zip('xyz',offset):
            # Rotation fixtures preserve untouched XML attributes exactly.
            # The original translation-only serialization remains unchanged.
            if rotations is not None and value==0:continue
            current=float(position.get(axis,'0').replace('*mm',''))
            position.set(axis,f'{current+value:.17g}*mm')
        # Reject changes to sensor shape, material, rotation, or segmentation.
        original_component=copy.deepcopy(original.find('./module_component'))
        comparison=copy.deepcopy(component)
        comparison.remove(comparison.find('position'));original_component.remove(original_component.find('position'))
        assert ET.tostring(comparison)==ET.tostring(original_component)
        if rotations and layer in rotations:
            from geometry.rotations import rotate_component
            rotate_component(component,rotations[layer],axis=rotation_axis)
        expand_sensor_envelopes(original, module, placement)

        detector.append(module);placement.set('module',module.get('name'))

    ET.indent(root);ET.ElementTree(root).write(output,encoding='unicode')


def expand_sensor_envelopes(original_module, updated_module, layer):
    """Grow air envelopes to contain the corrected sensitive component.

    Preserve the original clearance and never change the sensitive
    component dimensions, material, segmentation or placement.
    """
    from geometry.placements import xml_number
    from geometry.safety import canonical_layer
    from geometry.rotations import component_matrix
    canonical_layer(original_module, layer)
    canonical_layer(updated_module, layer)

    def values(node, names):
        return np.array(
            [xml_number(node.get(name), {}) for name in names],
            dtype=float
        )

    def required_half_size(component):
        dimensions = values(
            component, ("width", "length", "thickness")
        ) / 2.0
        position = values(
            component.find("position"), ("x", "y", "z")
        )
        rotation = component_matrix(component)
        return np.abs(rotation) @ dimensions + np.abs(position)

    old_component = original_module.find("module_component")
    new_component = updated_module.find("module_component")

    old_envelope = original_module.find("module_envelope")
    new_envelope = updated_module.find("module_envelope")

    names = ("width", "length", "thickness")
    old_half = values(old_envelope, names) / 2.0

    old_required = required_half_size(old_component)
    new_required = required_half_size(new_component)

    # Retain the original clearance. Do not introduce an angle cut.
    clearance = np.maximum(old_half - old_required, 0.0)
    new_half = np.maximum(old_half, new_required + clearance)

    if not np.isfinite(new_half).all():
        raise ValueError("Invalid corrected envelope dimensions")

    for name, old, new in zip(names, old_half, new_half):
        if new > old + 1e-12:
            new_envelope.set(name, f"{2.0 * new:.17g}*mm")

    # The barrel layer must also contain the enlarged module box.
    module_to_global = np.asarray(MODULE_TO_GLOBAL, dtype=float)
    old_world = np.abs(module_to_global) @ old_half
    new_world = np.abs(module_to_global) @ new_half

    radius = xml_number(layer.find("rphi_layout").get("rc"), {})
    barrel = layer.find("barrel_envelope")

    old_inner = xml_number(barrel.get("inner_r"), {})
    old_outer = xml_number(barrel.get("outer_r"), {})
    old_z = xml_number(barrel.get("z_length"), {})

    inner_margin = max(radius - old_world[0] - old_inner, 0.0)
    outer_margin = max(
        old_outer - math.hypot(radius + old_world[0], old_world[1]),
        0.0
    )
    z_margin = max(old_z / 2.0 - old_world[2], 0.0)

    new_inner = min(
        old_inner, radius - new_world[0] - inner_margin
    )
    new_outer = max(
        old_outer,
        math.hypot(radius + new_world[0], new_world[1])
        + outer_margin
    )
    new_z = max(old_z, 2.0 * (new_world[2] + z_margin))

    # Only rewrite dimensions that actually need to grow.
    if new_inner < old_inner - 1e-12:
        barrel.set("inner_r", f"{new_inner:.17g}*mm")
    if new_outer > old_outer + 1e-12:
        barrel.set("outer_r", f"{new_outer:.17g}*mm")
    if new_z > old_z + 1e-12:
        barrel.set("z_length", f"{new_z:.17g}*mm")

