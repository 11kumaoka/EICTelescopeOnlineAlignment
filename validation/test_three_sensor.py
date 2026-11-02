"""Selected-sensor geometry isolation and bounded joint-error design checks."""
import math
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from geometry.transforms import rotation_exp
from validation.telescope_closure.closure import displaced_xml, MODULE_TO_GLOBAL
from validation.telescope_closure.multi_sensor import (
    aligned_sensors, configuration, fixture, labels, mapping, pose_increments,
    references, xml_identity,
)
from validation.telescope_closure.multi_preflight import (
    _bounds, _pose_bounds, sampled_offsets,
)
from validation.telescope_closure.rotation import component_matrix


SCOPE_C = 'l1_l2_y_rx_ry_rz_l3_y'
SCOPE_D = 'l1_l2_l3_y_rx_ry_rz'


def active_component(path, layer):
    detector = ET.parse(path).getroot().find("./detectors/detector[@name='SiMAPS']")
    name = detector.find(f"./layer[@id='{layer}']").get('module')
    return detector.find(f"./module[@name='{name}']/module_component")


def structure(node):
    return node.tag, dict(node.attrib), (node.text or '').strip(), [
        structure(child) for child in node]


def pose(path, layer):
    component = active_component(path, layer)
    local = np.array([float(component.find('position').get(axis).replace('*mm', ''))
                      for axis in 'xyz'])
    parent = np.array(MODULE_TO_GLOBAL)
    return parent @ local, parent @ component_matrix(component)


class NativeThreeSensor(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.nominal = self.root / 'nominal.xml'
        detector = ET.Element('detector', name='SiMAPS', id='31')
        module = ET.SubElement(detector, 'module', name='shared')
        ET.SubElement(module, 'module_envelope', width='100.5*mm',
                      length='100.5*mm', thickness='0.65*mm')
        component = ET.SubElement(module, 'module_component', material='Silicon',
                                  sensitive='true', width='100*mm', length='0.05*mm',
                                  thickness='100*mm')
        ET.SubElement(component, 'position', x='0*mm', y='0*mm', z='0*mm')
        ET.SubElement(component, 'rotation', x='-pi/2', y='-pi/2', z='0')
        for layer in range(5):
            radius = 250 + 50 * layer
            placement = ET.SubElement(detector, 'layer', id=str(layer), module='shared')
            ET.SubElement(placement, 'barrel_envelope', inner_r=f'{radius-1.025}*mm',
                          outer_r=f'{math.hypot(radius+.425,50.3)+1}*mm', z_length='102*mm')
            ET.SubElement(placement, 'rphi_layout', phi0='0', phi_tilt='0', nphi='1',
                          rc=f'{radius}*mm', dr='0')
            ET.SubElement(placement, 'z_layout', z0='0', nz='1', dr='0')
        root = ET.Element('lccdd')
        ET.SubElement(root, 'detectors').append(detector)
        ET.ElementTree(root).write(self.nominal, encoding='unicode')

    def test_configuration_selects_nine_or_twelve_physical_parameters(self):
        previous = labels('l1_l2_y_rx_ry_rz')
        self.assertEqual(labels(SCOPE_C), previous + [700302])
        self.assertEqual(labels(SCOPE_D), previous + [700302, 700304, 700305, 700306])
        identities = {1: (272631839, 216173056991691039),
                      2: (272633887, 216173194430644511),
                      3: (272635935, 216173331869597983)}
        for mode in (SCOPE_C, SCOPE_D):
            config = configuration(mode)
            self.assertEqual(aligned_sensors(mode), [1, 2, 3])
            self.assertEqual(references(mode), [0, 4])
            self.assertEqual(config['fixed_components'], ['global_x', 'global_z'])
            self.assertEqual(config['correction_pivot'], 'sensitive_surface_center')
            self.assertEqual(config['rotation_composition'], 'left_global_exp')
            self.assertEqual(config['translation_convention'], 'world_translation')
            self.assertEqual(config['containment'], {'module_envelope': 'preserve_clearance_air_growth',
                             'layer_envelope': 'preserve_clearance_air_growth', 'sensitive_corners': 8})
            for parameter in config['parameters']:
                layer, column = mapping(parameter['label'])
                self.assertEqual(parameter['layer'], layer)
                self.assertEqual(parameter['unit'], 'mm' if column == 1 else 'rad')
                self.assertEqual((parameter['volume_id'], parameter['geometry_id']), identities[layer])
            for sensor in config['aligned_sensors']:
                actual = {parameter['axis'] for parameter in config['parameters']
                          if parameter['layer'] == sensor['layer']}
                self.assertEqual(set(sensor['components']), actual)

    def test_signed_three_sensor_fixture_preserves_the_two_sensor_injection(self):
        previous_shifts, previous_rotations = fixture('l1_l2_y_rx_ry_rz')
        for mode in (SCOPE_C, SCOPE_D):
            shifts, rotations = fixture(mode)
            negative_shifts, negative_rotations = fixture(mode + '_negative')
            self.assertEqual({layer: shifts[layer] for layer in (1, 2)}, previous_shifts)
            self.assertEqual({layer: rotations[layer] for layer in (1, 2)}, previous_rotations)
            self.assertNotEqual(shifts[3], 0.)
            self.assertEqual(negative_shifts, shifts)
            self.assertEqual(set(rotations), {1, 2} if mode == SCOPE_C else {1, 2, 3})
            for layer, rotation in rotations.items():
                np.testing.assert_array_equal(negative_rotations[layer], -np.array(rotation))

    def test_l3_label_decoding_is_separate_and_requires_complete_rotation(self):
        expected = {700302: (3, 1), 700304: (3, 3), 700305: (3, 4), 700306: (3, 5)}
        self.assertEqual({label: mapping(label) for label in expected}, expected)
        values = {700302: .05, 700304: .0008, 700305: -.0004, 700306: .0004}
        self.assertEqual(pose_increments(values), ({3: .05}, {3: [.0008, -.0004, .0004]}))
        for absent in (700304, 700305, 700306):
            incomplete = dict(values)
            incomplete.pop(absent)
            with self.assertRaises(ValueError):
                pose_increments(incomplete)
        for fixed in (700301, 700303, 700402, 700404):
            with self.assertRaises(ValueError):
                mapping(fixed)

    def test_partial_l3_edit_preserves_other_selected_sensor_clones(self):
        parent = self.root / 'two_sensor.xml'
        shifts, rotations = fixture('l1_l2_y_rx_ry_rz')
        displaced_xml(self.nominal, parent, shifts, rotations=rotations, rotation_axis='xyz')
        candidate = self.root / 'l3_only.xml'
        displaced_xml(parent, candidate, {3: .05}, rotations={3: [.0008, -.0004, .0004]},
                      rotation_axis='xyz', allowed_layers=(1, 2, 3))
        xml_identity(parent, candidate, labels(SCOPE_D))
        for layer in (0, 1, 2, 4):
            self.assertEqual(structure(active_component(parent, layer)),
                             structure(active_component(candidate, layer)))
        with self.assertRaises(ValueError):
            displaced_xml(parent, self.root / 'default_refusal.xml', {3: .05})

    def test_three_sensitive_centers_and_so3_updates_compose_independently(self):
        shifts, rotations = fixture(SCOPE_D)
        cross_shifts = {1: -.01, 2: .02, 3: -.015}
        cross_rotations = {1: [-.0003, .0002, .0001],
                           2: [.0002, -.0003, .0001], 3: [-.0001, .0002, -.0003]}
        updates = [(shifts, rotations), (cross_shifts, cross_rotations),
                   ({layer: -value for layer, value in cross_shifts.items()},
                    {layer: -np.array(vector) for layer, vector in cross_rotations.items()}),
                   ({layer: -value for layer, value in shifts.items()},
                    {layer: -np.array(vector) for layer, vector in rotations.items()})]
        parent = self.nominal
        for index, (dy, dw) in enumerate(updates):
            output = self.root / f'joint_{index}.xml'
            displaced_xml(parent, output, dy, rotations=dw, rotation_axis='xyz',
                          allowed_layers=aligned_sensors(SCOPE_D))
            xml_identity(parent, output, labels(SCOPE_D))
            if index == 1:
                for layer in (1, 2, 3):
                    center, rotation = pose(output, layer)
                    np.testing.assert_allclose(center, [0, shifts[layer]+cross_shifts[layer], 0],
                                               atol=1e-15, rtol=0)
                    expected = (np.array(rotation_exp(cross_rotations[layer])) @
                                np.array(rotation_exp(rotations[layer])) @ pose(self.nominal, layer)[1])
                    np.testing.assert_allclose(rotation, expected, atol=5e-13, rtol=0)
            parent = output
        for layer in range(5):
            center, rotation = pose(parent, layer)
            np.testing.assert_allclose(center, pose(self.nominal, layer)[0], atol=1e-15, rtol=0)
            np.testing.assert_allclose(rotation, pose(self.nominal, layer)[1], atol=5e-13, rtol=0)

    def test_scope_c_forbids_l3_rotation_and_all_scopes_preserve_references(self):
        output = self.root / 'all_rotations.xml'
        shifts, rotations = fixture(SCOPE_D)
        displaced_xml(self.nominal, output, shifts, rotations=rotations, rotation_axis='xyz',
                      allowed_layers=(1, 2, 3))
        xml_identity(self.nominal, output, labels(SCOPE_D))
        with self.assertRaises(AssertionError):
            xml_identity(self.nominal, output, labels(SCOPE_C))
        tree = ET.parse(output)
        tree.getroot().find("./detectors/detector/layer[@id='4']/rphi_layout").set('rc', '451*mm')
        tree.write(output, encoding='unicode')
        with self.assertRaises(AssertionError):
            xml_identity(self.nominal, output, labels(SCOPE_D))

    def test_preflight_reads_the_selected_l3_dimensions_from_xml(self):
        base = [{'layer': layer, 'center_mm': [250+50*layer, 0., 0.]} for layer in range(5)]
        tree = ET.parse(self.nominal)
        detector = tree.getroot().find('./detectors/detector')
        import copy
        module = copy.deepcopy(detector.find("./module[@name='shared']"))
        module.set('name', 'different_l3_envelope')
        module.find('module_envelope').set('thickness', '0.8*mm')
        detector.append(module)
        detector.find("./layer[@id='3']").set('module', module.get('name'))
        tree.write(self.nominal, encoding='unicode')
        bounds = _bounds(self.nominal, base, labels(SCOPE_C))
        self.assertEqual(set(bounds), {1, 2, 3})
        np.testing.assert_array_equal(bounds[3]['sensitive_half_mm'], [50., .025, 50.])
        np.testing.assert_array_equal(bounds[3]['module_half_mm'], [50.25, 50.25, .4])
        self.assertEqual(bounds[1]['module_half_mm'][2], .325)
        self.assertEqual(bounds[3]['layer_inner_radius_mm'], 398.975)

    def test_high_dimensional_lift_preserves_joint_radius_and_correlations(self):
        lower = np.eye(12) + .03*np.tril(np.ones((12, 12)), -1)
        covariance = lower @ lower.T
        groups = [list(range(4)), list(range(4, 8)), list(range(8, 12))]
        offsets = sampled_offsets(covariance, parameter_groups=groups)
        self.assertEqual(len(offsets), 264)
        inverse = np.linalg.inv(covariance)
        for delta in offsets:
            self.assertAlmostEqual(float(delta @ inverse @ delta), 25., places=11)
        for group, block in zip(groups, (offsets[:88], offsets[88:176], offsets[176:])):
            marginal = covariance[np.ix_(group, group)]
            for delta in block:
                expected = covariance[:, group] @ np.linalg.solve(marginal, delta[group])
                np.testing.assert_allclose(delta, expected, atol=2e-15, rtol=0)
        self.assertTrue(any(abs(delta[8]) > 0 for delta in offsets[:88]))

    def test_sampler_keeps_old_counts_and_reaches_each_coordinate_extremum(self):
        for dimension, count in ((5, 252), (8, 6576)):
            offsets = np.array(sampled_offsets(np.eye(dimension)))
            self.assertEqual(len(offsets), count)
            np.testing.assert_array_equal(offsets[0], -5*np.ones(dimension)/np.sqrt(dimension))
            np.testing.assert_array_equal(offsets[-1], -5*np.eye(dimension)[-1])
        for groups in ([list(range(4)), list(range(4, 8)), [8]],
                       [list(range(4)), list(range(4, 8)), list(range(8, 12))]):
            dimension = sum(map(len, groups))
            diagonal = np.arange(1, dimension+1, dtype=float)
            offsets = np.array(sampled_offsets(np.diag(diagonal), parameter_groups=groups))
            self.assertEqual(len(offsets), 180 if dimension == 9 else 264)
            np.testing.assert_allclose(np.abs(offsets).max(axis=0), 5*np.sqrt(diagonal),
                                       atol=5e-15, rtol=0)

    def test_sampler_refuses_missing_overlapping_or_incomplete_sensor_groups(self):
        with self.assertRaises(ValueError):
            sampled_offsets(np.eye(12))
        for groups in ([[0, 1, 2, 3], [3, 4, 5, 6], [7, 8, 9, 10]],
                       [list(range(5)), list(range(5, 9)), [9, 10, 11]],
                       [list(range(4)), list(range(4, 8))]):
            with self.assertRaises(ValueError):
                sampled_offsets(np.eye(12), parameter_groups=groups)

    def test_preflight_keeps_module_and_layer_failures_distinct(self):
        base = [{'layer': layer, 'center_mm': [250+50*layer, 0., 0.]} for layer in range(5)]
        bounds = _bounds(self.nominal, base, labels(SCOPE_C))[3]
        center = np.array([400., 0., 0.])
        rotation = pose(self.nominal, 3)[1]
        nominal = _pose_bounds(center, rotation, bounds)
        self.assertTrue(nominal['contained'] and nominal['contained_in_layer'])
        module_failure = _pose_bounds(center+[.4, 0., 0.], rotation, bounds)
        self.assertFalse(module_failure['contained'])
        layer_failure = _pose_bounds(center+[0., 0., 2.], rotation, bounds)
        self.assertFalse(layer_failure['contained_in_layer'])


if __name__ == '__main__':
    unittest.main()
