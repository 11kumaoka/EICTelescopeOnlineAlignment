"""Independent npsim/Geant4 production for the radial native telescope fixture.

This experimental input stage preserves the legacy +Z simulation interface.
It does not construct channels, fit tracks, or calculate alignment corrections.
"""
import argparse
import copy
import math
import importlib.util
import os
import platform
import subprocess
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from research_common import REPO, digest, done, execute, stage, write_json


def simulation_geometry(source, destination):
    """Enclose all seven radial sensors in the MC particle-retention region.

    These two constants configure DDSim's particle bookkeeping, not physical
    placements or material. The canonical +Z telescope has a smaller radius.
    """
    root = ET.parse(source).getroot()
    original = copy.deepcopy(root)
    changes = {}
    for name, value in (('tracker_region_rmax', '600*mm'),
                        ('tracker_region_zmax', '100*mm')):
        node = root.find(f"./define/constant[@name='{name}']")
        if node is None:
            raise ValueError('Missing MC particle-retention constant: ' + name)
        changes[name] = {'before': node.get('value'), 'after': value}
        node.set('value', value)
    normalized = copy.deepcopy(root)
    for name, values in changes.items():
        normalized.find(f"./define/constant[@name='{name}']").set('value', values['before'])
    if ET.tostring(normalized) != ET.tostring(original):
        raise RuntimeError('Unexpected simulation Geometry change')
    with destination.open('x') as stream:
        ET.ElementTree(root).write(stream, encoding='unicode')
    return changes


PARTICLE_PDGS = {'e-': 11, 'e+': -11, 'mu-': 13, 'mu+': -13,
                 'pi+': 211, 'pi-': -211, 'proton': 2212, 'neutron': 2112,
                 'gamma': 22}


def gun_configuration(beam='pencil', particle='e-', energy_gev=1.,
                      position_mm=(249., 0., 0.), direction=(1., 0., 0.),
                      vertex_sigma_mm=None, theta_range_rad=None, phi_range_rad=None):
    """Validate a small explicit subset of the installed DDSim gun interface.

    Alternative particles and energies are simulation-only capabilities. The
    native reconstruction retains its separately declared electron seed prior.
    """
    if beam not in ('pencil', 'broad') or particle not in PARTICLE_PDGS:
        raise ValueError('Unsupported beam recipe or Geant4 particle name')
    def finite(values, count, name):
        values = list(map(float, values))
        if len(values) != count or not all(math.isfinite(v) for v in values):
            raise ValueError('Expected finite ' + name)
        return values
    energy = float(energy_gev)
    if not math.isfinite(energy) or energy <= 0:
        raise ValueError('Gun energy must be finite and positive')
    position = finite(position_mm, 3, 'position coordinates')
    direction = finite(direction, 3, 'direction components')
    if math.hypot(*direction) == 0:
        raise ValueError('Gun direction must be nonzero')
    if beam == 'broad' and direction != [1., 0., 0.]:
        raise ValueError('The broad spherical-angle recipe uses +X as its reference; --direction is pencil-only')
    sigma = finite(vertex_sigma_mm if vertex_sigma_mm is not None else
                   ((0., 0., 0.) if beam == 'pencil' else (0., 12., 10.)), 3, 'vertex widths')
    if any(v < 0 for v in sigma):
        raise ValueError('Vertex widths must be nonnegative')
    theta = finite(theta_range_rad if theta_range_rad is not None else
                   (math.pi / 2 - .08, math.pi / 2 + .08), 2, 'theta range')
    phi = finite(phi_range_rad if phi_range_rad is not None else (-.08, .08), 2, 'phi range')
    if not 0 <= theta[0] < theta[1] <= math.pi or not -math.pi <= phi[0] < phi[1] <= math.pi:
        raise ValueError('Invalid ordered spherical-angle ranges')
    if beam == 'pencil' and (theta_range_rad is not None or phi_range_rad is not None):
        raise ValueError('Angular ranges require the broad uniform-angle recipe')
    return {'beam': beam, 'particle': particle, 'pdg': PARTICLE_PDGS[particle],
            'energy_GeV': energy, 'position_mm': position, 'direction': direction,
            'vertex_sigma_mm': sigma, 'theta_range_rad': theta, 'phi_range_rad': phi,
            'angular_distribution': 'fixed_direction' if beam == 'pencil' else 'uniform_spherical_angles'}


def gun_arguments(beam, **options):
    """Use installed DDSim gun distributions, without a custom generator."""
    gun = gun_configuration(beam, **options)
    text = lambda value: format(value, '.17g')
    args = ['--enableGun', '--gun.particle', gun['particle'], '--gun.energy', text(gun['energy_GeV']) + '*GeV',
            '--gun.position', ' '.join(text(v) + '*mm' for v in gun['position_mm'])]
    if beam == 'pencil':
        args += ['--gun.direction', ' '.join(text(v) for v in gun['direction'])]
        if any(gun['vertex_sigma_mm']):
            args += ['--vertexSigma', *[text(v) for v in gun['vertex_sigma_mm']], '0']
        return args
    return args + ['--vertexSigma', *[text(v) for v in gun['vertex_sigma_mm']], '0',
        '--gun.distribution', 'uniform',
        '--gun.thetaMin', text(gun['theta_range_rad'][0]) + '*rad',
        '--gun.thetaMax', text(gun['theta_range_rad'][1]) + '*rad',
        '--gun.phiMin=' + format(gun['phi_range_rad'][0], '.15g') + '*rad',
        '--gun.phiMax', format(gun['phi_range_rad'][1], '.15g') + '*rad']


def software_snapshot(build):
    """Record actual executables, configuration declarations and build inputs."""
    def identity(path):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        return {'path': str(path), 'realpath': str(path.resolve()),
                'sha256': digest(path), 'bytes': path.stat().st_size}
    programs = {}
    for name in ('npsim', 'eicrecon', 'root-config', 'geant4-config'):
        path = shutil.which(name)
        if path is None:
            raise RuntimeError('Required installed program is missing: ' + name)
        programs[name] = identity(path)
    commands = {}
    for name in ('root-config', 'geant4-config'):
        proc = subprocess.run([name, '--version'], capture_output=True, text=True, check=True)
        commands[name] = {'argv': [name, '--version'], 'stdout': proc.stdout,
                          'stderr': proc.stderr, 'exit_code': proc.returncode}
    declarations = {}
    for package in ('DD4hep', 'Acts', 'EDM4HEP', 'podio'):
        candidates = sorted(Path('/opt/local/lib/cmake').glob(package + '/*ConfigVersion.cmake'))
        if len(candidates) == 1:
            path = candidates[0]
            lines = [line.strip() for line in path.read_text().splitlines()
                     if line.strip().startswith('set(PACKAGE_VERSION ')]
            declarations[package] = {'configuration': identity(path), 'declarations': lines}
    spec = importlib.util.find_spec('DDSim')
    if spec is None or spec.origin is None:
        raise RuntimeError('Installed DDSim Python package is required')
    dd = Path(spec.origin).resolve().parent
    sources = [identity(dd / 'DD4hepSimulation.py'), identity(dd / 'Helper/Gun.py')]
    npsim_source = Path(programs['npsim']['realpath']).parent / 'npsim.py'
    sources.append(identity(npsim_source))
    for name in ('TelescopeChannels', 'libTestbenchBarrelMetadata.so', 'libTestbenchBarrelMetadata.components'):
        identity(build / name)
    return {'python': platform.python_version(), 'fixed_image': os.environ.get('TESTBENCH_IMAGE'),
            'programs': programs, 'version_commands': commands, 'package_versions': declarations,
            'simulation_sources': sources,
            'build_inputs': [identity(build / name) for name in
                ('TelescopeChannels', 'libTestbenchBarrelMetadata.so', 'libTestbenchBarrelMetadata.components')],
            'npsim_version_note': 'The executable and npsim.py SHA identify the installed implementation; inspect its recorded source for its declared version.'}


def inspect_simulation(path, expected, gun=None):
    """Read real MC vertices and detector hits after transport, for QA only."""
    import ROOT  # Load the pinned EDM dictionaries before opening podio frames.
    from podio import reading
    gun = gun_configuration() if gun is None else gun
    events = []
    for frame in reading.get_reader(str(path)).get('events'):
        event = {'primaries': [], 'hits': {}}
        for particle in frame.get('MCParticles'):
            if particle.getGeneratorStatus() != 1:
                continue
            v = particle.getVertex(); p = particle.getMomentum()
            event['primaries'].append({'pdg': int(particle.getPDG()),
                'vertex_mm': [float(v.x), float(v.y), float(v.z)],
                'momentum_GeV': [float(p.x), float(p.y), float(p.z)]})
        for name in ('SiMAPSHits', 'MPGDHits', 'ACLGADHits'):
            hits = []
            for hit in frame.get(name):
                p = hit.getPosition()
                hits.append({'cell_id': int(hit.getCellID()),
                    'position_mm': [float(p.x), float(p.y), float(p.z)],
                    'energy_deposit_GeV': float(hit.getEDep()),
                    'time_ns': float(hit.getTime())})
            event['hits'][name] = hits
        if len(event['primaries']) != 1 or event['primaries'][0]['pdg'] != gun['pdg']:
            raise RuntimeError('Expected one primary of the declared gun particle per event')
        for axis, sigma in enumerate(gun['vertex_sigma_mm']):
            if sigma == 0 and abs(event['primaries'][0]['vertex_mm'][axis] - gun['position_mm'][axis]) > 1e-9:
                raise RuntimeError('Unexpected unsmeared primary vertex coordinate')
        events.append(event)
    if len(events) != expected:
        raise RuntimeError('Geant4 event count differs from the requested count')
    return {'events': len(events), 'event_details': events,
        'sim_hit_counts': {name: sum(len(e['hits'][name]) for e in events)
                           for name in ('SiMAPSHits', 'MPGDHits', 'ACLGADHits')},
        'truth_used_for': 'post-transport validation only; never alignment inference'}


def simulate(args):
    from calibration.native_operations import environment, geometry_audit
    output = args.output_dir.resolve(); source = args.geometry.resolve()
    build = args.build.resolve()
    if not output.is_relative_to(REPO / 'result') or output == REPO / 'result':
        raise ValueError('Use a new child directory of result/')
    if args.events <= 0 or not 0 < args.seed < 2**31:
        raise ValueError('Positive events and a Geant4-compatible positive 31-bit seed required')
    npsim = shutil.which('npsim')
    if npsim is None:
        raise RuntimeError('Installed npsim is required; no silent ddsim fallback')
    gun_options = {name: getattr(args, name, default) for name, default in (
        ('particle', 'e-'), ('energy_gev', 1.), ('position_mm', (249., 0., 0.)),
        ('direction', (1., 0., 0.)), ('vertex_sigma_mm', None),
        ('theta_range_rad', None), ('phi_range_rad', None))}
    gun = gun_configuration(args.beam, **gun_options)
    software = software_snapshot(build)
    output.mkdir(parents=True)
    geometry = output / 'simulation.xml'
    changes = simulation_geometry(source, geometry)
    before = geometry_audit(build, output / 'source_geometry_audit', source)
    after = geometry_audit(build, output / 'simulation_geometry_audit', geometry)
    if before['sensors'] != after['sensors']:
        raise RuntimeError('Simulation bookkeeping changed physical sensor identity')
    recipe = {'schema': 'native-npsim-v1', 'particle': gun['particle'], 'energy_GeV': gun['energy_GeV'],
        'events': args.events, 'seed': args.seed, 'beam': args.beam,
        'position_mm': gun['position_mm'], 'mean_direction': gun['direction'],
        'generator': gun, 'generator_arguments': gun_arguments(args.beam, **gun_options), 'software': software,
        'energy_semantics': 'Value passed unchanged to official gun.energy in GeV; actual MCParticles momenta are recorded in simulation_qa.json',
        'native_reconstruction_contract': 'Existing fixed 1 GeV electron seed prior; other gun species or energies are simulation-only until separately validated',
        'vertex_distribution': ('point' if not any(gun['vertex_sigma_mm']) else
            'Independent Gaussian coordinate smearing with widths recorded in generator.vertex_sigma_mm'),
        'angular_distribution': ('Fixed direction recorded in generator.direction' if args.beam == 'pencil' else
            'DDSim independent uniform theta and phi in the recorded generator ranges'),
        'synthetic_beam_equivalent': False,
        'source_geometry': {'path': str(source), 'sha256': digest(source)},
        'simulation_geometry_sha256': digest(geometry),
        'particle_retention_constants': changes,
        'physical_sensor_identity_unchanged': True,
        'transport': 'Geant4 FTFP_BERT with installed npsim optical additions; real Air/Silicon',
        'material_map_created': False, 'geometry_activated': False,
        'npsim': {'path': npsim, 'sha256': digest(Path(npsim).resolve())}}
    write_json(output / 'configuration.json', recipe)
    run = stage(output, 'transport'); target = run / 'events.edm4hep.root'
    command = [npsim, '--compactFile', geometry, '--runType', 'batch',
        '--numberOfEvents', args.events, '--random.seed', args.seed,
        '--physics.list', 'FTFP_BERT',
        '--outputConfig.forceEDM4HEP', '--outputFile', target] + gun_arguments(args.beam, **gun_options)
    execute(command, run, env=environment(build))
    write_json(output / 'simulation_qa.json', inspect_simulation(target, args.events, gun))
    done(output, passed=True, simulation_sha256=digest(target), geometry_activated=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--geometry', type=Path, required=True,
                        help='Actual native radial Truth Geometry XML; never the old endcap XML')
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--events', type=int, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--beam', choices=('pencil', 'broad'), default='pencil')
    parser.add_argument('--particle', choices=tuple(PARTICLE_PDGS), default='e-',
                        help='Geant4 particle; alternative particles are simulation-only')
    parser.add_argument('--energy-gev', type=float, default=1., help='Value passed to official gun.energy in GeV')
    parser.add_argument('--position-mm', type=float, nargs=3, default=(249., 0., 0.))
    parser.add_argument('--direction', type=float, nargs=3, default=(1., 0., 0.), help='Fixed direction for pencil beam')
    parser.add_argument('--vertex-sigma-mm', type=float, nargs=3, help='Gaussian widths; defaults to 0/0/0 pencil or 0/12/10 broad')
    parser.add_argument('--theta-range-rad', type=float, nargs=2, help='Broad recipe: uniform theta endpoints')
    parser.add_argument('--phi-range-rad', type=float, nargs=2, help='Broad recipe: uniform phi endpoints')
    simulate(parser.parse_args())


if __name__ == '__main__':
    main()
