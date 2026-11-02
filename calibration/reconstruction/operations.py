"""EICrecon execution and immutable readout payload checks."""
import os
import json
import hashlib
import struct
from research_common import REPO, execute, stage, done, digest

def environment(build):
    env=os.environ.copy()
    env['LD_LIBRARY_PATH']=f'{build}:{REPO}/external/install/lib:'+env.get('LD_LIBRARY_PATH','')
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


def reconstruct(build,output,geometry,source,config,events,cut=None,input_origin='synthetic',geometry_aware_holes=False):
    """Run the maintained native chain on one immutable channel file."""
    if input_origin not in ('synthetic','geant4'):
        raise ValueError('Unsupported native input origin')
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
      f'-PTelescopeMille:output={p}/mille.dat',f'-PTelescopeMille:report={p}/mille.json',
      f'-PTelescopeMille:config={config}',
      f'-PTelescopeMille:derivativeGate={gate}/derivatives.json',f'-Ppodio:output_file={p}/reco.root',
      '-Ppodio:output_collections=EventHeader,TelescopeChannels,TelescopeHits,TelescopeHitAssociations,TelescopeMeasurements,TelescopeSeeds,TelescopeSeedParameters,TelescopeTrajectories,TelescopeTrackParameters,TelescopeTracks,TelescopeTrackLinks,TelescopeTrackAssociations']
    if input_origin=='geant4':command.append('-PTelescopeAudit:inputOrigin=geant4')
    if geometry_aware_holes:command.append('-PTelescopeAudit:geometryAwareHoles=true')
    if cut is not None:command.append(f'-PTelescopeTracking:TelescopeCKF:Chi2CutOff={cut}')
    command.append(source)
    execute(command,p,env=env)
    assert digest(source)==before
    audit=json.loads((p/'audit.json').read_text())
    payload=channel_values(p/'reco.root');original=channel_values(source)[:events]
    assert payload==original
    done(p,input_sha256=before,channel_payload_sha256=channel_digest(payload),processed=audit['processed'],
         accepted=audit['accepted'],rejected=audit['rejected'],measurement_selector_chi2_cut=cut or 15.)

