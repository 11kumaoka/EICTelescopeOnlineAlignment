"""Controller integrity and mocked lifecycle tests, without physics claims.

The simulated stage fixtures below are deliberately not valid detector data.
Real EICrecon, DD4hep, Mille and Pede integration is tested separately.
"""
from contextlib import contextmanager, ExitStack, redirect_stderr
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from calibration import native_controller as controller
from research_common import REPO, digest


HAS_NUMPY = importlib.util.find_spec('numpy') is not None
if HAS_NUMPY:
    # Keep sys.modules restoration from unloading a newly imported C extension.
    import numpy
CONFIG = REPO / 'validation/telescope_alignment/l1_l2_l3_y_rx_ry_rz.json'


class ControllerFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='native-controller-unit-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.serial = 0

    @contextmanager
    def initialized(self, *, collections=('EventHeader', 'TelescopeChannels'), channel_count=5,
                    geant4_provenance=False, input_events=5, requested_events=5,
                    header_offset=0, event_header_count=1, provenance_events=None):
        """Patch only external discovery and ROOT reading for a structural run."""
        self.serial += 1
        directory = self.root / str(self.serial)
        directory.mkdir()
        raw = directory / 'simulated-channels.root'
        raw.write_bytes(b'SIMULATED UNIT FIXTURE: NOT A ROOT FILE\n')
        geometry = directory / 'initial.xml'
        geometry.write_text('<simulated-unit-geometry version="initial"/>\n')
        source = directory / 'implementation.py'
        source.write_text('SIMULATED_IMPLEMENTATION = 1\n')
        library = directory / 'simulated-library.so'
        library.write_bytes(b'SIMULATED UNIT FIXTURE: NOT AN ELF LIBRARY\n')
        sources = {str(source): digest(source)}
        module = types.ModuleType('calibration.native_operations')
        payload = [(i, [(layer, 1, 0) for layer in range(channel_count)]) for i in range(input_events)]
        module.channel_values = lambda _: payload
        module.channel_digest = lambda value: hashlib.sha256(json.dumps(value).encode()).hexdigest()
        podio = types.ModuleType('podio')
        frames = []
        for i in range(input_events):
            header = types.SimpleNamespace(getRunNumber=lambda: 7, getEventNumber=lambda i=i: i + header_offset)
            frames.append(types.SimpleNamespace(getAvailableCollections=lambda: collections,
                get=lambda name, header=header: [header] * event_header_count))
        podio.reading = types.SimpleNamespace(get_reader=lambda _: types.SimpleNamespace(get=lambda _: frames))
        args = types.SimpleNamespace(
            raw=raw, geometry=geometry, config=CONFIG, run=directory / 'run',
            build=directory, events=requested_events, chi2_cut=1000., minimum_corrections=1,
            maximum_corrections=3, max_abs_y_mm=2e-5,
            max_angular_sigma_ratio=.25, max_joint_sigma=.25,
            max_scaled_condition=100.)
        if geant4_provenance:
            provenance = directory / 'producer.json'
            value = {'schema': 'native-geant4-input-v1', 'input_origin': 'geant4_digitized',
                'raw_root_sha256': digest(raw), 'channel_payload_sha256': module.channel_digest(payload)}
            if provenance_events is not None:
                value['accepted_events'] = provenance_events
            provenance.write_text(json.dumps(value))
            args.input_provenance = provenance
        with ExitStack() as stack:
            stack.enter_context(patch.dict('sys.modules', {
                'calibration.native_operations': module, 'podio': podio}))
            stack.enter_context(patch.object(controller, 'build_identity', return_value={
                'files': {str(library): digest(library)}, 'ldd': {}, 'head': 'simulated'}))
            stack.enter_context(patch.object(controller, 'source_identity', side_effect=lambda: dict(sources)))
            stack.enter_context(patch('validation.telescope_closure.multi_sensor.fixture',
                                      side_effect=AssertionError('Truth fixture must not be consulted')))
            stack.enter_context(patch('validation.telescope_closure.multi_sensor.injection',
                                      side_effect=AssertionError('Injection must not be consulted')))
            run = controller.initialize(args)
            manifest = json.loads((run / 'manifest.json').read_text())
            yield types.SimpleNamespace(run=run, manifest=manifest, args=args, payload=payload,
                                        channel_digest=module.channel_digest,
                                        source=source, sources=sources, library=library)

    def sensors(self, manifest):
        identities = {p['layer']: (p['volume_id'], p['geometry_id'])
                      for p in manifest['parameters']}
        return [{'layer': layer, 'volume_id': identities.get(layer, (layer, layer))[0],
                 'geometry_id': identities.get(layer, (layer, layer))[1],
                 'center_mm': [0., 0., float(layer)],
                 'acts_rotation': [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]}
                for layer in range(5)]

    def checkpoint(self, state, sequence, *, previous=None, geometry=None,
                   termination='continue', applied=None, attempt=0, **updates):
        run = state.run
        directory = run / 'jobs' / f'attempt_{attempt:03d}' / f'iteration_{sequence:03d}'
        directory.mkdir(parents=True)
        current = geometry or state.manifest['input']['geometry']
        if termination == 'continue':
            candidate = directory / 'simulated-candidate.xml'
            candidate.write_text(f'<simulated-unit-geometry sequence="{sequence}"/>\n')
            next_geometry = controller.identity(candidate)
        else:
            next_geometry = current
        metrics = directory / 'metrics.json'
        metrics.write_text('{"simulated": true}\n')
        information = directory / 'information.json'
        information.write_text('{"simulated": true}\n')
        record = {
            'sequence': sequence, 'previous_marker': previous,
            'geometry': current, 'next_geometry': next_geometry,
            'applied_corrections': (sequence + 1 if termination == 'continue' else sequence)
                                   if applied is None else applied,
            'termination': termination, 'initial_metrics': str(metrics),
            'initial_information': str(information), 'metrics': str(metrics),
            'sensor_poses': self.sensors(state.manifest),
            'next_sensor_poses': self.sensors(state.manifest),
            'current_information': controller.identity(information),
            'remaining_parameters': {},
            'parent_version': 'geo-' + current['sha256'],
            'child_version': 'geo-' + next_geometry['sha256']}
        record.update(updates)
        controller.atomic_json(directory / 'checkpoint.json', record)
        controller.seal(directory, kind='native-iteration-v1', checkpoint=record)
        return dict(record, marker=controller.identity(directory / 'complete.json'))


class AtomicControllerArtifacts(ControllerFixture):
    def test_atomic_json_publishes_complete_json_once(self):
        target = self.root / 'record.json'
        controller.atomic_json(target, {'value': 17})
        self.assertEqual(json.loads(target.read_text()), {'value': 17})
        self.assertEqual(list(self.root.glob('*.partial-*')), [])
        with self.assertRaises(FileExistsError):
            controller.atomic_json(target, {'value': 19})
        self.assertEqual(json.loads(target.read_text()), {'value': 17})
        self.assertEqual(len(list(self.root.glob('*.partial-*'))), 1)

    def test_interrupted_publication_retains_partial_without_final_marker(self):
        target = self.root / 'complete.json'
        with patch.object(controller.os, 'link', side_effect=OSError('SIMULATED link interruption')):
            with self.assertRaisesRegex(OSError, 'SIMULATED'):
                controller.atomic_json(target, {'passed': True})
        self.assertFalse(target.exists())
        partials = list(self.root.glob('complete.json.partial-*'))
        self.assertEqual(len(partials), 1)
        self.assertEqual(json.loads(partials[0].read_text()), {'passed': True})

    def test_nonfinite_json_is_not_published(self):
        target = self.root / 'complete.json'
        with self.assertRaises(ValueError):
            controller.atomic_json(target, {'value': float('nan')})
        self.assertFalse(target.exists())

    def test_parent_seal_includes_nested_completion_records(self):
        child = self.root / 'child'
        child.mkdir()
        (child / 'payload').write_text('preserve me')
        controller.seal(child, passed=True)
        controller.seal(self.root, passed=True)
        record = controller.verify(self.root)
        self.assertIn('child/complete.json', record['output_hashes'])
        (child / 'complete.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'Modified sealed output'):
            controller.verify(self.root)

    def test_sealed_tree_refuses_missing_changed_or_added_outputs(self):
        for mutation in ('missing', 'changed', 'added'):
            with self.subTest(mutation=mutation):
                directory = self.root / mutation
                directory.mkdir()
                payload = directory / 'payload'
                payload.write_text('original')
                controller.seal(directory, passed=True)
                if mutation == 'missing':
                    payload.unlink()
                elif mutation == 'changed':
                    payload.write_text('changed')
                else:
                    (directory / 'unexpected').write_text('extra')
                with self.assertRaises(RuntimeError):
                    controller.verify(directory)

    def test_corrupt_completion_record_cannot_be_a_checkpoint(self):
        (self.root / 'complete.json').write_text('{"output_hashes":')
        with self.assertRaises(json.JSONDecodeError):
            controller.verify(self.root)

    def test_sealed_output_cannot_escape_through_symlink(self):
        directory = self.root / 'sealed'
        directory.mkdir()
        external = self.root / 'outside'
        external.write_text('outside')
        (directory / 'link').symlink_to(external)
        controller.seal(directory, passed=True)
        with self.assertRaisesRegex(RuntimeError, 'escapes'):
            controller.verify(directory)


class PrefixControllerInputs(ControllerFixture):
    def test_full_input_selection_records_all_header_identities(self):
        with self.initialized() as state:
            selected = state.manifest['input_selection']
            self.assertEqual(selected['full_input_events'], 5)
            self.assertEqual(selected['processed_events'], 5)
            self.assertEqual(selected['event_identities'],
                             [{'run_number': 7, 'event_number': i} for i in range(5)])
            self.assertEqual(selected['selected_channel_payload_sha256'],
                             state.manifest['channel_payload_sha256'])

    def test_prefix_is_identified_without_truncating_the_immutable_input(self):
        with self.initialized(input_events=8, requested_events=5,
                              geant4_provenance=True, provenance_events=8) as state:
            selected = state.manifest['input_selection']
            self.assertEqual(selected['method'], 'first_n_events_in_file_order')
            self.assertEqual((selected['first_entry_inclusive'], selected['last_entry_exclusive']), (0, 5))
            self.assertEqual((selected['full_input_events'], selected['processed_events']), (8, 5))
            self.assertEqual(selected['event_identities'],
                             [{'run_number': 7, 'event_number': i} for i in range(5)])
            self.assertEqual(selected['selected_channel_payload_sha256'], state.channel_digest(state.payload[:5]))
            self.assertEqual(state.manifest['channel_payload_sha256'], state.channel_digest(state.payload))
            self.assertNotEqual(selected['selected_channel_payload_sha256'], state.manifest['channel_payload_sha256'])
            self.assertEqual(Path(state.manifest['input']['raw']['path']).read_bytes(), state.args.raw.read_bytes())
            controller.verify(state.run / 'input')
            controller.validate_run_inputs(state.manifest)
            state.args.raw.write_bytes(state.args.raw.read_bytes() + b'UNUSED TAIL CHANGED')
            with self.assertRaisesRegex(RuntimeError, 'Immutable identity changed'):
                controller.validate_run_inputs(state.manifest)

    def test_prefix_still_requires_complete_channels_in_the_full_input(self):
        with self.assertRaisesRegex(ValueError, 'five SiMAPS channels'):
            with self.initialized(input_events=8, requested_events=5, channel_count=4):
                self.fail('Invalid full input unexpectedly accepted')

    def test_request_cannot_exceed_available_input(self):
        with self.assertRaisesRegex(ValueError, 'Requested events exceed'):
            with self.initialized(input_events=5, requested_events=6):
                self.fail('Excess requested count unexpectedly accepted')

    def test_requested_count_rejects_invalid_types_and_values(self):
        for value in (True, 5., 0, -1, 4):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'integer >=5'):
                with self.initialized(requested_events=value):
                    self.fail('Invalid requested count unexpectedly accepted')

    def test_header_event_order_must_match_payload(self):
        with self.assertRaisesRegex(ValueError, 'EventHeader order/count'):
            with self.initialized(input_events=8, requested_events=5, header_offset=1):
                self.fail('Mismatched header order unexpectedly accepted')

    def test_exactly_one_header_is_required_for_each_full_input_event(self):
        for count in (0, 2):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'exactly one EventHeader'):
                with self.initialized(input_events=8, requested_events=5, event_header_count=count):
                    self.fail('Invalid header cardinality unexpectedly accepted')

    def test_producer_event_count_describes_full_input_not_prefix(self):
        for count in (5, 9, True, 8.):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'Producer accepted-event count'):
                with self.initialized(input_events=8, requested_events=5,
                                      geant4_provenance=True, provenance_events=count):
                    self.fail('Mismatched producer count unexpectedly accepted')


class ImmutableControllerInputs(ControllerFixture):
    def test_geant4_provenance_is_snapshotted_sealed_and_checked_on_resume(self):
        with self.initialized(geant4_provenance=True) as state:
            self.assertEqual(state.manifest['input_origin'], 'geant4')
            controller.verify(state.run / 'input')
            controller.validate_run_inputs(state.manifest)
            snapshot = Path(state.manifest['input']['provenance']['path'])
            source = Path(state.manifest['input']['provenance_source']['path'])
            self.assertEqual(snapshot.read_bytes(), source.read_bytes())
            source.write_bytes(source.read_bytes() + b'changed')
            with self.assertRaisesRegex(RuntimeError, 'Immutable identity changed'):
                controller.validate_run_inputs(state.manifest)

    def test_truth_collections_or_missing_channels_are_not_accepted_as_native_input(self):
        with self.assertRaisesRegex(ValueError, 'only EventHeader,TelescopeChannels'):
            with self.initialized(collections=('EventHeader', 'TelescopeChannels', 'MCParticles')):
                self.fail('Truth collection unexpectedly accepted')
        with self.assertRaisesRegex(ValueError, 'five SiMAPS channels'):
            with self.initialized(channel_count=4):
                self.fail('Incomplete channel set unexpectedly accepted')

    def test_initialize_snapshots_inputs_without_truth_or_activation(self):
        with self.initialized() as state:
            controller.verify(state.run / 'input')
            controller.validate_run_inputs(state.manifest)
            self.assertEqual(state.manifest['mode'], 'l1_l2_l3_y_rx_ry_rz')
            self.assertFalse(state.manifest['geometry_activated'])
            self.assertNotIn('truth', state.manifest['input'])
            self.assertEqual(set(state.manifest['input']),
                             {'raw', 'geometry', 'config', 'raw_source', 'geometry_source', 'config_source'})
            self.assertEqual(digest(Path(state.manifest['input']['raw']['path'])),
                             state.manifest['input']['raw_source']['sha256'])
            with self.assertRaises(FileExistsError):
                controller.initialize(state.args)

    def test_input_source_snapshot_and_missing_file_mutations_are_refused(self):
        for name in ('raw_source', 'raw', 'geometry_source', 'config', 'missing'):
            with self.subTest(identity=name), self.initialized() as state:
                key = 'raw' if name == 'missing' else name
                path = Path(state.manifest['input'][key]['path'])
                if name == 'missing':
                    path.unlink()
                else:
                    # The configuration snapshot is a private unit-test copy.
                    self.assertNotEqual(path, CONFIG)
                    path.write_bytes(path.read_bytes() + b'changed')
                with self.assertRaises((RuntimeError, FileNotFoundError)):
                    controller.validate_run_inputs(state.manifest)

    def test_changed_implementation_and_new_source_are_refused(self):
        with self.initialized() as state:
            state.source.write_text('SIMULATED_IMPLEMENTATION = 2\n')
            state.sources[str(state.source)] = digest(state.source)
            with self.assertRaisesRegex(RuntimeError, 'Implementation'):
                controller.validate_run_inputs(state.manifest)
        with self.initialized() as state:
            addition = state.source.parent / 'new_module.py'
            addition.write_text('SIMULATED_ADDITION = True\n')
            state.sources[str(addition)] = digest(addition)
            with self.assertRaisesRegex(RuntimeError, 'Implementation'):
                controller.validate_run_inputs(state.manifest)

    def test_modified_runtime_dependency_is_refused(self):
        with self.initialized() as state:
            state.library.write_bytes(b'changed simulated dependency')
            with self.assertRaisesRegex(RuntimeError, 'runtime dependency'):
                controller.validate_run_inputs(state.manifest)


class ControllerCheckpointChain(ControllerFixture):
    def test_incomplete_iteration_is_not_a_valid_checkpoint(self):
        with self.initialized() as state:
            partial = state.run / 'jobs/attempt_000/iteration_000'
            partial.mkdir(parents=True)
            (partial / 'checkpoint.json.partial-17').write_text('{')
            self.assertEqual(controller.checkpoints(state.run), [])

    def test_valid_chain_can_cross_attempt_directories(self):
        with self.initialized() as state:
            first = self.checkpoint(state, 0)
            self.checkpoint(state, 1, previous=first['marker'],
                            geometry=first['next_geometry'], termination='converged', attempt=1)
            chain = controller.checkpoints(state.run)
            self.assertEqual([v['sequence'] for v in chain], [0, 1])
            self.assertEqual(chain[1]['geometry'], chain[0]['next_geometry'])

    def test_broken_marker_duplicate_or_sequence_gap_is_refused(self):
        for failure in ('marker', 'duplicate', 'gap'):
            with self.subTest(failure=failure), self.initialized() as state:
                if failure == 'gap':
                    self.checkpoint(state, 1)
                else:
                    first = self.checkpoint(state, 0)
                    self.checkpoint(state, 0 if failure == 'duplicate' else 1,
                                    previous=None, geometry=first['next_geometry'], attempt=1)
                with self.assertRaisesRegex(RuntimeError, 'checkpoint'):
                    controller.checkpoints(state.run)

    def test_initial_or_successive_geometry_cannot_be_substituted(self):
        for first_wrong in (True, False):
            with self.subTest(first=first_wrong), self.initialized() as state:
                foreign = state.run / 'unrelated.xml'
                foreign.write_text('<simulated-unit-geometry unrelated="true"/>')
                if first_wrong:
                    self.checkpoint(state, 0, geometry=controller.identity(foreign))
                else:
                    first = self.checkpoint(state, 0)
                    self.checkpoint(state, 1, previous=first['marker'],
                                    geometry=controller.identity(foreign), attempt=1)
                with self.assertRaisesRegex(RuntimeError, 'parent/child'):
                    controller.checkpoints(state.run)

    def test_invalid_count_version_or_termination_is_refused(self):
        for update in ({'applied': 2}, {'parent_version': 'geo-wrong'},
                       {'child_version': 'geo-wrong'}, {'termination': 'activate'}):
            with self.subTest(update=update), self.initialized() as state:
                self.checkpoint(state, 0, **update)
                with self.assertRaises(RuntimeError):
                    controller.checkpoints(state.run)

    def test_a_terminal_checkpoint_cannot_have_a_successor(self):
        with self.initialized() as state:
            first = self.checkpoint(state, 0)
            terminal = self.checkpoint(state, 1, previous=first['marker'],
                                       geometry=first['next_geometry'], termination='converged')
            self.checkpoint(state, 2, previous=terminal['marker'],
                            geometry=terminal['next_geometry'], applied=2, attempt=1)
            with self.assertRaisesRegex(RuntimeError, 'parent/child'):
                controller.checkpoints(state.run)


@unittest.skipUnless(HAS_NUMPY, 'Lifecycle reporting requires the installed NumPy environment')
class SimulatedControllerLifecycle(ControllerFixture):
    @contextmanager
    def simulated_operations(self, decisions, *, interrupt=None, fail_candidate=False):
        """Exercise real Controller bookkeeping with explicitly mocked stages."""
        calls = []

        def run_stage(directory, action, manifest, lock_fd, **arguments):
            calls.append((action, dict(arguments)))
            destination = directory / action
            output = destination / 'artifacts'
            output.mkdir(parents=True)
            sensors = self.sensors(manifest)
            if action == 'geometry':
                (output / 'geometry.json').write_text(json.dumps({'sensors': sensors}))
            elif action == 'reconstruct':
                (output / 'simulated-output.txt').write_text('No EICrecon was run.\n')
                if interrupt == directory.name:
                    raise InterruptedError('SIMULATED stage interruption')
            elif action == 'solve':
                count = len(manifest['parameters'])
                (output / 'independent.json').write_text(json.dumps({
                    'parameters': {str(p['label']): 0. for p in manifest['parameters']},
                    'normal': [[float(i == j) for j in range(count)] for i in range(count)],
                    'parameter_units': {str(p['label']): p['unit'] for p in manifest['parameters']},
                    'pede_reported_sigma': [1.] * count,
                    'correlation': [[float(i == j) for j in range(count)] for i in range(count)],
                    'nominal_sigma': [1.] * count, 'rank': count,
                    'condition_diagonal_scaled': 1.}))
            elif action == 'correct':
                (output / 'candidate.xml').write_text(
                    '<simulated-unit-geometry candidate="' + directory.name + '"/>\n')
                if fail_candidate:
                    raise RuntimeError('SIMULATED physical containment refusal')
                audit = output / 'geometry_audit'
                audit.mkdir()
                (audit / 'geometry.json').write_text(json.dumps({'sensors': sensors}))
            controller.seal(destination, action=action, passed=True, simulated=True)
            return output

        def summarize(_reconstruction, _solve, _sensors, _selected, baseline=None):
            return {'tracks': 5, 'rejected': 0, 'processed': 5,
                    'chi2_ratio': 10. if baseline is None else 1.,
                    'residual_norm': 1. if baseline is None else .1,
                    'association_sha256': 'SIMULATED-ASSOCIATIONS', 'measurements': []}

        # The omitted legacy service is represented only by an activation tripwire.
        legacy = types.ModuleType('calibration.geometry_manager')
        legacy.Geometries = types.SimpleNamespace(activate=lambda: None)
        with patch.dict('sys.modules', {'calibration.geometry_manager': legacy}), \
                patch.object(controller, 'run_stage', side_effect=run_stage), \
                patch.object(controller, 'summarize', side_effect=summarize), \
                patch('calibration.convergence.evaluate_native',
                      side_effect=[{'status': value, 'reason': 'SIMULATED decision'} for value in decisions]), \
                patch('calibration.geometry_manager.Geometries.activate',
                      side_effect=AssertionError('Production activation is forbidden')) as activation:
            yield calls
            activation.assert_not_called()

    def test_simulated_fresh_iterations_publish_final_without_activation(self):
        with self.initialized() as state, self.simulated_operations(['continue', 'converged']) as calls:
            report = controller.calibrate(state.run)
            self.assertEqual(report['status'], 'converged')
            self.assertEqual(report['applied_corrections'], 1)
            self.assertFalse(report['geometry_activated'])
            controller.verify(state.run / 'final')
            chain = controller.checkpoints(state.run)
            self.assertEqual(len(chain), 2)
            reconstructions = [arguments for action, arguments in calls if action == 'reconstruct']
            self.assertEqual(len(reconstructions), 2)
            self.assertEqual({value['raw'] for value in reconstructions},
                             {state.manifest['input']['raw']['path']})
            self.assertNotEqual(reconstructions[0]['geometry'], reconstructions[1]['geometry'])

    def test_simulated_interruption_preserves_partial_and_resumes_last_checkpoint(self):
        with self.initialized() as state:
            with self.simulated_operations(['continue'], interrupt='iteration_001'), redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(InterruptedError, 'SIMULATED'):
                    controller.calibrate(state.run)
            chain = controller.checkpoints(state.run)
            self.assertEqual(len(chain), 1)
            partial = state.run / 'jobs/attempt_000/iteration_001'
            self.assertTrue((partial / 'reconstruct/artifacts/simulated-output.txt').is_file())
            self.assertFalse((partial / 'complete.json').exists())
            self.assertFalse((state.run / 'final').exists())
            with self.simulated_operations(['converged']):
                report = controller.calibrate(state.run)
            self.assertEqual(report['status'], 'converged')
            resumed = json.loads((state.run / 'jobs/attempt_001/start.json').read_text())
            self.assertEqual(resumed['resumed_checkpoint'], chain[-1]['marker'])
            self.assertTrue(partial.is_dir())
            self.assertEqual(len(controller.checkpoints(state.run)), 2)

    def test_simulated_candidate_refusal_keeps_initial_verified_geometry(self):
        with self.initialized() as state:
            with self.simulated_operations(['continue'], fail_candidate=True), redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'containment refusal'):
                    controller.calibrate(state.run)
            self.assertEqual(controller.checkpoints(state.run), [])
            report = json.loads((state.run / 'jobs/attempt_000/report.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['last_valid_geometry'], state.manifest['input']['geometry'])
            self.assertFalse(report['geometry_activated'])
            self.assertFalse((state.run / 'final').exists())

    def test_iteration_limit_is_not_published_as_converged(self):
        with self.initialized() as state, self.simulated_operations(
                ['continue', 'continue', 'continue', 'iteration_limit']):
            report = controller.calibrate(state.run)
            self.assertEqual(report['status'], 'iteration_limit')
            self.assertEqual(report['applied_corrections'], 3)
            self.assertFalse((state.run / 'final').exists())
            self.assertFalse(report['geometry_activated'])

    def test_existing_controller_guard_prevents_any_stage(self):
        with self.initialized() as state:
            guard = state.run / 'controller.guard'
            guard.mkdir()
            with patch.object(controller, 'run_stage') as stage:
                with self.assertRaisesRegex(RuntimeError, 'guard exists'):
                    controller.calibrate(state.run)
                stage.assert_not_called()
            self.assertTrue(guard.is_dir())
            self.assertFalse((state.run / 'jobs').exists())

    def test_terminal_checkpoint_can_complete_publication_without_new_fit(self):
        with self.initialized() as state:
            first = self.checkpoint(state, 0)
            self.checkpoint(state, 1, previous=first['marker'],
                            geometry=first['next_geometry'], termination='converged')
            partial = state.run / 'final-pending-attempt_000'
            partial.mkdir()
            sentinel = partial / 'interrupted-copy'
            sentinel.write_text('preserve the incomplete publication')
            with patch.object(controller, 'run_stage', side_effect=AssertionError('No numerical rerun allowed')):
                report = controller.calibrate(state.run)
            self.assertEqual(report['status'], 'converged')
            controller.verify(state.run / 'final')
            self.assertEqual(digest(state.run / 'final/candidate.xml'), first['next_geometry']['sha256'])
            self.assertEqual(sentinel.read_text(), 'preserve the incomplete publication')

    def test_final_publication_failure_preserves_diagnostics_and_can_resume(self):
        with self.initialized() as state:
            with self.simulated_operations(['continue', 'converged']), \
                    patch.object(controller.os, 'rename', side_effect=OSError('SIMULATED final rename interruption')), \
                    redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(OSError, 'SIMULATED'):
                    controller.calibrate(state.run)
            attempt = state.run / 'jobs/attempt_000'
            self.assertEqual(json.loads((attempt / 'report.json').read_text())['status'], 'converged')
            failure = json.loads((attempt / 'failure.json').read_text())
            self.assertEqual(failure['status'], 'failed')
            self.assertIn('SIMULATED', failure['error'])
            self.assertFalse((state.run / 'final').exists())
            partial = state.run / 'final-pending-attempt_000'
            controller.verify(partial)
            with patch.object(controller, 'run_stage', side_effect=AssertionError('No numerical rerun allowed')):
                report = controller.calibrate(state.run)
            self.assertEqual(report['status'], 'converged')
            controller.verify(state.run / 'final')
            self.assertTrue(partial.is_dir())


if __name__ == '__main__':
    unittest.main()
