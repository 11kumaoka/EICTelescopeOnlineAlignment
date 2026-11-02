"""Approved Air-envelope contract for the fixed single-module +X telescope.

These XML checks do not construct Geometry or infer alignment corrections.
Actual DD4hep/ACTS reloads remain mandatory in the calibration operations.
"""
import math

import numpy as np

from geometry.placements import xml_number
from geometry.transforms import multiply, rotation_exp


AIR_ENVELOPE_CONVENTION = 'preserve_clearance_air_growth'
MODULE_TO_GLOBAL = ((0., 0., 1.), (-1., 0., 0.), (0., -1., 0.))
LENGTH_TOLERANCE_MM = 1e-9


def values(node, names, *, positive=False):
    """Read finite dimensions in mm, refusing missing or invalid XML nodes."""
    if node is None:
        raise ValueError('Missing Geometry dimension or placement node')
    result = np.array([xml_number(node.get(name), {}) for name in names], dtype=float)
    if not np.isfinite(result).all() or (positive and np.any(result <= 0)):
        raise ValueError('Geometry dimensions must be finite' + (' and positive' if positive else ''))
    return result


def component_rotation(component):
    """Pinned DD4hep component chart, independently checked after real reload."""
    x, y, z = values(component.find('rotation'), 'xyz')
    return np.array(multiply(rotation_exp((x, 0., 0.)),
                    multiply(rotation_exp((0., y, 0.)), rotation_exp((0., 0., z)))))


def canonical_layer(module, layer):
    """Reject layouts outside the actual one-module telescope contract."""
    rphi, zlayout = layer.find('rphi_layout'), layer.find('z_layout')
    phi, tilt, nphi, radius, dr = values(rphi, ('phi0', 'phi_tilt', 'nphi', 'rc', 'dr'))
    z0, nz, z_dr = values(zlayout, ('z0', 'nz', 'dr'))
    if (phi != 0. or tilt != 0. or z0 != 0. or dr != 0. or z_dr != 0.
            or nphi != 1. or nz != 1. or radius <= 0.):
        raise ValueError('Unsupported telescope layout: require one module, canonical phi/z and zero dr')
    components = module.findall('module_component')
    if len(components) != 1:
        raise ValueError('Expected one real sensitive component per telescope module')
    component = components[0]
    if component.get('sensitive') != 'true' or component.get('material') != 'Silicon':
        raise ValueError('Expected the unchanged sensitive Silicon component')
    sensor_half = values(component, ('width', 'length', 'thickness'), positive=True) / 2.
    module_half = values(module.find('module_envelope'), ('width', 'length', 'thickness'), positive=True) / 2.
    inner, outer, length = values(layer.find('barrel_envelope'),
                                  ('inner_r', 'outer_r', 'z_length'), positive=True)
    if inner >= outer:
        raise ValueError('Layer inner radius must be smaller than its outer radius')
    return {'radius': float(radius), 'sensor_half': sensor_half, 'module_half': module_half,
            'position': values(component.find('position'), 'xyz'),
            'rotation': component_rotation(component),
            'tube': np.array([inner, outer, length])}


def expected_air_envelopes(original_module, updated_module, layer):
    """Recompute the existing monotonic, preserved-clearance growth rule."""
    old = canonical_layer(original_module, layer)
    new_component = updated_module.find('module_component')
    new_half = values(new_component, ('width', 'length', 'thickness'), positive=True) / 2.
    old_required = np.abs(old['rotation']) @ old['sensor_half'] + np.abs(old['position'])
    new_required = (np.abs(component_rotation(new_component)) @ new_half
                    + np.abs(values(new_component.find('position'), 'xyz')))
    clearance = np.maximum(old['module_half'] - old_required, 0.)
    grown = np.maximum(old['module_half'], new_required + clearance)
    parent = np.asarray(MODULE_TO_GLOBAL)
    old_world, new_world = np.abs(parent) @ old['module_half'], np.abs(parent) @ grown
    radius = old['radius']; inner, outer, length = old['tube']
    inner_margin = max(radius - old_world[0] - inner, 0.)
    outer_margin = max(outer - math.hypot(radius + old_world[0], old_world[1]), 0.)
    z_margin = max(length / 2. - old_world[2], 0.)
    tube = np.array([min(inner, radius-new_world[0]-inner_margin),
                     max(outer, math.hypot(radius+new_world[0], new_world[1])+outer_margin),
                     max(length, 2.*(new_world[2]+z_margin))])
    if not np.isfinite(grown).all() or not np.isfinite(tube).all() or tube[0] <= 0.:
        raise ValueError('Air-envelope growth produced invalid dimensions')
    # Match the retained serializer's exact rewrite threshold.
    return {
        'module_envelope': {key: float(2.*new if new > previous+1e-12 else 2.*previous)
                            for key, previous, new in zip(('width', 'length', 'thickness'), old['module_half'], grown)},
        'barrel_envelope': {key: float(new if direction*(new-previous) > 1e-12 else previous)
                            for key, previous, new, direction in zip(('inner_r', 'outer_r', 'z_length'), old['tube'], tube, (-1., 1., 1.))},
    }


def normalize_air_envelopes(original_module, candidate_module, original_layer, candidate_layer):
    """Whitelist only exact expected dimensions; never normalize arbitrary growth."""
    expected = expected_air_envelopes(original_module, candidate_module, original_layer)
    canonical_layer(candidate_module, candidate_layer)
    for tag, parent, candidate in (('module_envelope', original_module, candidate_module),
                                   ('barrel_envelope', original_layer, candidate_layer)):
        old, new = parent.find(tag), candidate.find(tag)
        if set(old.attrib) != set(new.attrib):
            raise ValueError('Unexpected ' + tag + ' attributes')
        for key, required in expected[tag].items():
            actual = float(values(new, (key,))[0])
            if actual != required:
                raise ValueError('Unexpected Air-envelope growth: ' + tag + '.' + key)
            new.set(key, old.get(key))


def layer_specs(root):
    """Read every real layer in the supported three-subdetector telescope."""
    result = {}
    for name in ('SiMAPS', 'MPGDPlane', 'ACLGADPlane'):
        detectors = [d for d in root.findall('./detectors/detector') if d.get('name') == name]
        if len(detectors) > 1:
            raise ValueError('Duplicate telescope detector: ' + name)
        if not detectors:
            continue
        detector = detectors[0]
        for layer in detector.findall('layer'):
            identifier = int(layer.get('id'))
            modules = [m for m in detector.findall('module') if m.get('name') == layer.get('module')]
            if identifier in result or len(modules) != 1:
                raise ValueError('Duplicate layer or missing/duplicate module')
            spec = canonical_layer(modules[0], layer)
            spec.update(layer=identifier, module=modules[0], layer_xml=layer)
            result[identifier] = spec
    return result
