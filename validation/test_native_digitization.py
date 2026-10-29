"""CellID selection and PODIO value-preservation tests, without Simulation Truth."""
import json
from pathlib import Path
import tempfile
import unittest

from digitization.native import checked_simulation_binding, bit_fields, checked_geometry_contract, decode, geometry_contract, select_channels, write_channels


DESCRIPTOR = 'system:8,barrel:3,layer:6,wedge:5,module:6,sensor:4,x:32:-16,z:-16'


def cell(layer, x=0, z=0):
    return 31 | (layer << 11) | (1 << 22) | (1 << 28) | ((x & 65535) << 32) | ((z & 65535) << 48)


class NativeDigitization(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.geometry = self.root/'native.xml'
        self.geometry.write_text('<lccdd><readouts><readout name="SiMAPSHits">'
            '<segmentation type="CartesianGridXZ" grid_size_x="0.034641*mm" grid_size_z="0.034641*mm"/>'
            '<id>' + DESCRIPTOR + '</id></readout></readouts><detectors>'
            '<detector name="SiMAPS" id="31" type="DD4hep_SiTrackerBarrel" readout="SiMAPSHits"/>'
            '</detectors></lccdd>')
        self.audit_path = self.root/'geometry.json'
        self.audit = {'passed': True, 'events': [], 'geometry': str(self.geometry), 'sensors': [
            {'layer': layer, 'volume_id': cell(layer) if layer < 5 else (100 if layer == 5 else 92) | (layer << 11) | (1 << 22) | (1 << 28),
             'geometry_id': 1000 + layer} for layer in range(7)]}
        self.audit_path.write_text(json.dumps(self.audit))
        self.contract = geometry_contract(self.geometry, self.audit_path)

    def test_descriptor_decodes_signed_pixels_and_nonzero_offsets(self):
        fields = bit_fields(DESCRIPTOR)
        value = cell(3, -17, -33)
        self.assertEqual(decode(value, fields['system']), 31)
        self.assertEqual(decode(value, fields['layer']), 3)
        self.assertEqual(decode(value, fields['x']), -17)
        self.assertEqual(decode(value, fields['z']), -33)
        self.assertEqual(value & self.contract['volume_mask'], cell(3))
        self.assertEqual(fields['x'], {'offset': 32, 'width': 16, 'signed': True})

    def test_invalid_descriptors_are_rejected(self):
        for descriptor in ('', 'a:0', 'a:65', 'a:-1:8', 'a:63:2',
                           'a:8,a:8', 'a:0:8,b:7:8', 'a:1:2:3'):
            with self.subTest(descriptor=descriptor), self.assertRaises(ValueError):
                bit_fields(descriptor)

    def test_selection_orders_only_by_real_layer_and_preserves_payload(self):
        order = [3, 1, 4, 0, 2]
        values = [(cell(layer, -layer-1, layer+2), 101+layer, -200+layer) for layer in order]
        original = list(values)
        indices, decision = select_channels(values, self.contract)
        self.assertTrue(decision['accepted'])
        self.assertEqual(indices, [3, 1, 4, 0, 2])
        self.assertEqual([values[i] for i in indices],
                         [(cell(layer, -layer-1, layer+2), 101+layer, -200+layer) for layer in range(5)])
        self.assertEqual(values, original)

    def test_missing_and_multiple_channels_are_both_reported(self):
        values = [(cell(layer), 1, 0) for layer in (0, 1, 2, 2, 3)]
        indices, decision = select_channels(values, self.contract)
        self.assertEqual(indices, [])
        self.assertFalse(decision['accepted'])
        self.assertEqual(decision['layer_multiplicity'], {'0': 1, '1': 1, '2': 2, '3': 1, '4': 0})
        self.assertEqual(decision['reasons'], ['layer_2_multiplicity_2', 'layer_4_multiplicity_0'])

    def test_wrong_barrel_system_and_sensor_cannot_be_reinterpreted(self):
        for wrong in (cell(2) | (1 << 8), cell(2) ^ 31 ^ 100, cell(2) ^ (1 << 28), cell(5)):
            with self.subTest(cell=wrong):
                values = [(cell(layer), 1, 0) for layer in range(5)]; values[2] = (wrong, 7, -9)
                _, decision = select_channels(values, self.contract)
                self.assertFalse(decision['accepted'])
                self.assertEqual(decision['invalid_channels'][0]['source_hit_index'], 2)
                self.assertIn('invalid_detector_cell_id', decision['reasons'])

    def test_audit_identity_and_zero_event_contract_are_required(self):
        for mutate in (lambda a: a.update(events=[{}]),
                       lambda a: a.update(geometry=str(self.root/'other.xml')),
                       lambda a: a['sensors'][1].update(volume_id=a['sensors'][0]['volume_id'])):
            audit = json.loads(json.dumps(self.audit)); mutate(audit)
            self.audit_path.write_text(json.dumps(audit))
            with self.assertRaises(ValueError): geometry_contract(self.geometry, self.audit_path)

    def test_original_endcap_constructor_is_explicitly_refused(self):
        self.geometry.write_text(self.geometry.read_text().replace('DD4hep_SiTrackerBarrel', 'DD4hep_SiTrackerEndcap2'))
        with self.assertRaises(ValueError): geometry_contract(self.geometry, self.audit_path)

    def test_completed_audit_detects_xml_changes_after_real_reload(self):
        from research_common import digest, done
        done(self.root, passed=True, geometry_sha256=digest(self.geometry))
        self.assertEqual(checked_geometry_contract(self.geometry, self.audit_path), self.contract)
        self.geometry.write_text(self.geometry.read_text() + '\n')
        with self.assertRaises(RuntimeError):
            checked_geometry_contract(self.geometry, self.audit_path)

    def test_invalid_unsigned_cell_ids_are_recorded(self):
        for wrong in (-1, 1 << 64):
            _, decision = select_channels([(wrong, 3, 8)], self.contract)
            self.assertFalse(decision['accepted'])
            self.assertEqual(decision['invalid_channels'][0]['reason'], 'invalid_uint64_cell_id')

    def test_podio_clones_preserve_complete_header_and_raw_integer_values(self):
        try:
            import ROOT
            from podio import reading
            from podio.frame import Frame
        except ImportError:
            self.skipTest('The fixed ROOT/PODIO environment is required')
        headers = ROOT.edm4hep.EventHeaderCollection(); header = headers.create()
        header.setRunNumber(119); header.setEventNumber(207)
        header.setTimeStamp(123456789012345); header.setWeight(2.25)
        raw = ROOT.edm4eic.RawTrackerHitCollection()
        values = [(cell(layer, -17-layer, -33-layer), 5100+layer, -18+layer) for layer in range(5)]
        for identifier, charge, timestamp in values:
            hit = raw.create(); hit.setCellID(identifier); hit.setCharge(charge); hit.setTimeStamp(timestamp)
        target = self.root/'channels.root'
        selected = [(header.clone(), [hit.clone() for hit in raw], (207, values))]
        write_channels(selected, target)
        frames = list(reading.get_reader(str(target)).get('events'))
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(set(frame.getAvailableCollections()), {'EventHeader', 'TelescopeChannels'})
        actual = frame.get('EventHeader')[0]
        self.assertEqual((actual.getRunNumber(), actual.getEventNumber(), actual.getTimeStamp(), actual.getWeight()),
                         (119, 207, 123456789012345, 2.25))
        self.assertEqual([(int(h.getCellID()), int(h.getCharge()), int(h.getTimeStamp()))
                          for h in frame.get('TelescopeChannels')], values)
        with self.assertRaises(FileExistsError): write_channels(selected, target)


class SimulationProducerBinding(unittest.TestCase):
    """Simulated file fixtures exercise receipt integrity without running Geant4."""

    def setUp(self):
        from research_common import digest, done
        from simulation.native import gun_arguments
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.stage = self.root/'simulated_producer'; self.stage.mkdir()
        self.geometry = self.stage/'simulation.xml'
        readouts = ''.join('<readout name="%s"><segmentation type="%s" grid_size_x="1*mm" grid_size_%s="1*mm"/><id>%s</id></readout>'
            % (name, segmentation, axis, DESCRIPTOR) for name, segmentation, axis in
            (('SiMAPSHits', 'CartesianGridXZ', 'z'), ('MPGDHits', 'CartesianGridXY', 'y'), ('ACLGADHits', 'CartesianGridXY', 'y')))
        detectors = ''.join('<detector name="%s" id="%s" type="DD4hep_SiTrackerBarrel" readout="%s"/>'
            % values for values in (('SiMAPS', '31', 'SiMAPSHits'), ('MPGDPlane', '100', 'MPGDHits'), ('ACLGADPlane', '92', 'ACLGADHits')))
        self.geometry.write_text('<lccdd><readouts>'+readouts+'</readouts><detectors>'+detectors+'</detectors></lccdd>')
        self.audit = self.stage/'simulation_geometry_audit'; self.audit.mkdir()
        self.audit_path = self.audit/'geometry.json'
        self.sensors = [{'layer': layer, 'volume_id': cell(layer) if layer < 5 else (100 if layer == 5 else 92) | (layer << 11) | (1 << 22) | (1 << 28),
                         'geometry_id': 1000+layer, 'center_mm': [250.+50*layer, 0., 0.]} for layer in range(7)]
        self.audit_path.write_text(json.dumps({'passed': True, 'events': [], 'geometry': str(self.geometry), 'sensors': self.sensors}))
        done(self.audit, passed=True, geometry_sha256=digest(self.geometry))
        transport = self.stage/'transport'; transport.mkdir()
        self.simulation = transport/'events.edm4hep.root'; self.simulation.write_bytes(b'Simulated ROOT bytes for receipt-only unit tests')
        self.config = {'schema': 'native-npsim-v1', 'particle': 'e-', 'energy_GeV': 1., 'beam': 'pencil',
            'position_mm': [249., 0., 0.], 'mean_direction': [1., 0., 0.], 'events': 5, 'seed': 123,
            'simulation_geometry_sha256': digest(self.geometry)}
        self.command = {'argv': ['npsim', '--compactFile', str(self.geometry), '--outputFile', str(self.simulation),
            '--numberOfEvents', '5', '--random.seed', '123'] + gun_arguments('pencil')}
        self.publish()

    def publish(self):
        from research_common import digest, done
        (self.stage/'configuration.json').write_text(json.dumps(self.config))
        (self.stage/'transport/command.json').write_text(json.dumps(self.command))
        marker = self.stage/'complete.json'
        if marker.exists(): marker.unlink()
        done(self.stage, passed=True, simulation_sha256=digest(self.simulation))

    def binding(self):
        return checked_simulation_binding(self.simulation, self.geometry, self.audit_path, self.stage)

    def test_exact_completed_producer_is_bound(self):
        record = self.binding()
        self.assertTrue(record['exact_simulation_xml'])
        self.assertTrue(record['all_seven_actual_detector_identities_equal'])
        self.assertFalse(record['truth_used_for_selection'])
        self.assertEqual(record['events'], 5)

    def test_missing_receipt_and_modified_root_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'simulation-stage'):
            checked_simulation_binding(self.simulation, self.geometry, self.audit_path, None)
        self.simulation.write_bytes(b'Corrupted simulated ROOT')
        with self.assertRaisesRegex(RuntimeError, 'Modified stage'):
            self.binding()

    def test_same_bytes_from_unrelated_root_cannot_be_reinterpreted(self):
        other = self.root/'unrelated.root'; other.write_bytes(self.simulation.read_bytes())
        with self.assertRaisesRegex(ValueError, 'does not bind'):
            checked_simulation_binding(other, self.geometry, self.audit_path, self.stage)

    def test_executed_seed_energy_and_generator_must_match_snapshot(self):
        original = json.loads(json.dumps(self.command))
        for option, wrong in (('--random.seed', '456'), ('--numberOfEvents', '6'), ('--gun.energy', '5*GeV')):
            self.command = json.loads(json.dumps(original))
            self.command['argv'][self.command['argv'].index(option)+1] = wrong
            self.publish()
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.binding()

    def test_equivalent_survey_readout_requires_all_real_detector_ids(self):
        from research_common import digest, done
        supplied = self.root/'survey.xml'; supplied.write_bytes(self.geometry.read_bytes())
        audit = self.root/'survey_audit'; audit.mkdir()
        sensors = json.loads(json.dumps(self.sensors)); sensors[1]['center_mm'][1] = .08
        path = audit/'geometry.json'
        path.write_text(json.dumps({'passed': True, 'events': [], 'geometry': str(supplied), 'sensors': sensors}))
        done(audit, passed=True, geometry_sha256=digest(supplied))
        binding = checked_simulation_binding(self.simulation, supplied, path, self.stage)
        self.assertFalse(binding['physical_sensor_poses_identical'])
        self.assertTrue(binding['readout_definitions_equal'])
        sensors[6]['geometry_id'] += 1
        path.write_text(json.dumps({'passed': True, 'events': [], 'geometry': str(supplied), 'sensors': sensors}))
        (audit/'complete.json').unlink(); done(audit, passed=True, geometry_sha256=digest(supplied))
        with self.assertRaisesRegex(ValueError, 'identities differ'):
            checked_simulation_binding(self.simulation, supplied, path, self.stage)

    def test_endcap_or_changed_pixel_layout_is_refused(self):
        from research_common import digest, done
        for old, new in (('DD4hep_SiTrackerBarrel', 'DD4hep_SiTrackerEndcap2'), ('grid_size_z="1*mm"', 'grid_size_z="2*mm"')):
            supplied = self.root/('changed_'+str(len(list(self.root.glob('changed*'))))+'.xml')
            supplied.write_text(self.geometry.read_text().replace(old, new))
            audit = supplied.with_suffix(''); audit.mkdir()
            path = audit/'geometry.json'
            path.write_text(json.dumps({'passed': True, 'events': [], 'geometry': str(supplied), 'sensors': self.sensors}))
            done(audit, passed=True, geometry_sha256=digest(supplied))
            with self.subTest(change=new), self.assertRaises(ValueError):
                checked_simulation_binding(self.simulation, supplied, path, self.stage)


if __name__ == '__main__':
    unittest.main()
