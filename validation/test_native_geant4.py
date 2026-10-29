"""Non-physics safety checks for explicit Geant4 input provenance and gun setup."""
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from calibration.native_controller import input_provenance
from simulation.native import gun_arguments, gun_configuration, simulation_geometry


class NativeGeant4InputSafety(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_provenance_requires_both_file_and_payload_identity(self):
        path = self.directory / 'producer.json'
        record = {'schema': 'native-geant4-input-v1',
                  'input_origin': 'geant4_digitized',
                  'raw_root_sha256': 'a' * 64,
                  'channel_payload_sha256': 'b' * 64}
        path.write_text(json.dumps(record))
        self.assertEqual(input_provenance(path, 'a' * 64, 'b' * 64), record)
        for file_sha, payload_sha in [('c' * 64, 'b' * 64), ('a' * 64, 'c' * 64)]:
            with self.assertRaisesRegex(ValueError, 'immutable RawHits'):
                input_provenance(path, file_sha, payload_sha)

    def test_unknown_or_synthetic_sidecar_cannot_claim_geant4(self):
        path = self.directory / 'producer.json'
        for record in ({}, {'schema': 'native-geant4-input-v1', 'input_origin': 'synthetic'}):
            path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                input_provenance(path, 'a' * 64, 'b' * 64)

    def test_retention_region_changes_only_two_bookkeeping_constants(self):
        source = self.directory / 'source.xml'
        source.write_text('<lccdd><define><constant name="tracker_region_rmax" value="120*mm"/>'
                          '<constant name="tracker_region_zmax" value="350*mm"/></define>'
                          '<detectors><detector name="protected"/></detectors></lccdd>')
        before = source.read_bytes(); target = self.directory / 'transport.xml'
        changes = simulation_geometry(source, target)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(set(changes), {'tracker_region_rmax', 'tracker_region_zmax'})
        self.assertEqual(ET.parse(target).find('./detectors/detector').attrib,
                         {'name': 'protected'})
        with self.assertRaises(FileExistsError):
            simulation_geometry(source, target)

    def test_region_metadata_is_required_and_legacy_beam_is_not_reinterpreted(self):
        source = self.directory / 'incomplete.xml'; source.write_text('<lccdd/>')
        with self.assertRaisesRegex(ValueError, 'Missing'):
            simulation_geometry(source, self.directory / 'output.xml')
        pencil = gun_arguments('pencil')
        self.assertIn('249*mm 0*mm 0*mm', pencil)
        self.assertIn('1 0 0', pencil)
        self.assertIn('1*GeV', pencil)
        self.assertIn('--vertexSigma', gun_arguments('broad'))
        with self.assertRaises(ValueError):
            gun_arguments('undeclared')

    def test_default_official_gun_arguments_remain_unchanged(self):
        self.assertEqual(gun_arguments('pencil'), ['--enableGun', '--gun.particle', 'e-',
            '--gun.energy', '1*GeV', '--gun.position', '249*mm 0*mm 0*mm',
            '--gun.direction', '1 0 0'])
        broad = gun_arguments('broad')
        self.assertEqual(broad[:7], gun_arguments('pencil')[:7])
        self.assertEqual(broad[7:13], ['--vertexSigma', '0', '12', '10', '0', '--gun.distribution'])
        self.assertEqual(broad[-3:], ['--gun.phiMin=-0.08*rad', '--gun.phiMax', '0.08*rad'])

    def test_explicit_simulation_only_particle_energy_and_vertex(self):
        argv = gun_arguments('pencil', particle='mu-', energy_gev=5.,
            position_mm=(240., 2., -3.), direction=(2., 0., 0.), vertex_sigma_mm=(0., .5, .75))
        self.assertEqual(argv[2], 'mu-')
        self.assertEqual(argv[4], '5*GeV')
        self.assertEqual(argv[6], '240*mm 2*mm -3*mm')
        self.assertEqual(argv[-5:], ['--vertexSigma', '0', '0.5', '0.75', '0'])
        self.assertEqual(gun_configuration('pencil', particle='mu-')['pdg'], 13)

    def test_invalid_or_silently_ignored_generator_options_are_refused(self):
        for options in ({'energy_gev': 0}, {'energy_gev': float('nan')},
                        {'particle': 'unsupported'}, {'position_mm': (1., 2.)},
                        {'position_mm': (1., 2., float('inf'))}, {'direction': (0., 0., 0.)},
                        {'vertex_sigma_mm': (0., -1., 0.)}, {'theta_range_rad': (0., .1)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                gun_arguments('pencil', **options)
        for options in ({'direction': (0., 0., 1.)}, {'theta_range_rad': (.2, .1)},
                        {'phi_range_rad': (-4., 1.)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                gun_arguments('broad', **options)

    def test_broad_ranges_use_official_uniform_spherical_gun(self):
        argv = gun_arguments('broad', theta_range_rad=(1., 2.), phi_range_rad=(-.2, .3),
            vertex_sigma_mm=(0., 3., 4.))
        self.assertIn('uniform', argv)
        self.assertEqual(argv[argv.index('--gun.thetaMin')+1], '1*rad')
        self.assertIn('--gun.phiMin=-0.2*rad', argv)
        self.assertEqual(argv[argv.index('--gun.phiMax')+1], '0.3*rad')


if __name__ == '__main__':
    unittest.main()
