"""Digitize Geant4 SimTrackerHits and project a truth-free native input.

The projection accepts exactly one real SiMAPS channel on each layer L0--L4.
It does not select using MCParticles, SimTrackerHits, fitted tracks or residuals.
All source events, including rejected multiplicities, remain in the full
digitization output and are accounted for in a separate selection ledger.
"""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from research_common import REPO, digest, done, execute, require, write_json


RAW_COLLECTION = 'TelescopeSiMAPSRawHits'
RAW_OUTPUTS = tuple('Telescope' + detector + suffix
                    for detector in ('SiMAPS', 'MPGD', 'ACLGAD')
                    for suffix in ('RawHits', 'RawHitLinks', 'RawHitAssociations'))
CONDITIONS = {
    'SiMAPS': {'threshold_keV': .54, 'time_resolution_ns': 8.},
    'MPGD': {'threshold_keV': .1, 'time_resolution_ns': 10.},
    'ACLGAD': {'threshold_keV': 0., 'time_resolution_ns': .025},
}
CONDITIONS_SHA256 = hashlib.sha256(json.dumps(CONDITIONS, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def bit_fields(descriptor):
    """Decode DD4hep's name:[offset:]signed-width CellID descriptor."""
    fields = {}; cursor = 0; occupied = 0
    for token in descriptor.split(','):
        parts = token.strip().split(':')
        if len(parts) not in (2, 3) or not parts[0] or parts[0] in fields:
            raise ValueError('Invalid or duplicate CellID field')
        name = parts[0]
        offset = cursor if len(parts) == 2 else int(parts[1])
        width = int(parts[-1]); size = abs(width)
        if offset < 0 or not 0 < size <= 64 or offset + size > 64:
            raise ValueError('CellID field exceeds the 64-bit encoding')
        mask = ((1 << size) - 1) << offset
        if occupied & mask: raise ValueError('Overlapping CellID fields')
        fields[name] = {'offset': offset, 'width': size, 'signed': width < 0}
        occupied |= mask; cursor = offset + size
    if not fields: raise ValueError('Empty CellID descriptor')
    return fields


def decode(cell, field):
    value = (cell >> field['offset']) & ((1 << field['width']) - 1)
    if field['signed'] and value & (1 << (field['width'] - 1)):
        value -= 1 << field['width']
    return value


def geometry_contract(geometry, audit_path):
    """Bind the XML readout layout to an actual zero-event Geometry reload."""
    geometry = Path(geometry).resolve(); audit_path = Path(audit_path).resolve()
    audit = json.loads(audit_path.read_text())
    if audit.get('passed') is not True or audit.get('events') != []:
        raise ValueError('A successful zero-event DD4hep/ACTS Geometry audit is required')
    if Path(audit['geometry']).resolve() != geometry:
        raise ValueError('Geometry audit was produced with another XML file')
    sensors = audit['sensors']
    if len(sensors) != 7 or [s['layer'] for s in sensors] != list(range(7)):
        raise ValueError('Expected seven uniquely ordered audited sensors')
    for key in ('volume_id', 'geometry_id'):
        values = [s[key] for s in sensors]
        if any(type(v) is not int or v <= 0 for v in values) or len(set(values)) != 7:
            raise ValueError('Missing or duplicated audited detector identity')
    root = ET.parse(geometry).getroot()
    detector = root.find("./detectors/detector[@name='SiMAPS']")
    if detector is None or detector.get('type') != 'DD4hep_SiTrackerBarrel':
        raise ValueError('Only the actual native barrel telescope Geometry is supported')
    readout = root.find("./readouts/readout[@name='%s']" % detector.get('readout'))
    if readout is None: raise ValueError('Missing SiMAPS readout')
    segmentation = readout.find('segmentation')
    if segmentation is None or segmentation.get('type') != 'CartesianGridXZ':
        raise ValueError('Expected the native SiMAPS CartesianGridXZ readout')
    descriptor = readout.findtext('id', '').strip(); fields = bit_fields(descriptor)
    if not {'system', 'barrel', 'layer', 'module', 'sensor', 'x', 'z'} <= fields.keys():
        raise ValueError('Missing native detector or segmentation CellID fields')
    pixel_fields = [segmentation.get('identifier_x', 'x'), segmentation.get('identifier_z', 'z')]
    if len(set(pixel_fields)) != 2 or any(name not in fields for name in pixel_fields):
        raise ValueError('Invalid SiMAPS pixel identifier fields')
    pixel_mask = sum(((1 << fields[name]['width']) - 1) << fields[name]['offset']
                     for name in pixel_fields)
    volume_mask = ((1 << 64) - 1) ^ pixel_mask
    for sensor in sensors[:5]:
        cell = sensor['volume_id']
        if (cell & volume_mask) != cell or decode(cell, fields['system']) != 31 or decode(cell, fields['barrel']) != 0 or decode(cell, fields['layer']) != sensor['layer']:
            raise ValueError('Audited SiMAPS Volume IDs disagree with the native readout')
    return {'geometry_sha256': digest(geometry), 'geometry_audit_sha256': digest(audit_path),
            'readout': detector.get('readout'), 'descriptor': descriptor,
            'segmentation': dict(segmentation.attrib), 'fields': fields,
            'volume_mask': volume_mask, 'sensors': sensors[:5]}


def select_channels(values, contract):
    """Return layer-ordered source indices and a complete multiplicity decision."""
    by_layer = {layer: [] for layer in range(5)}; invalid = []
    expected = {s['layer']: s['volume_id'] for s in contract['sensors']}
    for index, (cell, charge, timestamp) in enumerate(values):
        if type(cell) is not int or not 0 <= cell < (1 << 64):
            invalid.append({'source_hit_index': index, 'cell_id': cell, 'reason': 'invalid_uint64_cell_id'})
            continue
        layer = decode(cell, contract['fields']['layer'])
        if (layer not in expected or decode(cell, contract['fields']['system']) != 31
                or cell & contract['volume_mask'] != expected[layer]):
            invalid.append({'source_hit_index': index, 'cell_id': cell, 'reason': 'unmatched_real_simaps_volume'})
            continue
        by_layer[layer].append(index)
    counts = {str(layer): len(indices) for layer, indices in by_layer.items()}
    accepted = not invalid and all(count == 1 for count in counts.values())
    decision = {'accepted': accepted, 'raw_hit_count': len(values),
                'layer_multiplicity': counts, 'invalid_channels': invalid,
                'reasons': ([] if accepted else
                            (['invalid_detector_cell_id'] if invalid else []) +
                            ['layer_%d_multiplicity_%d' % (layer, len(indices))
                             for layer, indices in by_layer.items() if len(indices) != 1])}
    return ([by_layer[layer][0] for layer in range(5)] if accepted else []), decision


def _collections(path):
    from podio import reading
    names = None; count = 0
    for frame in reading.get_reader(str(path)).get('events'):
        available = set(frame.getAvailableCollections())
        if names is None: names = available
        elif names != available: raise ValueError('Source collection names vary between events')
        count += 1
    if not count: raise ValueError('Empty source event file')
    return sorted(names), count


def _environment(build):
    from calibration.native_operations import environment
    return environment(build)


def checked_geometry_contract(geometry, audit_path):
    """Require the existing audit stage's checksums and exact XML identity."""
    require(audit_path.parent.parent, audit_path.parent.name)
    marker = json.loads((audit_path.parent/'complete.json').read_text())
    if not marker.get('passed') or marker.get('geometry_sha256') != digest(geometry):
        raise ValueError('Completed Geometry audit does not identify this XML checksum')
    return geometry_contract(geometry, audit_path)


def checked_simulation_binding(simulation, geometry, audit_path, simulation_stage):
    """Bind an actual npsim ROOT to its completed producer and readout Geometry.

    A different Survey XML is allowed only with the same barrel constructors,
    full readout definitions and all seven actual VolumeID/GeometryIdentifier
    pairs. Pose differences are recorded explicitly; they do not enter RawHit
    multiplicity selection or numerical alignment inference.
    """
    if simulation_stage is None:
        raise ValueError('A completed --simulation-stage producer receipt is required')
    simulation_stage = Path(simulation_stage).resolve()
    simulation = Path(simulation).resolve(); geometry = Path(geometry).resolve()
    require(simulation_stage.parent, simulation_stage.name)
    marker_path = simulation_stage / 'complete.json'
    marker = json.loads(marker_path.read_text())
    config_path = simulation_stage / 'configuration.json'
    command_path = simulation_stage / 'transport/command.json'
    config = json.loads(config_path.read_text())
    command = json.loads(command_path.read_text())
    argv = list(map(str, command['argv']))
    def option(name):
        values = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == name]
        values += [arg[len(name) + 1:] for arg in argv if arg.startswith(name + '=')]
        if len(values) != 1:
            raise ValueError('Producer command must identify exactly one ' + name)
        return values[0]
    from simulation.native import gun_arguments
    gun_options = {'particle': config['particle'], 'energy_gev': config['energy_GeV'],
                   'position_mm': config['position_mm'], 'direction': config['mean_direction']}
    generator = config.get('generator')
    if generator is not None:
        if (generator['beam'] != config['beam'] or generator['particle'] != config['particle']
                or generator['energy_GeV'] != config['energy_GeV']
                or generator['position_mm'] != config['position_mm']
                or generator['direction'] != config['mean_direction']):
            raise ValueError('Inconsistent generator and producer configuration')
        gun_options['vertex_sigma_mm'] = generator['vertex_sigma_mm']
        if config['beam'] == 'broad':
            gun_options['theta_range_rad'] = generator['theta_range_rad']
            gun_options['phi_range_rad'] = generator['phi_range_rad']
    expected_gun = gun_arguments(config['beam'], **gun_options)
    if config.get('generator_arguments', expected_gun) != expected_gun:
        raise ValueError('Snapshot gun arguments disagree with declared generator settings')
    if argv.count('--enableGun') != 1 or argv[argv.index('--enableGun'):] != expected_gun:
        raise ValueError('Executed gun arguments disagree with the producer configuration')
    produced_geometry = Path(option('--compactFile')).resolve()
    if (marker.get('passed') is not True or config.get('schema') != 'native-npsim-v1'
            or marker.get('simulation_sha256') != digest(simulation)
            or Path(option('--outputFile')).resolve() != simulation
            or not simulation.is_relative_to(simulation_stage)
            or not produced_geometry.is_relative_to(simulation_stage)
            or config['simulation_geometry_sha256'] != digest(produced_geometry)
            or int(option('--numberOfEvents')) != config['events']
            or int(option('--random.seed')) != config['seed']
            or option('--gun.particle') != config['particle']):
        raise ValueError('Simulation producer does not bind the supplied ROOT, XML, seed and configuration')
    root_relative = str(simulation.relative_to(simulation_stage))
    if marker.get('output_hashes', {}).get(root_relative) != digest(simulation):
        raise ValueError('Simulation ROOT is absent from the completed producer output hashes')
    actual_audit = simulation_stage / 'simulation_geometry_audit/geometry.json'
    produced = checked_geometry_contract(produced_geometry, actual_audit)
    supplied = checked_geometry_contract(geometry, audit_path)
    def readouts(path):
        root = ET.parse(path).getroot()
        records = []
        for name in ('SiMAPS', 'MPGDPlane', 'ACLGADPlane'):
            detector = root.find("./detectors/detector[@name='%s']" % name)
            if detector is None or detector.get('type') != 'DD4hep_SiTrackerBarrel':
                raise ValueError('Producer and digitization must use actual barrel detector constructors')
            node = root.find("./readouts/readout[@name='%s']" % detector.get('readout'))
            if node is None:
                raise ValueError('Missing producer detector readout')
            def normalized(element):
                return {'tag': element.tag, 'attributes': dict(element.attrib),
                        'text': (element.text or '').strip(),
                        'children': [normalized(child) for child in element]}
            records.append({'detector': name, 'id': detector.get('id'),
                            'type': detector.get('type'), 'readout': normalized(node)})
        return records
    producer_readouts = readouts(produced_geometry)
    if producer_readouts != readouts(geometry):
        raise ValueError('Simulation and digitization readout definitions differ')
    actual_sensors = json.loads(actual_audit.read_text())['sensors']
    supplied_sensors = json.loads(Path(audit_path).read_text())['sensors']
    if any(a.get(key) != b.get(key)
           for a, b in zip(actual_sensors, supplied_sensors)
           for key in ('layer', 'volume_id', 'geometry_id')):
        raise ValueError('Simulation and digitization actual detector identities differ')
    return {'schema': 'native-npsim-binding-v1',
        'simulation_stage': str(simulation_stage),
        'producer_complete_sha256': digest(marker_path),
        'configuration_sha256': digest(config_path), 'command_sha256': digest(command_path),
        'simulation_root_sha256': digest(simulation),
        'simulation_geometry': {'path': str(produced_geometry), 'sha256': digest(produced_geometry)},
        'simulation_geometry_audit_sha256': digest(actual_audit),
        'digitization_geometry_sha256': digest(geometry),
        'exact_simulation_xml': digest(geometry) == digest(produced_geometry),
        'physical_sensor_poses_identical': actual_sensors == supplied_sensors,
        'all_seven_actual_detector_identities_equal': True,
        'readout_definitions_equal': True, 'readout_definitions': producer_readouts,
        'events': config['events'], 'seed': config['seed'], 'particle': config['particle'],
        'energy_GeV': config['energy_GeV'],
        'truth_used_for_selection': False, 'truth_used_for_alignment_inference': False}


def digitize(simulation, geometry, audit_path, build, output, simulation_stage=None):
    """Execute only the installed official digitizers, preserving source data."""
    binding = checked_simulation_binding(simulation, geometry, audit_path, simulation_stage)
    contract = checked_geometry_contract(geometry, audit_path)
    source_names, count = _collections(simulation)
    if not {'EventHeader', 'MCParticles', 'SiMAPSHits', 'MPGDHits', 'ACLGADHits'} <= set(source_names):
        raise ValueError('Missing Geant4 event, MCParticle or SimTrackerHit collections')
    if count != binding['events']:
        raise ValueError('Source event count disagrees with the completed npsim producer')
    if set(source_names) & set(RAW_OUTPUTS): raise ValueError('Simulation input already contains target RawHits')
    plugin = build / 'TelescopeDigitization.so'
    if not plugin.is_file(): raise FileNotFoundError(plugin)
    before = {str(p): digest(p) for p in (simulation, geometry, audit_path, plugin)}
    output.mkdir(); target = output / 'digitized.root'
    command = ['eicrecon', '-Pplugins_to_ignore=default',
        '-Pplugins=log,dd4hep,evaluator,acts,algorithms_init,podio,TelescopeDigitization',
        '-Pnthreads=1', '-Pjana:nevents=%d' % count, '-Pacts:MaterialMap=',
        '-Pdd4hep:xml_files=' + str(geometry), '-Ppodio:output_file=' + str(target),
        '-Ppodio:output_collections=' + ','.join(source_names + list(RAW_OUTPUTS)), str(simulation)]
    execute(command, output, env=_environment(build))
    names, actual_count = _collections(target)
    if actual_count != count or set(names) != set(source_names) | set(RAW_OUTPUTS):
        raise ValueError('Official digitization lost source events or requested collections')
    if any(digest(Path(path)) != sha for path, sha in before.items()):
        raise RuntimeError('Digitization changed an immutable input or plugin')
    if checked_simulation_binding(simulation, geometry, audit_path, simulation_stage) != binding:
        raise RuntimeError('Simulation producer changed during digitization')
    metadata = {'schema': 'native-geant4-digitization-v1', 'input_origin': 'geant4_digitized',
        'simulation': {'path': str(simulation), 'sha256': digest(simulation)},
        'digitized_root': {'path': str(target), 'sha256': digest(target)},
        'plugin': {'path': str(plugin), 'sha256': digest(plugin)},
        'registration_sha256': digest(REPO/'localDetEICrecon/experimental/telescope/TelescopeDigitization.cc'),
        'geometry': {'path': str(geometry), 'sha256': digest(geometry)},
        'geometry_audit': {'path': str(audit_path), 'sha256': digest(audit_path)},
        'conditions': CONDITIONS, 'conditions_sha256': CONDITIONS_SHA256,
        'source_collections': source_names, 'output_collections': names,
        'producer_factory_names': ['Telescope' + detector + 'RawHits' for detector in ('SiMAPS', 'MPGD', 'ACLGAD')],
        'digitizer_random_seed': 'Official UniqueIDGenSvc: EventHeader and producer factory name; names differ from the legacy plugin',
        'charge_convention': 'Official digitizer: per-SimTrackerHit threshold, per-step round(GeV*1e6), summed by CellID',
        'time_convention': 'Official digitizer: earliest smeared time per CellID, integer ps',
        'source_events': count, 'readout_contract': contract, 'simulation_producer_binding': binding,
        'reconstruction_executed': False, 'geant4_material_in_simulation': True,
        'acts_material_map_created': False}
    write_json(output/'provenance.json', metadata); done(output, passed=True, events=count)
    return target


def write_channels(selected, target):
    """Write independent PODIO clones without changing headers or RawHit values."""
    import ROOT
    from podio import root_io
    from podio.frame import Frame
    if target.exists(): raise FileExistsError(target)
    writer = root_io.Writer(str(target))
    for header, channels, _ in selected:
        headers = ROOT.edm4hep.EventHeaderCollection(); headers.push_back(header)
        hits = ROOT.edm4eic.RawTrackerHitCollection()
        for hit in channels: hits.push_back(hit)
        frame = Frame(); frame.put(headers, 'EventHeader'); frame.put(hits, 'TelescopeChannels')
        writer.write_frame(frame, 'events')
    writer._writer.finish()


def project(digitized, simulation, geometry, audit_path, build, output, simulation_stage=None):
    """Clone official digitized channels into the existing immutable input schema."""
    import ROOT
    from podio import reading
    from calibration.native_operations import channel_digest, channel_values
    require(digitized.parent.parent, digitized.parent.name)
    binding = checked_simulation_binding(simulation, geometry, audit_path, simulation_stage)
    metadata_path = digitized.parent / 'provenance.json'
    metadata = json.loads(metadata_path.read_text())
    contract = checked_geometry_contract(geometry, audit_path)
    if (metadata['schema'] != 'native-geant4-digitization-v1' or metadata['conditions'] != CONDITIONS
            or metadata.get('simulation_producer_binding') != binding
            or metadata['digitized_root']['sha256'] != digest(digitized)
            or metadata['simulation']['sha256'] != digest(simulation)
            or metadata['geometry']['sha256'] != digest(geometry)
            or metadata['geometry_audit']['sha256'] != digest(audit_path)
            or metadata['plugin']['sha256'] != digest(build/'TelescopeDigitization.so')):
        raise ValueError('Digitization provenance does not match immutable inputs')
    protected = {str(path): digest(path) for path in (digitized, simulation, geometry, audit_path,
                                                     metadata_path, build/'TelescopeDigitization.so')}
    output.mkdir(); ledger = []; selected = []; seen = set()
    reader = reading.get_reader(str(digitized))
    for source_index, frame in enumerate(reader.get('events')):
        headers = frame.get('EventHeader')
        if len(headers) != 1: raise ValueError('Exactly one EventHeader is required per source event')
        header = headers[0]; identity = (int(header.getRunNumber()), int(header.getEventNumber()))
        if identity in seen: raise ValueError('Duplicate source run/event identity')
        seen.add(identity)
        raw = frame.get(RAW_COLLECTION)
        values = [(int(hit.getCellID()), int(hit.getCharge()), int(hit.getTimeStamp())) for hit in raw]
        indices, decision = select_channels(values, contract)
        decision.update(source_event_index=source_index, run_number=identity[0], event_number=identity[1])
        if decision['accepted']:
            decision['projected_event_index'] = len(selected)
            decision['source_hit_indices_in_layer_order'] = indices
            selected.append((header.clone(), [raw[i].clone() for i in indices],
                             (identity[1], [values[i] for i in indices])))
        ledger.append(decision)
    write_json(output/'selection.json', {'policy': 'exactly_one_valid_real_simaps_channel_per_layer_L0_L4',
        'truth_used_for_selection': False, 'track_fit_used_for_selection': False,
        'source_events': len(ledger), 'accepted_events': len(selected),
        'rejected_events': len(ledger)-len(selected), 'events': ledger})
    if not selected: raise ValueError('No source events satisfy the declared RawHit-only multiplicity rule')
    target = output/'channels.root'; write_channels(selected, target)
    values = channel_values(target)
    if values != [entry[2] for entry in selected]: raise RuntimeError('Projected RawHit values changed on ROOT read-back')
    names, count = _collections(target)
    if set(names) != {'EventHeader', 'TelescopeChannels'} or count != len(selected):
        raise RuntimeError('Unexpected collections or event count in projected input')
    if any(digest(Path(path)) != sha for path, sha in protected.items()):
        raise RuntimeError('Projection changed or concurrently read a modified immutable input')
    if checked_simulation_binding(simulation, geometry, audit_path, simulation_stage) != binding:
        raise RuntimeError('Simulation producer changed during projection')
    provenance = {'schema': 'native-geant4-input-v1', 'input_origin': 'geant4_digitized',
        'raw_root_sha256': digest(target), 'channel_payload_sha256': channel_digest(values),
        'raw_root': str(target), 'simulation': metadata['simulation'],
        'digitization': {'path': str(digitized), 'sha256': digest(digitized),
                         'provenance_sha256': digest(metadata_path)},
        'geometry': metadata['geometry'], 'geometry_audit': metadata['geometry_audit'],
        'simulation_producer_binding': binding,
        'plugin': metadata['plugin'], 'conditions': CONDITIONS, 'conditions_sha256': CONDITIONS_SHA256,
        'digitization_registration_sha256': metadata['registration_sha256'], 'readout_contract': contract,
        'selection_ledger_sha256': digest(output/'selection.json'), 'selection': ledger,
        'source_events': len(ledger), 'accepted_events': len(selected), 'rejected_events': len(ledger)-len(selected),
        'truth_used_for_selection': False, 'track_fit_used_for_selection': False,
        'channel_values_preserved': True, 'event_headers_cloned': True,
        'source_raw_collection': RAW_COLLECTION, 'output_collections': names,
        'acts_reconstruction_material_free': True, 'geometry_activated': False}
    write_json(output.parent/'provenance.json', provenance)
    done(output, passed=True, accepted=count, rejected=len(ledger)-count,
         raw_root_sha256=provenance['raw_root_sha256'], channel_payload_sha256=provenance['channel_payload_sha256'])
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation', choices=('all', 'digitize', 'project'), default='all')
    parser.add_argument('--output-dir', type=Path, required=True, help='Unused child directory of result/')
    parser.add_argument('--simulation', type=Path, required=True, help='Actual Geant4 EDM4hep input')
    parser.add_argument('--simulation-stage', type=Path, required=True,
                        help='Completed simulation.native producer directory binding this ROOT to its actual XML')
    parser.add_argument('--geometry', type=Path, required=True, help='Native telescope XML used for digitization/readout')
    parser.add_argument('--geometry-audit', type=Path, required=True, help='Fresh successful zero-event TelescopeChannels Geometry JSON')
    parser.add_argument('--build', type=Path, required=True, help='Build containing TelescopeDigitization.so')
    parser.add_argument('--digitized', type=Path, help='Previously completed native digitization output, for project only')
    args = parser.parse_args()
    for key, value in vars(args).items():
        if isinstance(value, Path): setattr(args, key, value.resolve())
    if args.output_dir == REPO/'result' or not args.output_dir.is_relative_to(REPO/'result'):
        parser.error('--output-dir must be an unused child of result/')
    if args.operation == 'project' and args.digitized is None: parser.error('project requires --digitized')
    if args.operation != 'project' and args.digitized is not None: parser.error('--digitized is only valid for project')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    digitized = args.digitized
    if args.operation in ('all', 'digitize'):
        digitized = digitize(args.simulation, args.geometry, args.geometry_audit, args.build, args.output_dir/'digitization', args.simulation_stage)
    if args.operation in ('all', 'project'):
        project(digitized, args.simulation, args.geometry, args.geometry_audit, args.build, args.output_dir/'projection', args.simulation_stage)
    done(args.output_dir, passed=True, operation=args.operation)


if __name__ == '__main__':
    main()
