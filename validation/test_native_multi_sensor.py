"""Independent sensor selection and physical pose checks for native alignment."""
import json
import math
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from geometry.transforms import rotation_exp
from validation.telescope_closure.closure import displaced_xml, MODULE_TO_GLOBAL
from validation.telescope_closure.multi_sensor import (
    fixture, is_multi, labels, mapping, pose_increments, xml_identity,
    normalize_expanded_envelopes,
)
from validation.telescope_closure.rotation import component_matrix


MODE = 'l1_y_rx_ry_rz_l2_y'


def detector(path):
    return ET.parse(path).getroot().find("./detectors/detector[@name='SiMAPS']")


def component(path, layer):
    node = detector(path)
    name = node.find(f"./layer[@id='{layer}']").get('module')
    return node.find(f"./module[@name='{name}']/module_component")


def structure(node):
    return node.tag, dict(node.attrib), (node.text or '').strip(), [
        structure(child) for child in node]


def world_pose(path, layer):
    node = component(path, layer)
    offset = np.array([float(node.find('position').get(k).replace('*mm', ''))
                       for k in 'xyz'])
    parent = np.array(MODULE_TO_GLOBAL)
    return parent @ offset, parent @ component_matrix(node)


class NativeMultiSensor(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.nominal = self.root / 'nominal.xml'
        self.nominal.write_text(
            '<lccdd><define><constant name="untouched" value="17*mm"/></define>'
            '<detectors><detector name="SiMAPS" id="31">'
            '<module name="shared"><module_envelope width="100.5*mm" '
            'length="100.5*mm" thickness="0.65*mm"/>'
            '<module_component material="Silicon" sensitive="true" '
            'width="100*mm" length="0.05*mm" thickness="100*mm">'
            '<position x="0*mm" y="0*mm" z="0*mm"/>'
            '<rotation x="-pi/2" y="-pi/2" z="0"/>'
            '</module_component></module>'
            + ''.join(f'<layer id="{layer}" module="shared"><barrel_envelope '
                      f'inner_r="{250+50*layer-1.025}*mm" '
                      f'outer_r="{math.hypot(250+50*layer+.425,50.3)+1}*mm" z_length="102*mm"/>'
                      f'<rphi_layout phi0="0" phi_tilt="0" nphi="1" rc="{250+50*layer}*mm" dr="0"/>'
                      '<z_layout z0="0" nz="1" dr="0"/></layer>' for layer in range(5))
            + '</detector><detector name="unrelated" id="99"/>'
            '</detectors></lccdd>'
        )

    def test_scope_a_has_five_distinct_physical_parameters(self):
        expected = {700102: (1, 1), 700104: (1, 3), 700105: (1, 4),
                    700106: (1, 5), 700202: (2, 1)}
        self.assertTrue(is_multi(MODE))
        self.assertTrue(is_multi(MODE + '_negative'))
        self.assertFalse(is_multi('l1_y_rx_ry_rz_contained'))
        self.assertEqual(labels(MODE), list(expected))
        self.assertEqual(labels(MODE + '_negative'), list(expected))
        self.assertEqual({label: mapping(label) for label in labels(MODE)}, expected)
        for label in (700101, 700103, 700301, 700404):
            with self.assertRaises(ValueError):
                mapping(label)

    def test_solver_values_cannot_mix_sensors_or_units(self):
        values = {700102: .11, 700104: .0012, 700105: -.0007,
                  700106: .0009, 700202: -.09}
        shifts, rotations = pose_increments(values)
        self.assertEqual(shifts, {1: .11, 2: -.09})
        self.assertEqual(rotations, {1: [.0012, -.0007, .0009]})
        self.assertEqual(values[700202], -.09)
        for invalid in ({700102: float('nan')}, {700104: .001},
                        {700102: .1, 700402: .1}):
            with self.assertRaises(ValueError):
                pose_increments(invalid)

    def test_signed_fixture_keeps_the_selected_sensor_scope(self):
        positive_shifts, positive_rotations = fixture(MODE)
        negative_shifts, negative_rotations = fixture(MODE + '_negative')
        self.assertEqual(set(positive_shifts), {1, 2})
        self.assertTrue(all(value != 0 for value in positive_shifts.values()))
        self.assertEqual(positive_shifts, negative_shifts)
        self.assertEqual(set(positive_rotations), {1})
        np.testing.assert_allclose(negative_rotations[1],
                                   -np.array(positive_rotations[1]), atol=0, rtol=0)

    def test_editing_one_sensor_does_not_edit_a_shared_template(self):
        output = self.root / 'l1.xml'
        source = detector(self.nominal)
        original_module = structure(source.find("./module[@name='shared']"))
        displaced_xml(self.nominal, output, {1: .08},
                      rotations={1: [.001, .0005, -.0005]}, rotation_axis='xyz')
        actual = detector(output)
        self.assertEqual(structure(actual.find("./module[@name='shared']")),
                         original_module)
        self.assertNotEqual(actual.find("./layer[@id='1']").get('module'), 'shared')
        for layer in (0, 2, 3, 4):
            self.assertEqual(actual.find(f"./layer[@id='{layer}']").get('module'),
                             'shared')
            self.assertEqual(structure(component(output, layer)),
                             structure(component(self.nominal, layer)))

    def test_simultaneous_updates_preserve_all_unrelated_xml(self):
        output = self.root / 'both.xml'
        displaced_xml(self.nominal, output, {1: .08, 2: -.06},
                      rotations={1: [.001, .0005, -.0005]}, rotation_axis='xyz')
        parent = ET.parse(self.nominal).getroot()
        actual = ET.parse(output).getroot()
        changed = actual.find("./detectors/detector[@name='SiMAPS']")
        original = parent.find("./detectors/detector[@name='SiMAPS']")
        for layer in (1, 2):
            placement = changed.find(f"./layer[@id='{layer}']")
            active_name = placement.get('module')
            self.assertNotEqual(active_name, 'shared')
            active_module=changed.find(f"./module[@name='{active_name}']")
            normalize_expanded_envelopes(original.find("./module[@name='shared']"),
                                         active_module,original.find(f"./layer[@id='{layer}']"),placement)
            changed.remove(active_module)
            placement.set('module', 'shared')
        self.assertEqual(structure(actual), structure(parent))
        self.assertEqual(len(original.findall('module')), 1)

    def test_world_translation_and_noncommuting_rotation_compose_independently(self):
        first, second, third, restored = [self.root / (name + '.xml')
                                          for name in ('first', 'second', 'third', 'restored')]
        w = np.array([.001, .0005, -.0005])
        v = np.array([-.0004, .0003, .0002])
        displaced_xml(self.nominal, first, {1: .08, 2: -.06},
                      rotations={1: w}, rotation_axis='xyz')
        displaced_xml(first, second, {1: -.01, 2: .02},
                      rotations={1: v}, rotation_axis='xyz')
        nominal_center, nominal_rotation = world_pose(self.nominal, 1)
        center, rotation = world_pose(second, 1)
        np.testing.assert_allclose(center - nominal_center, [0, .07, 0], atol=1e-15, rtol=0)
        np.testing.assert_allclose(rotation,
                                   np.array(rotation_exp(v)) @ np.array(rotation_exp(w)) @ nominal_rotation,
                                   atol=5e-13, rtol=0)
        np.testing.assert_allclose(world_pose(second, 2)[0], [0, -.04, 0], atol=1e-15, rtol=0)
        np.testing.assert_allclose(world_pose(second, 2)[1], world_pose(self.nominal, 2)[1],
                                   atol=5e-13, rtol=0)
        displaced_xml(second, third, {1: .01, 2: -.02},
                      rotations={1: -v}, rotation_axis='xyz')
        displaced_xml(third, restored, {1: -.08, 2: .06},
                      rotations={1: -w}, rotation_axis='xyz')
        for layer in range(5):
            center, rotation = world_pose(restored, layer)
            expected_center, expected_rotation = world_pose(self.nominal, layer)
            np.testing.assert_allclose(center, expected_center, atol=1e-15, rtol=0)
            np.testing.assert_allclose(rotation, expected_rotation, atol=5e-13, rtol=0)

    def test_configuration_pins_distinct_actual_sensor_identities(self):
        config = json.loads((Path(__file__).parent /
                             'telescope_alignment/l1_y_rx_ry_rz_l2_y.json').read_text())
        self.assertEqual([parameter['label'] for parameter in config['parameters']], labels(MODE))
        identities = {1: (272631839, 216173056991691039),
                      2: (272633887, 216173194430644511)}
        for parameter in config['parameters']:
            layer, column = mapping(parameter['label'])
            self.assertEqual(parameter['layer'], layer)
            self.assertEqual(parameter['unit'], 'mm' if column == 1 else 'rad')
            self.assertEqual((parameter['volume_id'], parameter['geometry_id']), identities[layer])
        self.assertEqual(set(parameter['layer'] for parameter in config['parameters']), {1, 2})

    def test_refuses_fixed_sensor_mutation_and_output_overwrite(self):
        output = self.root / 'existing.xml'
        output.write_text('preserved output')
        with self.assertRaisesRegex(ValueError, 'must be new'):
            displaced_xml(self.nominal, output, {1: .08, 2: -.06})
        self.assertEqual(output.read_text(), 'preserved output')
        for layer in (0, 3, 4):
            with self.assertRaises(ValueError):
                displaced_xml(self.nominal, self.root / f'fixed_{layer}.xml', {layer: .01})

    def test_scope_a_xml_gate_refuses_an_unselected_l2_rotation(self):
        output = self.root / 'forbidden_l2_rotation.xml'
        displaced_xml(self.nominal, output, {1: .08, 2: -.06},
                      rotations={1: [.001, .0005, -.0005]}, rotation_axis='xyz')
        xml_identity(self.nominal, output, labels(MODE))
        tree = ET.parse(output)
        sensor = tree.getroot().find("./detectors/detector[@name='SiMAPS']")
        name = sensor.find("./layer[@id='2']").get('module')
        sensor.find(f"./module[@name='{name}']/module_component/rotation").set('z', '.001')
        tree.write(output, encoding='unicode')
        with self.assertRaises(AssertionError):
            xml_identity(self.nominal, output, labels(MODE))

    def test_xml_gate_refuses_changes_to_the_parent_envelope(self):
        output = self.root / 'forbidden_envelope.xml'
        displaced_xml(self.nominal, output, {1: .08, 2: -.06},
                      rotations={1: [.001, .0005, -.0005]}, rotation_axis='xyz')
        tree = ET.parse(output)
        tree.getroot().find("./detectors/detector[@name='SiMAPS']/"
                            "module[@name='shared']/module_envelope").set('thickness', '0.7*mm')
        tree.write(output, encoding='unicode')
        with self.assertRaises(AssertionError):
            xml_identity(self.nominal, output, labels(MODE))

    def test_scope_b_adds_only_the_three_l2_rotation_columns(self):
        mode = 'l1_l2_y_rx_ry_rz'
        expected = {700102: (1, 1), 700104: (1, 3), 700105: (1, 4),
                    700106: (1, 5), 700202: (2, 1), 700204: (2, 3),
                    700205: (2, 4), 700206: (2, 5)}
        self.assertTrue(is_multi(mode))
        self.assertTrue(is_multi(mode + '_negative'))
        self.assertEqual(labels(mode), list(expected))
        self.assertEqual(labels(mode + '_negative'), list(expected))
        self.assertEqual({label: mapping(label) for label in labels(mode)}, expected)
        self.assertEqual(labels(mode)[:5], labels(MODE))
        self.assertNotIn(700304, labels(mode))
        for fixed in (700101, 700103, 700201, 700203, 700404, 700405):
            with self.assertRaises(ValueError):
                mapping(fixed)

    def test_scope_b_rotation_decoding_has_no_cross_sensor_leakage(self):
        first = {700104: .0011, 700105: -.0002, 700106: .0003}
        second = {700204: -.0012, 700205: .0006, 700206: .0007}
        self.assertEqual(pose_increments(first), ({}, {1: [.0011, -.0002, .0003]}))
        self.assertEqual(pose_increments(second), ({}, {2: [-.0012, .0006, .0007]}))
        values = {700102: .08, **first, 700202: -.06, **second}
        self.assertEqual(pose_increments(values),
                         ({1: .08, 2: -.06},
                          {1: [.0011, -.0002, .0003], 2: [-.0012, .0006, .0007]}))
        for missing in second:
            incomplete = dict(values)
            incomplete.pop(missing)
            with self.assertRaises(ValueError):
                pose_increments(incomplete)
        with self.assertRaises(ValueError):
            pose_increments({**values, 700205: float('inf')})

    def test_scope_b_signed_fixture_does_not_redefine_scope_a(self):
        a_shifts, a_rotations = fixture(MODE)
        shifts, rotations = fixture('l1_l2_y_rx_ry_rz')
        negative_shifts, negative_rotations = fixture('l1_l2_y_rx_ry_rz_negative')
        self.assertEqual(shifts, a_shifts)
        self.assertEqual(rotations[1], a_rotations[1])
        self.assertEqual(set(rotations), {1, 2})
        self.assertTrue(all(value != 0 for value in rotations[2]))
        self.assertNotEqual(rotations[1], rotations[2])
        self.assertEqual(negative_shifts, shifts)
        for layer in (1, 2):
            np.testing.assert_allclose(negative_rotations[layer],
                                       -np.array(rotations[layer]), atol=0, rtol=0)

    def test_scope_b_both_sensor_rotations_compose_and_invert_independently(self):
        w = {1: np.array([.001, .0005, -.0005]),
             2: np.array([-.0012, .0006, .0006])}
        v = {1: np.array([-.0004, .0003, .0002]),
             2: np.array([.0002, -.0001, .0003])}
        updates = [({1: .08, 2: -.06}, w), ({1: -.01, 2: .02}, v),
                   ({1: .01, 2: -.02}, {layer: -vector for layer, vector in v.items()}),
                   ({1: -.08, 2: .06}, {layer: -vector for layer, vector in w.items()})]
        parent = self.nominal
        stages = []
        for index, (shifts, rotations) in enumerate(updates):
            output = self.root / f'both_rotations_{index}.xml'
            displaced_xml(parent, output, shifts, rotations=rotations, rotation_axis='xyz')
            xml_identity(parent, output, labels('l1_l2_y_rx_ry_rz'))
            stages.append(output)
            parent = output
        for layer, y in ((1, .07), (2, -.04)):
            center, rotation = world_pose(stages[1], layer)
            np.testing.assert_allclose(center, [0, y, 0], atol=1e-15, rtol=0)
            expected = (np.array(rotation_exp(v[layer])) @ np.array(rotation_exp(w[layer]))
                        @ world_pose(self.nominal, layer)[1])
            np.testing.assert_allclose(rotation, expected, atol=5e-13, rtol=0)
        for layer in range(5):
            center, rotation = world_pose(stages[-1], layer)
            nominal_center, nominal_rotation = world_pose(self.nominal, layer)
            np.testing.assert_allclose(center, nominal_center, atol=1e-15, rtol=0)
            np.testing.assert_allclose(rotation, nominal_rotation, atol=5e-13, rtol=0)
        for layer in (0, 3, 4):
            self.assertEqual(structure(component(stages[1], layer)),
                             structure(component(self.nominal, layer)))

    def test_scope_b_xml_gate_allows_both_rotations_and_rejects_fixed_layer_changes(self):
        output = self.root / 'scope_b.xml'
        shifts, rotations = fixture('l1_l2_y_rx_ry_rz')
        displaced_xml(self.nominal, output, shifts, rotations=rotations, rotation_axis='xyz')
        xml_identity(self.nominal, output, labels('l1_l2_y_rx_ry_rz'))
        with self.assertRaises(AssertionError):
            xml_identity(self.nominal, output, labels(MODE))
        tree = ET.parse(output)
        fixed = tree.getroot().find("./detectors/detector[@name='SiMAPS']/"
                                    "layer[@id='3']/rphi_layout")
        fixed.set('rc', '401*mm')
        tree.write(output, encoding='unicode')
        with self.assertRaises(AssertionError):
            xml_identity(self.nominal, output, labels('l1_l2_y_rx_ry_rz'))

    def test_scope_b_configuration_has_eight_unique_labels_and_actual_sensor_ids(self):
        mode = 'l1_l2_y_rx_ry_rz'
        config = json.loads((Path(__file__).parent /
                             'telescope_alignment/l1_l2_y_rx_ry_rz.json').read_text())
        parameters = config['parameters']
        self.assertEqual([parameter['label'] for parameter in parameters], labels(mode))
        self.assertEqual(len({parameter['label'] for parameter in parameters}), 8)
        identities = {1: (272631839, 216173056991691039),
                      2: (272633887, 216173194430644511)}
        columns = {1: 'global_y', 3: 'global_rx', 4: 'global_ry', 5: 'global_rz'}
        for layer in (1, 2):
            selected = [parameter for parameter in parameters if parameter['layer'] == layer]
            self.assertEqual(len(selected), 4)
            self.assertEqual({parameter['axis'] for parameter in selected}, set(columns.values()))
            for parameter in selected:
                sensor, column = mapping(parameter['label'])
                self.assertEqual(sensor, layer)
                self.assertEqual(parameter['axis'], columns[column])
                self.assertEqual(parameter['unit'], 'mm' if column == 1 else 'rad')
                self.assertEqual((parameter['volume_id'], parameter['geometry_id']), identities[layer])
                self.assertEqual(parameter['pivot'], 'sensitive_surface_center')
                self.assertEqual(parameter['composition'],
                                 'world_translation' if column == 1 else 'left_global_exp')

    def test_measurement_edm_rounding_and_one_ulp_corruption_are_distinguishable(self):
        import ROOT
        collection = ROOT.edm4eic.Measurement2DCollection()
        measurement = collection.create()
        local = np.array([-17.112654700154234, -9.422352232365252])
        measurement.setLoc(ROOT.edm4hep.Vector2f(*map(float, local)))
        stored = np.array([measurement.getLoc().a, measurement.getLoc().b])
        np.testing.assert_array_equal(stored, [-17.112655639648438, -9.422351837158203])
        np.testing.assert_array_equal(stored, local.astype(np.float32).astype(float))
        # The real EDM round trip exceeds the former fixed 2D norm threshold,
        # while each coordinate is rounded within half its binary32 spacing.
        self.assertGreater(float(np.linalg.norm(stored - local)), 1e-6)
        half_ulp = np.abs(np.spacing(stored.astype(np.float32))).astype(float) / 2
        self.assertTrue(np.all(np.abs(stored - local) <= half_ulp))

        small = np.array([.25, -.125], dtype=np.float32)
        measurement.setLoc(ROOT.edm4hep.Vector2f(*map(float, small)))
        np.testing.assert_array_equal([measurement.getLoc().a, measurement.getLoc().b], small)
        corrupt = small.copy()
        corrupt[0] = np.nextafter(corrupt[0], np.float32(np.inf))
        measurement.setLoc(ROOT.edm4hep.Vector2f(*map(float, corrupt)))
        actual = np.array([measurement.getLoc().a, measurement.getLoc().b])
        self.assertGreater(abs(actual[0] - small[0]), 0.)
        self.assertLess(float(np.linalg.norm(actual - small)), 1e-6)
        self.assertFalse(np.array_equal(actual, small.astype(float)))


if __name__ == '__main__':
    unittest.main()
