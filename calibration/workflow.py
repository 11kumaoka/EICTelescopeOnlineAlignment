"""Public orchestration of the existing native telescope calibration stages.

Run inside the fixed Docker environment. Each stage remains independently
executable; this module adds configuration snapshots and an artifact index,
not a simulator, fitter, solver, or Geometry correction implementation.
"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import shutil
import sys
import traceback

from research_common import REPO, digest, done, execute, require, write_json


IMAGE = 'sha256:67c914295a92396040c3051360d70786df9c1ee8458264ad0b83cd1a8ccf3d05'
STAGES = ('prepare', 'simulate', 'digitize', 'calibrate', 'report')


def require_fixed_environment():
    """Accept the pinned image forms used by both Docker launcher branches."""
    if os.environ.get('TESTBENCH_IMAGE') not in (IMAGE, 'eicweb/eic_xl@' + IMAGE):
        raise RuntimeError('Run this command through bash environment/run.sh in the fixed Docker image')


def input_path(value):
    path = Path(value)
    return (path if path.is_absolute() else REPO / path).resolve()


def validate_configuration(value):
    """Reject unsupported reconstruction contracts before producing any data."""
    from simulation.native import gun_configuration
    from calibration.native_controller import selected_mode
    value = copy.deepcopy(value)
    allowed = {'schema', 'description', 'geometry', 'simulation', 'alignment', 'controller', 'environment'}
    if not isinstance(value, dict) or set(value) - allowed or value.get('schema') != 'native-workflow-v1':
        raise ValueError('Expected a native-workflow-v1 configuration with documented keys')
    sections = {
        'geometry': {'source', 'initial', 'truth', 'global_y_mm', 'global_rotation_vector_rad'},
        'simulation': {'particle', 'energy_gev', 'beam', 'events', 'seed', 'position_mm', 'direction',
                       'vertex_sigma_mm', 'theta_range_rad', 'phi_range_rad'},
        'alignment': {'config', 'chi2_cut'},
        'controller': {'validation_mode', 'maximum_corrections'},
        'environment': {'kind', 'image'},
    }
    for name, keys in sections.items():
        section = value.setdefault(name, {})
        if not isinstance(section, dict) or set(section) - keys:
            raise ValueError('Unknown or invalid configuration section: ' + name)
    geometry = value['geometry']; geometry.setdefault('source', 'native_barrel'); geometry.setdefault('initial', 'nominal')
    if geometry.get('truth') and (geometry.get('global_y_mm') or geometry.get('global_rotation_vector_rad')):
        raise ValueError('Choose an explicit Truth XML or injected displacements, not both')
    for key in ('source', 'initial', 'truth'):
        sentinel = {'source': 'native_barrel', 'initial': 'nominal'}.get(key)
        if key in geometry and geometry[key] != sentinel:
            path = input_path(geometry[key])
            if not path.is_file(): raise FileNotFoundError(path)
            geometry[key] = str(path)
    for key, size in (('global_y_mm', 1), ('global_rotation_vector_rad', 3)):
        entries = geometry.setdefault(key, {})
        if not isinstance(entries, dict): raise ValueError('Displacements must be indexed by sensor layer')
        for layer, entry in entries.items():
            if str(layer) not in ('1', '2', '3'): raise ValueError('Only existing SiMAPS L1/L2/L3 displacement adapters are supported')
            numbers = [entry] if size == 1 else entry
            if (not isinstance(numbers, (list, tuple)) or len(numbers) != size
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in numbers)):
                raise ValueError('Displacements require finite mm or three-component radian values')
    simulation = value['simulation']
    for key, default in (('particle', 'e-'), ('energy_gev', 1.), ('beam', 'pencil'),
                         ('position_mm', [249., 0., 0.]), ('direction', [1., 0., 0.])):
        simulation.setdefault(key, default)
    if simulation['particle'] != 'e-' or isinstance(simulation['energy_gev'], bool) or simulation['energy_gev'] != 1.:
        raise ValueError('The complete native workflow currently requires e- at 1 GeV gun energy because the electron seed prior is fixed. Use simulation.native independently for other particles or energies.')
    for key in ('events', 'seed'):
        if type(simulation.get(key)) is not int: raise ValueError('An explicit integer Simulation ' + key + ' is required')
    if simulation['events'] < 5 or not 0 < simulation['seed'] < 2**31:
        raise ValueError('Require at least five events and a positive 31-bit seed')
    gun_configuration(**{key: item for key, item in simulation.items() if key not in ('events', 'seed')})
    alignment = value['alignment']; alignment.setdefault('config', 'validation/telescope_alignment/l1_global_y.json')
    alignment['config'] = str(input_path(alignment['config']))
    mode, _ = selected_mode(Path(alignment['config']))
    alignment.setdefault('chi2_cut', 1000.)
    if (isinstance(alignment['chi2_cut'], bool) or not isinstance(alignment['chi2_cut'], (int, float))
            or not math.isfinite(alignment['chi2_cut']) or alignment['chi2_cut'] <= 0):
        raise ValueError('A finite positive measurement selection cut is required')
    controller = value['controller']; controller.setdefault('validation_mode', 'strict'); controller.setdefault('maximum_corrections', 1)
    if controller['validation_mode'] not in ('strict', 'diagnostic') or type(controller['maximum_corrections']) is not int or not 1 <= controller['maximum_corrections'] <= 3:
        raise ValueError('Require strict/diagnostic mode and one to three maximum applied corrections')
    environment = value['environment']; environment.setdefault('kind', 'fixed-docker'); environment.setdefault('image', IMAGE)
    if environment != {'kind': 'fixed-docker', 'image': IMAGE}:
        raise ValueError('This workflow supports only the existing fixed Docker environment')
    return value, mode


def result_path(path):
    path = Path(path).resolve()
    if path == REPO / 'result' or not path.is_relative_to(REPO / 'result') or any(c.isspace() for c in str(path)):
        raise ValueError('Use a whitespace-free child directory of result/')
    return path


def fresh_output(path):
    path = result_path(path)
    if path.exists(): raise FileExistsError(path)
    return path


def checked_reuse(specification, producer):
    """Require the declared gun and the actual completed npsim producer."""
    from digitization.native import checked_simulation_binding
    from simulation.native import gun_configuration
    producer = Path(producer).resolve()
    binding = checked_simulation_binding(producer / 'transport/events.edm4hep.root', producer / 'simulation.xml',
        producer / 'simulation_geometry_audit/geometry.json', producer)
    recorded = json.loads((producer / 'configuration.json').read_text())
    actual = recorded.get('generator') or gun_configuration(recorded['beam'], particle=recorded['particle'],
        energy_gev=recorded['energy_GeV'], position_mm=recorded['position_mm'], direction=recorded['mean_direction'])
    expected = gun_configuration(**{key: item for key, item in specification.items() if key not in ('events', 'seed')})
    if actual != expected or recorded['events'] != specification['events'] or recorded['seed'] != specification['seed']:
        raise ValueError('Reused Simulation must match the declared gun, events and seed')
    return binding


def initialize(args):
    """Freeze public inputs and prerequisites before starting expensive stages."""
    from calibration.native_controller import build_identity, source_identity
    from simulation.native import software_snapshot
    source = args.config.resolve(); value = json.loads(source.read_text())
    overrides = {'geometry': ('source', 'initial'), 'simulation': ('particle', 'energy_gev', 'beam', 'events', 'seed', 'position_mm', 'direction', 'vertex_sigma_mm', 'theta_range_rad', 'phi_range_rad')}
    for section, keys in overrides.items():
        for key in keys:
            attribute = {'source': 'geometry', 'initial': 'initial_geometry'}.get(key, key)
            item = getattr(args, attribute, None)
            if item is not None: value.setdefault(section, {})[key] = item
    for attribute, key in (('validation_mode', 'validation_mode'), ('max_iterations', 'maximum_corrections')):
        item = getattr(args, attribute, None)
        if item is not None: value.setdefault('controller', {})[key] = item
    if getattr(args, 'alignment_config', None) is not None:
        value.setdefault('alignment', {})['config'] = args.alignment_config
    if getattr(args, 'environment', None) is not None:
        value.setdefault('environment', {})['kind'] = args.environment
    value, mode = validate_configuration(value)
    output = fresh_output(args.output_dir); build = args.build.resolve()
    require_fixed_environment()
    software = software_snapshot(build); binaries = build_identity(build)
    digitizer = build / 'TelescopeDigitization.so'
    if not digitizer.is_file(): raise FileNotFoundError(digitizer)
    reuse = args.simulation_stage.resolve() if getattr(args, 'simulation_stage', None) else None
    if reuse is not None: checked_reuse(value['simulation'], reuse)
    originals = {str(source): digest(source), value['alignment']['config']: digest(value['alignment']['config'])}
    for key in ('source', 'initial', 'truth'):
        item = value['geometry'].get(key)
        if item and item not in ('native_barrel', 'nominal'): originals[item] = digest(item)
    output.mkdir(parents=True); snapshot = output / 'configuration'; snapshot.mkdir()
    shutil.copyfile(source, snapshot / 'requested.json')
    shutil.copyfile(value['alignment']['config'], snapshot / 'alignment.json')
    write_json(snapshot / 'configuration.json', value)
    write_json(snapshot / 'software.json', software)
    write_json(snapshot / 'invocation.json', {'argv': getattr(sys, 'orig_argv', None), 'sys_argv': sys.argv,
        'cwd': str(Path.cwd()), 'fixed_image': os.environ.get('TESTBENCH_IMAGE'),
        'note': 'argv is the actual original Python process invocation when available; sys_argv alone does not retain -m'})
    plan = {'schema': 'native-public-run-plan-v1', 'mode': mode, 'run_dir': str(output), 'build': str(build),
        'source_inputs': originals, 'sources': source_identity(), 'build_identity': binaries,
        'digitizer': {'path': str(digitizer), 'sha256': digest(digitizer)},
        'reused_simulation_stage': str(reuse) if reuse else None,
        'paths': {'truth': 'preparation/truth.xml', 'initial': 'preparation/initial.xml',
                  'simulation': 'simulation', 'digitization': 'raw/digitization/digitized.root',
                  'immutable_channels': 'raw/projection/channels.root', 'controller': 'controller',
                  'candidate': 'controller/final/candidate.xml', 'report': 'report/summary.json',
                  'root_diagnostics': 'report/diagnostics.root', 'final_manifest': 'run_manifest.json'},
        'geometry_activation': 'never', 'physics_validation': 'not_validated'}
    write_json(snapshot / 'run_plan.json', plan); done(snapshot, passed=True)
    return output


def load_run(run):
    from calibration.native_controller import source_identity
    run = Path(run).resolve(); require(run, 'configuration')
    value = json.loads((run / 'configuration/configuration.json').read_text())
    plan = json.loads((run / 'configuration/run_plan.json').read_text())
    for path, sha in plan['source_inputs'].items():
        if digest(path) != sha: raise RuntimeError('Immutable workflow input changed: ' + path)
    if source_identity() != plan['sources']:
        raise RuntimeError('Implementation source set or contents changed after workflow creation')
    for path, sha in plan['build_identity']['files'].items():
        if digest(path) != sha: raise RuntimeError('Build/runtime dependency changed: ' + path)
    if digest(plan['digitizer']['path']) != plan['digitizer']['sha256']:
        raise RuntimeError('Official digitization plugin changed')
    return value, plan, Path(plan['build'])


def prepare_stage(run):
    from calibration.native_operations import displaced_xml, geometry_audit
    from validation.telescope_geometry.prepare import prepare
    value, _, build = load_run(run); output = run / 'preparation'; output.mkdir()
    specification = value['geometry']
    if specification['source'] == 'native_barrel':
        prepare(output / 'fixtures', initialize_metadata=True); base = output / 'fixtures/nominal.xml'
    else:
        base = output / 'base.xml'; shutil.copyfile(specification['source'], base)
    shutil.copyfile(base if specification['initial'] == 'nominal' else specification['initial'], output / 'initial.xml')
    if specification.get('truth'):
        shutil.copyfile(specification['truth'], output / 'truth.xml')
    else:
        shifts = {int(k): v for k, v in specification['global_y_mm'].items()}
        rotations = {int(k): v for k, v in specification['global_rotation_vector_rad'].items()}
        displaced_xml(base, output / 'truth.xml', shifts, rotations=rotations, rotation_axis='xyz', allowed_layers=(1, 2, 3))
    config = json.loads((run / 'configuration/alignment.json').read_text())
    initial = geometry_audit(build, output / 'initial_audit', output / 'initial.xml', config)
    truth = geometry_audit(build, output / 'truth_audit', output / 'truth.xml', config)
    if [(s['volume_id'], s['geometry_id']) for s in initial['sensors']] != [(s['volume_id'], s['geometry_id']) for s in truth['sensors']]:
        raise RuntimeError('Truth and initial Geometry detector identities differ')
    done(output, passed=True, truth_sha256=digest(output / 'truth.xml'), initial_sha256=digest(output / 'initial.xml'),
         truth_used_for='Simulation only; not Controller inference', geometry_activated=False)


def simulation_stage(run):
    require(run, 'simulation')
    reference = run / 'simulation/reference.json'
    return Path(json.loads(reference.read_text())['producer']) if reference.exists() else run / 'simulation'


def simulate_stage(run):
    from digitization.native import checked_simulation_binding
    value, plan, build = load_run(run); require(run, 'preparation')
    specification = value['simulation']; reuse = plan['reused_simulation_stage']
    if reuse:
        producer = Path(reuse); root = producer / 'transport/events.edm4hep.root'
        checked_reuse(specification, producer)
        binding = checked_simulation_binding(root, run / 'preparation/truth.xml', run / 'preparation/truth_audit/geometry.json', producer)
        if not binding['physical_sensor_poses_identical']:
            raise ValueError('Reused Simulation must match all actual declared Truth sensor poses')
        output = run / 'simulation'; output.mkdir(); write_json(output / 'reference.json', {'producer': str(producer), 'binding': binding})
        done(output, passed=True, reused=True, new_simulation_executed=False)
        return
    command = [sys.executable, '-B', '-m', 'simulation.native', '--output-dir', run / 'simulation',
               '--geometry', run / 'preparation/truth.xml', '--build', build]
    for key, item in specification.items():
        command.append('--' + key.replace('_', '-')); command.extend(item if isinstance(item, list) else [item])
    execute(command, run / 'execution/simulate', cwd=REPO)


def digitize_stage(run):
    _, _, build = load_run(run); producer = simulation_stage(run)
    execute([sys.executable, '-B', '-m', 'digitization.native', '--operation', 'all', '--output-dir', run / 'raw',
             '--simulation', producer / 'transport/events.edm4hep.root', '--simulation-stage', producer,
             '--geometry', producer / 'simulation.xml', '--geometry-audit', producer / 'simulation_geometry_audit/geometry.json',
             '--build', build], run / 'execution/digitize', cwd=REPO)


def calibrate_stage(run):
    value, _, build = load_run(run); require(run, 'raw'); require(run, 'preparation')
    projected = json.loads((run / 'raw/provenance.json').read_text())
    execute([sys.executable, '-B', '-m', 'calibration.native_controller', 'start', '--run', run / 'controller',
             '--raw', run / 'raw/projection/channels.root', '--input-provenance', run / 'raw/provenance.json',
             '--geometry', run / 'preparation/initial.xml', '--config', run / 'configuration/alignment.json', '--build', build,
             '--events', projected['accepted_events'], '--chi2-cut', value['alignment']['chi2_cut'],
             '--validation-mode', value['controller']['validation_mode'], '--geometry-aware-holes',
             '--maximum-corrections', value['controller']['maximum_corrections']], run / 'execution/calibrate', cwd=REPO)


def root_report(run, output):
    """Plot actual fitted states and Pede updates; never use injected corrections."""
    import ROOT
    from calibration.native_controller import verify
    ROOT.gROOT.SetBatch(True)
    target = output / 'diagnostics.root'; file = ROOT.TFile(str(target), 'CREATE')
    if file.IsZombie(): raise RuntimeError('Cannot create fresh diagnostic ROOT output')
    audits = sorted((run / 'controller/jobs').glob('attempt_*/iteration_*/reconstruct/artifacts/audit.json'))
    objects = []; ratios = ROOT.TGraph(); ratios.SetName('acts_chi2_over_stored_ndf')
    ratios.SetTitle('ACTS reconstruction;Fit evaluation;ACTS chi2 / stored NDF')
    pede = ROOT.TGraph(); pede.SetName('pede_chi2_over_ndf')
    pede.SetTitle('Official Millepede-II;Fit evaluation;Pede chi2 / NDF')
    iterations = []; incomplete = []
    def read(path):
        try: return json.loads(path.read_text())
        except (OSError, ValueError) as error:
            incomplete.append({'path': str(path), 'error': str(error)})
            return None
    for index, path in enumerate(audits):
        audit = read(path); directory = path.parents[2]
        if audit is None: continue
        if not (path.parent.parent / 'complete.json').exists():
            incomplete.append({'path': str(path), 'error': 'Reconstruction stage was not completely sealed; retained as partial evidence'})
            continue
        try: verify(path.parent.parent)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            incomplete.append({'path': str(path), 'error': str(error)}); continue
        accepted = [e for e in audit['events'] if e.get('accepted')]
        chi = sum(e['chi2'] for e in accepted); ndf = sum(e['stored_ndf'] for e in accepted)
        histogram = ROOT.TH1D('track_chi2_evaluation_' + str(index), 'ACTS track quality;ACTS chi2 / stored NDF;Tracks', 120, 0, 120)
        for event in accepted: histogram.Fill(event['chi2'] / event['stored_ndf'])
        objects.append(histogram)
        if ndf: ratios.SetPoint(index, index, chi / ndf)
        geometry = directory / 'geometry/artifacts/geometry.json'
        geometry_record = read(geometry) if geometry.exists() else None
        ids = {s['geometry_id']: s['layer'] for s in geometry_record['sensors']} if geometry_record else {}
        for layer in range(5):
            for coordinate in range(2):
                hist = ROOT.TH1D(f'residual_L{layer}_{coordinate}_evaluation_{index}',
                    f'L{layer} fitted Hit Residuals;Local {"u" if coordinate == 0 else "v"} residual [mm];Measurements', 150, -.3, .3)
                for event in accepted:
                    for state in event['smoothed_states']:
                        if ids.get(state['geometry_id']) == layer: hist.Fill(state['residual_mm'][coordinate])
                objects.append(hist)
        information = directory / 'parameter_information.json'
        if information.exists():
            record = read(information)
            if record is None: continue
            for label in record['selected_labels']:
                name = 'remaining_parameter_' + str(label)
                graph = next((item for item in objects if item.GetName() == name), None)
                if graph is None:
                    graph = ROOT.TGraphErrors(); graph.SetName(name)
                    graph.SetTitle('Pede remaining correction ' + str(label) + ';Fit evaluation;Correction [' + record['parameter_units'][str(label)] + ']')
                    objects.append(graph)
                point = graph.GetN(); graph.SetPoint(point, index, record['pede_parameters'][str(label)])
                graph.SetPointError(point, 0., record['pede_reported_sigma'][record['selected_labels'].index(label)])
        metrics_path = directory / 'metrics.json'
        metrics = read(metrics_path) if metrics_path.exists() else None
        if metrics and metrics.get('pede'):
            pede.SetPoint(pede.GetN(), index, metrics['pede']['chi2_per_ndf'])
        iterations.append({'audit': str(path), 'accepted_tracks': len(accepted), 'chi2_sum': chi, 'stored_ndf': ndf,
                           'chi2_over_stored_ndf': chi / ndf if ndf else None, 'metrics': metrics})
    geometries = []
    for role, path in (('initial', run / 'preparation/initial_audit/geometry.json'),
                       ('truth_for_validation_only', run / 'preparation/truth_audit/geometry.json')):
        if path.exists():
            record = read(path)
            if record: geometries.append((role, record['sensors']))
    completed = sorted((run / 'controller/jobs').glob('attempt_*/iteration_*/checkpoint.json'))
    initial_poses = None
    for path in completed:
        if not (path.parent / 'complete.json').exists(): continue
        item = read(path)
        if item is None: continue
        if initial_poses is None: initial_poses = {s['layer']: s for s in item['sensor_poses']}
        for sensor in item['next_sensor_poses']:
            layer = sensor['layer']; name = 'cumulative_y_correction_L' + str(layer)
            graph = next((entry for entry in objects if entry.GetName() == name), None)
            if graph is None:
                graph = ROOT.TGraph(); graph.SetName(name)
                graph.SetTitle(f'L{layer} actual Geometry Correction;Completed fit evaluation;Global Y correction [mm]')
                graph.SetPoint(0, 0, 0.); objects.append(graph)
            graph.SetPoint(graph.GetN(), item['sequence'] + 1, sensor['center_mm'][1] - initial_poses[layer]['center_mm'][1])
        if item['termination'] != 'continue': geometries.append(('unapproved_candidate', item['next_sensor_poses']))
    for role, sensors in geometries:
        for coordinate, axis in ((1, 'y'), (2, 'z')):
            graph = ROOT.TGraph(); graph.SetName('geometry_global_' + axis + '_' + role)
            graph.SetTitle('Detector Geometry: ' + role + ';Global X [mm];Global ' + axis.upper() + ' [mm]')
            for sensor in sensors: graph.SetPoint(graph.GetN(), sensor['center_mm'][0], sensor['center_mm'][coordinate])
            objects.append(graph)
    objects.extend((ratios, pede))
    for item in objects: item.Write()
    canvas = ROOT.TCanvas('calibration_summary', 'Native telescope calibration diagnostics', 900, 650)
    if ratios.GetN(): ratios.Draw('ALP')
    canvas.Write(); file.Close()
    check = ROOT.TFile.Open(str(target)); keys = [key.GetName() for key in check.GetListOfKeys()]; check.Close()
    return {'path': str(target), 'sha256': digest(target), 'keys': keys, 'evaluations': iterations,
            'truth_used_for_corrections': False, 'truth_geometry_displayed_for_validation_only': True,
            'uncertainties_combined_across_fits': False, 'incomplete_artifacts': incomplete}


def report_stage(run):
    from calibration.native_controller import check_identity, checkpoints, verify
    require(run, 'configuration'); output = run / 'report'; output.mkdir()
    controller = run / 'controller'; final = controller / 'final/report.json'
    reports = sorted(controller.glob('jobs/attempt_*/report.json'))
    selected = final if final.exists() else reports[-1] if reports else None
    controller_report = json.loads(selected.read_text()) if selected else {}
    integrity_error = None; chain = []
    try:
        load_run(run)
        if (controller / 'manifest.json').exists():
            check_identity(json.loads((controller / 'manifest.seal.json').read_text()))
            verify(controller / 'input'); chain = checkpoints(controller)
        if final.exists(): verify(final.parent)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        integrity_error = str(error)
    success = (integrity_error is None and final.exists() and len(chain) >= 2
               and controller_report.get('applied_corrections', 0) >= 1
               and controller_report.get('status') in ('converged', 'iteration_limit')
               and controller_report.get('engineering_success', True)
               and chain[-1]['geometry'] == chain[-1]['next_geometry']
               and digest(final.parent / 'candidate.xml') == chain[-1]['geometry']['sha256'])
    root = root_report(run, output)
    if root['incomplete_artifacts']: success = False
    summary = {'schema': 'native-public-workflow-result-v1', 'engineering_success': success,
        'physics_quality': 'not_validated', 'approval_status': 'unapproved', 'geometry_activated': False,
        'integrity_status': 'failed' if integrity_error or controller_report.get('status') == 'failed' else ('passed' if success else 'incomplete'),
        'integrity_error': integrity_error, 'controller_report': str(selected) if selected else None,
        'controller': controller_report, 'root_diagnostics': root,
        'quality_warnings': controller_report.get('quality_warnings', []),
        'interpretation': 'Technical integration and same-data consistency do not establish accuracy, uncertainty coverage or material-aware performance.'}
    write_json(output / 'summary.json', summary)
    lines = ['# Native telescope calibration result', '',
        'Engineering success: ' + str(success), 'Physics-quality validation: not validated.',
        'Geometry approval: unapproved. Geometry activation: never.', '',
        'Controller status: ' + controller_report.get('status', 'not started'),
        'Reason: ' + controller_report.get('reason', 'See preserved execution logs.'), '',
        'Quality warnings are retained in `report/summary.json` and original Pede/Controller logs.', '',
        'Open `report/diagnostics.root` with ROOT TBrowser. Its fit residuals, parameter errors and chi2 use actual reconstruction/solver outputs; no Truth-based correction is plotted.', '',
        'See `run_manifest.json` for all important artifact paths, checksums and stage receipts.']
    (output / 'report.md').write_text('\n'.join(lines) + '\n')
    done(output, passed=True, engineering_success=success, physics_quality='not_validated', geometry_activated=False)
    artifacts = {str(p.relative_to(run)): {'bytes': p.stat().st_size, 'sha256': digest(p)}
                 for p in sorted(run.rglob('*')) if p.is_file()}
    write_json(run / 'run_manifest.json', {'schema': 'native-public-artifact-index-v1', 'result': summary,
        'configuration': 'configuration/configuration.json', 'run_plan': 'configuration/run_plan.json',
        'artifacts': artifacts, 'geometry_activated': False})
    print(json.dumps(summary, indent=2))
    return success


def execute_stage(action, run):
    directory = run / 'execution' / action
    directory.mkdir(parents=True)
    if action in ('prepare', 'report'):
        write_json(directory / 'invocation.json', {'actual_process_argv': getattr(sys, 'orig_argv', None),
            'sys_argv': sys.argv, 'equivalent_command': [sys.executable, '-B', '-m', 'calibration.workflow', action, '--run-dir', str(run)],
            'cwd': str(REPO), 'fixed_image': os.environ.get('TESTBENCH_IMAGE')})
    return {'prepare': prepare_stage, 'simulate': simulate_stage, 'digitize': digitize_stage,
            'calibrate': calibrate_stage, 'report': report_stage}[action](run)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('run', *STAGES):
        command = commands.add_parser(action)
        command.add_argument('--run-dir', type=Path)
        if action in ('run', 'prepare'):
            command.add_argument('--config', type=Path); command.add_argument('--output-dir', type=Path)
            command.add_argument('--build', type=Path); command.add_argument('--simulation-stage', type=Path)
            command.add_argument('--validation-mode', choices=('strict', 'diagnostic')); command.add_argument('--max-iterations', type=int)
            command.add_argument('--geometry'); command.add_argument('--initial-geometry')
            command.add_argument('--alignment-config')
            command.add_argument('--particle'); command.add_argument('--energy-gev', type=float)
            command.add_argument('--beam', choices=('pencil', 'broad')); command.add_argument('--events', type=int); command.add_argument('--seed', type=int)
            command.add_argument('--environment', choices=('fixed-docker',))
            for name, count in (('position-mm', 3), ('direction', 3), ('vertex-sigma-mm', 3), ('theta-range-rad', 2), ('phi-range-rad', 2)):
                command.add_argument('--' + name, type=float, nargs=count)
    args = parser.parse_args()
    if args.action == 'run' or (args.action == 'prepare' and args.run_dir is None):
        if args.run_dir is not None or not all((args.config, args.output_dir, args.build)):
            parser.error('New run/prepare requires --config, --output-dir and --build; no --run-dir')
        run = initialize(args)
    else:
        if args.run_dir is None: parser.error('An existing --run-dir is required')
        if args.action == 'prepare' and any(getattr(args, key, None) is not None for key in
                ('config', 'output_dir', 'build', 'simulation_stage', 'validation_mode', 'max_iterations',
                 'geometry', 'initial_geometry', 'alignment_config', 'particle', 'energy_gev', 'beam', 'events', 'seed',
                 'environment', 'position_mm', 'direction', 'vertex_sigma_mm', 'theta_range_rad', 'phi_range_rad')):
            parser.error('An existing run uses its immutable snapshot; do not pass configuration overrides with --run-dir')
        run = result_path(args.run_dir)
    os.chdir(REPO)
    if args.action != 'run':
        success = execute_stage(args.action, run)
        if args.action == 'report' and success is False: raise SystemExit(1)
        return
    failed = False
    for action in STAGES:
        try:
            result = execute_stage(action, run)
            if action == 'report' and result is False: failed = True
        except BaseException as error:
            failed = True
            directory = run / 'execution' / action
            write_json(directory / 'failure.json', {'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()})
            if action != 'report':
                try: execute_stage('report', run)
                except BaseException as report_error:
                    print('Report generation also failed: ' + str(report_error), file=sys.stderr)
            print('Workflow stopped; partial outputs retained: ' + str(error), file=sys.stderr)
            break
    if failed: raise SystemExit(1)


if __name__ == '__main__':
    main()
