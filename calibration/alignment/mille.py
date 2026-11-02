"""Official Mille Binary decoding and local-parameter profiling."""
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

