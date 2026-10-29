"""Synthetic solver-boundary checks; these do not execute or validate real Pede.

Exercise the actual Python covariance checks with a weak one-parameter design
whose data-only and diagonally stabilized errors are deliberately distinguishable.
Official binary/solver integration is covered by independent real-run tests.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HAS_NUMPY = importlib.util.find_spec('numpy') is not None
if HAS_NUMPY:
    import numpy as np
    from calibration import native_operations as operations
    from research_common import done


@unittest.skipUnless(HAS_NUMPY, 'Covariance checks require the installed NumPy environment')
class NativePedeCovariance(unittest.TestCase):
    def solve(self, reported_sigma, presigma=1.):
        temporary = tempfile.TemporaryDirectory(prefix='native-covariance-unit-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        reconstruction = root / 'reconstruct'
        reconstruction.mkdir()
        (reconstruction / 'mille.dat').write_bytes(b'SYNTHETIC UNIT INPUT: NOT A MILLE BINARY')
        done(reconstruction, passed=True)
        information = np.array([[4.]])
        design = {'fixed_reference_selected_design_full_rank': True,
                  'fixed_reference_rows_have_no_selected_global_columns': True,
                  'released_reference_gauge_diagnostic': {'common_rigid_gauge_is_null': True},
                  'selected': {'information': information.tolist(), 'rank': 1,
                               'diagonal_scaled_condition': 1., 'sigma': [.5]},
                  'omitted_X_Z_diagnostic': {}}
        commands = []

        def simulated_pede(command, directory, **kwargs):
            commands.append([str(value) for value in command])
            (directory / 'millepede.res').write_text(
                f'700102 0.000000000000 {presigma:.16g} 0.0 {reported_sigma:.16g}\n')
            (directory / 'millepede.log').write_text('No rank defect of the symmetric matrix\n')
            (directory / 'millepede.end').write_text('0 Ended normally\n')
            (directory / 'stdout.log').write_text('1 1 1 1 0 0 FMS\n')

        output = root / 'solve'
        with patch.object(operations, 'records', return_value=[[None] * 10 for _ in range(5)]), \
                patch.object(operations, 'profile', return_value=(information / 5, np.zeros(1), 0.)), \
                patch('validation.telescope_closure.multi_information.analyze', return_value=design), \
                patch.object(operations, 'execute', side_effect=simulated_pede):
            operations.solve(root, output, reconstruction, 'l1_y', 5, diagnostic=True)
        return json.loads((output / 'independent.json').read_text()), (output / 'pede.txt').read_text(), commands

    def test_stabilized_reported_errors_remain_distinct_from_data_only_errors(self):
        report, steering, commands = self.solve(1 / np.sqrt(5.))
        self.assertEqual(report['nominal_sigma'], [.5])
        np.testing.assert_allclose(report['pede_reported_sigma'], [1 / np.sqrt(5.)], rtol=1e-15)
        np.testing.assert_allclose(report['pede_expected_stabilized_covariance_mixed_units'], [[.2]], rtol=1e-15)
        self.assertNotAlmostEqual(report['pede_reported_sigma'][0], report['nominal_sigma'][0], places=3)
        self.assertIn('inverse(H)', report['pede_error_note'])
        self.assertIn('equality with data-only errors is not assumed', report['pede_error_note'])
        self.assertFalse(report['error_rescaling_applied'])
        self.assertFalse(report['pede_regularization_enabled'])
        self.assertIn('700102 0.0 1.0\n', steering)
        self.assertEqual(len(commands), 1)
        self.assertEqual(Path(commands[0][0]).name, 'pede')

    def test_reported_data_only_error_cannot_pass_the_stabilized_error_check(self):
        with self.assertRaisesRegex(AssertionError, 'retained diagonal stabilization'):
            self.solve(.5)

    def test_changed_presigma_remains_a_hard_failure(self):
        with self.assertRaisesRegex(AssertionError, 'pre-sigmas changed'):
            self.solve(1 / np.sqrt(4.25), presigma=2.)


if __name__ == '__main__':
    unittest.main()
