"""Selected native rotation closure gates, not a general alignment framework."""
import json
import re
import copy
import xml.etree.ElementTree as ET
import math
import numpy as np
from geometry.transforms import rotation_exp,rotation_log,multiply,transpose
from geometry.placements import xml_number
from research_common import stage,execute,digest,require,done,write_json

from validation.telescope_closure import multi_sensor as multi
from calibration.alignment.modes import is_four, is_three, rotation_labels, is_coupled, axis, rotation_label, labels, config_name
from geometry.rotations import rotate_component, pose_checks, component_matrix, rotate_coupled_component

INJECTED_RX_RAD=.0018
INJECTED_RY_RAD=.005
INJECTED_RZ_RAD=.005








def fixture_shifts(mode):
    return {1:.150} if is_four(mode) else ({1:.150,2:.200} if mode=='l1_y_rx_l2_y' else {})




def injected_vector(mode):
    return injected_rotation(mode) if is_three(mode) else [0.,*injected_rotation(mode)]






def injected_rx(mode):
    return -INJECTED_RX_RAD if mode=='l1_rx_negative' else INJECTED_RX_RAD


def injected_rotation(mode):
    if is_three(mode):
        vector=[.0018,.001,-.001] if '_contained' in mode else [.0018,.0025,-.0025]
        return [-v for v in vector] if mode.endswith('_negative') else vector
    if is_coupled(mode):return [-.0025,.0025] if mode.endswith('_negative') else [.0025,-.0025]
    if axis(mode)=='x':return injected_rx(mode)
    magnitude=INJECTED_RY_RAD if axis(mode)=='y' else INJECTED_RZ_RAD
    return -magnitude if mode.endswith('_negative') else magnitude










def physical_derivatives(args,reconstruction="nominal"):
    """Frozen native reference against fresh real DD4hep/ACTS surface perturbations."""
    if is_coupled(args.mode):return coupled_physical_derivatives(args,reconstruction)
    from validation.telescope_closure.closure import displaced_xml,environment
    p=stage(args.result,'physical_derivatives' if reconstruction=='nominal' else ('refined_physical_derivatives' if reconstruction=='refined' else 'corrected_physical_derivatives'))
    native=json.loads((args.result/reconstruction/'mille.json').read_text())
    geometry=args.result/('geometry/fixtures/nominal.xml' if reconstruction=='nominal' else ('refinement/refined.xml' if reconstruction=='refined' else 'correction/corrected.xml'))
    base_path=args.result/('data/control/channels_truth.json' if reconstruction=='nominal' else ('refinement/geometry_audit/geometry.json' if reconstruction=='refined' else 'correction/geometry_audit/geometry.json'))
    base=json.loads(base_path.read_text())['sensors']
    maximum=0.;checks=0;errors={}
    for step in (1e-4,1e-5,1e-6):
        poses=[]
        for sign in (1,-1):
            q=stage(p,f'step_{step:g}_{sign:+d}')
            displaced_xml(geometry,q/'geometry.xml',{},rotations={1:sign*step},rotation_axis=axis(args.mode))
            execute([args.build/'TelescopeChannels',q/'geometry.xml',0,q/'geometry.json'],q,env=environment(args.build))
            sensors=json.loads((q/'geometry.json').read_text())['sensors']
            pose_checks(base,sensors,{}, {1:sign*step},axis=axis(args.mode))
            poses.append(sensors[1])
        error=0.
        for event in native['events']:
            point=np.array(event['reference_position_mm']);direction=np.array(event['reference_direction'])
            predictions=[]
            for sensor in poses:
                r=np.array(sensor['acts_rotation']);center=np.array(sensor['center_mm']);normal=r[:,2]
                intersection=point+direction*(normal@(center-point))/(normal@direction)
                predictions.append((r.T@(intersection-center))[:2])
            finite=(predictions[0]-predictions[1])/(2*step)
            equation=next(e for e in event['equations'] if e['layer']==1)
            error=max(error,float(np.max(np.abs(finite-np.array(equation['global'])[:,3+'xyz'.index(axis(args.mode))]))));checks+=1
        assert error<2e-5,(step,error)
        errors[str(step)]=error;maximum=max(maximum,error)
    write_json(p/'report.json',{'passed':True,'checks':checks,'errors_mm_per_rad':errors,
      'maximum_error_mm_per_rad':maximum,'method':'frozen actual native reference, independently perturbed real DD4hep surfaces',
      'model_sha256':native['model_sha256'],'residual_convention':'measured-predicted = A*localDelta + G*physicalDelta'})
    done(p,passed=True)


def correct(args):
    from validation.telescope_closure.closure import displaced_xml,environment
    from calibration.alignment.pede import read_pede
    require(args.result,'solve');p=stage(args.result,'correction')
    values=read_pede(args.result/'solve/millepede.res')
    shifts={l:values[k] for l,k in [(1,700102),(2,700202)] if k in values}
    rotations=({} if multi.is_multi(args.mode) else {1:[values[k] for k in rotation_labels(args.mode)] if is_coupled(args.mode) else values[rotation_label(args.mode)]})
    if multi.is_multi(args.mode):shifts,rotations=multi.pose_increments(values)
    nominal=args.result/'geometry/fixtures/nominal.xml'
    from geometry.operations import correct as operation, GeometrySafetyError
    base=json.loads((args.result/'data/control/channels_truth.json').read_text())['sensors']
    try:operation(args.build,p/'corrected.xml',nominal,base,values,args.mode)
    except GeometrySafetyError as error:raise AssertionError(str(error)) from error
    write_json(p/'provenance.json',{'parent_sha256':digest(nominal),'pede_sha256':digest(args.result/'solve/millepede.res'),
       'physical_global_y_mm':shifts,'physical_global_r'+axis(args.mode)+'_rad':rotations,'pivot':'current sensitive center',
       'composition':('per-sensor cnew=ccurrent+(0,dY,0); Rnew=Exp([dRx,dRy,dRz]) Rcurrent; current sensitive-center pivot' if multi.is_multi(args.mode) else (('Rnew=Exp([dRx,dRy,dRz]) Rcurrent; bounded L1 full rotation-vector update' if is_three(args.mode) else 'Rnew=Exp([0,dRy,dRz]) Rcurrent; current full pose, not a closed two-axis subgroup') if is_coupled(args.mode) else 'Rnew=Exp(dw_global) Rcurrent; selected '+axis(args.mode)+' subgroup')),'truth_used':False,'activated':False})
    done(p,physical_global_y_mm=shifts,physical_rotation_rad=rotations,rotation_axis=axis(args.mode))


def refine(args):
    """One explicitly requested finite relinearization; never a Controller loop."""
    from validation.telescope_closure.closure import displaced_xml,environment
    from calibration.alignment.pede import read_pede
    if not (is_four(args.mode) or multi.is_multi(args.mode)) or not args.refine_once:raise ValueError('One refinement requires the explicit four-parameter opt-in')
    require(args.result,'postsolve');require(args.result,'correction')
    p=stage(args.result,'refinement');values=read_pede(args.result/'postsolve/millepede.res')
    if multi.is_multi(args.mode):shifts,rotations=multi.pose_increments(values)
    else:shifts={1:values[700102]};rotations={1:[values[k] for k in rotation_labels(args.mode)]}
    parent=args.result/'correction/corrected.xml'
    from geometry.operations import correct as operation, GeometrySafetyError
    previous=json.loads((args.result/'correction/geometry_audit/geometry.json').read_text())['sensors']
    try:operation(args.build,p/'refined.xml',parent,previous,values,args.mode)
    except GeometrySafetyError as error:raise AssertionError(str(error)) from error
    first=json.loads((args.result/'solve/independent.json').read_text());selected=labels(args.mode)
    thresholds={k:(2e-5 if k%100==2 else first['nominal_sigma'][selected.index(k)]/4) for k in selected}
    write_json(p/'provenance.json',{'parent_sha256':digest(parent),'pede_sha256':digest(args.result/'postsolve/millepede.res'),
        'increments':values,'units':first['parameter_units'],'initial_one_step_thresholds':thresholds,
        'initial_one_step_post_gate_passed':all(abs(values[k])<thresholds[k] for k in selected),
        'input_sha256':digest(args.result/'data/shifted.root'),'refinement_count':1,'maximum_refinements':1,
        'composition':'c_new=c_current+(0,dY,0); R_new=Exp(dw_global) R_current',
        'truth_used_for_inference':False,'geometry_activated':False})
    done(p,passed=True,refinement_count=1)


def check(args):
    import ROOT
    from podio import reading
    from validation.telescope_closure.closure import channel_values,channel_digest
    from calibration.alignment.mille import records,profile
    from calibration.alignment.pede import read_pede
    for name in ('geometry','data','nominal','physical_derivatives','corrected_physical_derivatives','solve','correction','corrected','postsolve','control','controlsolve'):require(args.result,name)
    p=stage(args.result,args.validation_name)
    refined=getattr(args,'refine_once',False)
    if refined:
        assert is_four(args.mode) or multi.is_multi(args.mode)
        for name in ('refinement','refined','refined_physical_derivatives','refinesolve'):require(args.result,name)
    final='refined' if refined else 'corrected'
    final_solver='refinesolve' if refined else 'postsolve'
    paths={name:args.result/(final if name=='corrected' else name) for name in ('nominal','corrected','control')}
    if refined:paths['first_corrected']=args.result/'corrected'
    selected=labels(args.mode);combined=args.mode=='l1_y_rx_l2_y'
    injected=multi.injection(args.mode) if multi.is_multi(args.mode) else (dict(zip(rotation_labels(args.mode),injected_rotation(args.mode))) if is_coupled(args.mode) else {rotation_label(args.mode):injected_rotation(args.mode)})
    if combined:injected.update({700102:.150,700202:.200})
    if is_four(args.mode):injected[700102]=.150
    truth=json.loads((args.result/'data/shifted/channels_truth.json').read_text())
    base=json.loads((args.result/'data/control/channels_truth.json').read_text())
    if multi.is_multi(args.mode):
        dy,dw=multi.fixture(args.mode);pose_checks(base['sensors'],truth['sensors'],dy,dw,axis='xyz')
    else:pose_checks(base['sensors'],truth['sensors'],fixture_shifts(args.mode),{1:injected_rotation(args.mode)},axis=axis(args.mode))
    # Independent XML whitelist: only selected module references, component X
    # translation and selected sensor orientation serialization may vary.
    def structure(n):return n.tag,dict(n.attrib),(n.text or '').strip(),tuple(structure(c) for c in n)
    nominal_xml=ET.parse(args.result/'geometry/fixtures/nominal.xml').getroot()
    pairs=[(args.result/'geometry/fixtures/nominal.xml',args.result/'geometry/truth.xml'),(args.result/'geometry/fixtures/nominal.xml',args.result/'correction/corrected.xml')]
    if refined:pairs.append((args.result/'correction/corrected.xml',args.result/'refinement/refined.xml'))
    for parent,xml in pairs:
        if multi.is_multi(args.mode):
            multi.xml_identity(parent,xml,selected)
            continue
        nominal_xml=ET.parse(parent).getroot()
        candidate=ET.parse(xml).getroot();detector=candidate.find("./detectors/detector[@name='SiMAPS']")
        original=nominal_xml.find("./detectors/detector[@name='SiMAPS']")
        for layer in ([1,2] if combined else [1]):
            a=original.find(f"./layer[@id='{layer}']");b=detector.find(f"./layer[@id='{layer}']")
            module=detector.find(f"./module[@name='{b.get('module')}']")
            reference=original.find(f"./module[@name='{a.get('module')}']")
            normalized=copy.deepcopy(module);normalized.set('name',reference.get('name'))
            multi.normalize_expanded_envelopes(reference,normalized,a,b)
            normalized.find('./module_component/position').set('x',reference.find('./module_component/position').get('x'))
            if layer==1:
                attributes='xyz' if axis(args.mode) in ('z','yz','xyz') else ('x' if axis(args.mode)=='y' else 'y')
                for attribute in attributes:
                    normalized.find('./module_component/rotation').set(attribute,reference.find('./module_component/rotation').get(attribute))
            assert structure(normalized)==structure(reference)
            detector.remove(module);b.set('module',a.get('module'))
        assert structure(candidate)==structure(nominal_xml)
    solution=json.loads((args.result/'solve/independent.json').read_text())
    control_solution=json.loads((args.result/'controlsolve/independent.json').read_text())
    values=read_pede(args.result/'solve/millepede.res');post=read_pede(args.result/final_solver/'millepede.res')
    actual=json.loads((args.result/('refinement/geometry_audit/geometry.json' if refined else 'correction/geometry_audit/geometry.json')).read_text())['sensors']
    # Geometry tolerance is predeclared as five original-covariance nominal errors,
    # separately from the much tighter actual pose serialization consistency gate.
    tolerances={k:5*solution['nominal_sigma'][i] for i,k in enumerate(selected)}
    for i,k in enumerate(selected):
        assert abs(values[k]-injected[k])<tolerances[k]
        assert abs(control_solution['parameters'][str(k)])<5*control_solution['nominal_sigma'][i]
        assert abs(post[k])<(2e-5 if (is_four(args.mode) or multi.is_multi(args.mode)) and k%100==2 else (solution['nominal_sigma'][i]/4 if axis(args.mode) in ('y','z','yz','xyz') else 2e-5)),(k,post[k])
    from geometry.transforms import orientation_distance
    angle_error=orientation_distance(truth['sensors'][1]['acts_rotation'],actual[1]['acts_rotation'])
    sensor_errors={}
    if is_coupled(args.mode):
        covariance=np.linalg.inv(np.array(solution['normal']))
        error=np.array([values[k]-injected[k] for k in selected])
        assert error@np.linalg.solve(covariance,error)<25
        if multi.is_multi(args.mode):
            shifts,rotations=multi.fixture(args.mode)
            for layer in multi.aligned_sensors(args.mode):
                angular=[i for i,k in enumerate(selected) if multi.mapping(k)[0]==layer and multi.mapping(k)[1]>=3]
                bias=np.array(actual[layer]['center_mm'])-truth['sensors'][layer]['center_mm']
                assert abs(bias[1])<min(abs(shifts[layer]),5*solution['nominal_sigma'][selected.index(700002+100*layer)])
                assert max(abs(bias[0]),abs(bias[2]))<1e-9
                distance=orientation_distance(truth['sensors'][layer]['acts_rotation'],actual[layer]['acts_rotation'])
                if angular:
                    assert distance<5*np.sqrt(np.linalg.eigvalsh(covariance[np.ix_(angular,angular)]).max())
                    assert distance<np.linalg.norm(rotations[layer])
                else:assert distance<1e-12
                sensor_errors[layer]={'orientation_error_rad':distance,'center_bias_mm':bias.tolist()}
            angular=[selected.index(k) for k in (700104,700105,700106)]
        else:angular=[selected.index(k) for k in rotation_labels(args.mode)]
        angular_covariance=covariance[np.ix_(angular,angular)]
        assert angle_error<5*np.sqrt(np.linalg.eigvalsh(angular_covariance).max())
        if is_four(args.mode):
            bias=np.array(actual[1]['center_mm'])-truth['sensors'][1]['center_mm']
            assert abs(bias[1])<min(.150,5*solution['nominal_sigma'][selected.index(700102)])
            assert max(abs(bias[0]),abs(bias[2]))<1e-9
        if not multi.is_multi(args.mode):assert angle_error<np.linalg.norm(injected_rotation(args.mode))
        require(args.result,'coupled_pose')
    else:assert angle_error<tolerances[rotation_label(args.mode)]
    input_path=args.result/'data/shifted.root';payload=channel_values(input_path)
    assert digest(input_path)==json.loads((args.result/'data/shifted_input.json').read_text())['file_sha256']
    assert channel_values(args.result/'nominal/reco.root')==channel_values(paths['corrected']/'reco.root')==payload
    if refined:assert channel_values(paths['first_corrected']/'reco.root')==payload
    changed=sum(a[1][1][0]!=b[1][1][0] for a,b in zip(payload,channel_values(args.result/'data/control.root')))
    changed_by_sensor={}
    if multi.is_multi(args.mode):
        control_payload=channel_values(args.result/'data/control.root')
        changed_by_sensor={layer:sum(a[1][layer][0]!=b[1][layer][0] for a,b in zip(payload,control_payload)) for layer in multi.aligned_sensors(args.mode)}
        assert min(changed_by_sensor.values())>=args.events//32
    assert changed>=args.events//(32 if axis(args.mode) in ('y','z','yz','xyz') else 4),'Rotation/translation fixture must have nontrivial quantized channel changes'
    nominal_audit=json.loads((args.result/'nominal/audit.json').read_text())
    corrected_audit=json.loads((paths['corrected']/'audit.json').read_text())
    for a,b in zip(nominal_audit['events'],corrected_audit['events']):
        for ha,hb in zip(a['measurement_details'],b['measurement_details']):
            assert all(ha[k]==hb[k] for k in ('cell_id','volume_id','geometry_id'))
            assert np.allclose(ha['loc_mm'],hb['loc_mm'],atol=2e-6,rtol=0)
            assert np.allclose(ha['covariance_mm2'],hb['covariance_mm2'],atol=1e-12,rtol=0)
    input_frame=next(iter(reading.get_reader(str(input_path)).get('events')))
    assert set(input_frame.getAvailableCollections())=={'EventHeader','TelescopeChannels'}
    summaries={};distributions={}
    for name in ('nominal','corrected','control'):
        audit=json.loads((paths[name]/'audit.json').read_text());mille=json.loads((paths[name]/'mille.json').read_text())
        assert audit['processed']==audit['accepted']==args.events and audit['rejected']==0
        blocks=list(records(paths[name]/'mille.dat'));assert len(blocks)==mille['records']==args.events
        local_residuals=[];by_layer={l:[] for l in range(5)}
        gid_to_layer={d['geometry_id']:l for l,d in enumerate(audit['events'][0]['measurement_details'])}
        for event,rows in zip(mille['events'],blocks):
            assert len(rows)==10 and {k for row in rows for k in row[3]}==set(selected)
            for equation in event['equations']:
                w=np.array(equation['whitening']);cov=np.array(equation['covariance'])
                assert np.allclose(w@cov@w.T,np.eye(2),atol=1e-10)
                # Audit every selected official Mille coefficient against the
                # unchanged ACTS columns and calibrated covariance whitening.
                g=w@np.array(equation['global']);l=equation['layer']
                for j in (0,1):
                    coefficients=rows[2*l+j][3]
                    expected={k:g[j,k-700101 if k in (700104,700105,700106) else 1] for k in selected if (1 if k in (700104,700105,700106) else (k-700002)//100)==l and g[j,k-700101 if k in (700104,700105,700106) else 1]!=0}
                    if multi.is_multi(args.mode):expected={k:g[j,c] for k in selected for sensor,c in [multi.mapping(k)] if sensor==l and g[j,c]!=0}
                    assert coefficients.keys()==expected.keys()
                    assert all(abs(coefficients[k]-expected[k])<1e-10 for k in expected)
        for event in audit['events']:
            assert event['measurements']==5 and event['outliers']==0 and event['holes']==2
            local_residuals.extend(s['residual_mm'] for s in event['smoothed_states'])
            for state in event['smoothed_states']:by_layer[gid_to_layer[state['geometry_id']]].append(state['residual_mm'])
        # EDM read-back and reconstructed-position/local-coordinate consistency.
        frames=list(reading.get_reader(str(paths[name]/'reco.root')).get('events'));assert len(frames)==args.events
        geometry=actual if name=='corrected' else base['sensors']
        rec_truth=[]
        for i,frame in enumerate(frames):
            hits=frame.get('TelescopeHits');tracks=frame.get('TelescopeTracks');assert len(hits)==5 and len(tracks)==1
            assert len(frame.get('TelescopeHitAssociations'))==0 and tracks[0].getPdg()==11
            assert frame.get('EventHeader')[0].getEventNumber()==i
            seeds=frame.get('TelescopeSeeds');assert len(seeds)==1 and len(tracks[0].getMeasurements())==5
            assert tracks[0].getTrajectory().getSeed()==seeds[0]
            points=np.array([[h.getPosition().x,h.getPosition().y,h.getPosition().z] for h in hits])
            direction=points[-1]-points[0];direction/=np.linalg.norm(direction)
            expected=points[0]+(249.-points[0,0])/direction[0]*direction
            center=seeds[0].getPerigee();params=seeds[0].getParams()
            assert np.allclose([center.x,center.y,center.z],expected,atol=2e-6,rtol=0)
            assert abs(params.getPhi()-math.atan2(direction[1],direction[0]))<1e-6
            assert abs(params.getTheta()-math.acos(direction[2]))<1e-6
            for hit,measurement in zip(hits,frame.get('TelescopeMeasurements')):
                layer=(hit.getCellID()>>11)&63;sensor=geometry[layer]
                point=np.array([hit.getPosition().x,hit.getPosition().y,hit.getPosition().z])
                local=np.array(sensor['acts_rotation']).T@(point-sensor['center_mm'])
                assert np.allclose(local[:2],[measurement.getLoc().a,measurement.getLoc().b],atol=3e-6,rtol=0)
                assert hit.getRawHit().isAvailable() and measurement.getSurface()==sensor['geometry_id']
                if name!='control':rec_truth.append((point-np.array(truth['events'][i]['channels'][layer]['physical_intersection_mm'])).tolist())
        chi2=np.array([e['chi2'] for e in audit['events']]);ndf=sum(e['stored_ndf'] for e in audit['events']);assert ndf==10*args.events
        residual=np.array(local_residuals)
        summaries[name]={'accepted':audit['accepted'],'records':len(blocks),'chi2_sum':float(chi2.sum()),'stored_ndf_sum':ndf,
          'chi2_per_stored_ndf':float(chi2.sum()/ndf),'residual_mean_uv_mm':residual.mean(axis=0).tolist(),
          'residual_rms_uv_mm':np.sqrt(np.mean(residual**2,axis=0)).tolist(),'maximum_abs_residual_mm':float(np.max(np.abs(residual))),
          'layer_residual_mean_uv_mm':{l:np.mean(v,axis=0).tolist() for l,v in by_layer.items()},
          'layer_residual_rms_uv_mm':{l:np.sqrt(np.mean(np.array(v)**2,axis=0)).tolist() for l,v in by_layer.items()},
          'rec_minus_truth_global_rms_mm':np.sqrt(np.mean(np.array(rec_truth)**2,axis=0)).tolist() if rec_truth else None}
        distributions[name]={'chi2':chi2.tolist(),'residual':residual.tolist()}
    assert summaries['corrected']['chi2_sum']<summaries['nominal']['chi2_sum']
    assert np.linalg.norm(summaries['corrected']['residual_rms_uv_mm'])<np.linalg.norm(summaries['nominal']['residual_rms_uv_mm'])
    diagnostics={}
    for name in (('solve','postsolve','refinesolve','controlsolve') if refined else ('solve','postsolve','controlsolve')):
        log=(args.result/name/'stdout.log').read_text();rejects=[]
        for line in log.splitlines():
            parts=line.split()
            if len(parts)>=7 and parts[0].isdigit() and parts[1].isdigit() and parts[-1] in ('FMS','FM','F','MS') and parts[-2].isdigit():rejects.append(int(parts[-3]))
        assert rejects and max(rejects)==0
        match=re.search(r'Sum\(Chi\^2\)/Sum\(Ndf\)\s*=\s*([0-9.Ee+\-]+)\s*/\s*\(\s*(\d+)\s*-\s*(\d+)\s*\)',log)
        assert match and int(match[2])==6*args.events and int(match[3])==len(selected)
        reconstruction={'solve':'nominal','postsolve':('first_corrected' if refined else 'corrected'),'refinesolve':'corrected','controlsolve':'control'}[name]
        pieces=[profile(block,selected,require_global_rank=not is_coupled(args.mode)) for block in records(paths[reconstruction]/'mille.dat')]
        normal=sum((v[0] for v in pieces),np.zeros((len(selected),len(selected))))
        rhs=sum((v[1] for v in pieces),np.zeros(len(selected)))
        chi2=sum(v[2] for v in pieces)-rhs@np.linalg.solve(normal,rhs)
        assert abs(chi2-float(match[1]))<1e-5
        ndf=int(match[2])-int(match[3])
        diagnostics[name]={'end_status':(args.result/name/'millepede.end').read_text().strip(),'rejected_local_fits':rejects,
          'chi2':float(match[1]),'ndf':ndf,'chi2_per_ndf':float(match[1])/ndf,'independent_profiled_chi2':float(chi2),
          'error_rescaling_recommended':'multiply all input standard deviations' in log,'error_rescaling_applied':False}
    ROOT.gROOT.SetBatch(True);f=ROOT.TFile.Open(str(p/'rotation.root'),'NEW');assert f and not f.IsZombie()
    for name,data in distributions.items():
        h=ROOT.TH1D(name+'_chi2',name+';ACTS chi2/stored-NDF;Tracks',100,0,max(2,max(data['chi2'])/10*1.05))
        for x in data['chi2']:h.Fill(x/10)
        h.Write()
        for j,coordinate in enumerate(('u','v')):
            h=ROOT.TH1D(name+'_'+coordinate,name+';Local residual [mm];Measurements',160,-.3,.3)
            for r in data['residual']:h.Fill(r[j])
            h.Write()
    f.Close();f=ROOT.TFile.Open(str(p/'rotation.root'));assert f.GetListOfKeys().GetEntries()==9;f.Close()
    result={'passed':True,'mode':args.mode,'events':args.events,'injected':injected,'parameters':values,'parameter_units':solution['parameter_units'],
      'errors':{k:values[k]-injected[k] for k in selected},'geometric_tolerance':tolerances,'orientation_error_rad':angle_error,
      'center_bias_mm':{l:(np.array(actual[l]['center_mm'])-truth['sensors'][l]['center_mm']).tolist() for l in (multi.aligned_sensors(args.mode) if multi.is_multi(args.mode) else (1,2))},
      'actual_l1_geometry_id':actual[1]['geometry_id'],'actual_l1_volume_id':actual[1]['volume_id'],
      'rank':solution['rank'],'condition_raw_mixed_units':solution['condition'],'condition_diagonal_scaled':solution['condition_diagonal_scaled'],
      'correlation':solution['correlation'],'nominal_sigma':solution['nominal_sigma'],'pede_reported_sigma':solution['pede_reported_sigma'],'post_correction':post,
      'summaries':summaries,'pede_diagnostics':diagnostics,'changed_l1_channel_events':changed,
      'input_channel_sha256':channel_digest(payload),'immutable_input_file_sha256':digest(input_path),
      'truth_used_for_inference':False,'geometry_activated':False,'scope':'quantized synthetic channels, zero field, no assigned ACTS material; not statistical coverage'}
    if is_coupled(args.mode) and not multi.is_multi(args.mode):
        result.update(injected_rotation_vector_rad=injected_vector(args.mode),
            injected_matrix=rotation_exp(injected_vector(args.mode)),
            recovered_rotation_vector_rad=([values[k] for k in rotation_labels(args.mode)] if is_three(args.mode) else [0.,values[700105],values[700106]]),
            **{('nominal_covariance_mixed_units' if is_four(args.mode) else 'nominal_covariance_rad2'):covariance.tolist()},
            initial_error_mahalanobis_squared=float(error@np.linalg.solve(covariance,error)),
            truth_definition='single rotation vector, not an ordered Euler product')
    if multi.is_multi(args.mode):
        result.update(changed_channel_events_by_sensor=changed_by_sensor,sensor_errors=sensor_errors,nominal_covariance_mixed_units=covariance.tolist(),initial_error_mahalanobis_squared=float(error@np.linalg.solve(covariance,error)),sensor_identities={l:{k:actual[l][k] for k in ('volume_id','geometry_id')} for l in multi.aligned_sensors(args.mode)},cumulative_geometry={l:{'global_y_mm':float(actual[l]['center_mm'][1]-base['sensors'][l]['center_mm'][1]),'rotation_vector_rad':list(rotation_log((np.array(actual[l]['acts_rotation'])@np.array(base['sensors'][l]['acts_rotation']).T).tolist()))} for l in multi.aligned_sensors(args.mode)},cumulative_uncertainty_combined=False,refinement_count=int(refined))
        first_post=read_pede(args.result/'postsolve/millepede.res')
        result.update(final_reconstruction_stage=final,
            refinement_increments=(first_post if refined else {}),
            initial_one_step_post_gate_passed=all(abs(first_post[k])<
                (2e-5 if multi.mapping(k)[1]==1 else solution['nominal_sigma'][i]/4)
                for i,k in enumerate(selected)))
    if multi.is_multi(args.mode) and 'pede_expected_stabilized_covariance_mixed_units' in solution:
        for key in ('covariance_source','pede_presigma','pede_expected_stabilized_covariance_mixed_units','pede_regularization_enabled','pede_error_note'):result[key]=solution[key]
    if refined and not multi.is_multi(args.mode):
        nominal_basis=np.array(base['sensors'][1]['acts_rotation'])
        result.update(refinement_count=1,final_reconstruction_stage=final,
            refinement_increments=read_pede(args.result/'postsolve/millepede.res'),
            cumulative_global_y_mm=float(actual[1]['center_mm'][1]-base['sensors'][1]['center_mm'][1]),
            cumulative_global_rotation_vector_rad=list(rotation_log((np.array(actual[1]['acts_rotation'])@nominal_basis.T).tolist())),
            initial_one_step_post_gate_passed=json.loads((args.result/'refinement/provenance.json').read_text())['initial_one_step_post_gate_passed'],
            cumulative_uncertainty_combined=False)
    write_json(p/'report.json',result);done(p,passed=True)






def coupled_pose_gate(args):
    """Independent real-constructor composition/inverse checks before inference."""
    from validation.telescope_closure.closure import displaced_xml,environment
    from geometry.transforms import orientation_distance
    if multi.is_multi(args.mode):return multi.pose_gate(args)
    p=stage(args.result,'coupled_pose')
    base=json.loads((args.result/'data/control/channels_truth.json').read_text())['sensors']
    if is_three(args.mode):
        truth=json.loads((args.result/'data/shifted/channels_truth.json').read_text())['sensors']
        pose_checks(base,truth,fixture_shifts(args.mode), {1:injected_rotation(args.mode)},axis=axis(args.mode))
    original=args.result/'geometry/fixtures/nominal.xml'
    previous=base;geometry=original;chain=[]
    deltas=[('forward',[.0025,-.0025]),('cross',[-.001,.0015]),('inverse_cross',[.001,-.0015]),('inverse_forward',[-.0025,.0025])]
    if is_three(args.mode):deltas=[('forward',[.0018,.0025,-.0025]),('cross',[-.0007,-.001,.0015]),('inverse_cross',[.0007,.001,-.0015]),('inverse_forward',[-.0018,-.0025,.0025])]
    for name,delta in deltas:
        q=stage(p,name)
        shifts={1:dict(forward=.150,cross=-.025,inverse_cross=.025,inverse_forward=-.150)[name]} if is_four(args.mode) else {}
        displaced_xml(geometry,q/'geometry.xml',shifts,rotations={1:delta},rotation_axis=axis(args.mode))
        execute([args.build/'TelescopeChannels',q/'geometry.xml',0,q/'geometry.json'],q,env=environment(args.build))
        actual=json.loads((q/'geometry.json').read_text())['sensors']
        pose_checks(previous,actual,shifts, {1:delta},axis=axis(args.mode))
        chain.append(actual);previous=actual;geometry=q/'geometry.xml'
    assert np.allclose(chain[2][1]['acts_rotation'],chain[0][1]['acts_rotation'],atol=2e-12,rtol=0)
    assert np.allclose(chain[3][1]['acts_rotation'],base[1]['acts_rotation'],atol=2e-12,rtol=0)
    if is_four(args.mode):assert np.allclose(chain[3][1]['center_mm'],base[1]['center_mm'],atol=1e-9,rtol=0)
    joint=rotation_exp([0,.0025,-.0025])
    ordered=multiply(rotation_exp([0,.0025,0]),rotation_exp([0,0,-.0025]))
    if is_three(args.mode):
        joint=rotation_exp(injected_vector(args.mode))
        # Keep historical diagnostic values; new fixtures compare like signs.
        w=injected_vector(args.mode) if '_contained' in args.mode else [.0018,.0025,-.0025]
        ordered=multiply(rotation_exp([w[0],0,0]),multiply(rotation_exp([0,w[1],0]),rotation_exp([0,0,w[2]])))
    difference=orientation_distance(joint,ordered)
    assert difference>1e-7,'Finite rotation vector and ordered product must remain distinct'
    write_json(p/'report.json',{'passed':True,'ordered_vs_joint_distance_rad':difference,
        'inverse_chain_max_matrix_error':float(np.max(np.abs(np.array(chain[3][1]['acts_rotation'])-base[1]['acts_rotation']))),
        'current_pose_retains_noncommutative_cross_axis_terms':True,'geometry_activated':False})
    done(p,passed=True)


def coupled_physical_derivatives(args,reconstruction):
    """Selected rotation columns against independent actual DD4hep poses."""
    from validation.telescope_closure.closure import displaced_xml,environment
    if reconstruction=='nominal':
        if (args.result/'coupled_pose').exists():require(args.result,'coupled_pose')
        else:coupled_pose_gate(args)
    from calibration.alignment.derivatives import physical_derivatives as operation
    output=args.result/('physical_derivatives' if reconstruction=='nominal' else ('refined_physical_derivatives' if reconstruction=='refined' else 'corrected_physical_derivatives'))
    geometry=args.result/('geometry/fixtures/nominal.xml' if reconstruction=='nominal' else ('refinement/refined.xml' if reconstruction=='refined' else 'correction/corrected.xml'))
    base_path=args.result/('data/control/channels_truth.json' if reconstruction=='nominal' else ('refinement/geometry_audit/geometry.json' if reconstruction=='refined' else 'correction/geometry_audit/geometry.json'))
    base=json.loads(base_path.read_text())['sensors']
    return operation(args.build,output,args.result/reconstruction,geometry,base,args.mode)
