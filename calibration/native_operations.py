"""Shared native calibration operations with explicit Geometry and data inputs.

These operations retain the numerical reconstruction, Mille, Pede and physical
correction implementation used by the independent closure validation driver.
They neither generate channel samples nor inspect injected sensor parameters.
"""
import copy
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

import numpy as np

from research_common import REPO,execute,stage,done,require,write_json,digest
from geometry.transforms import matvec,transpose
from alignment.pede import read_pede
from validation.telescope_alignment.check import records,profile

LABELS=[700102,700202]
MODULE_TO_GLOBAL=((0.,0.,1.),(-1.,0.,0.),(0.,-1.,0.))


class GeometrySafetyError(RuntimeError):
    """A rejected physical candidate with retained diagnostics."""

    def __init__(self,message,diagnostics):
        super().__init__(message)
        self.diagnostics=diagnostics


def validate_geometry(sensors, config=None, *, geometry_xml):
    """Check actual seven-sensor poses against the approved Air-envelope contract."""
    from geometry.native_safety import layer_specs, values, LENGTH_TOLERANCE_MM
    from geometry.placements import xml_number
    diagnostics={'passed':False,'sensor_count':len(sensors),'sensors':{},'failures':[],
                 'envelope_convention':'preserve_clearance_air_growth'}
    def fail(message):diagnostics['failures'].append(message)
    try:
        root=ET.parse(geometry_xml).getroot();specs=layer_specs(root)
        if set(specs)!=set(range(7)):raise ValueError('Expected the seven real telescope layers')
        constants={c.get('name'):c.get('value') for c in root.findall('./define/constant')}
        world_node=root.find('./world')
        if world_node is None or world_node.get('material')!='Air':
            raise ValueError('Air-envelope growth requires the unchanged Air world')
        world=world_node.find('shape')
        if world is None or world.get('type')!='Box':raise ValueError('Expected the telescope Box world')
        world_half=np.array([xml_number(world.get(k),constants) for k in ('dx','dy','dz')])
        if not np.isfinite(world_half).all() or np.any(world_half<=0):raise ValueError('Invalid world bounds')
        diagnostics['world_half_mm']=world_half.tolist()
    except (OSError,ET.ParseError,TypeError,ValueError,KeyError,AttributeError) as error:
        fail('Geometry XML: '+str(error))
        raise GeometrySafetyError('; '.join(diagnostics['failures']),diagnostics) from error
    layers=[s.get('layer') for s in sensors]
    if len(sensors)!=7 or layers!=list(range(7)) or any(type(v) is not int for v in layers):
        fail('sensor identity: expected seven unique ordered layers 0..6')
    for key in ('volume_id','geometry_id'):
        ids=[s.get(key) for s in sensors]
        if any(type(v) is not int or v<=0 for v in ids) or len(set(ids))!=7:
            fail('sensor identity: missing or duplicate '+key)
    by_layer={s.get('layer'):s for s in sensors}
    if config is not None:
        for parameter in config['parameters']:
            sensor=by_layer.get(parameter['layer'])
            if sensor is None or any(sensor.get(k)!=parameter[k] for k in ('volume_id','geometry_id')):
                fail('sensor identity: configuration mismatch on layer '+str(parameter['layer']))
    parent=np.asarray(MODULE_TO_GLOBAL)
    sensor_from_acts=np.array(((0.,0.,-1.),(1.,0.,0.),(0.,-1.,0.)))
    previous_outer=None;diagnostics['layer_radial_gaps_mm']=[]
    for layer in range(7):
        spec=specs[layer];inner,outer,length=spec['tube']
        if previous_outer is not None:
            gap=inner-previous_outer;diagnostics['layer_radial_gaps_mm'].append(float(gap))
            if gap < -LENGTH_TOLERANCE_MM:fail('layer Air envelopes overlap before layer '+str(layer))
        previous_outer=outer
    for sensor in sensors:
        layer=sensor.get('layer')
        if layer not in specs:
            fail('sensor identity: unknown layer '+str(layer));continue
        spec=specs[layer]
        try:
            center=np.asarray(sensor['center_mm'],dtype=float)
            dd=np.asarray(sensor['dd4hep_rotation'],dtype=float)
            acts=np.asarray(sensor['acts_rotation'],dtype=float)
            if center.shape!=(3,) or dd.shape!=(3,3) or acts.shape!=(3,3):raise ValueError('invalid pose dimensions')
            if not all(np.isfinite(v).all() for v in (center,dd,acts)):raise ValueError('nonfinite pose')
            for matrix in (dd,acts):
                if not np.allclose(matrix.T@matrix,np.eye(3),atol=2e-12,rtol=0) or abs(np.linalg.det(matrix)-1.)>2e-12:
                    raise ValueError('pose is not a proper rotation')
            module_center=np.array([spec['radius'],0.,0.])
            if not np.allclose(center,module_center+parent@spec['position'],atol=1e-9,rtol=0):
                raise ValueError('actual DD4hep center disagrees with canonical XML placement')
            if not np.allclose(dd,parent@spec['rotation'],atol=2e-12,rtol=0):
                raise ValueError('actual DD4hep rotation disagrees with XML component chart')
            if not np.allclose(dd,acts@sensor_from_acts,atol=2e-12,rtol=0):
                raise ValueError('DD4hep and ACTS sensor axes disagree')
        except (KeyError,TypeError,ValueError) as error:
            fail('sensor pose: layer '+str(layer)+': '+str(error));continue
        local_center=parent.T@(center-module_center)
        sensor_extent=np.abs(parent.T@dd)@spec['sensor_half']
        module_clearance=spec['module_half']-np.abs(local_center)-sensor_extent
        half_world=np.abs(parent)@spec['module_half']
        inner,outer,length=spec['tube'];radius=spec['radius']
        layer_clearance=np.array([radius-half_world[0]-inner,
            outer-math.hypot(radius+half_world[0],half_world[1]),length/2.-half_world[2]])
        world_clearance=world_half-np.abs(module_center)-half_world
        module_ok=bool(np.all(module_clearance>=-LENGTH_TOLERANCE_MM))
        layer_ok=bool(np.all(layer_clearance>=-LENGTH_TOLERANCE_MM))
        world_ok=bool(np.all(world_clearance>=-LENGTH_TOLERANCE_MM))
        corners=[(center+dd@(np.array(signs)*spec['sensor_half'])).tolist()
                 for signs in itertools.product((-1,1),repeat=3)]
        diagnostics['sensors'][str(layer)]={'module_half_local_mm':spec['module_half'].tolist(),
            'sensor_half_local_mm':spec['sensor_half'].tolist(),'module_clearance_local_mm':module_clearance.tolist(),
            'layer_clearance_inner_outer_z_mm':layer_clearance.tolist(),'world_clearance_mm':world_clearance.tolist(),
            'global_corners_mm':corners,'module_contained':module_ok,'layer_contained':layer_ok,'world_contained':world_ok}
        if not module_ok:fail('module containment failure on layer '+str(layer))
        if not layer_ok:fail('layer containment failure on layer '+str(layer))
        if not world_ok:fail('world containment failure on layer '+str(layer))
    diagnostics['passed']=not diagnostics['failures']
    if not diagnostics['passed']:raise GeometrySafetyError('; '.join(diagnostics['failures']),diagnostics)
    return diagnostics



def geometry_audit(build,output,xml,config=None):
    """Reload real DD4hep/ACTS Geometry without generating any events."""
    p=stage(output.parent,output.name)
    execute([build/'TelescopeChannels',xml,0,p/'geometry.json'],p,env=environment(build))
    report=json.loads((p/'geometry.json').read_text())
    try:
        if not report.get('passed') or report.get('events')!=[]:
            raise GeometrySafetyError('Geometry audit did not return a zero-event success',
                                      {'passed':False,'failures':['invalid Geometry audit output']})
        diagnostics = validate_geometry(
            report['sensors'], config, geometry_xml=xml
        )
        physical=stage(p,'physical')
        execute([build/'TelescopePhysicalGeometryAudit',xml,physical/'geometry.json'],physical,env=environment(build))
        if not json.loads((physical/'geometry.json').read_text()).get('passed'):
            raise GeometrySafetyError('Actual DD4hep/ACTS physical Geometry audit failed',
                                      {'passed':False,'failures':['physical Geometry audit failed']})
        done(physical,passed=True,geometry_sha256=digest(xml))
    except GeometrySafetyError as error:
        write_json(p/'geometry_safety.json',error.diagnostics)
        raise
    write_json(p/'geometry_safety.json',diagnostics)
    done(p,passed=True,geometry_sha256=digest(xml))
    return report


def _xml_identity(parent,candidate,selected,mode):
    """Retain the selected-node whitelist and full XML comparison."""
    from validation.telescope_closure import multi_sensor as multi
    from validation.telescope_closure.rotation import axis
    if multi.is_multi(mode):
        return multi.xml_identity(parent,candidate,selected)
    def structure(n):return n.tag,dict(n.attrib),(n.text or '').strip(),tuple(structure(c) for c in n)
    original_xml=ET.parse(parent).getroot();candidate_xml=ET.parse(candidate).getroot()
    original=original_xml.find("./detectors/detector[@name='SiMAPS']")
    detector=candidate_xml.find("./detectors/detector[@name='SiMAPS']")
    for layer in ([1,2] if mode in ('l1_l2_y','l1_y_rx_l2_y') else [1]):
        a=original.find(f"./layer[@id='{layer}']");b=detector.find(f"./layer[@id='{layer}']")
        module=detector.find(f"./module[@name='{b.get('module')}']")
        reference=original.find(f"./module[@name='{a.get('module')}']")
        normalized=copy.deepcopy(module);normalized.set('name',reference.get('name'))
        multi.normalize_expanded_envelopes(reference, normalized, a, b)
        normalized.find('./module_component/position').set('x',reference.find('./module_component/position').get('x'))
        if mode=='l1_l2_y':
            from geometry.placements import xml_number
            position=normalized.find('./module_component/position')
            original_position=reference.find('./module_component/position')
            for attribute in ('y','z'):
                # The unchanged translation serializer spells 0 as 0*mm.
                # Require exact physical equality before normalizing units.
                assert xml_number(position.get(attribute),{})==xml_number(original_position.get(attribute),{}), 'Nonselected component position changed'
                position.set(attribute,original_position.get(attribute))
        if layer==1 and mode!='l1_l2_y':
            attributes='xyz' if axis(mode) in ('z','yz','xyz') else ('x' if axis(mode)=='y' else 'y')
            for attribute in attributes:
                normalized.find('./module_component/rotation').set(attribute,reference.find('./module_component/rotation').get(attribute))

        assert structure(normalized)==structure(reference)
        detector.remove(module);b.set('module',a.get('module'))
    assert structure(candidate_xml)==structure(original_xml)


def correct(build,output,current_xml,current_sensors,parameters,mode):
    """Write and validate one candidate XML; the caller seals its version.

    The output is the new XML filename in a fresh correction directory.
    """
    from validation.telescope_closure import multi_sensor as multi
    from validation.telescope_closure.rotation import axis,is_coupled,labels,rotation_label,rotation_labels,pose_checks
    selected=LABELS if mode=='l1_l2_y' else labels(mode)
    if set(parameters)!=set(selected):
        raise ValueError('Pede parameters do not match the selected alignment configuration')
    config=multi.configuration(mode) if multi.is_multi(mode) else None
    validate_geometry(
        current_sensors, config, geometry_xml=current_xml
    )
    shifts={l:parameters[k] for l,k in [(1,700102),(2,700202)] if k in parameters}
    if mode=='l1_l2_y':rotations=None
    elif multi.is_multi(mode):shifts,rotations=multi.pose_increments(parameters)
    else:rotations={1:[parameters[k] for k in rotation_labels(mode)] if is_coupled(mode) else parameters[rotation_label(mode)]}
    write_json(output.parent/'parameters.json',{'parameters':parameters,'selected_labels':selected})
    displaced_xml(current_xml,output,shifts,rotations=rotations,rotation_axis=axis(mode),
                  allowed_layers=multi.aligned_sensors(mode) if multi.is_multi(mode) else (1,2))
    _xml_identity(current_xml,output,selected,mode)
    report=geometry_audit(build,output.parent/'geometry_audit',output,config=config)
    pose_checks(current_sensors,report['sensors'],shifts,rotations or {},axis=axis(mode))
    return {'shifts':shifts,'rotations':rotations,'rotation_axis':axis(mode),'sensors':report['sensors']}


def displaced_xml(nominal,output,shifts,rotations=None,rotation_axis='x',allowed_layers=(1,2)):
    """Apply selected physical Y shifts and center-preserving rotations."""
    if output.exists():raise ValueError('Geometry output must be new')
    root=ET.parse(nominal).getroot()
    detector=root.find("./detectors/detector[@name='SiMAPS']")
    for layer in sorted(set(shifts)|set(rotations or {})):
        delta=shifts.get(layer,0.)
        if int(layer) not in allowed_layers or int(layer) not in (1,2,3) or not math.isfinite(delta):
            raise ValueError('Only finite explicitly selected SiMAPS Global Y supported')
        placement=detector.find(f"./layer[@id='{int(layer)}']")
        original=detector.find(f"./module[@name='{placement.get('module')}']")
        module=copy.deepcopy(original);module.set('name',original.get('name')+f'_L{layer}')
        component=module.find('./module_component')
        position=component.find('position')
        offset=matvec(transpose(MODULE_TO_GLOBAL),(0.,delta,0.))
        for axis,value in zip('xyz',offset):
            # Rotation fixtures preserve untouched XML attributes exactly.
            # The original translation-only serialization remains unchanged.
            if rotations is not None and value==0:continue
            current=float(position.get(axis,'0').replace('*mm',''))
            position.set(axis,f'{current+value:.17g}*mm')
        # Reject changes to sensor shape, material, rotation, or segmentation.
        original_component=copy.deepcopy(original.find('./module_component'))
        comparison=copy.deepcopy(component)
        comparison.remove(comparison.find('position'));original_component.remove(original_component.find('position'))
        assert ET.tostring(comparison)==ET.tostring(original_component)
        if rotations and layer in rotations:
            from validation.telescope_closure.rotation import rotate_component
            rotate_component(component,rotations[layer],axis=rotation_axis)
        expand_sensor_envelopes(original, module, placement)

        detector.append(module);placement.set('module',module.get('name'))

    ET.indent(root);ET.ElementTree(root).write(output,encoding='unicode')

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



def solve(build,output,reconstruction,mode,events,diagnostic=False):
    """Run unchanged profiling and official Pede after the caller's FD gate."""
    from validation.telescope_closure.multi_sensor import is_multi,mapping
    from calibration.native_policy import require_precision
    quality_warnings=[]
    def precision(passed,message):
        require_precision(passed,message,diagnostic,quality_warnings)
    rotation=mode!='l1_l2_y'
    selected=LABELS
    if rotation:
        from validation.telescope_closure.rotation import labels
        selected=labels(mode)
    require(reconstruction.parent,reconstruction.name);p=stage(output.parent,output.name)
    binary=reconstruction/'mille.dat'
    blocks=list(records(binary));assert len(blocks)==events and all(len(b)==10 for b in blocks)
    import numpy as np
    dimension=len(selected)
    normal=np.zeros((dimension,dimension));rhs=np.zeros(dimension)
    for block in blocks:
        a,b,_=profile(block,selected,require_global_rank=not is_multi(mode) and mode not in ('l1_ry_rz','l1_ry_rz_negative','l1_rx_ry_rz','l1_rx_ry_rz_negative','l1_y_rx_ry_rz','l1_y_rx_ry_rz_negative','l1_y_rx_ry_rz_contained','l1_y_rx_ry_rz_contained_negative'));normal+=a;rhs+=b
    assert np.linalg.matrix_rank(normal)==dimension
    if mode in ('l1_ry','l1_ry_negative','l1_rz','l1_rz_negative','l1_ry_rz','l1_ry_rz_negative','l1_rx_ry_rz','l1_rx_ry_rz_negative','l1_y_rx_ry_rz','l1_y_rx_ry_rz_negative','l1_y_rx_ry_rz_contained','l1_y_rx_ry_rz_contained_negative'):
        # Refuse to run Pede before the actual native design resolves this tilt.
        # Include transverse translations and other rotations as diagnostic nuisance
        # columns; none of these additional columns is enabled in the Mille fit.
        from validation.telescope_closure.observability import analyze
        design=analyze(reconstruction/'mille.json')
        last=design['prefix_designs'][str(events)]
        assert last['rank']==5 and last['diagonal_scaled_condition']<100
        from validation.telescope_closure.rotation import axis,is_coupled,is_three,is_four
        index=3 if axis(mode)=='y' else 4
        write_json(p/'observability.json',design)
        if is_four(mode):
            selected_design=last['selected_Y_Rx_Ry_Rz']
            assert selected_design['rank']==4 and selected_design['diagonal_scaled_condition']<100
            precision(selected_design['sigma'][0]<.001 and max(selected_design['sigma'][1:])<.0005,'Selected translation/rotation resolution target not met')
            full=design['all_translation_diagnostic']
            assert full['rank']==6 and full['diagonal_scaled_condition']<100
            precision(full['sigma'][1]<.001 and max(full['sigma'][3:])<.0005,'Extended translation/rotation resolution target not met')
        elif is_three(mode):
            precision(max(last['sigma'][2:5])<.0005,'Three-axis rotation resolution target not met')
            precision(max(design['all_translation_diagnostic']['sigma'][3:6])<.0005,'Extended three-axis resolution target not met')
            assert last['selected_Rx_Ry_Rz']['rank']==3 and last['selected_Rx_Ry_Rz']['diagonal_scaled_condition']<100
        elif is_coupled(mode):
            precision(max(last['sigma'][3:5])<.0005,'Coupled tilt resolution target not met')
            precision(max(design['all_translation_diagnostic']['sigma'][4:6])<.0005,'Extended coupled tilt resolution target not met')
        else:precision(last['sigma'][index]<.001,'Selected tilt resolution target not met')
    if is_multi(mode):
        from validation.telescope_closure.multi_information import analyze
        design=analyze(reconstruction/'mille.json',selected)
        write_json(p/'observability.json',design)
        assert design['fixed_reference_selected_design_full_rank'] and design['fixed_reference_rows_have_no_selected_global_columns']
        assert design['released_reference_gauge_diagnostic']['common_rigid_gauge_is_null']
        actual=design['selected']
        assert np.allclose(normal,actual['information'],rtol=1e-10,atol=1e-6),'Mille/native profiled information mismatch'
        assert actual['rank']==dimension and actual['diagonal_scaled_condition']<100
        precision(all(v<(.001 if mapping(k)[1]==1 else .0005) for k,v in zip(selected,actual['sigma'])),'Selected resolution target not met')
        full=design['omitted_X_Z_diagnostic']
        layers=sorted({mapping(k)[0] for k in selected})
        # A one-translation example need not identify unrequested rotations.
        # Existing multi-DOF scopes retain their established nuisance-rank gate.
        if mode!='l1_y':
            assert full['rank']==6*len(layers) and full['diagonal_scaled_condition']<100
            precision(all(full['sigma'][6*layers.index(mapping(k)[0])+mapping(k)[1]]<(.001 if mapping(k)[1]==1 else .0005) for k in selected),'Selected resolution target not met with diagnostic nuisance columns')

    text=f'Cfiles\n{binary}\n\nentries 5\nmethod inversion 3 0.001\n\nParameter\n700102 0.0 1.0\n700202 0.0 1.0\n\nprintcounts\nend\n'
    if rotation:
        text=f'Cfiles\n{binary}\n\nentries 5\nmethod inversion 3 0.001\n\nParameter\n'+''.join(f'{label} 0.0 1.0\n' for label in selected)+'\nprintcounts\nend\n'
    (p/'pede.txt').write_text(text)
    execute([REPO/'external/install/bin/pede','pede.txt'],p,env=environment(build))
    status=(p/'millepede.end').read_text().strip()
    if status.split()[0] not in ('0','1'):raise ValueError('Pede failed: '+status)
    solver_diagnostic=None
    if diagnostic:
        from calibration.native_policy import solver_diagnostics
        solver_diagnostic=solver_diagnostics((p/'stdout.log').read_text(),status,diagnostic=True)
    parameters=read_pede(p/'millepede.res')
    expected=np.linalg.solve(normal,rhs)
    if rotation:
        from decimal import Decimal
        printed={int(row.split()[0]):row.split() for row in (p/'millepede.res').read_text().splitlines() if row.split() and row.split()[0].isdigit()}
        for k,value in zip(selected,expected):
            printed_resolution=10.**Decimal(printed[k][1]).as_tuple().exponent
            assert abs(parameters[k]-value)<.51*printed_resolution+1e-10,'Pede/independent solution beyond printed precision'
    else:
        assert np.allclose([parameters[k] for k in selected],expected,atol=6e-6)
    log=(p/'millepede.log').read_text();assert 'No rank defect of the symmetric matrix' in log
    covariance=np.linalg.inv(normal)
    if rotation:
        sigma=np.sqrt(np.diag(covariance))
        reported=np.array([float(printed[k][4]) for k in selected])
        if is_multi(mode):
            presigmas=np.array([float(printed[k][2]) for k in selected])
            assert np.all(presigmas==1.),'Maintained official parameter pre-sigmas changed'
            stabilized_covariance=np.linalg.inv(normal+np.diag(1/presigmas**2))
            assert np.allclose(reported,np.sqrt(np.diag(stabilized_covariance)),rtol=5e-5,atol=0),'Pede inversion errors differ from retained diagonal stabilization'
        else:
            assert np.allclose(reported, sigma, rtol=5e-5, atol=0), 'Pede nominal errors differ from unscaled information'

        correlation=covariance/np.outer(sigma,sigma)
        scaled=normal/np.sqrt(np.outer(np.diag(normal),np.diag(normal)))
        assert np.linalg.cond(scaled)<100,'Insufficient selected information'
        assert np.isfinite(expected).all() and np.isfinite(sigma).all() and np.all(sigma>0),'Invalid selected estimate/covariance'
        precision(all(v<(.001 if k%100 in (4,5,6) else .02) for k,v in zip(selected,sigma)),'Angular/linear resolution target not met')
        write_json(p/'independent.json',{'parameters':parameters,'schur_solution':expected.tolist(),
          'parameter_units':{k:('rad' if k%100 in (4,5,6) else 'mm') for k in selected},'nominal_sigma':sigma.tolist(),'pede_reported_sigma':reported.tolist(),
          'rank':dimension,'condition':float(np.linalg.cond(normal)),'condition_diagonal_scaled':float(np.linalg.cond(scaled)),
          'correlation':correlation.tolist(),'normal':normal.tolist(),'status':status,'records':len(blocks),
          **({'validation_mode':'diagnostic','quality_warnings':quality_warnings,'solver_diagnostics':solver_diagnostic,'error_rescaling_applied':False} if diagnostic else {}),
          **({'covariance_source':'Inverse data-only locally profiled native information; no stabilization or prior',
              'pede_presigma':{k:float(printed[k][2]) for k in selected},
              'pede_expected_stabilized_covariance_mixed_units':stabilized_covariance.tolist(),
              'pede_regularization_enabled':False,
              'pede_error_note':'Data-only formal covariance is inverse(H). Pede-reported errors are checked against inverse(H+diag(presigma^-2)); equality with data-only errors is not assumed. Positive presigmas retain the existing diagonal stabilization, with no regularization objective enabled and no empirical error rescaling.'} if is_multi(mode) else {})})
    else:
        write_json(p/'independent.json',{'parameters_mm':parameters,'schur_solution_mm':expected.tolist(),
          'nominal_sigma_mm':np.sqrt(np.diag(covariance)).tolist(),'rank':2,'condition':float(np.linalg.cond(normal)),
          'normal':normal.tolist(),'status':status,'records':len(blocks)})
    done(p,records=len(blocks),pede_status=status)


def expand_sensor_envelopes(original_module, updated_module, layer):
    """Grow air envelopes to contain the corrected sensitive component.

    Preserve the original clearance and never change the sensitive
    component dimensions, material, segmentation or placement.
    """
    from geometry.placements import xml_number
    from geometry.native_safety import canonical_layer
    from validation.telescope_closure.rotation import component_matrix
    canonical_layer(original_module, layer)
    canonical_layer(updated_module, layer)

    def values(node, names):
        return np.array(
            [xml_number(node.get(name), {}) for name in names],
            dtype=float
        )

    def required_half_size(component):
        dimensions = values(
            component, ("width", "length", "thickness")
        ) / 2.0
        position = values(
            component.find("position"), ("x", "y", "z")
        )
        rotation = component_matrix(component)
        return np.abs(rotation) @ dimensions + np.abs(position)

    old_component = original_module.find("module_component")
    new_component = updated_module.find("module_component")

    old_envelope = original_module.find("module_envelope")
    new_envelope = updated_module.find("module_envelope")

    names = ("width", "length", "thickness")
    old_half = values(old_envelope, names) / 2.0

    old_required = required_half_size(old_component)
    new_required = required_half_size(new_component)

    # Retain the original clearance. Do not introduce an angle cut.
    clearance = np.maximum(old_half - old_required, 0.0)
    new_half = np.maximum(old_half, new_required + clearance)

    if not np.isfinite(new_half).all():
        raise ValueError("Invalid corrected envelope dimensions")

    for name, old, new in zip(names, old_half, new_half):
        if new > old + 1e-12:
            new_envelope.set(name, f"{2.0 * new:.17g}*mm")

    # The barrel layer must also contain the enlarged module box.
    module_to_global = np.asarray(MODULE_TO_GLOBAL, dtype=float)
    old_world = np.abs(module_to_global) @ old_half
    new_world = np.abs(module_to_global) @ new_half

    radius = xml_number(layer.find("rphi_layout").get("rc"), {})
    barrel = layer.find("barrel_envelope")

    old_inner = xml_number(barrel.get("inner_r"), {})
    old_outer = xml_number(barrel.get("outer_r"), {})
    old_z = xml_number(barrel.get("z_length"), {})

    inner_margin = max(radius - old_world[0] - old_inner, 0.0)
    outer_margin = max(
        old_outer - math.hypot(radius + old_world[0], old_world[1]),
        0.0
    )
    z_margin = max(old_z / 2.0 - old_world[2], 0.0)

    new_inner = min(
        old_inner, radius - new_world[0] - inner_margin
    )
    new_outer = max(
        old_outer,
        math.hypot(radius + new_world[0], new_world[1])
        + outer_margin
    )
    new_z = max(old_z, 2.0 * (new_world[2] + z_margin))

    # Only rewrite dimensions that actually need to grow.
    if new_inner < old_inner - 1e-12:
        barrel.set("inner_r", f"{new_inner:.17g}*mm")
    if new_outer > old_outer + 1e-12:
        barrel.set("outer_r", f"{new_outer:.17g}*mm")
    if new_z > old_z + 1e-12:
        barrel.set("z_length", f"{new_z:.17g}*mm")


def physical_derivatives(build,output,reconstruction,geometry,base_sensors,mode):
    """Validate selected native columns with the same real-Geometry FD loops."""
    from validation.telescope_closure import multi_sensor as multi
    from validation.telescope_closure.rotation import is_four,is_three,labels,axis,pose_checks
    p=stage(output.parent,output.name)
    native=json.loads((reconstruction/'mille.json').read_text())
    validate_geometry(base_sensors, geometry_xml=geometry)
    base=base_sensors;errors={};translation_errors={};checks=0
    columns=(1,3,4,5) if is_four(mode) else ((3,4,5) if is_three(mode) else (4,5))
    selected_columns=[multi.mapping(k) for k in labels(mode)] if multi.is_multi(mode) else [(1,c) for c in columns]
    for layer,column in selected_columns:
        for step in ((1e-3,1e-4,1e-5) if column==1 else (1e-4,1e-5,1e-6)):
            poses=[]
            for sign in (1,-1):
                delta=[0.]*(3 if is_three(mode) or multi.is_multi(mode) else 2)
                if column!=1:delta[column-(3 if is_three(mode) or multi.is_multi(mode) else 4)]=sign*step
                shifts={layer:sign*step} if column==1 else {}
                rotations=None if column==1 else {layer:delta}
                q=stage(p,(f'layer_{layer}_' if multi.is_multi(mode) else '')+f'column_{column}_step_{step:g}_{sign:+d}')
                displaced_xml(geometry,q/'geometry.xml',shifts,rotations=rotations,rotation_axis=axis(mode),allowed_layers=multi.aligned_sensors(mode) if multi.is_multi(mode) else (1,2))
                execute([build/'TelescopeChannels',q/'geometry.xml',0,q/'geometry.json'],q,env=environment(build))
                sensors = json.loads((q/'geometry.json').read_text())['sensors']
                # Check the envelope of this perturbed geometry.
                validate_geometry(sensors, geometry_xml=q/'geometry.xml')

                pose_checks(
                    base, sensors, shifts,
                    {} if column == 1 else {layer: delta},
                    axis=axis(mode)
                )
                poses.append(sensors[layer])
            error=0.
            for event in native['events']:
                point=np.array(event['reference_position_mm']);direction=np.array(event['reference_direction']);predictions=[]
                for sensor in poses:
                    r=np.array(sensor['acts_rotation']);center=np.array(sensor['center_mm']);normal=r[:,2]
                    intersection=point+direction*(normal@(center-point))/(normal@direction)
                    predictions.append((r.T@(intersection-center))[:2])
                finite=(predictions[0]-predictions[1])/(2*step)
                equation=next(e for e in event['equations'] if e['layer']==layer)
                error=max(error,float(np.max(np.abs(finite-np.array(equation['global'])[:,column]))));checks+=1
            assert error<2e-5,(column,step,error)
            (translation_errors if column==1 else errors)[(f'{layer}_' if multi.is_multi(mode) else '')+f'{column}_{step}']=error
    report={'passed':True,'checks':checks,'errors_mm_per_rad':errors,
        'maximum_error_mm_per_rad':max(errors.values()) if errors else None,'model_sha256':native['model_sha256'],
        'method':'independent actual DD4hep/ACTS perturbations; selected left-Global columns, original native reference'}
    if is_four(mode) or multi.is_multi(mode):report.update(translation_errors_mm_per_mm=translation_errors,maximum_translation_error_mm_per_mm=max(translation_errors.values()))
    write_json(p/'report.json',report)
    done(p,passed=True)

