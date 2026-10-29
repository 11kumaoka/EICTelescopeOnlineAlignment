"""Public workflow configuration and artifact-integrity tests.

Receipt-only fixtures and mocked native checkpoints are explicitly simulated;
these tests do not establish Geant4 transport or alignment physics performance.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from calibration import workflow
from research_common import digest, done
from validation import test_native_digitization as digitization_tests


def example():
    return json.loads((workflow.REPO/'calibration/config/native_workflow_smoke.json').read_text())


class WorkflowConfiguration(unittest.TestCase):
    def test_fixed_environment_accepts_both_exact_launcher_forms_only(self):
        for image in (workflow.IMAGE, 'eicweb/eic_xl@' + workflow.IMAGE):
            with self.subTest(image=image), patch.dict('os.environ', {'TESTBENCH_IMAGE': image}):
                workflow.require_fixed_environment()
        for image in ('', 'eicweb/eic_xl:nightly', 'another/image@' + workflow.IMAGE,
                      'sha256:' + '0' * 64, 'eicweb/eic_xl@sha256:' + '0' * 64):
            with self.subTest(image=image), patch.dict('os.environ', {'TESTBENCH_IMAGE': image}):
                with self.assertRaisesRegex(RuntimeError, 'fixed Docker image'):
                    workflow.require_fixed_environment()

    def test_examples_resolve_to_existing_supported_alignment_configs(self):
        for name, mode in (('smoke', 'l1_y'), ('example', 'l1_y'), ('multi', 'l1_y_rx_ry_rz_l2_y')):
            config = json.loads((workflow.REPO/f'calibration/config/native_workflow_{name}.json').read_text())
            actual, selected = workflow.validate_configuration(config)
            self.assertEqual(selected, mode)
            self.assertTrue(Path(actual['alignment']['config']).is_file())
            self.assertEqual(actual['geometry']['global_y_mm']['1'], .080)
            self.assertEqual(actual['environment']['kind'], 'fixed-docker')

    def test_unsupported_full_reconstruction_contract_is_refused(self):
        for key, item in (('particle', 'mu-'), ('energy_gev', 5.), ('energy_gev', True)):
            value = example(); value['simulation'][key] = item
            with self.subTest(key=key, value=item), self.assertRaisesRegex(ValueError, 'seed prior is fixed'):
                workflow.validate_configuration(value)

    def test_configuration_typos_nonfinite_inputs_and_unsafe_counts_are_refused(self):
        changes = [lambda v: v.update(unknown=True),
                   lambda v: v['simulation'].update(unknown=1),
                   lambda v: v['simulation'].update(events=4),
                   lambda v: v['simulation'].update(seed=0),
                   lambda v: v['controller'].update(maximum_corrections=4),
                   lambda v: v['geometry']['global_y_mm'].update({'1': float('nan')}),
                   lambda v: v['geometry']['global_rotation_vector_rad'].update({'1': [.1, .2]}),
                   lambda v: v['alignment'].update(chi2_cut=-1),
                   lambda v: v['environment'].update(kind='unreviewed-installation')]
        for mutate in changes:
            value = example(); mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                workflow.validate_configuration(value)

    def test_result_paths_refuse_external_root_existing_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = (Path(temporary)/'repository').resolve(); repository.mkdir()
            result = repository/'result'; result.mkdir()
            outside = Path(temporary)/'outside'; outside.mkdir()
            with patch.object(workflow, 'REPO', repository):
                for path in (result, outside, result/'with whitespace'):
                    with self.subTest(path=path), self.assertRaises(ValueError): workflow.fresh_output(path)
                existing = result/'existing'; existing.mkdir()
                with self.assertRaises(FileExistsError): workflow.fresh_output(existing)
                (result/'escape').symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ValueError): workflow.fresh_output(result/'escape/new')
                self.assertEqual(workflow.fresh_output(result/'new'), result/'new')

    def test_source_set_addition_is_detected_even_when_old_bytes_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary); directory = run/'configuration'; directory.mkdir()
            (directory/'configuration.json').write_text('{}')
            (directory/'run_plan.json').write_text(json.dumps({'sources': {'old.py': 'same'}, 'source_inputs': {}}))
            done(directory, passed=True)
            with patch('calibration.native_controller.source_identity', return_value={'old.py': 'same', 'new.py': 'added'}):
                with self.assertRaisesRegex(RuntimeError, 'source set'):
                    workflow.load_run(run)


class WorkflowProducerReuse(unittest.TestCase):
    def setUp(self):
        self.fixture = digitization_tests.SimulationProducerBinding(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.specification = {'particle': 'e-', 'energy_gev': 1., 'beam': 'pencil',
                              'events': 5, 'seed': 123, 'position_mm': [249., 0., 0.], 'direction': [1., 0., 0.]}

    def test_completed_producer_requires_exact_event_seed_and_gun_declaration(self):
        self.assertEqual(workflow.checked_reuse(self.specification, self.fixture.stage)['events'], 5)
        for key, item in (('events', 6), ('seed', 124), ('position_mm', [240., 0., 0.]), ('beam', 'broad')):
            value = dict(self.specification, **{key: item})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'declared gun'):
                workflow.checked_reuse(value, self.fixture.stage)

    def prepared_run(self, change_pose=False):
        run = self.fixture.root/'public_run'; run.mkdir()
        preparation = run/'preparation'; preparation.mkdir()
        truth = preparation/'truth.xml'; truth.write_bytes(self.fixture.geometry.read_bytes())
        audit = preparation/'truth_audit'; audit.mkdir()
        sensors = copy.deepcopy(self.fixture.sensors)
        if change_pose: sensors[1]['center_mm'][1] = .080
        (audit/'geometry.json').write_text(json.dumps({'passed': True, 'events': [], 'geometry': str(truth), 'sensors': sensors}))
        done(audit, passed=True, geometry_sha256=digest(truth)); done(preparation, passed=True)
        return run

    def test_reuse_publishes_only_reference_and_never_relabels_truth_geometry(self):
        run = self.prepared_run()
        with patch.object(workflow, 'load_run', return_value=({'simulation': self.specification},
                {'reused_simulation_stage': str(self.fixture.stage)}, Path('simulated-build'))):
            workflow.simulate_stage(run)
        self.assertTrue((run/'simulation/reference.json').is_file())
        self.assertEqual(workflow.simulation_stage(run), self.fixture.stage)
        self.assertFalse((run/'simulation/transport/events.edm4hep.root').exists())
        self.assertFalse(json.loads((run/'simulation/complete.json').read_text())['new_simulation_executed'])

    def test_reuse_refuses_different_actual_truth_poses_before_publishing(self):
        run = self.prepared_run(change_pose=True)
        with patch.object(workflow, 'load_run', return_value=({'simulation': self.specification},
                {'reused_simulation_stage': str(self.fixture.stage)}, Path('simulated-build'))):
            with self.assertRaisesRegex(ValueError, 'Truth sensor poses'):
                workflow.simulate_stage(run)
        self.assertFalse((run/'simulation').exists())


class WorkflowReport(unittest.TestCase):
    def test_manifest_indexes_actual_artifacts_and_does_not_claim_physics_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary); (run/'configuration').mkdir(); done(run/'configuration', passed=True)
            controller = run/'controller'; controller.mkdir(); (controller/'input').mkdir()
            manifest = controller/'manifest.json'; manifest.write_text('{}')
            (controller/'manifest.seal.json').write_text(json.dumps({'path': str(manifest), 'sha256': digest(manifest)}))
            final = controller/'final'; final.mkdir(); candidate = final/'candidate.xml'
            candidate.write_text('<simulated-checkpoint-for-report-test/>')
            identity = {'path': str(candidate), 'sha256': digest(candidate)}
            report = {'status': 'iteration_limit', 'applied_corrections': 1, 'engineering_success': True,
                      'quality_warnings': ['Simulated high chi2 advisory'], 'reason': 'Simulated report-only fixture'}
            (final/'report.json').write_text(json.dumps(report))
            chain = [{}, {'geometry': identity, 'next_geometry': identity}]
            def simulated_root(run, output):
                target = output/'diagnostics.root'; target.write_bytes(b'Simulated report artifact, not a ROOT file')
                return {'path': str(target), 'sha256': digest(target), 'keys': [], 'incomplete_artifacts': []}
            with patch.object(workflow, 'load_run'), patch('calibration.native_controller.verify'), \
                    patch('calibration.native_controller.checkpoints', return_value=chain), \
                    patch.object(workflow, 'root_report', side_effect=simulated_root):
                self.assertTrue(workflow.report_stage(run))
            summary = json.loads((run/'report/summary.json').read_text())
            self.assertTrue(summary['engineering_success'])
            self.assertEqual(summary['physics_quality'], 'not_validated')
            self.assertEqual(summary['approval_status'], 'unapproved')
            self.assertFalse(summary['geometry_activated'])
            index = json.loads((run/'run_manifest.json').read_text())
            for relative in ('controller/final/candidate.xml', 'report/diagnostics.root', 'report/report.md', 'report/summary.json'):
                self.assertEqual(index['artifacts'][relative]['sha256'], digest(run/relative))

    def test_actual_root_report_serializes_fitted_metrics_and_candidate_centers(self):
        try:
            import ROOT
        except ImportError:
            self.skipTest('The fixed ROOT environment is required')
        from calibration.native_controller import seal
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary); iteration = run/'controller/jobs/attempt_000/iteration_000'
            artifacts = iteration/'reconstruct/artifacts'; artifacts.mkdir(parents=True)
            sensors = [{'layer': layer, 'geometry_id': 100+layer,
                        'center_mm': [250.+50*layer, 0., 0.]} for layer in range(7)]
            states = [{'geometry_id': 100+layer, 'residual_mm': [.001, -.002]} for layer in range(5)]
            (artifacts/'audit.json').write_text(json.dumps({'events': [{'accepted': True, 'chi2': 20.,
                'stored_ndf': 10, 'smoothed_states': states}]}))
            seal(artifacts.parent, passed=True)
            geometry = iteration/'geometry/artifacts'; geometry.mkdir(parents=True)
            (geometry/'geometry.json').write_text(json.dumps({'sensors': sensors}))
            for role in ('initial_audit', 'truth_audit'):
                directory = run/'preparation'/role; directory.mkdir(parents=True)
                (directory/'geometry.json').write_text(json.dumps({'sensors': sensors}))
            candidate = copy.deepcopy(sensors); candidate[1]['center_mm'][1] = .080
            (iteration/'checkpoint.json').write_text(json.dumps({'sensor_poses': sensors, 'next_sensor_poses': candidate,
                'sequence': 0, 'termination': 'iteration_limit'}))
            (iteration/'parameter_information.json').write_text(json.dumps({'selected_labels': [700102],
                'parameter_units': {'700102': 'mm'}, 'pede_parameters': {'700102': .080}, 'pede_reported_sigma': [.001]}))
            (iteration/'metrics.json').write_text(json.dumps({'pede': {'chi2_per_ndf': 2.5}}))
            seal(iteration, kind='simulated-report-fixture')
            output = run/'report'; output.mkdir()
            report = workflow.root_report(run, output)
            self.assertEqual(report['incomplete_artifacts'], [])
            file = ROOT.TFile.Open(report['path'])
            self.assertEqual(file.Get('track_chi2_evaluation_0').GetEntries(), 1)
            self.assertEqual(file.Get('residual_L1_0_evaluation_0').GetEntries(), 1)
            self.assertEqual(file.Get('acts_chi2_over_stored_ndf').GetPointY(0), 2.)
            self.assertEqual(file.Get('pede_chi2_over_ndf').GetPointY(0), 2.5)
            self.assertEqual(file.Get('geometry_global_y_unapproved_candidate').GetPointY(1), .080)
            self.assertEqual(file.Get('remaining_parameter_700102').GetErrorY(0), .001)
            self.assertTrue(file.Get('calibration_summary').InheritsFrom('TCanvas'))
            file.Close()


if __name__ == '__main__':
    unittest.main()
