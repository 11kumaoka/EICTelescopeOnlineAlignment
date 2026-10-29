"""Opt-in coordinate serialization proofs without changing fitted measurements.

These structural fixtures exercise Controller checks, not DD4hep construction
or tracking. Actual Geometry and converter validation is executed separately.
"""
import copy
import unittest

import numpy as np

from calibration import native_controller as controller


def fixture(local=None):
    local = [-34.918601989746094, 8.1925687789917] if local is None else list(local)
    hit = {'loc_mm':local,'covariance_mm2':[.0001,.0001,0.],
           'position_mm':[300.,34.918601989746094,-8.1925687789917]}
    hit['serialization_audit'] = {'passed':True,
        'canonical_sensor_local_mm':[-34.918600767999994,0.,8.192568499999999],
        'canonical_cell_dimensions_mm':[.034641,.034641],
        'expected_global_float32_mm':list(hit['position_mm']),
        'expected_measurement_local_float32_mm':list(local),
        'expected_measurement_covariance_float32_mm2':list(hit['covariance_mm2'])}
    return hit


class MeasurementSerialization(unittest.TestCase):
    def check(self, hit): return controller.checked_measurement(hit,geometry_aware=True)

    def compare(self, current, original):
        return controller.compare_measurements(current,original,geometry_aware=True)

    def test_default_output_retains_the_historical_two_fields(self):
        hit = fixture()
        self.assertEqual(controller.checked_measurement(hit),
            {'loc_mm':hit['loc_mm'],'covariance_mm2':hit['covariance_mm2']})

    def test_default_path_does_not_require_or_interpret_the_new_proof(self):
        hit = fixture(); del hit['serialization_audit']
        self.assertEqual(set(controller.checked_measurement(hit)),{'loc_mm','covariance_mm2'})
        hit['serialization_audit'] = {'passed':False}
        self.assertEqual(set(controller.checked_measurement(hit)),{'loc_mm','covariance_mm2'})

    def test_opt_in_retains_actual_measurements_and_adds_canonical_values(self):
        hit = fixture(); result = self.check(hit)
        self.assertEqual(result['loc_mm'],hit['loc_mm'])
        self.assertEqual(result['covariance_mm2'],hit['covariance_mm2'])
        self.assertEqual(result['canonical_sensor_local_mm'],hit['serialization_audit']['canonical_sensor_local_mm'])
        self.assertEqual(result['canonical_cell_dimensions_mm'],[.034641,.034641])
        self.assertNotIn('serialization_audit',result)

    def test_exact_canonical_identity_allows_proven_geometry_specific_float_rounding(self):
        first = fixture(); second = copy.deepcopy(first)
        second['loc_mm'][0] = float(np.nextafter(np.float32(first['loc_mm'][0]),np.float32(np.inf)))
        displacement = abs(second['loc_mm'][0]-first['loc_mm'][0])
        self.assertEqual(displacement,2.**-18)
        self.assertGreater(displacement,2e-6)
        second['serialization_audit']['expected_measurement_local_float32_mm'] = list(second['loc_mm'])
        self.compare(self.check(second),self.check(first))
        with self.assertRaises(AssertionError):
            controller.compare_measurements(controller.checked_measurement(second),controller.checked_measurement(first))

    def test_any_changed_canonical_coordinate_is_refused(self):
        original = self.check(fixture())
        for index in range(3):
            current = copy.deepcopy(original); current['canonical_sensor_local_mm'][index] += 1e-10
            with self.subTest(index=index), self.assertRaises((RuntimeError,AssertionError,ValueError)):
                self.compare(current,original)

    def test_any_changed_canonical_cell_dimension_is_refused(self):
        original = self.check(fixture())
        for index in range(2):
            current = copy.deepcopy(original); current['canonical_cell_dimensions_mm'][index] += 1e-12
            with self.subTest(index=index), self.assertRaises((RuntimeError,AssertionError,ValueError)):
                self.compare(current,original)

    def test_missing_or_unsuccessful_proof_is_refused(self):
        for value in (None,{}, {'passed':False},{'passed':1}):
            hit = fixture()
            if value is None: del hit['serialization_audit']
            else: hit['serialization_audit'] = value
            with self.subTest(value=value), self.assertRaises((RuntimeError,AssertionError,ValueError,KeyError)):
                self.check(hit)

    def test_changed_global_float32_value_is_refused(self):
        hit = fixture()
        hit['position_mm'][0] = float(np.nextafter(np.float32(300.),np.float32(np.inf)))
        with self.assertRaises((RuntimeError,AssertionError,ValueError)): self.check(hit)

    def test_one_ulp_local_corruption_below_old_absolute_tolerance_is_refused(self):
        hit = fixture([.125,-.25])
        hit['loc_mm'][0] = float(np.nextafter(np.float32(.125),np.float32(np.inf)))
        self.assertLess(abs(hit['loc_mm'][0]-.125),2e-6)
        with self.assertRaises((RuntimeError,AssertionError,ValueError)): self.check(hit)

    def test_one_ulp_covariance_corruption_is_refused_before_cross_fit_comparison(self):
        hit = fixture(); hit['covariance_mm2'][0] = 1e-6
        hit['serialization_audit']['expected_measurement_covariance_float32_mm2'] = list(hit['covariance_mm2'])
        hit['covariance_mm2'][0] = float(np.nextafter(np.float32(1e-6),np.float32(np.inf)))
        self.assertLess(abs(hit['covariance_mm2'][0]-1e-6),1e-12)
        with self.assertRaises((RuntimeError,AssertionError,ValueError)): self.check(hit)

    def test_nonfinite_values_in_every_proof_coordinate_group_are_refused(self):
        for key in ('canonical_sensor_local_mm','canonical_cell_dimensions_mm','expected_global_float32_mm',
                    'expected_measurement_local_float32_mm','expected_measurement_covariance_float32_mm2'):
            for value in (float('nan'),float('inf'),-float('inf')):
                hit = fixture(); hit['serialization_audit'][key][0] = value
                if key == 'expected_global_float32_mm': hit['position_mm'][0] = value
                if key == 'expected_measurement_local_float32_mm': hit['loc_mm'][0] = value
                if key == 'expected_measurement_covariance_float32_mm2': hit['covariance_mm2'][0] = value
                with self.subTest(key=key,value=value), self.assertRaises((RuntimeError,AssertionError,ValueError)):
                    self.check(hit)

    def test_malformed_dimensions_and_nonpositive_cell_widths_are_refused(self):
        for key, values in (('canonical_sensor_local_mm',[1.,2.]),
                            ('canonical_cell_dimensions_mm',[.01]),
                            ('canonical_cell_dimensions_mm',[0.,.01]),
                            ('canonical_cell_dimensions_mm',[-.01,.01]),
                            ('expected_global_float32_mm',[1.,2.]),
                            ('expected_measurement_local_float32_mm',[1.]),
                            ('expected_measurement_covariance_float32_mm2',[1.,2.])):
            hit = fixture(); hit['serialization_audit'][key] = values
            with self.subTest(key=key,values=values), self.assertRaises((RuntimeError,AssertionError,ValueError)):
                self.check(hit)

    def test_legacy_local_tolerance_remains_exactly_the_existing_bound(self):
        original = controller.checked_measurement(fixture())
        within = copy.deepcopy(original); within['loc_mm'][0] += 1.5e-6
        controller.compare_measurements(within,original)
        outside = copy.deepcopy(original); outside['loc_mm'][0] += 2.5e-6
        with self.assertRaises(AssertionError): controller.compare_measurements(outside,original)

    def test_covariance_cross_fit_tolerance_is_unchanged_in_both_modes(self):
        for enabled in (False,True):
            original = controller.checked_measurement(fixture(),geometry_aware=enabled)
            within = copy.deepcopy(original); within['covariance_mm2'][0] += 1e-13
            controller.compare_measurements(within,original,geometry_aware=enabled)
            outside = copy.deepcopy(original); outside['covariance_mm2'][0] += 2e-12
            with self.subTest(enabled=enabled), self.assertRaises(AssertionError):
                controller.compare_measurements(outside,original,geometry_aware=enabled)


if __name__ == '__main__': unittest.main()
