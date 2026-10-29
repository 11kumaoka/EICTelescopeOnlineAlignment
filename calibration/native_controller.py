"""Bounded, Truth-independent native calibration; candidates are never activated.

The retained queue lock and process supervisor protect one immutable input file.
Numerical operations are shared with the independent closure driver. Completion
markers publish a verified iteration only after every child process has exited.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import traceback

from research_common import REPO, digest, write_json
from calibration.queue_manager import exclusive, recover_guard
from calibration.runner import stage as supervised_stage


def atomic_json(path, value):
    """Publish once, atomically; retain any interrupted temporary file."""
    path = Path(path)
    temporary = path.with_name(path.name + '.partial-' + str(os.getpid()))
    with temporary.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    # A hard link provides atomic, exclusive publication without overwriting.
    os.link(temporary, path)
    temporary.unlink()
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def seal(directory, **information):
    hashes = {str(p.relative_to(directory)): digest(p)
              for p in sorted(directory.rglob('*'))
              if p.is_file() and p != directory / 'complete.json'}
    atomic_json(directory / 'complete.json', dict(information, output_hashes=hashes))


def verify(directory):
    """Verify the entire sealed tree, including nested completion records."""
    record = json.loads((directory / 'complete.json').read_text())
    hashes = record['output_hashes']
    actual = {str(p.relative_to(directory)) for p in directory.rglob('*')
              if p.is_file() and p != directory / 'complete.json'}
    if actual != set(hashes): raise RuntimeError('Sealed artifact set changed: ' + str(directory))
    for relative, expected in hashes.items():
        path = directory / relative
        if not path.resolve().is_relative_to(directory.resolve()):
            raise RuntimeError('Artifact escapes its sealed directory')
        if digest(path) != expected: raise RuntimeError('Modified sealed output: ' + str(path))
    return record


def identity(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': digest(path)}


def check_identity(record):
    if digest(Path(record['path'])) != record['sha256']:
        raise RuntimeError('Immutable identity changed: ' + record['path'])


def source_identity():
    names = subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=REPO).decode().split('\0')
    selected = {}
    for name in sorted(set(names)):
        path = REPO / name
        if not path.is_file() or name.startswith(('result/', 'results/', 'build/', 'external/')): continue
        if path.suffix in ('.py', '.cc', '.cpp', '.h', '.hpp', '.json', '.sh', '.cmake') or path.name == 'CMakeLists.txt':
            selected[str(path)] = digest(path)
    if not selected: raise RuntimeError('No maintained implementation sources found')
    return selected


def repository_identity():
    """Identify an uncommitted candidate honestly, retaining complete source hashes."""
    top = subprocess.check_output(
        ['git', 'rev-parse', '--show-toplevel'], cwd=REPO, text=True).strip()
    if Path(top).resolve() != REPO.resolve():
        raise RuntimeError('Implementation must have its own Git repository')
    revision = subprocess.run(['git', 'rev-parse', '--verify', '--quiet', 'HEAD'],
                              cwd=REPO, text=True, capture_output=True)
    if revision.returncode == 0:
        head = revision.stdout.strip(); state = 'resolved'
    elif revision.returncode == 1:
        branch = subprocess.run(['git', 'symbolic-ref', '--quiet', 'HEAD'],
                                cwd=REPO, text=True, capture_output=True)
        if branch.returncode != 0:
            raise RuntimeError('Unresolvable detached Git HEAD')
        exists = subprocess.run(['git', 'show-ref', '--verify', '--quiet', branch.stdout.strip()],
                                cwd=REPO, capture_output=True)
        if exists.returncode != 1:
            raise RuntimeError('Git HEAD failed to resolve an existing reference')
        head = None; state = 'unborn'
    else:
        raise RuntimeError('Unable to inspect implementation Git HEAD')
    sources = {str(Path(path).relative_to(REPO)): value
               for path, value in source_identity().items()}
    fingerprint = hashlib.sha256(json.dumps(sources, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    return {'head': head, 'git_head_state': state, 'source_tree_sha256': fingerprint}


def build_identity(build):
    """Record the actual ELF dependencies selected by the production environment."""
    from calibration.native_operations import environment
    binaries = ['TelescopeTracking.so', 'TelescopeMille.so', 'libTestbenchBarrelMetadata.so',
                'TelescopeChannels', 'TelescopeMilleDerivatives', 'TelescopePhysicalGeometryAudit']
    required = binaries + ['CMakeCache.txt']
    files = {str(build / name): digest(build / name) for name in required}
    files[str(REPO / 'external/install/bin/pede')] = digest(REPO / 'external/install/bin/pede')
    files[str(REPO / 'external/install/lib/libMille2.so')] = digest(REPO / 'external/install/lib/libMille2.so')
    linkage = {}
    for name in binaries:
        text = subprocess.check_output(['ldd', str(build / name)], env=environment(build), text=True)
        if 'not found' in text: raise RuntimeError('Unresolved runtime dependency: ' + name)
        linkage[name] = text
        for line in text.splitlines():
            match = re.search(r'(?:=>\s+)?(/\S+)\s+\(', line)
            if match:
                path = Path(match[1]).resolve(); files[str(path)] = digest(path)
    eicrecon = Path(shutil.which('eicrecon') or '').resolve()
    if not eicrecon.is_file(): raise RuntimeError('Installed EICrecon executable not found')
    files[str(eicrecon)] = digest(eicrecon)
    return {'files': files, 'ldd': linkage, **repository_identity(),
            'image': os.environ.get('TESTBENCH_IMAGE'), 'python': sys.version}


def selected_mode(config):
    """Accept maintained explicit-sensor configurations without inferring Truth."""
    from validation.telescope_closure.multi_sensor import MODE_CONFIG, configuration
    value = json.loads(config.read_text())
    found = [mode for mode in MODE_CONFIG if configuration(mode) == value]
    if len(found) != 1: raise ValueError('Alignment configuration is not an existing validated native scope')
    return found[0], value


def validate_run_inputs(manifest):
    for item in manifest['input'].values(): check_identity(item)
    if source_identity() != manifest['sources']:
        raise RuntimeError('Implementation source set or contents changed since run creation')
    for path, sha in manifest['build']['files'].items():
        if digest(Path(path)) != sha: raise RuntimeError('Build/runtime dependency changed: ' + path)


def checkpoints(run):
    manifest = json.loads((run / 'manifest.json').read_text())
    items = []
    for path in run.glob('jobs/attempt_*/iteration_*/complete.json'):
        record = verify(path.parent)
        if record.get('kind') != 'native-iteration-v1': raise RuntimeError('Unknown checkpoint schema')
        item = record['checkpoint']; item = dict(item, marker=identity(path))
        items.append(item)
    items.sort(key=lambda item: item['sequence'])
    previous = None
    parent = manifest['input']['geometry']; corrections = 0; terminal = False
    for index, item in enumerate(items):
        if item['sequence'] != index or item['previous_marker'] != previous:
            raise RuntimeError('Broken or duplicate Geometry checkpoint chain')
        check_identity(item['geometry']); check_identity(item['next_geometry'])
        if terminal or item['geometry'] != parent:
            raise RuntimeError('Geometry checkpoint parent/child mismatch')
        if item['termination'] not in ('continue', 'converged', 'iteration_limit'):
            raise RuntimeError('Unknown checkpoint termination')
        if item['termination'] == 'continue':
            corrections += 1
        elif item['next_geometry'] != item['geometry']:
            raise RuntimeError('Terminal checkpoint must retain its evaluated Geometry')
        if item['applied_corrections'] != corrections or corrections > manifest['maximum_corrections']:
            raise RuntimeError('Invalid checkpoint correction count')
        if item['parent_version'] != 'geo-' + item['geometry']['sha256'] or item['child_version'] != 'geo-' + item['next_geometry']['sha256']:
            raise RuntimeError('Geometry version does not identify its exact XML')
        terminal = item['termination'] != 'continue'; parent = item['next_geometry']
        previous = item['marker']
    return items


def worker(request_path):
    """One supervised stage; numerical subprocesses share its process group."""
    from calibration import native_operations as operations
    request = json.loads(request_path.read_text()); output = Path(request['output'])
    action = request['action']; build = Path(request['build'])
    if action == 'geometry':
        operations.geometry_audit(build, output, Path(request['geometry']))
    elif action == 'reconstruct':
        operations.reconstruct(build, output, Path(request['geometry']), Path(request['raw']),
                               Path(request['config']), request['events'], request['chi2_cut'],
                               input_origin=request.get('input_origin', 'synthetic'),
                               geometry_aware_holes=request.get('geometry_aware_holes', False))
    elif action == 'derivatives':
        operations.physical_derivatives(build, output, Path(request['reconstruction']),
            Path(request['geometry']), request['sensors'], request['mode'])
    elif action == 'solve':
        operations.solve(build, output, Path(request['reconstruction']), request['mode'], request['events'],
                         diagnostic=request.get('validation_mode', 'strict') == 'diagnostic')
    elif action == 'correct':
        output.mkdir()
        operations.correct(build, output / 'candidate.xml', Path(request['geometry']),
                           request['sensors'], {int(k): v for k, v in request['parameters'].items()}, request['mode'])
    else: raise ValueError('Unknown native operation')
    # Operations retain their historical markers; the enclosing worker seal is
    # authoritative for the Controller and is published atomically by the parent.


def run_stage(directory, action, manifest, lock_fd, **arguments):
    request = dict(action=action, build=manifest['build_dir'], mode=manifest['mode'],
                   output=str(directory / action / 'artifacts'), **arguments)
    request_path = directory / (action + '-request.json'); write_json(request_path, request)
    destination = directory / action
    try:
        supervised_stage('calibration.native_controller', ['stage', '--request', request_path],
                         destination, lock_fd, threading.Event(), lambda _: None)
    except RuntimeError as error:
        failure = destination / 'failure.json'
        if failure.exists():
            record = json.loads(failure.read_text())
            from calibration.native_policy import QualityGateError
            failure_type = QualityGateError if record['type'] == 'QualityGateError' else RuntimeError
            raise failure_type('Native ' + action + ' failed: ' + record['type'] + ': ' + record['message'] +
                               '; see ' + str(destination / 'stdout.log')) from error
        raise
    seal(destination, action=action, passed=True)
    return destination / 'artifacts'


def checked_measurement(hit, geometry_aware=False):
    """Keep intrinsic Raw coordinates distinct from rounded EDM coordinates.

    The official path serializes the global Hit position and local Measurement
    as binary32 separately. A changed Geometry can therefore move a stored
    local value by one ULP without changing its physical channel. The opt-in
    observer verifies each serialization exactly against the actual DD4hep/
    ACTS transforms; compare direct segmentation coordinates across versions.
    """
    result = {key: hit[key] for key in ('loc_mm', 'covariance_mm2')}
    if not geometry_aware:
        return result
    proof = hit.get('serialization_audit', {})
    if proof.get('passed') is not True:
        raise RuntimeError('Missing/failed intrinsic channel serialization audit')
    shapes = {'canonical_sensor_local_mm': 3, 'canonical_cell_dimensions_mm': 2,
              'expected_global_float32_mm': 3, 'expected_measurement_local_float32_mm': 2,
              'expected_measurement_covariance_float32_mm2': 3}
    for key, size in shapes.items():
        values = proof.get(key)
        if (not isinstance(values, list) or len(values) != size
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)):
            raise RuntimeError('Invalid intrinsic channel serialization field: ' + key)
    if not all(v > 0 for v in proof['canonical_cell_dimensions_mm']):
        raise RuntimeError('Invalid intrinsic channel dimensions')
    for expected, actual in (('expected_global_float32_mm', 'position_mm'),
                             ('expected_measurement_local_float32_mm', 'loc_mm'),
                             ('expected_measurement_covariance_float32_mm2', 'covariance_mm2')):
        if proof[expected] != hit.get(actual):
            raise RuntimeError('Serialized measurement differs from its actual Geometry/channel audit: ' + actual)
    result.update({key: proof[key] for key in ('canonical_sensor_local_mm', 'canonical_cell_dimensions_mm')})
    return result


def compare_measurements(current, original, geometry_aware=False):
    """Require immutable intrinsic channels, or retain the legacy strict check."""
    import numpy as np
    if geometry_aware:
        for key in ('canonical_sensor_local_mm', 'canonical_cell_dimensions_mm'):
            if current[key] != original[key]:
                raise RuntimeError('Intrinsic channel coordinates/dimensions changed across Geometry versions')
    else:
        np.testing.assert_allclose(current['loc_mm'], original['loc_mm'], atol=2e-6, rtol=0)
    np.testing.assert_allclose(current['covariance_mm2'], original['covariance_mm2'], atol=1e-12, rtol=0)


def summarize(reconstruction, solve, sensors, selected, baseline=None, diagnostic=False, geometry_aware_holes=False):
    """Data-only identity, covariance, binary and solver diagnostics."""
    import numpy as np
    from validation.telescope_alignment.check import records, profile
    from validation.telescope_closure.multi_sensor import mapping
    audit = json.loads((reconstruction / 'audit.json').read_text())
    native = json.loads((reconstruction / 'mille.json').read_text())
    blocks = list(records(reconstruction / 'mille.dat'))
    if not audit['passed'] or not native['passed']: raise RuntimeError('Reconstruction audit failed')
    if not (audit['processed'] == audit['accepted'] == len(audit['events']) == len(native['events']) == native['records'] == len(blocks)):
        raise RuntimeError('Missing/rejected native track records')
    if audit['rejected'] != 0: raise RuntimeError('Rejected reconstruction tracks')
    associations = []; residuals = []; measurements = []
    sensor_ids = {s['volume_id']: s['geometry_id'] for s in sensors}
    for event, equations, rows in zip(audit['events'], native['events'], blocks):
        if (not event['accepted'] or event['outliers'] or event['measurements'] != 5 or event['stored_ndf'] != 10
                or len(event['measurement_details']) != 5 or len(event['smoothed_states']) != 5):
            raise RuntimeError('Unexpected native measurement selection')
        if not event['material_free'] or event['field_T'] != [0., 0., 0.]:
            raise RuntimeError('Unsupported native reconstruction physics configuration')
        ids = []
        for hit in event['measurement_details']:
            if sensor_ids.get(hit['volume_id']) != hit['geometry_id']:
                raise RuntimeError('Measurement VolumeID/GeometryIdentifier mismatch')
            ids.append([hit[k] for k in ('cell_id', 'volume_id', 'geometry_id')])
            measurements.append(checked_measurement(hit, geometry_aware_holes))
        associations.append([event['run_number'], event['event_number'], ids,
                             [s['geometry_id'] for s in event['smoothed_states']]])
        residuals.extend(s['residual_mm'] for s in event['smoothed_states'])
        if len(rows) != 10 or {k for row in rows for k in row[3]} != set(selected):
            raise RuntimeError('Official Mille record labels/count mismatch')
        for equation in equations['equations']:
            w = np.array(equation['whitening']); cov = np.array(equation['covariance'])
            np.testing.assert_allclose(w @ cov @ w.T, np.eye(2), atol=1e-10)
            global_ = w @ np.array(equation['global']); local = w @ np.array(equation['local'])
            residual = w @ (np.array(equation['measurement']).ravel() - np.array(equation['prediction']).ravel())
            for j in (0, 1):
                row = rows[2 * equation['layer'] + j]
                expected = {k: global_[j, c] for k in selected for layer, c in [mapping(k)]
                            if layer == equation['layer'] and global_[j, c] != 0}
                if row[3].keys() != expected.keys(): raise RuntimeError('Mille coefficient labels mismatch')
                if any(abs(row[3][k] - value) >= 1e-10 for k, value in expected.items()):
                    raise RuntimeError('Mille global coefficient mismatch')
                if row[1] != 1 or abs(row[0] - residual[j]) >= 1e-10:
                    raise RuntimeError('Mille residual/whitening mismatch')
                np.testing.assert_allclose([row[2].get(k, 0.) for k in range(1, 5)], local[j], atol=1e-10, rtol=0)
    association_sha = hashlib.sha256(json.dumps(associations, separators=(',', ':')).encode()).hexdigest()
    if baseline is not None:
        if association_sha != baseline['association_sha256']: raise RuntimeError('Track/measurement association changed')
        if len(measurements) != len(baseline['measurements']): raise RuntimeError('Measurement count changed')
        for current, original in zip(measurements, baseline['measurements']):
            compare_measurements(current, original, geometry_aware_holes)
    log = (solve / 'stdout.log').read_text()
    status = (solve / 'millepede.end').read_text().strip()
    from calibration.native_policy import solver_diagnostics
    solver = solver_diagnostics(log, status, diagnostic=diagnostic)
    if not diagnostic:
        solver.pop('diagnostic_continuation')
    match = re.search(r'Sum\(Chi\^2\)/Sum\(Ndf\)\s*=\s*([0-9.Ee+\-]+)\s*/\s*\(\s*(\d+)\s*-\s*(\d+)\s*\)', log)
    if not match or int(match[2]) != 6 * len(blocks) or int(match[3]) != len(selected):
        raise RuntimeError('Missing/inconsistent Pede chi2/NDF')
    pieces = [profile(block, selected, require_global_rank=False) for block in blocks]
    normal = sum((v[0] for v in pieces), np.zeros((len(selected), len(selected))))
    rhs = sum((v[1] for v in pieces), np.zeros(len(selected)))
    chi2 = sum(v[2] for v in pieces) - rhs @ np.linalg.solve(normal, rhs)
    if abs(chi2 - float(match[1])) >= 1e-5: raise RuntimeError('Pede/profiled chi2 mismatch')
    residuals = np.array(residuals); ndf = sum(e['stored_ndf'] for e in audit['events'])
    chi = sum(e['chi2'] for e in audit['events'])
    navigation = [event.get('navigation_audit') for event in audit['events']]
    navigation_summary = {}
    if geometry_aware_holes and any(item is None for item in navigation):
        raise RuntimeError('Missing required geometry-aware navigation audit')
    if any(item is not None for item in navigation):
        for event, item in zip(audit['events'], navigation):
            if (item is None or item.get('passed') is not True
                    or item.get('navigator_end_of_world') is not True
                    or len(item['actual_hole_geometry_ids']) != event['holes']
                    or sorted(item['actual_hole_geometry_ids']) != sorted(item['expected_hole_geometry_ids'])
                    or sorted(item['navigator_sensitive_geometry_ids']) != sorted(item['bounded_forward_geometry_ids'])):
                raise RuntimeError('Incomplete or inconsistent geometry-aware navigation audit')
        navigation_summary = {'navigation': {'passed': True, 'tracks': len(navigation),
            'hole_count_distribution': {str(k): sum(e['holes'] == k for e in audit['events'])
                                        for k in sorted({e['holes'] for e in audit['events']})},
            'method': 'Actual filtered tangent, official ACTS Navigator and finite sensitive bounds'}}
    return {'tracks': audit['accepted'], 'rejected': audit['rejected'], 'processed': audit['processed'],
            'chi2_sum': chi, 'stored_ndf_sum': ndf, 'chi2_ratio': chi / ndf,
            'residual_rms_uv_mm': np.sqrt(np.mean(residuals ** 2, axis=0)).tolist(),
            'residual_norm': float(np.linalg.norm(np.sqrt(np.mean(residuals ** 2, axis=0)))),
            'association_sha256': association_sha, 'measurements': measurements,
            'pede': {**solver,
                     'chi2': float(match[1]), 'ndf': int(match[2]) - int(match[3]),
                     'chi2_per_ndf': float(match[1]) / (int(match[2]) - int(match[3])),
                     'independent_profiled_chi2': float(chi2), 'error_rescaling_applied': False},
            'official_mille_records': len(blocks), 'official_mille_coefficient_audit_passed': True,
            **navigation_summary}


def input_provenance(path, raw_sha256, payload_sha256):
    """Validate producer identity; no Truth values enter the calibration model."""
    value = json.loads(path.read_text())
    if value.get('schema') != 'native-geant4-input-v1' or value.get('input_origin') != 'geant4_digitized':
        raise ValueError('Unsupported native input provenance')
    if value.get('raw_root_sha256') != raw_sha256 or value.get('channel_payload_sha256') != payload_sha256:
        raise ValueError('Geant4 provenance does not identify the immutable RawHits')
    return value


def initialize(args):
    from calibration.native_operations import channel_values, channel_digest
    from validation.telescope_closure.multi_sensor import references
    if type(args.events) is not int or args.events < 5:
        raise ValueError('Requested event count must be an integer >=5')
    raw = args.raw.resolve(); geometry = args.geometry.resolve(); config = args.config.resolve()
    raw_sha256 = digest(raw)
    mode, configuration = selected_mode(config)
    from podio import reading
    headers = []
    for frame in reading.get_reader(str(raw)).get('events'):
        if set(frame.getAvailableCollections()) != {'EventHeader', 'TelescopeChannels'}:
            raise ValueError('Native calibration requires only EventHeader,TelescopeChannels; keep Truth in separate files')
        event_headers = frame.get('EventHeader')
        if len(event_headers) != 1:
            raise ValueError('Native input requires exactly one EventHeader per event')
        header = event_headers[0]
        headers.append({'run_number': int(header.getRunNumber()), 'event_number': int(header.getEventNumber())})
    payload = channel_values(raw)
    if not payload or len(payload) < args.events: raise ValueError('Requested events exceed immutable input event count')
    if len(headers) != len(payload) or [h['event_number'] for h in headers] != [event for event, _ in payload]:
        raise ValueError('EventHeader order/count does not match the immutable channel payload')
    if any(len(hits) != 5 for _, hits in payload): raise ValueError('Native input requires five SiMAPS channels per event')
    if digest(raw) != raw_sha256:
        raise RuntimeError('Immutable RawHit input changed while reading')
    payload_sha256 = channel_digest(payload)
    selection = {'method': 'first_n_events_in_file_order',
        'full_input_events': len(payload), 'processed_events': args.events,
        'first_entry_inclusive': 0, 'last_entry_exclusive': args.events,
        'event_identities': headers[:args.events],
        'selected_channel_payload_sha256': channel_digest(payload[:args.events])}
    provenance = getattr(args, 'input_provenance', None)
    if provenance is not None:
        provenance = provenance.resolve()
        producer = input_provenance(provenance, raw_sha256, payload_sha256)
        if ('accepted_events' in producer and
                (type(producer['accepted_events']) is not int or producer['accepted_events'] != len(payload))):
            raise ValueError('Producer accepted-event count does not match the full immutable input')
    run = args.run.resolve()
    if run.exists(): raise FileExistsError('Use an unused run directory; resume explicitly instead')
    run.mkdir(parents=True); inputs = run / 'input'; inputs.mkdir()
    original = {key: identity(path) for key, path in [('raw', raw), ('geometry', geometry), ('config', config)]}
    if original['raw']['sha256'] != raw_sha256:
        raise RuntimeError('Immutable RawHit input changed before snapshotting')
    snapshots = {}
    for key, source in [('raw', raw), ('geometry', geometry), ('config', config)]:
        target = inputs / {'raw': 'channels.root', 'geometry': 'initial.xml', 'config': 'alignment.json'}[key]
        shutil.copyfile(source, target); snapshots[key] = identity(target)
        if snapshots[key]['sha256'] != original[key]['sha256']: raise RuntimeError('Input changed while snapshotting')
    if provenance is not None:
        original['provenance'] = identity(provenance)
        target = inputs / 'provenance.json'
        shutil.copyfile(provenance, target); snapshots['provenance'] = identity(target)
        if snapshots['provenance']['sha256'] != original['provenance']['sha256']:
            raise RuntimeError('Input provenance changed while snapshotting')
    manifest = {'schema': 'native-calibration-v1', 'mode': mode, 'events': args.events,
        'chi2_cut': args.chi2_cut, 'minimum_corrections': args.minimum_corrections,
        'maximum_corrections': args.maximum_corrections,
        'policy': {'policy_label': 'demonstration same-data consistency; no accuracy/coverage certification',
                   'max_abs_y_mm': args.max_abs_y_mm, 'max_angular_sigma_ratio': args.max_angular_sigma_ratio,
                   'max_joint_sigma': args.max_joint_sigma, 'max_scaled_condition': args.max_scaled_condition},
        'input': {**{key + '_source': value for key, value in original.items()}, **snapshots},
        'channel_payload_sha256': payload_sha256, 'input_selection': selection,
        'parameters': configuration['parameters'],
        'fixed_reference_sensors': references(mode),
        'build_dir': str(args.build.resolve()), 'build': build_identity(args.build.resolve()),
        'sources': source_identity(), 'geometry_activated': False,
        'iteration_convention': 'At most maximum_corrections applied updates; one extra fresh fit certifies the last candidate. Initial covariance is retained, never summed across fits.'}
    if provenance is not None:
        manifest['input_origin'] = 'geant4'
    if getattr(args, 'validation_mode', 'strict') != 'strict':
        manifest['validation_mode'] = args.validation_mode
    if getattr(args, 'geometry_aware_holes', False):
        manifest['geometry_aware_holes'] = True
    atomic_json(run / 'manifest.json', manifest); seal(inputs, passed=True)
    atomic_json(run / 'manifest.seal.json', identity(run / 'manifest.json'))
    return run


def publish_report(run, attempt, status, reason, chain, error=None):
    last = chain[-1] if chain else None
    initial_record = run / 'initial-verified.json'
    initial = None
    if initial_record.exists():
        verified = json.loads(initial_record.read_text())
        check_identity(verified['geometry']); check_identity(verified['audit_marker'])
        verify(Path(verified['audit_marker']['path']).parent)
        initial = verified['geometry']
    report = {'status': status, 'reason': reason, 'geometry_activated': False,
              'completed_iterations': len(chain), 'applied_corrections': last['applied_corrections'] if last else 0,
              'last_valid_geometry': last['next_geometry'] if last else initial,
              'last_checkpoint': last['marker'] if last else None, 'error': error}
    manifest = json.loads((run / 'manifest.json').read_text())
    if 'input_selection' in manifest:
        report['input_selection'] = manifest['input_selection']
    diagnostic = manifest.get('validation_mode', 'strict') == 'diagnostic'
    engineering_success = (error is None and status in ('converged', 'iteration_limit')
                           and len(chain) >= 2 and report['applied_corrections'] >= 1
                           and last['geometry'] == last['next_geometry'])
    if diagnostic:
        report.update(validation_mode='diagnostic', engineering_success=engineering_success,
            physics_validation='not_validated', approval_status='unapproved',
            integrity_status='passed' if engineering_success else ('failed' if status == 'failed' else 'incomplete'),
            interpretation='Offline technical demonstration; same-data convergence does not establish bias, coverage or a material-aware uncertainty model')
        report['quality_warnings'] = []
        for item in chain:
            metrics = json.loads(Path(item['metrics']).read_text())
            information = json.loads(Path(item['current_information']['path']).read_text())
            report['quality_warnings'].extend(information.get('quality_warnings', []))
            if metrics.get('pede', {}).get('diagnostic_continuation'):
                report['quality_warnings'].append({'iteration': item['sequence'], 'pede': metrics['pede']})
        if status != 'converged':
            report['quality_warnings'].append('Demonstration convergence criteria not met')
    if last:
        from geometry.transforms import rotation_log
        import numpy as np
        original = chain[0]['sensor_poses']; actual = last['next_sensor_poses']
        report['cumulative_poses'] = [{
            'layer': a['layer'], 'volume_id': a['volume_id'], 'geometry_id': a['geometry_id'],
            'center_mm': b['center_mm'], 'acts_rotation': b['acts_rotation'],
            'world_translation_mm': (np.array(b['center_mm']) - a['center_mm']).tolist(),
            'left_global_rotation_log_rad': list(rotation_log(np.array(b['acts_rotation']) @ np.array(a['acts_rotation']).T))}
            for a, b in zip(original, actual)]
    destination = attempt / ('failure.json' if (attempt / 'report.json').exists() else 'report.json')
    atomic_json(destination, report)
    if (not diagnostic and status == 'converged') or (diagnostic and engineering_success):
        final = run / 'final'
        if final.exists():
            verify(final)
            if digest(final / 'candidate.xml') != report['last_valid_geometry']['sha256']:
                raise RuntimeError('Published final Geometry differs from converged checkpoint')
        else:
            pending = run / ('final-pending-' + attempt.name); pending.mkdir()
            shutil.copyfile(report['last_valid_geometry']['path'], pending / 'candidate.xml')
            atomic_json(pending / 'report.json', report); seal(pending, passed=True, geometry_activated=False)
            os.rename(pending, final)
    return report


def calibrate(run):
    from calibration.convergence import evaluate_native
    with exclusive(run) as lock_fd:
        check_identity(json.loads((run / 'manifest.seal.json').read_text()))
        manifest = json.loads((run / 'manifest.json').read_text())
        verify(run / 'input'); validate_run_inputs(manifest)
        chain = checkpoints(run)
        jobs = run / 'jobs'; jobs.mkdir(exist_ok=True)
        number = 0
        while (jobs / f'attempt_{number:03d}').exists(): number += 1
        attempt = jobs / f'attempt_{number:03d}'; attempt.mkdir()
        atomic_json(attempt / 'start.json', {'resumed_checkpoint': chain[-1]['marker'] if chain else None})
        if chain and chain[-1]['termination'] != 'continue':
            return publish_report(run, attempt, chain[-1]['termination'], 'verified_terminal_checkpoint', chain)
        current = Path(chain[-1]['next_geometry']['path'] if chain else manifest['input']['geometry']['path'])
        applied = chain[-1]['applied_corrections'] if chain else 0
        initial_metrics = Path(chain[0]['initial_metrics']) if chain else None
        initial_information = Path(chain[0]['initial_information']) if chain else None
        selected = [p['label'] for p in manifest['parameters']]
        try:
            while True:
                validate_run_inputs(manifest)
                directory = attempt / f'iteration_{len(chain):03d}'; directory.mkdir()
                atomic_json(directory / 'start.json', {'geometry': identity(current), 'applied_corrections': applied})
                geometry = run_stage(directory, 'geometry', manifest, lock_fd, geometry=str(current))
                sensors = json.loads((geometry / 'geometry.json').read_text())['sensors']
                for parameter in manifest['parameters']:
                    sensor = next(s for s in sensors if s['layer'] == parameter['layer'])
                    if any(sensor[k] != parameter[k] for k in ('volume_id', 'geometry_id')):
                        raise RuntimeError('Configured sensor identity mismatch')
                if not chain and not (run / 'initial-verified.json').exists():
                    atomic_json(run / 'initial-verified.json', {
                        'geometry': identity(current), 'audit_marker': identity(geometry.parent / 'complete.json'),
                        'sensor_poses': sensors, 'geometry_activated': False})
                reconstruction = run_stage(directory, 'reconstruct', manifest, lock_fd,
                    geometry=str(current), raw=manifest['input']['raw']['path'], config=manifest['input']['config']['path'],
                    events=manifest['events'], chi2_cut=manifest['chi2_cut'],
                    **({'input_origin': manifest['input_origin']} if 'input_origin' in manifest else {}),
                    **({'geometry_aware_holes': True} if manifest.get('geometry_aware_holes') else {}))
                run_stage(directory, 'derivatives', manifest, lock_fd, reconstruction=str(reconstruction),
                          geometry=str(current), sensors=sensors)
                diagnostic = manifest.get('validation_mode', 'strict') == 'diagnostic'
                solution = run_stage(directory, 'solve', manifest, lock_fd, reconstruction=str(reconstruction), events=manifest['events'],
                    **({'validation_mode': 'diagnostic'} if diagnostic else {}))
                independent = json.loads((solution / 'independent.json').read_text())
                import numpy as np
                atomic_json(directory / 'parameter_information.json', {
                    'selected_labels': selected, 'parameter_units': independent['parameter_units'],
                    'pede_parameters': independent['parameters'], 'pede_reported_sigma': independent['pede_reported_sigma'],
                    'data_only_formal_sigma': independent['nominal_sigma'],
                    'data_only_covariance_mixed_units': np.linalg.inv(np.array(independent['normal'])).tolist(),
                    'correlation': independent['correlation'], 'rank': independent['rank'],
                    'condition_diagonal_scaled': independent['condition_diagonal_scaled'],
                    'successive_covariances_combined': False,
                    'initial_information': str(initial_information or solution / 'independent.json')})
                baseline = json.loads(initial_metrics.read_text()) if initial_metrics else None
                metrics = summarize(reconstruction, solution, sensors, selected, baseline,
                                    **({'diagnostic': True} if diagnostic else {}),
                                    **({'geometry_aware_holes': True} if manifest.get('geometry_aware_holes') else {}))
                atomic_json(directory / 'metrics.json', metrics)
                if initial_metrics is None:
                    initial_metrics = directory / 'metrics.json'; baseline = metrics
                    initial_information = solution / 'independent.json'
                original_information = json.loads(initial_information.read_text())
                delta = [independent['parameters'][str(k)] for k in selected]
                decision = evaluate_native(selected, delta, original_information['normal'], original_information['nominal_sigma'],
                    iteration=applied, minimum_corrections=manifest['minimum_corrections'], maximum_corrections=manifest['maximum_corrections'],
                    chi2_ratio=metrics['chi2_ratio'], residual_norm=metrics['residual_norm'],
                    initial_chi2_ratio=baseline['chi2_ratio'], initial_residual_norm=baseline['residual_norm'],
                    tracks=metrics['tracks'], rejected=metrics['rejected'], initial_tracks=baseline['tracks'], initial_rejected=baseline['rejected'],
                    rank=independent['rank'], condition=independent['condition_diagonal_scaled'],
                    association_identity=metrics['association_sha256'] == baseline['association_sha256'], solver_ok=True, geometry_ok=True,
                    policy=manifest['policy'])
                atomic_json(directory / 'convergence.json', decision)
                next_geometry = current
                next_sensors = sensors
                if decision['status'] == 'continue':
                    correction = run_stage(directory, 'correct', manifest, lock_fd, geometry=str(current), sensors=sensors,
                                           parameters=independent['parameters'])
                    next_geometry = correction / 'candidate.xml'; applied += 1
                    next_sensors = json.loads((correction / 'geometry_audit/geometry.json').read_text())['sensors']
                validate_run_inputs(manifest)
                checkpoint = {'sequence': len(chain), 'previous_marker': chain[-1]['marker'] if chain else None,
                    'geometry': identity(current), 'next_geometry': identity(next_geometry),
                    'applied_corrections': applied, 'termination': decision['status'],
                    'initial_metrics': str(initial_metrics), 'initial_information': str(initial_information),
                    'sensor_poses': sensors, 'next_sensor_poses': next_sensors, 'metrics': str(directory / 'metrics.json'),
                    'remaining_parameters': independent['parameters'], 'current_information': identity(solution / 'independent.json'),
                    'parent_version': 'geo-' + digest(current), 'child_version': 'geo-' + digest(next_geometry)}
                atomic_json(directory / 'checkpoint.json', checkpoint)
                seal(directory, kind='native-iteration-v1', checkpoint=checkpoint)
                chain = checkpoints(run); current = next_geometry
                if decision['status'] != 'continue':
                    return publish_report(run, attempt, decision['status'], decision['status'], chain)
        except BaseException as error:
            from calibration.native_policy import QualityGateError
            status = ('interrupted' if isinstance(error, (KeyboardInterrupt, InterruptedError)) else
                      ('quality_blocked' if isinstance(error, QualityGateError) else 'failed'))
            report = publish_report(run, attempt, status, type(error).__name__, chain, str(error))
            print(json.dumps(report, indent=2), file=sys.stderr)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    start = commands.add_parser('start', help='Start from one immutable TelescopeChannels ROOT file; no Truth input')
    start.add_argument('--run', type=Path, required=True)
    start.add_argument('--raw', type=Path, required=True)
    start.add_argument('--input-provenance', type=Path,
                       help='Producer receipt for Geant4-digitized RawHits; copied and verified, never used as Truth')
    start.add_argument('--geometry', type=Path, required=True)
    start.add_argument('--config', type=Path, required=True)
    start.add_argument('--build', type=Path, required=True)
    start.add_argument('--events', type=int, required=True)
    start.add_argument('--chi2-cut', type=float, required=True)
    start.add_argument('--validation-mode', choices=('strict', 'diagnostic'), default='strict',
                       help='Strict gates by default; offline diagnostic mode retains recognized high-chi2 warnings and never approves Geometry')
    start.add_argument('--geometry-aware-holes', action='store_true',
                       help='Validate actual bounded sensitive-surface crossings using ACTS Navigator')
    start.add_argument('--minimum-corrections', type=int, default=1, help='Predeclare an evaluation protocol; default1, maximum3')
    start.add_argument('--maximum-corrections', type=int, default=3, help='At most3 applied updates plus a final verification fit')
    start.add_argument('--max-abs-y-mm', type=float, default=2e-5, help='Strict remaining Y limit; may tighten the validated2e-5mm bound')
    start.add_argument('--max-angular-sigma-ratio', type=float, default=.25, help='Strict angular update/initial formal sigma limit; maximum0.25')
    start.add_argument('--max-joint-sigma', type=float, default=.25, help='Strict sqrt(delta^T H0 delta) demonstration limit; maximum0.25')
    start.add_argument('--max-scaled-condition', type=float, default=100., help='Current selected information condition bound; maximum100')
    for action in ('resume', 'status', 'verify', 'recover-lock'):
        command = commands.add_parser(action); command.add_argument('--run', type=Path, required=True)
        if action == 'recover-lock': command.add_argument('--reason', required=True)
    internal = commands.add_parser('stage', help=argparse.SUPPRESS)
    internal.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'stage':
        try: worker(args.request.resolve())
        except BaseException as error:
            request = json.loads(args.request.read_text())
            atomic_json(Path(request['output']).parent / 'failure.json', {
                'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()})
            raise
        return
    run = args.run.resolve()
    if not run.is_relative_to(REPO / 'result') or run == REPO / 'result' or any(c.isspace() for c in str(run)):
        parser.error('Use a whitespace-free child of result/')
    if args.action == 'recover-lock': recover_guard(run, args.reason); return
    if args.action in ('status', 'verify'):
        check_identity(json.loads((run / 'manifest.seal.json').read_text()))
        manifest = json.loads((run / 'manifest.json').read_text()); verify(run / 'input')
        if args.action == 'verify': validate_run_inputs(manifest)
        chain = checkpoints(run)
        print(json.dumps({'checkpoints': chain, 'geometry_activated': False}, indent=2)); return
    if args.action == 'start':
        if not 1 <= args.minimum_corrections <= args.maximum_corrections <= 3:
            parser.error('Require1 <= minimum-corrections <= maximum-corrections <=3')
        if args.events < 5 or not math.isfinite(args.chi2_cut) or args.chi2_cut <= 0:
            parser.error('Positive event count >=5 and finite positive selection cut required')
        for key, maximum in [('max_abs_y_mm', 2e-5), ('max_angular_sigma_ratio', .25), ('max_joint_sigma', .25), ('max_scaled_condition', 100.)]:
            value = getattr(args, key)
            if not math.isfinite(value) or not (1. if key == 'max_scaled_condition' else 0.) < value <= maximum:
                parser.error('Threshold must be positive and cannot relax the validated bound: ' + key)
        run = initialize(args)
    def interrupted(signum, _frame): raise InterruptedError('Controller interrupted by signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    report = calibrate(run); print(json.dumps(report, indent=2))
    if report['status'] != 'converged' and not report.get('engineering_success', False): raise SystemExit(2)


if __name__ == '__main__': main()
