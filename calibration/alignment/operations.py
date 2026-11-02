"""Official Pede execution and independent data-only information checks."""
import json
import numpy as np
from research_common import REPO, execute, stage, done, require, write_json
from calibration.reconstruction.operations import environment
from calibration.alignment.pede import read_pede
from calibration.alignment.mille import records, profile
LABELS=[700102,700202]

def solve(build,output,reconstruction,mode,events,diagnostic=False):
    """Run unchanged profiling and official Pede after the caller's FD gate."""
    from calibration.alignment.sensors import is_multi,mapping
    from calibration.policy import require_precision
    quality_warnings=[]
    def precision(passed,message):
        require_precision(passed,message,diagnostic,quality_warnings)
    rotation=mode!='l1_l2_y'
    selected=LABELS
    if rotation:
        from calibration.alignment.modes import labels
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
        from calibration.alignment.observability import analyze
        design=analyze(reconstruction/'mille.json')
        last=design['prefix_designs'][str(events)]
        assert last['rank']==5 and last['diagonal_scaled_condition']<100
        from calibration.alignment.modes import axis, is_coupled, is_three, is_four
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
        from calibration.alignment.information import analyze
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
        from calibration.policy import solver_diagnostics
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

