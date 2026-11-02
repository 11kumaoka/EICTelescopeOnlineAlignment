"""Single-sensor configuration, Geometry isolation and independent information checks."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from validation.telescope_closure.closure import displaced_xml, MODULE_TO_GLOBAL
from calibration.alignment.information import analyze
from validation.telescope_closure.multi_sensor import (
    aligned_sensors, configuration, fixture, labels, pose_increments, references,
    xml_identity,
)


def component(root, layer):
    detector = root.find("./detectors/detector[@name='SiMAPS']")
    name = detector.find("./layer[@id='%d']" % layer).get('module')
    return detector.find("./module[@name='%s']/module_component" % name)


def structure(node):
    return node.tag, dict(node.attrib), (node.text or '').strip(), tuple(structure(child) for child in node)


def native_fixture(path, tracks=8, displacement=.08, zero_requested_derivative=False):
    """Mathematical straight-line fixture; not Geant4 or a new inference model."""
    events = []
    for event in range(tracks):
        # Repeated rays intentionally leave unrequested pose components weak.
        sy, sz = .01, .02; u0, v0 = -2., 3.
        direction = np.array([1., sy, sz]); direction /= np.linalg.norm(direction)
        equations = []
        for layer in range(5):
            dx = 50.*layer; u, v = u0-sy*dx, v0-sz*dx
            global_ = [[-sy, 1., 0., v, sy*v, -sy*u],
                       [-sz, 0., 1., -u, sz*v, -sz*u]]
            if zero_requested_derivative and layer == 1: global_[0][1] = 0.
            equations.append({'layer': layer, 'source_index': layer,
                'cell_id': 31 | (layer << 11) | (1 << 22) | (1 << 28),
                'volume_id': 31 | (layer << 11) | (1 << 22) | (1 << 28),
                'geometry_id': 216172919552737567 + layer*137438953472,
                'center_mm': [250.+dx, 0., 0.],
                'measurement': [[u+(displacement if layer == 1 else 0.)], [v]],
                'prediction': [[u], [v]], 'covariance': [[1e-4, 0.], [0., 1e-4]],
                'whitening': [[100., 0.], [0., 100.]],
                'local': [[1., 0., dx, 0.], [0., 1., 0., dx]], 'global': global_})
        events.append({'accepted': True, 'event_number': event,
            'reference_direction': direction.tolist(), 'equations': equations})
    path.write_text(json.dumps({'model_sha256': 'unit-test-closed-form', 'events': events}))


class NativeSingleY(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name); self.nominal = self.directory/'nominal.xml'
        root = ET.Element('lccdd'); detectors = ET.SubElement(root, 'detectors')
        detector = ET.SubElement(detectors, 'detector', name='SiMAPS', id='31')
        module = ET.SubElement(detector, 'module', name='shared')
        ET.SubElement(module, 'module_envelope', width='100.5*mm', length='100.5*mm', thickness='.65*mm')
        sensor = ET.SubElement(module, 'module_component', width='100*mm', length='.05*mm',
                               thickness='100*mm', material='Silicon', sensitive='true')
        ET.SubElement(sensor, 'position', x='0', y='0', z='0')
        ET.SubElement(sensor, 'rotation', x='-pi/2', y='-pi/2', z='0')
        for layer in range(5):
            radius=250+50*layer
            placement=ET.SubElement(detector,'layer',id=str(layer),module='shared')
            ET.SubElement(placement,'barrel_envelope',inner_r=f'{radius-1.025}*mm',
                          outer_r=f'{math.hypot(radius+.425,50.3)+1}*mm',z_length='102*mm')
            ET.SubElement(placement,'rphi_layout',phi0='0',phi_tilt='0',nphi='1',
                          rc=f'{radius}*mm',dr='0')
            ET.SubElement(placement,'z_layout',z0='0',nz='1',dr='0')
        ET.ElementTree(root).write(self.nominal, encoding='unicode')

    def test_configuration_selects_only_actual_l1_global_y(self):
        config = configuration('l1_y')
        self.assertEqual(labels('l1_y'), [700102])
        self.assertEqual(aligned_sensors('l1_y'), [1])
        self.assertEqual(references('l1_y'), [0, 2, 3, 4])
        self.assertEqual(config['fixed_reference_sensors'], references('l1_y'))
        parameter = config['parameters'][0]
        self.assertEqual((parameter['system'], parameter['layer'], parameter['axis'], parameter['unit']),
                         (31, 1, 'global_y', 'mm'))
        self.assertEqual((parameter['volume_id'], parameter['geometry_id']),
                         (272631839, 216173056991691039))
        self.assertEqual(config['aligned_sensors'][0]['components'], ['global_y'])
        self.assertEqual(parameter['composition'], 'world_translation')
        self.assertEqual(config['containment'], {'module_envelope':'preserve_clearance_air_growth',
                         'layer_envelope':'preserve_clearance_air_growth', 'sensitive_corners':8})

    def test_existing_configurations_keep_their_exact_parameter_scopes(self):
        expected = {'l1_y_rx_ry_rz_l2_y': [700102,700104,700105,700106,700202],
                    'l1_l2_y_rx_ry_rz': [700102,700104,700105,700106,700202,700204,700205,700206],
                    'l1_l2_y_rx_ry_rz_l3_y': [700102,700104,700105,700106,700202,700204,700205,700206,700302],
                    'l1_l2_l3_y_rx_ry_rz': [700102,700104,700105,700106,700202,700204,700205,700206,700302,700304,700305,700306]}
        for mode, selected in expected.items(): self.assertEqual(labels(mode), selected)

    def test_translation_requires_no_rotation_and_rejects_nonfinite_values(self):
        self.assertEqual(pose_increments({700102:.08}), ({1:.08}, {}))
        self.assertEqual(fixture('l1_y'), ({1:.08}, {}))
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.assertRaises(ValueError): pose_increments({700102:value})

    def test_only_l1_is_cloned_and_displaced_without_rotation(self):
        before = self.nominal.read_bytes(); target = self.directory/'candidate.xml'
        displaced_xml(self.nominal, target, {1:.08}, rotations={}, rotation_axis='xyz', allowed_layers=(1,))
        xml_identity(self.nominal, target, [700102])
        original = ET.parse(self.nominal).getroot(); actual = ET.parse(target).getroot()
        for layer in range(5):
            a, b = component(original, layer), component(actual, layer)
            self.assertEqual(a.find('rotation').attrib, b.find('rotation').attrib)
            position = [float(b.find('position').get(axis).replace('*mm', '')) for axis in 'xyz']
            np.testing.assert_array_equal(np.asarray(MODULE_TO_GLOBAL) @ position,
                                          [0., .08 if layer == 1 else 0., 0.])
            if layer != 1: self.assertEqual(structure(a), structure(b))
        self.assertEqual(self.nominal.read_bytes(), before)

    def test_translation_inverse_preserves_all_fixed_components(self):
        first, restored = self.directory/'first.xml', self.directory/'restored.xml'
        displaced_xml(self.nominal, first, {1:.08}, rotations={}, rotation_axis='xyz', allowed_layers=(1,))
        displaced_xml(first, restored, {1:-.08}, rotations={}, rotation_axis='xyz', allowed_layers=(1,))
        xml_identity(first, restored, [700102])
        root = ET.parse(restored).getroot()
        for layer in range(5):
            self.assertTrue(all(float(component(root,layer).find('position').get(axis).replace('*mm',''))==0. for axis in 'xyz'))
            self.assertEqual(component(root,layer).find('rotation').attrib,
                             {'x':'-pi/2','y':'-pi/2','z':'0'})

    def test_xml_gate_rejects_rotations_shape_and_fixed_sensor_edits(self):
        target = self.directory/'candidate.xml'
        displaced_xml(self.nominal,target,{1:.08},rotations={},rotation_axis='xyz',allowed_layers=(1,))
        base = ET.parse(target).getroot()
        for index, mutate in enumerate((lambda r: component(r,1).find('rotation').set('z','.001'),
                                        lambda r: component(r,1).set('width','99*mm'),
                                        lambda r: component(r,2).find('position').set('z','.001*mm'),
                                        lambda r: component(r,1).find('position').set('y','.001*mm'))):
            root = copy.deepcopy(base); mutate(root); path = self.directory/f'invalid_{index}.xml'
            ET.ElementTree(root).write(path,encoding='unicode')
            with self.assertRaises(AssertionError): xml_identity(self.nominal,path,[700102])

    def test_information_matches_closed_form_single_parameter_line_fit(self):
        source = self.directory/'equations.json'; native_fixture(source, tracks=8)
        report = analyze(source, [700102]); selected = report['selected']
        # At x=300 among five equally spaced planes, straight-line leverage is
        # 1/5 + (300-350)^2 / sum((x-350)^2) = 0.3. Remaining information is 0.7/sigma^2.
        expected = 8*.7/1e-4
        self.assertEqual(selected['rank'],1)
        self.assertAlmostEqual(selected['information'][0][0],expected,places=7)
        self.assertAlmostEqual(selected['sigma'][0],1/math.sqrt(expected),places=14)
        self.assertAlmostEqual(selected['linearized_displacement'][0],.08,places=12)
        self.assertEqual(report['fixed_reference_layers'],[0,2,3,4])
        self.assertTrue(report['fixed_reference_rows_have_no_selected_global_columns'])

    def test_unrequested_six_dof_weak_modes_are_reported_separately(self):
        source = self.directory/'parallel.json'; native_fixture(source)
        report = analyze(source,[700102])
        self.assertTrue(report['fixed_reference_selected_design_full_rank'])
        self.assertLess(report['omitted_X_Z_diagnostic']['rank'],6)
        self.assertIsNone(report['omitted_X_Z_diagnostic']['covariance_mixed_units'])
        self.assertFalse(report['uncertainty_coverage_validated'])
        self.assertFalse(report['truth_used_for_inference'])

    def test_unobservable_requested_parameter_has_no_covariance_or_estimate(self):
        source = self.directory/'unobservable.json'
        native_fixture(source,zero_requested_derivative=True)
        report = analyze(source,[700102]); selected = report['selected']
        self.assertEqual(selected['rank'],0)
        self.assertFalse(report['fixed_reference_selected_design_full_rank'])
        self.assertIsNone(selected['covariance_mixed_units'])
        self.assertNotIn('linearized_displacement',selected)

    def test_surface_identity_changes_are_rejected_before_inference(self):
        source = self.directory/'wrong_id.json'; native_fixture(source)
        data = json.loads(source.read_text()); data['events'][1]['equations'][1]['geometry_id']+=1
        source.write_text(json.dumps(data))
        with self.assertRaises(ValueError): analyze(source,[700102])


if __name__=='__main__': unittest.main()
