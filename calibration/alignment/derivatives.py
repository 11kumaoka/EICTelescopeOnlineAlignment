"""Mandatory finite-difference validation against actual DD4hep/ACTS poses."""
import json
import numpy as np
from research_common import execute, stage, done, write_json
from geometry.operations import validate_geometry, displaced_xml
from calibration.reconstruction.operations import environment

def physical_derivatives(build,output,reconstruction,geometry,base_sensors,mode):
    """Validate selected native columns with the same real-Geometry FD loops."""
    from calibration.alignment import sensors as multi
    from calibration.alignment.modes import is_four, is_three, labels, axis
    from geometry.rotations import pose_checks
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

