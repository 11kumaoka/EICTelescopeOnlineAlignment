"""Truth-free native convergence and failure gates, independent of reconstruction."""
import unittest

import numpy as np

from calibration.convergence import evaluate, evaluate_native, evaluate_rotation


class NativeConvergenceTests(unittest.TestCase):
    def setUp(self):
        self.labels = [700102, 700104, 700105, 700106]
        self.sigma = np.array([.0002, .0001, .0001, .0001])
        self.normal = np.diag(1/self.sigma**2)
        self.arguments = dict(iteration=1, chi2_ratio=.6, residual_norm=.01,
            initial_chi2_ratio=12., initial_residual_norm=.05,
            tracks=4096, rejected=0, initial_tracks=4096, initial_rejected=0,
            rank=4, condition=3., association_identity=True,
            solver_ok=True, geometry_ok=True)

    def run_gate(self, delta=None, **updates):
        arguments = dict(self.arguments, **updates)
        return evaluate_native(self.labels, np.zeros(4) if delta is None else delta,
                               self.normal, self.sigma, **arguments)

    def test_converges_with_fixed_initial_errors_and_no_truth(self):
        report = self.run_gate([1e-6, -1e-6, 0., 0.])
        self.assertEqual(report['status'], 'converged')
        self.assertTrue(report['converged'])
        self.assertAlmostEqual(report['joint_correction_significance_squared'], .005**2+.01**2)
        self.assertAlmostEqual(report['per_parameter']['700104']['correction_over_initial_sigma'], -.01)
        self.assertEqual(report['per_parameter']['700102']['unit'], 'mm')
        self.assertEqual(report['per_parameter']['700104']['unit'], 'rad')
        self.assertFalse(report['truth_used'])
        self.assertFalse(report['covariance_combined'])

    def test_initial_geometry_cannot_skip_minimum_correction(self):
        report = self.run_gate(iteration=0, chi2_ratio=12., residual_norm=.05)
        self.assertEqual(report['status'], 'continue')
        self.assertFalse(report['checks']['minimum_corrections'])
        self.assertFalse(report['checks']['chi2_improved'])

    def test_explicit_two_correction_comparison_is_bounded(self):
        first = self.run_gate(minimum_corrections=2)
        self.assertEqual(first['status'], 'continue')
        second = self.run_gate(iteration=2, minimum_corrections=2)
        self.assertEqual(second['status'], 'converged')

    def test_iteration_exhaustion_is_distinct_from_convergence(self):
        report = self.run_gate([0., 1e-4, 0., 0.], iteration=3)
        self.assertFalse(report['converged'])
        self.assertEqual(report['status'], 'iteration_limit')
        self.assertEqual(report['reason'], 'maximum_corrections_exhausted')
        self.assertEqual(self.run_gate(iteration=3)['status'], 'converged')

    def test_small_components_do_not_replace_joint_significance(self):
        report = self.run_gate([0., 2e-5, 2e-5, 2e-5])
        self.assertTrue(report['checks']['component_updates'])
        self.assertFalse(report['checks']['joint_correction_significance'])
        self.assertAlmostEqual(report['joint_correction_sigma'], np.sqrt(.12))
        self.assertEqual(report['status'], 'continue')

    def test_absolute_y_gate_is_independent_of_joint_significance(self):
        report = self.run_gate([2e-5, 0., 0., 0.])
        self.assertFalse(report['checks']['component_updates'])
        self.assertTrue(report['checks']['joint_correction_significance'])
        self.assertFalse(report['per_parameter']['700102']['passed'])
        self.assertAlmostEqual(report['joint_correction_sigma'], .1)

    def test_component_and_joint_limits_are_strict(self):
        report = self.run_gate([0., self.sigma[1]/4, 0., 0.])
        self.assertFalse(report['per_parameter']['700104']['passed'])
        self.assertFalse(report['checks']['joint_correction_significance'])
        self.assertTrue(self.run_gate([0., np.nextafter(self.sigma[1]/4, 0.), 0., 0.])['converged'])

    def test_off_diagonal_information_changes_joint_decision(self):
        covariance = np.diag(self.sigma**2)
        covariance[1, 2] = covariance[2, 1] = .9*self.sigma[1]*self.sigma[2]
        delta = np.array([0., 1e-5, -1e-5, 0.])
        uncorrelated = self.run_gate(delta)
        correlated = evaluate_native(self.labels, delta, np.linalg.inv(covariance),
                                     self.sigma, **self.arguments)
        self.assertTrue(uncorrelated['converged'])
        self.assertTrue(correlated['checks']['component_updates'])
        self.assertFalse(correlated['checks']['joint_correction_significance'])
        self.assertAlmostEqual(correlated['joint_correction_significance_squared'], .2)

    def test_policy_can_tighten_but_cannot_relax_validated_gates(self):
        self.assertTrue(self.run_gate([0., 1e-5, 0., 0.])['converged'])
        tightened = self.run_gate([0., 1e-5, 0., 0.], policy={'max_joint_sigma': .05})
        self.assertFalse(tightened['checks']['joint_correction_significance'])
        for policy in ({'max_abs_y_mm': 2.1e-5}, {'max_angular_sigma_ratio': .251},
                       {'max_joint_sigma': .251}, {'max_scaled_condition': 101.},
                       {'max_joint_sigma': 0.}, {'max_abs_y_mm': float('nan')},
                       {'max_joint_sigma': True}, {'max_scaled_condition': 1.},
                       {'policy_label': ''}, {'truth_error': 0.}):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.run_gate(policy=policy)

    def test_initial_information_must_be_finite_symmetric_positive_definite(self):
        for normal in (np.eye(3), np.zeros((4, 4)), np.diag([1., 1., -1., 1.]),
                       np.full((4, 4), float('inf')), np.triu(np.ones((4, 4)))):
            with self.subTest(normal=normal), self.assertRaises(ValueError):
                evaluate_native(self.labels, np.zeros(4), normal, self.sigma, **self.arguments)

    def test_update_and_initial_sigma_shapes_and_finiteness(self):
        for delta, sigma in (([0.], self.sigma), ([0., 0., float('nan'), 0.], self.sigma),
                             (np.zeros(4), [1., 1., 0., 1.]),
                             (np.zeros(4), [1., 1., float('inf'), 1.])):
            with self.subTest(delta=delta, sigma=sigma), self.assertRaises(ValueError):
                evaluate_native(self.labels, delta, self.normal, sigma, **self.arguments)

    def test_physical_label_scope_and_complete_rotation_vector(self):
        for labels in ([], [700102, 700102], [700101], [700402], [True], [700104, 700105]):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                evaluate_native(labels, np.zeros(len(labels)), np.eye(len(labels)),
                                np.ones(len(labels)), **self.arguments)

    def test_actual_rank_and_scaled_condition_are_required(self):
        for updates in ({'rank': 3}, {'rank': True}, {'condition': 100.},
                        {'condition': .5}, {'condition': float('nan')}):
            with self.subTest(updates=updates), self.assertRaises(RuntimeError):
                self.run_gate(**updates)

    def test_stable_counts_associations_solver_and_geometry_are_required(self):
        for updates in ({'tracks': 4095}, {'rejected': 1}, {'association_identity': False},
                        {'solver_ok': False}, {'geometry_ok': False}):
            with self.subTest(updates=updates), self.assertRaises(RuntimeError):
                self.run_gate(**updates)
        for updates in ({'tracks': 0}, {'rejected': -1}, {'initial_tracks': True}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.run_gate(**updates)

    def test_quality_must_improve_relative_to_initial_not_each_previous_step(self):
        self.assertTrue(self.run_gate(iteration=2, chi2_ratio=.7, residual_norm=.011)['converged'])
        unchanged = self.run_gate(chi2_ratio=12.)
        self.assertEqual(unchanged['status'], 'continue')
        self.assertFalse(unchanged['checks']['chi2_improved'])
        for updates in ({'chi2_ratio': 12.01}, {'residual_norm': .051}):
            with self.subTest(updates=updates), self.assertRaises(RuntimeError):
                self.run_gate(**updates)

    def test_nonfinite_or_negative_reconstruction_metrics_are_rejected(self):
        for updates in ({'chi2_ratio': float('nan')}, {'residual_norm': float('inf')},
                        {'initial_chi2_ratio': -1.}, {'initial_residual_norm': True}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.run_gate(**updates)

    def test_iteration_bounds_are_explicit_integers(self):
        for updates in ({'iteration': -1}, {'iteration': 4}, {'iteration': True},
                        {'minimum_corrections': 0}, {'maximum_corrections': 4},
                        {'minimum_corrections': 3, 'maximum_corrections': 2}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.run_gate(**updates)

    def test_initial_information_and_errors_are_not_mutated(self):
        normal = self.normal.copy(); sigma = self.sigma.copy(); labels = list(self.labels)
        self.run_gate([1e-6, 0., 0., 0.], iteration=2)
        np.testing.assert_array_equal(self.normal, normal)
        np.testing.assert_array_equal(self.sigma, sigma)
        self.assertEqual(self.labels, labels)

    def test_no_truth_argument_or_hidden_truth_policy_is_accepted(self):
        with self.assertRaises(TypeError):
            self.run_gate(truth_error=0.)
        with self.assertRaises(ValueError):
            self.run_gate(policy={'injected_rotation_rad': [0., 0., 0.]})

    def test_legacy_evaluators_keep_their_original_contract(self):
        policy = {'min_tracks': 20, 'delta_tolerance_mm': .005, 'max_update_mm': .5,
                  'max_track_chi2_ratio': 1000., 'policy_label': 'demonstration',
                  'max_update_rad': .02, 'delta_tolerance_rad': .001}
        self.assertTrue(evaluate(.001, 2., 99, policy)['converged'])
        self.assertTrue(evaluate_rotation(.0001, 2., 99, policy)['converged'])


if __name__ == '__main__':
    unittest.main()
