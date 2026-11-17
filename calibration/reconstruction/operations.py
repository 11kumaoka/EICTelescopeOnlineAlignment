"""EICrecon execution and immutable readout payload checks."""
import os
import json
import hashlib
import struct
import subprocess
import time
from pathlib import Path
from research_common import REPO, execute, stage, done, digest, prefix, write_json

MILLE_COLLECTOR_TIMEOUT_MS = 30000
MILLE_COLLECTOR_READY_SCHEMA = 'telescope-mille-collector-ready-v1'
MILLE_COLLECTOR_REPORT_SCHEMA = 'telescope-mille-collector-report-v1'

def environment(build):
    env=os.environ.copy()
    env['LD_LIBRARY_PATH']=f'{prefix()}/lib:{build}:'+env.get('LD_LIBRARY_PATH','')
    env['JANA_PLUGIN_PATH']=str(build)+':'+env.get('JANA_PLUGIN_PATH','')
    env['PYTHONDONTWRITEBYTECODE']='1'
    return env


def channel_values(path):
    import ROOT
    from podio import reading
    reader=reading.get_reader(str(path));values=[]
    for frame in reader.get('events'):
        header=frame.get('EventHeader')[0]
        channels=frame.get('TelescopeChannels')
        values.append((int(header.getEventNumber()),[(int(h.getCellID()),int(h.getCharge()),int(h.getTimeStamp())) for h in channels]))
    return values


def channel_digest(values):
    data=b''
    for event,hits in values:
        data+=struct.pack('<qI',event,len(hits))
        for cell,charge,time in hits:data+=struct.pack('<Qiq',cell,charge,time)
    return hashlib.sha256(data).hexdigest()


def _collector_exit(path, process, *, terminated=False):
    if path.exists():
        return
    write = {'exit_code': process.returncode, 'terminated_by_controller': terminated}
    write_json(path, write)


def _stop_collector(process):
    """Stop the child while retaining the worker's outer supervised process group."""
    if process.poll() is not None:
        return False
    try:
        process.terminate()
    except ProcessLookupError:
        process.wait()
        return False
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    return True


def _wait_collector_ready(path, process, expected, timeout_ms):
    deadline = time.monotonic() + timeout_ms / 1000.
    last_error = None
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                ready = json.loads(path.read_text())
                for key, value in expected.items():
                    if ready.get(key) != value:
                        raise RuntimeError('Mille Collector ready identity mismatch: ' + key)
                endpoint = ready.get('endpoint')
                loopback = 'tcp://127.0.0.1:'
                port = endpoint[len(loopback):] if isinstance(endpoint, str) and endpoint.startswith(loopback) else ''
                if not port.isdigit() or not 1 <= int(port) <= 65535:
                    raise RuntimeError('Mille Collector did not publish a resolved loopback endpoint')
                return endpoint
            except (OSError, json.JSONDecodeError) as error:
                # Atomic publication is required, but tolerate a partially visible
                # file long enough to produce a useful failure for older prototypes.
                last_error = error
        code = process.poll()
        if code is not None:
            raise RuntimeError('Mille Collector exited before ready with code ' + str(code))
        time.sleep(.02)
    message = 'Timed out waiting for Mille Collector ready receipt'
    if last_error is not None:
        message += ': ' + str(last_error)
    raise TimeoutError(message)


def _validate_collector_report(path, binary, *, events, stream_id,
                               model_id, configuration_id, geometry_id):
    if not path.is_file():
        raise RuntimeError('Mille Collector did not publish its final report')
    report = json.loads(path.read_text())
    expected = {
        'schema': MILLE_COLLECTOR_REPORT_SCHEMA,
        'passed': True,
        'protocol_version': 3,
        'stream_id': stream_id,
        'records_received': events,
        'records_written': events,
        'expected_records': events,
        'eos_sequence': events,
        'model_id': model_id,
        'configuration_id': configuration_id,
        'geometry_id': geometry_id,
        'final_path': str(binary),
        'published': True,
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise RuntimeError('Mille Collector final report mismatch: ' + key)
    if report.get('error') not in (None, ''):
        raise RuntimeError('Mille Collector reported an error: ' + str(report['error']))
    for key in ('payload_bytes', 'output_bytes'):
        value = report.get(key)
        if type(value) is not int or value <= 0:
            raise RuntimeError('Mille Collector final report has invalid ' + key)
    if not binary.is_file() or binary.stat().st_size != report['output_bytes']:
        raise RuntimeError('Mille Collector output size differs from its final report')
    if report.get('scalar_measurements') != 10 * events:
        raise RuntimeError('Mille Collector scalar measurement count is invalid')
    event_records = report.get('events')
    if (not isinstance(event_records, list) or len(event_records) != events or
            any(item.get('sequence') != sequence or item.get('scalar_measurements') != 10
                for sequence, item in enumerate(event_records))):
        raise RuntimeError('Mille Collector event/sequence audit is incomplete')
    partial = Path(report.get('partial_path', ''))
    if partial.parent != binary.parent or partial.exists():
        raise RuntimeError('Mille Collector partial output publication is invalid')
    from calibration.alignment.mille import records
    blocks = list(records(binary))
    if len(blocks) != events or any(len(block) != 10 for block in blocks):
        raise RuntimeError('Mille Collector output has an incomplete record set')
    return report


def reconstruct(build,output,geometry,source,config,events,cut=None,input_origin='synthetic',
                geometry_aware_holes=False,mille_transport='file'):
    """Run the maintained native chain on one immutable channel file."""
    if input_origin not in ('synthetic','geant4'):
        raise ValueError('Unsupported native input origin')
    if mille_transport not in ('file', 'zeromq'):
        raise ValueError('Unsupported Mille transport: ' + str(mille_transport))
    collector_executable = build/'TelescopeMilleCollector'
    if mille_transport == 'zeromq' and not collector_executable.is_file():
        raise FileNotFoundError(collector_executable)
    p=stage(output.parent,output.name);env=environment(build)
    gate=stage(p,'gate')
    execute([build/'TelescopeMilleDerivatives',geometry,'geometry-center',gate/'derivatives.json'],gate,env=env)
    before=digest(source)
    # This invocation has no Truth trajectories, placements or MC collections.
    command=['eicrecon','-Pplugins_to_ignore=default',
      '-Pplugins=log,dd4hep,evaluator,acts,algorithms_init,beam,podio,TelescopeTracking,TelescopeMille',
      '-Pnthreads=1',f'-Pjana:nevents={events}',f'-Pdd4hep:xml_files={geometry}',
      '-Pacts:MaterialMap=','-PTelescopeTracking:channelInput=true','-PTelescopeAudit:multipleEvents=true',
      '-PTelescopeMille:multipleEvents=true',f'-PTelescopeAudit:output={p}/audit.json',
      f'-PTelescopeMille:report={p}/mille.json',
      f'-PTelescopeMille:config={config}',
      f'-PTelescopeMille:derivativeGate={gate}/derivatives.json',f'-Ppodio:output_file={p}/reco.root',
      '-Ppodio:output_collections=EventHeader,TelescopeChannels,TelescopeHits,TelescopeHitAssociations,TelescopeMeasurements,TelescopeSeeds,TelescopeSeedParameters,TelescopeTrajectories,TelescopeTrackParameters,TelescopeTracks,TelescopeTrackLinks,TelescopeTrackAssociations']
    if mille_transport == 'file':
        # Keep the established invocation unchanged unless ZeroMQ is explicit.
        command.insert(command.index(f'-PTelescopeMille:report={p}/mille.json'),
                       f'-PTelescopeMille:output={p}/mille.dat')
    if input_origin=='geant4':command.append('-PTelescopeAudit:inputOrigin=geant4')
    if geometry_aware_holes:command.append('-PTelescopeAudit:geometryAwareHoles=true')
    if cut is not None:command.append(f'-PTelescopeTracking:TelescopeCKF:Chi2CutOff={cut}')
    command.append(source)
    collector = None
    collector_log = None
    collector_terminated = False
    if mille_transport == 'zeromq':
        geometry_id=digest(geometry)
        configuration_id=digest(config)
        stream_id=int(geometry_id[:16],16)
        gate_report = json.loads((gate/'derivatives.json').read_text())
        model_id = gate_report.get('model_sha256')
        if (gate_report.get('passed') is not True or not isinstance(model_id, str)
                or model_id != model_id.lower() or len(model_id) != 64
                or any(character not in '0123456789abcdef' for character in model_id)):
            raise RuntimeError('Derivative gate did not publish a valid Mille model identity')
        ready = p/'mille-collector-ready.json'
        binary = p/'mille.dat'
        collector_report = p/'mille-collector.json'
        collector_command = [collector_executable,
            '--bind','tcp://127.0.0.1:*','--ready',ready,
            '--output',binary,'--report',collector_report,'--records',events,
            '--model-id',model_id,
            '--configuration-id',configuration_id,'--geometry-id',geometry_id,
            '--timeout-ms',MILLE_COLLECTOR_TIMEOUT_MS]
        write_json(p/'mille-collector-command.json',
                   {'argv':list(map(str,collector_command)),'cwd':str(p)})
        collector_log = (p/'mille-collector.stdout.log').open('x')
        try:
            collector = subprocess.Popen(list(map(str,collector_command)),cwd=p,env=env,
                stdout=collector_log,stderr=subprocess.STDOUT)
        except BaseException:
            collector_log.close()
            raise
        try:
            write_json(p/'mille-collector-process.json',
                       {'pid':collector.pid,'process_group':os.getpgrp()})
            endpoint = _wait_collector_ready(ready, collector, {
                'schema': MILLE_COLLECTOR_READY_SCHEMA,
                'pid': collector.pid,
                'expected_records': events,
                'model_id': model_id,
                'configuration_id': configuration_id,
                'geometry_id': geometry_id,
                'output': str(binary),
            }, MILLE_COLLECTOR_TIMEOUT_MS)
            command[-1:-1] = [
                '-PTelescopeMille:outputMode=zeromq',
                f'-PTelescopeMille:endpoint={endpoint}',
                f'-PTelescopeMille:streamId={stream_id}',
                f'-PTelescopeMille:configurationId={configuration_id}',
                f'-PTelescopeMille:geometryId={geometry_id}',
                f'-PTelescopeMille:timeoutMs={MILLE_COLLECTOR_TIMEOUT_MS}',
            ]
            execute(command,p,env=env)
            try:
                collector_code = collector.wait(timeout=MILLE_COLLECTOR_TIMEOUT_MS/1000.+5.)
            except subprocess.TimeoutExpired as error:
                collector_terminated = _stop_collector(collector)
                raise RuntimeError('Mille Collector did not terminate after producer EOS') from error
            if collector_code != 0:
                raise RuntimeError('Mille Collector exited with code ' + str(collector_code))
            collector_receipt = _validate_collector_report(collector_report, binary, events=events,
                stream_id=stream_id, model_id=model_id,
                configuration_id=configuration_id,
                geometry_id=geometry_id)
        finally:
            collector_terminated = _stop_collector(collector) or collector_terminated
            _collector_exit(p/'mille-collector-exit.json',collector,
                            terminated=collector_terminated)
            collector_log.close()
    else:
        execute(command,p,env=env)
    assert digest(source)==before
    audit=json.loads((p/'audit.json').read_text())
    if mille_transport == 'zeromq':
        received=[(item['run_number'],item['event_number'])
                  for item in collector_receipt['events']]
        reconstructed=[(item['run_number'],item['event_number'])
                       for item in audit['events']]
        if received != reconstructed:
            raise RuntimeError('Mille Collector run/event order differs from reconstruction audit')
    payload=channel_values(p/'reco.root');original=channel_values(source)[:events]
    assert payload==original
    done(p,input_sha256=before,channel_payload_sha256=channel_digest(payload),processed=audit['processed'],
         accepted=audit['accepted'],rejected=audit['rejected'],measurement_selector_chi2_cut=cut or 15.,
         mille_transport=mille_transport)

