"""Offline diagnostic safeguards and independent synthetic linear algebra.

These fixtures are mathematical tests, not Geant4 events or validation of new
operational alignment degrees of freedom. Existing native calibration is unchanged.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from studies.alignment_statistics import fit_18d_linearized_scan as fit
from studies.alignment_statistics import analyze_full_42d_information as information
from studies.alignment_statistics import plan_full_dof_scan as planner


def synthetic_report(count=8):
    """Construct full-rank independent synthetic equations, not detector Truth."""
    rng = np.random.default_rng(93428)
    parameters = np.array([.003, -.002, .001, .0002, -.0001, .00015] * 3)
    events = []
    dense_local = np.zeros((10 * count, 4 * count))
    dense_global = np.zeros((10 * count, 18))
    residual = np.zeros(10 * count)
    for event_index in range(count):
        a = rng.normal(size=(10, 4))
        local_parameters = rng.normal(size=4)
        equations = []
        for layer in range(5):
            block = rng.normal(size=(2, 6))
            g = np.zeros((2, 18))
            if layer in (1, 2, 3):
                g[:, 6 * (layer - 1):6 * layer] = block
            offset = 10 * event_index + 2 * layer
            row = slice(offset, offset + 2)
            local = a[2 * layer:2 * layer + 2]
            r = local @ local_parameters + g @ parameters
            dense_local[row, 4 * event_index:4 * event_index + 4] = local
            dense_global[row] = g
            residual[row] = r
            equations.append({'layer': layer, 'whitening': np.eye(2).tolist(),
                'local': local.tolist(), 'global': block.tolist(), 'residual': r.tolist()})
        events.append({'run_number': 7, 'event_number': event_index, 'passed': True,
            'accepted': True, 'model_sha256': 'a' * 64, 'equations': equations})
    return {'model_sha256': 'a' * 64, 'passed': True, 'processed': count,
            'accepted': count, 'records': count, 'rejected': 0, 'events': events}, dense_local, dense_global, residual, parameters


class LinearizedDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def source(self, value):
        path = self.root / 'input.json'
        path.write_text(json.dumps(value))
        return path

    def test_profiled_18d_solution_and_covariance_match_independent_dense_least_squares(self):
        report, local, global_, residual, expected = synthetic_report()
        h = np.zeros((18, 18)); b = np.zeros(18)
        for event in report['events']:
            normal, rhs, _ = fit.contribution(event)
            h += normal; b += rhs
        result, actual, sigma = fit.solve(h, b, 1e-10)
        design = np.column_stack((local, global_))
        dense = np.linalg.lstsq(design, residual, rcond=None)[0][-18:]
        covariance = np.linalg.inv(design.T @ design)[-18:, -18:]
        self.assertEqual(result['rank'], 18)
        np.testing.assert_allclose(actual, dense, atol=1e-10, rtol=1e-10)
        np.testing.assert_allclose(actual, expected, atol=1e-10, rtol=1e-10)
        np.testing.assert_allclose(sigma, np.sqrt(np.diag(covariance)), rtol=1e-9)

    def test_42d_profile_matches_independent_projection_and_unmeasured_columns_are_zero(self):
        event = synthetic_report()[0]['events'][0]
        actual = information.single_information(event)
        a = np.vstack([eq['local'] for eq in event['equations']])
        g = np.zeros((10, 42))
        for eq in event['equations']:
            layer = eq['layer']; g[2*layer:2*layer+2, 6*layer:6*layer+6] = eq['global']
        independent = g - a @ np.linalg.lstsq(a, g, rcond=None)[0]
        np.testing.assert_allclose(actual, independent.T @ independent, atol=1e-12)
        np.testing.assert_array_equal(actual[:, 30:], np.zeros((42, 12)))
        np.testing.assert_array_equal(actual[30:, :], np.zeros((12, 42)))
        self.assertLessEqual(information.effective_rank(actual, 1e-8)[0], 6)

    def test_normalized_42d_rank_is_invariant_to_translation_rotation_unit_scaling(self):
        h = sum((information.single_information(ev) for ev in synthetic_report()[0]['events']))
        scales = np.tile([1000., 1000., 1000., .001, .001, .001], 7)
        original = information.effective_rank(h, 1e-8)
        changed = information.effective_rank(scales[:, None] * h * scales[None, :], 1e-8)
        self.assertEqual(original[0], changed[0])
        np.testing.assert_allclose(original[1], changed[1], atol=1e-12)

    def test_singular_18d_information_is_reported_without_inventing_a_fit(self):
        result, parameters, sigma = fit.solve(np.diag([1.] * 17 + [0.]), np.zeros(18), 1e-10)
        self.assertEqual(result['rank'], 17)
        self.assertIsNone(parameters)
        self.assertIsNone(sigma)

    def test_equation_failures_are_not_silently_skipped(self):
        event = synthetic_report()[0]['events'][0]
        duplicate = copy.deepcopy(event); duplicate['equations'][0]['layer'] = 1
        nonfinite = copy.deepcopy(event); nonfinite['equations'][0]['local'][0][0] = float('nan')
        rankless = copy.deepcopy(event)
        for eq in rankless['equations']: eq['local'] = np.zeros((2, 4)).tolist()
        for bad in (duplicate, nonfinite, rankless):
            for function in (fit.contribution, information.single_information):
                with self.subTest(function=function.__name__), self.assertRaises(ValueError):
                    function(bad)

    def test_input_snapshot_hash_and_complete_selected_event_ids(self):
        value = synthetic_report()[0]; path = self.source(value); raw = path.read_bytes()
        parsed, provenance = fit.checked_input(path)
        self.assertEqual(provenance['input_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(provenance['event_identities'], [[7, i] for i in range(8)])
        self.assertEqual(parsed, value)
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse(provenance['parent_report_binding_independently_verified'])

    def test_duplicate_missing_unaccepted_and_mismatched_models_fail_before_analysis(self):
        source = synthetic_report()[0]
        variants = []
        duplicate = copy.deepcopy(source); duplicate['events'][1]['event_number'] = 0; variants.append(duplicate)
        missing = copy.deepcopy(source); del missing['events'][0]['run_number']; variants.append(missing)
        rejected = copy.deepcopy(source); rejected['events'][0]['accepted'] = False; variants.append(rejected)
        failed = copy.deepcopy(source); failed['events'][0]['passed'] = False; variants.append(failed)
        mismatched = copy.deepcopy(source); mismatched['events'][0]['model_sha256'] = 'b' * 64; variants.append(mismatched)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError): fit.checked_input(self.source(value))

    def test_filtered_subset_preserves_parent_failure_and_rejection_metadata(self):
        value = synthetic_report()[0]
        value.update(passed=False, processed=15, accepted=12, records=12, rejected=3,
            track_only_selection=True, requested_tracks=8, source_mille_json_sha256='b' * 64)
        _, provenance = fit.checked_input(self.source(value))
        self.assertEqual(provenance['selection'], 'explicit_accepted_track_subset')
        self.assertFalse(provenance['parent_producer_passed'])
        self.assertEqual(provenance['parent_producer_counts']['rejected'], 3)
        self.assertEqual(provenance['declared_parent_report_sha256'], 'b' * 64)
        self.assertFalse(provenance['parent_report_binding_independently_verified'])

    def test_filtered_subset_cannot_masquerade_as_a_complete_success(self):
        value = synthetic_report()[0]
        value.update(passed=False, processed=15, accepted=12, records=12, rejected=3)
        with self.assertRaises(ValueError): fit.checked_input(self.source(value))
        value.update(track_only_selection=True, requested_tracks=7, source_mille_json_sha256='b' * 64)
        with self.assertRaises(ValueError): fit.checked_input(self.source(value))
        value['requested_tracks'] = 8; value['source_mille_json_sha256'] = 'not-a-sha'
        with self.assertRaises(ValueError): fit.checked_input(self.source(value))
        value['source_mille_json_sha256'] = 'b' * 64; value['records'] = 9
        with self.assertRaises(ValueError): fit.checked_input(self.source(value))

    def test_truth_requires_exact_all_18_keys_finite_mm_rad_values(self):
        path = self.root / 'truth.json'; value = {'units': 'mm_rad', 'values': {k: 0. for k in fit.LABELS}}
        path.write_text(json.dumps(value)); np.testing.assert_array_equal(fit.expected_truth(path), np.zeros(18))
        for update in ({'values': {'L1_Y': 0.}}, {'units': 'mrad'},
                       {'values': dict(value['values'], L4_X=0.)},
                       {'values': dict(value['values'], L1_X=True)},
                       {'values': dict(value['values'], L1_X=float('nan'))}):
            path.write_text(json.dumps(dict(value, **update)))
            with self.subTest(update=update), self.assertRaises(ValueError): fit.expected_truth(path)

    def test_truth_only_changes_reported_errors_and_never_the_estimate(self):
        path = self.source(synthetic_report()[0]); truth = self.root / 'truth.json'
        truth.write_text(json.dumps({'units': 'mm_rad', 'values': {k: 3. for k in fit.LABELS}}))
        summaries = []
        for name, target in (('without', None), ('with', str(truth))):
            output = self.root / name
            args = SimpleNamespace(input=str(path), output=str(output), counts='4,8', eigen_cut=1e-10, truth=target)
            with patch.object(fit, 'create_graphs'): fit.run(args)
            summaries.append(json.loads((output / 'fit_18d_linearized_summary.json').read_text()))
        for a, b in zip(summaries[0]['cohorts'], summaries[1]['cohorts']):
            self.assertEqual(a['estimates'], b['estimates'])
            self.assertEqual(a['formal_sigma'], b['formal_sigma'])
        singleton = summaries[1]['statistics']['8']['parameters']['L1_X']
        self.assertIsNone(singleton['sample_sd'])
        self.assertEqual(summaries[1]['input_provenance']['event_identities'], [[7, i] for i in range(8)])

    def test_output_refusal_and_cutoff_refusal_preserve_input(self):
        path = self.source(synthetic_report()[0]); raw = path.read_bytes(); output = self.root / 'existing'; output.mkdir()
        sentinel = output / 'sentinel'; sentinel.write_text('preserve')
        args = SimpleNamespace(input=str(path), output=str(output), counts='8', eigen_cut=1e-10, truth=None)
        with self.assertRaises(FileExistsError): fit.run(args)
        self.assertEqual(sentinel.read_text(), 'preserve')
        self.assertEqual(path.read_bytes(), raw)
        for cutoff in (0., 1., float('nan'), True):
            args.output = str(self.root / 'new'); args.eigen_cut = cutoff
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError): fit.run(args)
            self.assertFalse((self.root / 'new').exists())
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                information.run(SimpleNamespace(input=str(path), output=str(self.root / 'new'), counts='8', relative_eigen_cut=cutoff))

    def test_planner_is_unbound_nonoverlapping_and_does_not_claim_executed_fits(self):
        plan = planner.make_plan(20000, 'full')
        self.assertEqual(plan['case_count'], 446)
        self.assertIsNone(plan['accepted_master_sha256'])
        self.assertTrue(all(c['status'] == 'planned_not_run' for c in plan['cohorts']))
        for n in planner.POINTS:
            rows = [c for c in plan['cohorts'] if c['N'] == n]
            self.assertTrue(all(a['stop_ordinal_exclusive'] <= b['start_ordinal'] for a, b in zip(rows, rows[1:])))
            self.assertTrue(all(c['stop_ordinal_exclusive'] <= 20000 for c in rows))
        for count in (True, 19999, 20000.0):
            with self.subTest(count=count), self.assertRaises(ValueError): planner.make_plan(count, 'full')


    def test_input_bytes_are_read_once_for_the_reported_hash(self):
        path = self.source(synthetic_report()[0]); original = Path.read_bytes; calls = []
        def read_once(item):
            if item == path: calls.append(item)
            return original(item)
        with patch.object(Path, 'read_bytes', read_once):
            _, provenance = fit.checked_input(path)
        self.assertEqual(calls, [path])
        self.assertEqual(provenance['input_sha256'], hashlib.sha256(original(path)).hexdigest())

    def test_parent_passed_flag_cannot_hide_recorded_rejections(self):
        value = synthetic_report()[0]
        value.update(passed=True, processed=15, accepted=12, records=12, rejected=3,
            track_only_selection=True, requested_tracks=8, source_mille_json_sha256='b' * 64)
        with self.assertRaisesRegex(ValueError, 'passed flag'):
            fit.checked_input(self.source(value))


class LinearizedPlotTests(unittest.TestCase):
    """Scientific plotting semantics without requiring ROOT for unit tests."""

    def example(self):
        import math
        from studies.alignment_statistics import plot_18d_individual_cohorts as plotting
        records = []
        statistics = {}
        for n, values in ((2, (-.001, .003)), (4, (.004,))):
            for index, value in enumerate(values):
                records.append({'N': n, 'cohort': index, 'rank': 18,
                                'error_mm_rad': {key: value for key in plotting.KEYS},
                                'formal_sigma': [.0002]*18})
            mean = sum(values)/len(values)
            sd = (math.sqrt(sum((v-mean)**2 for v in values)/(len(values)-1))
                  if len(values) > 1 else None)
            statistics[str(n)] = {'replicas': len(values), 'cohorts_successful': len(values),
                                  'parameters': {key: {'mean_error': mean, 'sample_sd': sd}
                                                 for key in plotting.KEYS}}
        return {'scope': 'OFFLINE_FIRST_ITERATION_LINEARIZED_18D_NOT_PEDE_OR_GEOMETRY_CORRECTION',
                'truth': {key: 0. for key in plotting.KEYS},
                'cohorts': records, 'statistics': statistics}

    def test_individual_plot_preserves_exact_n_values_and_formal_errors(self):
        import math
        from studies.alignment_statistics import plot_18d_individual_cohorts as plotting
        data = self.example()
        values = plotting.check_and_collect(data)
        exact, displayed, mean, single = plotting.plot_rows(data, values, 'L1_X')
        self.assertEqual(exact, [(2., -1., .2), (2., 3., .2), (4., 4., .2)])
        self.assertAlmostEqual(displayed[0][0], 2.*math.exp(-.07))
        self.assertAlmostEqual(displayed[1][0], 2.*math.exp(.07))
        self.assertEqual(displayed[2], exact[2])
        self.assertEqual([point[1:] for point in displayed], [point[1:] for point in exact])
        self.assertEqual(single, [(4., 4.)])
        self.assertEqual(len(mean), 1)
        self.assertEqual(mean[0][:2], (2., 1.))
        self.assertAlmostEqual(mean[0][2], math.sqrt(8.))

    def test_plot_singleton_cannot_claim_measured_variance(self):
        from studies.alignment_statistics import plot_18d_individual_cohorts as plotting
        data = self.example()
        data['statistics']['4']['parameters']['L1_X']['sample_sd'] = 0.
        with self.assertRaisesRegex(ValueError, 'Singleton'):
            plotting.plot_rows(data, plotting.check_and_collect(data), 'L1_X')

    def test_plot_rank_deficient_cohorts_are_explicitly_not_displayed(self):
        from studies.alignment_statistics import plot_18d_individual_cohorts as plotting
        data = self.example()
        data['cohorts'].append({'N': 2, 'cohort': 2, 'rank': 17})
        data['statistics']['2']['replicas'] = 3
        values = plotting.check_and_collect(data)
        self.assertEqual(len(values[2]), 2)
        data['statistics']['2']['cohorts_successful'] = 3
        with self.assertRaisesRegex(ValueError, 'entries'):
            plotting.check_and_collect(data)

    def test_plot_refuses_duplicate_identity_wrong_scope_and_absent_truth(self):
        import copy
        from studies.alignment_statistics import plot_18d_individual_cohorts as plotting
        data = self.example()
        duplicate = copy.deepcopy(data)
        duplicate['cohorts'].append(copy.deepcopy(duplicate['cohorts'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            plotting.check_and_collect(duplicate)
        with self.assertRaisesRegex(ValueError, 'scope'):
            plotting.check_and_collect(dict(data, scope='production'))
        with self.assertRaisesRegex(ValueError, 'truth'):
            plotting.check_and_collect(dict(data, truth=None))

    def test_plot_refuses_nonpositive_n_and_nonfinite_summary(self):
        from studies.alignment_statistics import plot_18d_individual_cohorts as plotting
        data = self.example()
        data['cohorts'][0]['N'] = 0
        with self.assertRaisesRegex(ValueError, 'Positive'):
            plotting.check_and_collect(data)
        data = self.example()
        data['statistics']['2']['parameters']['L1_X']['mean_error'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            plotting.plot_rows(data, plotting.check_and_collect(data), 'L1_X')


class OfflineROOTEmptySeries(unittest.TestCase):
    """Exercise real ROOT exports for scientifically valid empty series.

    These are rendering/integrity checks, not detector-observability or physics
    validation. No fit, rank criterion or covariance is changed for a plot.
    """
    def setUp(self):
        import ROOT
        ROOT.gROOT.SetBatch(True)
        self.ROOT = ROOT
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_rank_deficient_no_truth_scan_keeps_no_estimates_and_exports_empty_graphs(self):
        data = synthetic_report()[0]
        for event in data['events']:
            for equation in event['equations']:
                equation['global'] = np.zeros((2, 6)).tolist()
        source = self.root / 'zero_global.json'; source.write_text(json.dumps(data))
        original = source.read_bytes(); output = self.root / 'rank_deficient'
        fit.run(SimpleNamespace(input=str(source), output=str(output), counts='4,8',
                                eigen_cut=1e-10, truth=None))
        summary = json.loads((output / 'fit_18d_linearized_summary.json').read_text())
        self.assertTrue(all(row['rank'] == 0 and row['estimates'] is None
                            and row['formal_sigma'] is None for row in summary['cohorts']))
        rootfile = self.ROOT.TFile.Open(str(output / 'fit_18d_linearized.root'), 'READ')
        self.assertTrue(rootfile and not rootfile.IsZombie())
        try:
            self.assertEqual(str(rootfile.Get('analysis_scope').GetTitle()), summary['scope'])
            self.assertEqual(str(rootfile.Get('truth_available').GetTitle()), 'false')
            for key in fit.LABELS:
                for prefix in ('mean_estimate_', 'formal_sigma_', 'single_cohort_'):
                    graph = rootfile.Get(prefix + key)
                    self.assertTrue(graph)
                    self.assertEqual(graph.GetN(), 0)
                self.assertFalse(rootfile.Get('rms_error_' + key))
        finally:
            rootfile.Close()
        self.assertGreater((output / 'l2_6d_formal_sigma.png').stat().st_size, 0)
        self.assertEqual(source.read_bytes(), original)

    def test_rank_root_handles_singleton_only_and_repeated_only_series(self):
        source = self.root / 'equations.json'; source.write_text(json.dumps(synthetic_report()[0]))
        original = source.read_bytes()
        for counts, expected_mean, expected_single in (('8', 0, 1), ('4', 1, 0)):
            with self.subTest(counts=counts):
                output = self.root / ('information_' + counts)
                information.run(SimpleNamespace(input=str(source), output=str(output),
                    counts=counts, relative_eigen_cut=1e-8))
                rootfile = self.ROOT.TFile.Open(str(output / 'information_42d.root'), 'READ')
                self.assertTrue(rootfile and not rootfile.IsZombie())
                try:
                    self.assertEqual(rootfile.Get('first_cohort_rank').GetN(), 1)
                    self.assertEqual(rootfile.Get('mean_cohort_rank').GetN(), expected_mean)
                    self.assertEqual(rootfile.Get('single_cohort_rank').GetN(), expected_single)
                finally:
                    rootfile.Close()
        self.assertEqual(source.read_bytes(), original)

    def test_individual_root_handles_singleton_only_and_repeated_only_series(self):
        from studies.alignment_statistics import plot_18d_individual_cohorts as plotting
        for n, expected_individual, expected_mean, expected_single in ((4, 1, 0, 1), (2, 2, 1, 0)):
            with self.subTest(N=n):
                data = LinearizedPlotTests().example()
                data['cohorts'] = [row for row in data['cohorts'] if row['N'] == n]
                data['statistics'] = {str(n): data['statistics'][str(n)]}
                source = self.root / f'summary_{n}.json'; source.write_text(json.dumps(data))
                original = source.read_bytes(); output = self.root / f'individual_{n}'
                plotting.create_plots(source, output)
                rootfile = self.ROOT.TFile.Open(str(output / 'individual_18d_linearized.root'), 'READ')
                self.assertTrue(rootfile and not rootfile.IsZombie())
                try:
                    for key in plotting.KEYS:
                        self.assertEqual(rootfile.Get('individual_' + key).GetN(), expected_individual)
                        self.assertEqual(rootfile.Get('mean_' + key).GetN(), expected_mean)
                        self.assertEqual(rootfile.Get('single_cohort_' + key).GetN(), expected_single)
                finally:
                    rootfile.Close()
                self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
