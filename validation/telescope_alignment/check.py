#!/usr/bin/env python3
"""Read-only audit of native Mille rows, profiled likelihood, and optional Pede fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import numpy as np

def records(path):
    with path.open('rb') as stream:
        while header := stream.read(4):
            assert len(header)==4
            words, = struct.unpack('<i',header)
            assert words and abs(words)%2==0
            size=abs(words)//2
            code,width=('d',8) if words<0 else ('f',4)
            data=stream.read(width*size)
            labels=stream.read(4*size)
            assert len(data)==width*size and len(labels)==4*size
            pairs=list(zip(struct.unpack('<'+code*size,data),struct.unpack('<'+'i'*size,labels)))
            assert pairs[0][1]==0
            rows=[]; index=1
            while index<size:
                value,zero=pairs[index];assert zero==0;index+=1
                local={}
                while index<size and pairs[index][1]!=0:
                    derivative,label=pairs[index];assert label>0 and label not in local
                    local[label]=derivative;index+=1
                sigma,zero=pairs[index];assert zero==0 and sigma>0;index+=1
                global_={}
                while index<size and pairs[index][1]!=0:
                    derivative,label=pairs[index];assert label>0 and label not in global_
                    global_[label]=derivative;index+=1
                rows.append((value,sigma,local,global_))
            yield rows

def profile(rows,labels,require_global_rank=True):
    r=np.array([row[0]/row[1] for row in rows])
    local=np.array([[row[2].get(k,0.)/row[1] for k in range(1,5)] for row in rows])
    global_=np.array([[row[3].get(k,0.)/row[1] for k in labels] for row in rows])
    assert np.linalg.matrix_rank(local)==4
    basis,_=np.linalg.qr(local)
    projector=np.eye(len(rows))-basis@basis.T
    information=global_.T@projector@global_
    rhs=global_.T@projector@r
    # Some same-sensor global columns constrain only one combination per track.
    # The bounded coupled caller still requires full rank of the summed design.
    if require_global_rank:assert np.linalg.matrix_rank(information)==len(labels)
    return information,rhs,float(r@projector@r)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--legacy',type=Path,required=True)
    parser.add_argument('--gate',type=Path,required=True)
    parser.add_argument('--fixture',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Output must be new')
    report=json.loads(args.report.read_text());gate=json.loads(args.gate.read_text())
    assert report['passed'] and gate['passed']
    assert report['model_sha256']==gate['model_sha256']
    blocks=list(records(args.native));old=list(records(args.legacy))
    assert len(blocks)==len(old)==1 and len(blocks[0])==len(old[0])==10
    assert {k for row in blocks[0] for k in row[3]}=={700102,700202}
    for i,equation in enumerate(report['equations']):
        measured=np.array(equation['measurement']).ravel()
        covariance=np.array(equation['covariance'])
        whitening=np.array(equation['whitening'])
        prediction=np.array(equation['prediction']).ravel()
        local=whitening@np.array(equation['local'])
        global_=whitening@np.array(equation['global'])
        residual=whitening@(measured-prediction)
        assert np.allclose(whitening@covariance@whitening.T,np.eye(2),atol=1e-12)
        for j in range(2):
            value,sigma,actual_local,actual_global=blocks[0][2*i+j]
            assert sigma==1 and np.isclose(value,residual[j],atol=1e-10)
            assert np.allclose([actual_local.get(k,0.) for k in range(1,5)],local[j],atol=1e-10)
            label=equation['label']
            expected={label:global_[j,1]} if label and global_[j,1]!=0 else {}
            assert actual_global.keys()==expected.keys()
            assert all(np.isclose(actual_global[k],v,atol=1e-10) for k,v in expected.items())
    # All five transverse translations have two rigid line gauge freedoms.
    design=[];all_global=[]
    for equation in report['equations']:
        whitening=np.array(equation['whitening'])
        design.extend(whitening@np.array(equation['local']))
        block=np.zeros((2,5));block[:,equation['layer']]=(whitening@np.array(equation['global']))[:,1]
        all_global.extend(block)
    basis,_=np.linalg.qr(np.array(design))
    projector=np.eye(10)-basis@basis.T
    gauge_information=np.array(all_global).T@projector@np.array(all_global)
    assert np.linalg.matrix_rank(gauge_information,tol=1e-6)==3
    information,rhs,chi2=profile(blocks[0],[700102,700202])
    old_information,old_rhs,old_chi2=profile(old[0],[2001,2002])
    # Legacy Ay is a measurement bias, opposite the physical Global Y correction.
    assert np.allclose(information,old_information,rtol=2e-6,atol=1e-5)
    assert np.allclose(rhs,-old_rhs,rtol=2e-4,atol=.003)
    assert np.isclose(chi2,old_chi2,rtol=2e-4,atol=.003)
    result={'passed':True,'records':1,'scalar_measurements':10,'local_rank':4,'selected_global_rank':2,
        'physical_global_y_labels':[700102,700202],'all_five_y_free_rank':3,'all_five_y_gauge_nullity':2,'profiled_global_information':information.tolist(),
        'profiled_rhs':rhs.tolist(),'profiled_chi2_before_global_fit':chi2,
        'legacy_profiled_chi2':old_chi2,'legacy_physical_correction_sign':'physical Global Y = -legacy Ay',
        'legacy_information_max_abs_difference':float(np.max(np.abs(information-old_information))),
        'legacy_rhs_sign_adjusted_max_abs_difference':float(np.max(np.abs(rhs+old_rhs))),
        'native_mille_sha256':hashlib.sha256(args.native.read_bytes()).hexdigest(),
        'native_single_track_alignment_closure_claimed':False}
    if args.fixture:
        blocks=list(records(args.fixture/'fixture.dat'));assert len(blocks)==16 and all(len(b)==10 for b in blocks)
        total=np.zeros((2,2));total_rhs=np.zeros(2)
        for block in blocks:
            a,b,_=profile(block,[700102,700202]);total+=a;total_rhs+=b
        expected=np.linalg.solve(total,total_rhs)
        end=(args.fixture/'millepede.end').read_text().strip()
        assert end.split()[0] in ('0','1'),end
        parameters={};errors={}
        for line in (args.fixture/'millepede.res').read_text().splitlines():
            parts=line.split()
            if parts and parts[0] in ('700102','700202'):
                assert len(parts)>=5 and float(parts[4])>0
                parameters[int(parts[0])]=float(parts[1]);errors[int(parts[0])]=float(parts[4])
        formal=np.sqrt(np.diag(np.linalg.inv(total)))
        assert np.allclose([errors[k] for k in [700102,700202]],formal,rtol=2e-5)
        actual=np.array([parameters[k] for k in [700102,700202]])
        assert np.allclose(actual,expected,atol=2e-6)
        assert np.allclose(actual,[.150,.200],atol=2e-6)
        log=(args.fixture/'millepede.log').read_text()
        assert 'No rank defect of the symmetric matrix' in log
        result['noiseless_mathematical_fixture']={'records':16,'solution_mm':parameters,
            'independent_schur_solution_mm':expected.tolist(),'pede_status':end,'nominal_errors_mm':errors,
            'warning_reason':'Noiseless data have near-zero chi2; Pede recommends error rescaling, which was not applied',
            'physics_or_rawhit_closure':False}
    with args.output.open('x') as output:json.dump(result,output,indent=2);output.write('\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
