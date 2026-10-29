"""Offline advisory handling and publication rules; no physics claims from fixtures."""
from contextlib import contextmanager
import json
from pathlib import Path
import unittest

from calibration import native_controller as controller
from calibration.convergence import evaluate_native
from calibration.native_policy import QualityGateError, require_precision, solver_diagnostics
from validation.test_native_controller import ControllerFixture


NORMAL_STATUS = '0   Ended normally'
WARNING_STATUS = '1   Ended with warnings (bad measurements)'
TABLE = ('  0  1 0.24303E+06 0.0 0 00000 FMS\n'
         '  1  2 0.30939E+05 0.21E+06 0.000 0.456 0 0 1 1.000 0.0 0 00000 F\n')
HIGH_CHI2 = (TABLE + '\n'
    ' Sum(Chi^2)/Sum(Ndf) = 30939.298032245755\n'
    '                     / (13308 - 5)\n'
    '                     = 2.3257384072950278\n'
    ' WarningWarningWarningWarningWarningWarningWarningWarningWar\n'
    ' arningWarningWarningWarningWarningWarningWarningWarningWarn\n'
    ' Chi^2/Ndf = 2.33 (should be close to 1)\n'
    ' => multiply all input standard deviations by factor 1.53\n')


class DiagnosticSolverPolicy(unittest.TestCase):
    def test_normal_output_keeps_the_same_contract_in_both_modes(self):
        strict = solver_diagnostics(TABLE, NORMAL_STATUS)
        self.assertEqual(strict, solver_diagnostics(TABLE, NORMAL_STATUS, diagnostic=True))
        self.assertFalse(strict['diagnostic_continuation'])
        self.assertFalse(strict['error_rescaling_applied'])
        self.assertEqual(strict['warnings'], [])

    def test_strict_mode_still_blocks_the_known_high_chi2_advisory(self):
        with self.assertRaises(QualityGateError): solver_diagnostics(HIGH_CHI2, WARNING_STATUS)

    def test_diagnostic_continuation_keeps_warning_and_never_applies_scaling(self):
        original = HIGH_CHI2
        report = solver_diagnostics(HIGH_CHI2, WARNING_STATUS, diagnostic=True)
        self.assertTrue(report['diagnostic_continuation'])
        self.assertEqual(report['status'], WARNING_STATUS)
        self.assertEqual(report['suggested_error_scale_not_applied'], 1.53)
        self.assertFalse(report['error_rescaling_applied'])
        self.assertTrue(report['warnings'])
        self.assertEqual(report['rejected_local_fits'], [0, 0])
        self.assertEqual(HIGH_CHI2, original)

    def test_unknown_status_never_becomes_a_quality_only_warning(self):
        for status, log in (('2   Matrix failure', HIGH_CHI2), ('0   Unknown solver state', TABLE),
                            ('1   Unknown warning', HIGH_CHI2), ('', HIGH_CHI2),
                            (NORMAL_STATUS, HIGH_CHI2)):
            with self.subTest(status=status), self.assertRaises(RuntimeError) as raised:
                solver_diagnostics(log, status, diagnostic=True)
            self.assertNotIsInstance(raised.exception, QualityGateError)

    def test_unrecognized_warning_is_fatal_even_with_a_recognized_advisory(self):
        for text in ('\nWARNING: singular normal matrix\n', '\nWarning: unsupported parameter constraint\n'):
            with self.subTest(text=text), self.assertRaises(RuntimeError) as raised:
                solver_diagnostics(HIGH_CHI2+text, WARNING_STATUS, diagnostic=True)
            self.assertNotIsInstance(raised.exception, QualityGateError)

    def test_missing_or_rejected_local_fit_records_are_fatal(self):
        for log in (HIGH_CHI2.replace(TABLE, ''), HIGH_CHI2.replace('0 00000 F\n', '2 00000 F\n')):
            with self.subTest(log=log), self.assertRaises(RuntimeError) as raised:
                solver_diagnostics(log, WARNING_STATUS, diagnostic=True)
            self.assertNotIsInstance(raised.exception, QualityGateError)

    def test_advisory_requires_one_unambiguous_ratio_and_scale(self):
        for log in (HIGH_CHI2.replace(' Chi^2/Ndf = 2.33 (should be close to 1)\n', ''),
                    HIGH_CHI2+' => multiply all input standard deviations by factor 1.53\n',
                    HIGH_CHI2+' Chi^2/Ndf = 2.33 (should be close to 1)\n'):
            with self.subTest(log=log), self.assertRaises(RuntimeError):
                solver_diagnostics(log, WARNING_STATUS, diagnostic=True)

    def test_nonfinite_or_nonpositive_advisory_numbers_are_fatal(self):
        for factor in ('0', '1', '-1', '1e999', 'NaN'):
            with self.subTest(factor=factor), self.assertRaises(RuntimeError):
                solver_diagnostics(HIGH_CHI2.replace('factor 1.53', 'factor '+factor), WARNING_STATUS, diagnostic=True)
        for ratio in ('0', '1', '-1', '1e999', 'NaN'):
            with self.subTest(ratio=ratio), self.assertRaises(RuntimeError):
                solver_diagnostics(HIGH_CHI2.replace('2.33 (should', ratio+' (should'), WARNING_STATUS, diagnostic=True)

    def test_precision_warning_is_explicit_and_strict_mode_is_unchanged(self):
        warnings = []
        with self.assertRaises(QualityGateError): require_precision(False, 'Declared precision target unresolved', False, warnings)
        self.assertEqual(warnings, [])
        require_precision(False, 'Declared precision target unresolved', True, warnings)
        self.assertEqual(warnings, ['Declared precision target unresolved'])

    def test_satisfied_precision_target_adds_no_warning(self):
        for diagnostic in (False, True):
            warnings = []; require_precision(True, 'unused', diagnostic, warnings)
            self.assertEqual(warnings, [])


class DiagnosticSafetyBoundaries(unittest.TestCase):
    def setUp(self):
        self.advisory = solver_diagnostics(HIGH_CHI2, WARNING_STATUS, diagnostic=True)
        self.arguments = dict(iteration=1, chi2_ratio=.6, residual_norm=.01,
            initial_chi2_ratio=12., initial_residual_norm=.05, tracks=32, rejected=0,
            initial_tracks=32, initial_rejected=0, rank=1, condition=1.,
            association_identity=True, solver_ok=True, geometry_ok=True)

    def evaluate(self, delta=None, normal=None, **changes):
        return evaluate_native([700102], [0.] if delta is None else delta,
            [[1e8]] if normal is None else normal, [.0001], **dict(self.arguments, **changes))

    def test_advisory_permission_cannot_bypass_requested_rank_or_condition(self):
        self.assertTrue(self.advisory['diagnostic_continuation'])
        for changes in ({'rank':0}, {'condition':100.}, {'condition':float('nan')}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError): self.evaluate(**changes)

    def test_geometry_association_and_solver_integrity_are_still_mandatory(self):
        for changes in ({'geometry_ok':False}, {'association_identity':False}, {'solver_ok':False}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError): self.evaluate(**changes)

    def test_nonfinite_or_nonpositive_information_never_produces_a_candidate(self):
        for delta, normal in (([float('nan')],[[1e8]]), ([0.],[[float('inf')]]), ([0.],[[-1.]])):
            with self.subTest(delta=delta,normal=normal), self.assertRaises(ValueError):
                self.evaluate(delta,normal)


class DiagnosticReportPublication(ControllerFixture):
    @contextmanager
    def report_fixture(self, diagnostic=True):
        # These are publication-only fixtures, not execution of a modified run.
        with self.initialized() as state:
            if diagnostic:
                manifest = dict(state.manifest, validation_mode='diagnostic')
                (state.run/'manifest.json').write_text(json.dumps(manifest))
            yield state

    def report_chain(self, state, terminal='converged'):
        chain = []; previous = None; geometry = None
        for sequence in range(2):
            metrics = state.run/f'unit-metrics-{sequence}.json'
            metrics.write_text(json.dumps({'pede': solver_diagnostics(HIGH_CHI2,WARNING_STATUS,diagnostic=True)}))
            information = state.run/f'unit-information-{sequence}.json'
            information.write_text(json.dumps({'quality_warnings':['Declared precision target unresolved']}))
            item = self.checkpoint(state,sequence,previous=previous,geometry=geometry,
                termination='continue' if sequence == 0 else terminal,
                metrics=str(metrics), current_information=controller.identity(information))
            chain.append(item); previous = item['marker']; geometry = item['next_geometry']
        return chain

    def test_diagnostic_convergence_publishes_only_an_unapproved_technical_candidate(self):
        with self.report_fixture() as state:
            chain = self.report_chain(state)
            report = controller.publish_report(state.run,state.run/'jobs/attempt_000','converged','unit fixture',chain)
            self.assertTrue(report['engineering_success'])
            self.assertEqual(report['physics_validation'],'not_validated')
            self.assertEqual(report['approval_status'],'unapproved')
            self.assertEqual(report['integrity_status'],'passed')
            self.assertFalse(report['geometry_activated'])
            self.assertTrue(report['quality_warnings'])
            self.assertEqual((state.run/'final/candidate.xml').read_bytes(),
                             Path(chain[-1]['geometry']['path']).read_bytes())
            controller.verify(state.run/'final')

    def test_iteration_limit_may_publish_after_a_real_correction_and_fresh_fit(self):
        with self.report_fixture() as state:
            chain = self.report_chain(state,terminal='iteration_limit')
            report = controller.publish_report(state.run,state.run/'jobs/attempt_000','iteration_limit','unit fixture',chain)
            self.assertTrue(report['engineering_success'])
            self.assertEqual(report['status'],'iteration_limit')
            self.assertEqual(report['physics_validation'],'not_validated')
            self.assertIn('Demonstration convergence criteria not met',report['quality_warnings'])
            self.assertTrue((state.run/'final/candidate.xml').exists())

    def test_no_diagnostic_publication_without_correction_and_fresh_reconstruction(self):
        with self.report_fixture() as state:
            item = self.checkpoint(state,0,termination='converged',applied=0)
            report = controller.publish_report(state.run,state.run/'jobs/attempt_000','converged','insufficient history',[item])
            self.assertFalse(report['engineering_success'])
            self.assertEqual(report['physics_validation'],'not_validated')
            self.assertFalse((state.run/'final').exists())

    def test_integrity_failure_never_publishes_a_diagnostic_final_candidate(self):
        with self.report_fixture() as state:
            chain = self.report_chain(state)
            report = controller.publish_report(state.run,state.run/'jobs/attempt_000','failed','SIMULATED corruption',chain,error='SIMULATED corruption')
            self.assertFalse(report['engineering_success'])
            self.assertEqual(report['integrity_status'],'failed')
            self.assertFalse((state.run/'final').exists())

    def test_strict_iteration_limit_keeps_historical_publication_behavior(self):
        with self.report_fixture(diagnostic=False) as state:
            chain = self.report_chain(state,terminal='iteration_limit')
            report = controller.publish_report(state.run,state.run/'jobs/attempt_000','iteration_limit','unit fixture',chain)
            self.assertNotIn('engineering_success',report)
            self.assertNotIn('physics_validation',report)
            self.assertFalse((state.run/'final').exists())


if __name__=='__main__': unittest.main()
