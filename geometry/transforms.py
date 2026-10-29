"""Right-handed active rigid transforms: parent_point = rotation @ child_point + translation.

Lengths are mm, angles radians, and vectors are columns. ``A @ B`` applies B
first. A parent-frame update about pivot c maps p to Q(p-c)+c+d; a child-frame
update is right-composed. Euler angles are constructors, never additive state.
"""
from dataclasses import dataclass
import math


def matvec(matrix,vector):return tuple(sum(row[j]*vector[j] for j in range(3)) for row in matrix)
def add(a,b):return tuple(x+y for x,y in zip(a,b))
def multiply(a,b):return tuple(tuple(sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)) for i in range(3))
def transpose(a):return tuple(zip(*a))
IDENTITY=((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))


@dataclass(frozen=True)
class RigidTransform:
    rotation: tuple=IDENTITY
    translation: tuple=(0.,0.,0.)
    parent: str='global'
    child: str='sensor'

    def __post_init__(self):
        object.__setattr__(self,'rotation',tuple(tuple(row) for row in self.rotation))
        object.__setattr__(self,'translation',tuple(self.translation))
        if len(self.translation)!=3 or len(self.rotation)!=3 or any(len(row)!=3 for row in self.rotation):raise ValueError('Expected a 3D rigid transform')
        if not all(math.isfinite(v) for row in self.rotation for v in row) or not all(math.isfinite(v) for v in self.translation):raise ValueError('Non-finite transform')
        product=multiply(transpose(self.rotation),self.rotation)
        if any(abs(product[i][j]-IDENTITY[i][j])>1e-12 for i in range(3) for j in range(3)):raise ValueError('Rotation must be orthonormal')
        a=self.rotation
        determinant=a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])-a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])+a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0])
        if abs(determinant-1)>1e-12:raise ValueError('Rotation must be proper and right-handed')

    def apply(self,point):return add(matvec(self.rotation,point),self.translation)
    def __matmul__(self,other):
        if self.child!=other.parent:raise ValueError('Transform frame chain mismatch')
        return RigidTransform(multiply(self.rotation,other.rotation),self.apply(other.translation),self.parent,other.child)
    def inverse(self):
        rotation=transpose(self.rotation)
        return RigidTransform(rotation,matvec(rotation,tuple(-x for x in self.translation)),self.child,self.parent)
    def updated(self,rotation=IDENTITY,translation=(0.,0.,0.),pivot=(0.,0.,0.),frame='global'):
        if frame not in ('global','parent','local'):raise ValueError('Expected global or local update frame')
        if frame=='global' and self.parent!='global':raise ValueError('Global update requires an explicit transform to the global frame')
        origin=self.child if frame=='local' else self.parent
        offset=add(translation,add(pivot,tuple(-x for x in matvec(rotation,pivot))))
        delta=RigidTransform(rotation,offset,origin,origin)
        return self@delta if frame=='local' else delta@self


def axis_rotation(axis,angle):
    c,s=math.cos(angle),math.sin(angle)
    if axis=='x':return ((1.,0.,0.),(0.,c,-s),(0.,s,c))
    if axis=='y':return ((c,0.,s),(0.,1.,0.),(-s,0.,c))
    if axis=='z':return ((c,-s,0.),(s,c,0.),(0.,0.,1.))
    raise ValueError('Unknown rotation axis')


def rotation_exp(vector):
    """SO(3) exponential of a Global-frame rotation vector in radians."""
    if len(vector)!=3 or not all(math.isfinite(x) for x in vector):
        raise ValueError('Expected a finite three-component rotation vector')
    x,y,z=vector;theta2=x*x+y*y+z*z
    if theta2<1e-8:
        a=1-theta2/6+theta2*theta2/120
        b=.5-theta2/24+theta2*theta2/720
    else:
        theta=math.sqrt(theta2);a=math.sin(theta)/theta;b=(1-math.cos(theta))/theta2
    k=((0.,-z,y),(z,0.,-x),(-y,x,0.));k2=multiply(k,k)
    return tuple(tuple(IDENTITY[i][j]+a*k[i][j]+b*k2[i][j] for j in range(3)) for i in range(3))


def rotation_log(rotation):
    """Principal SO(3) rotation vector; its norm is the orientation distance.

    Quaternion extraction avoids loss of precision from acos(trace) near identity.
    The axis at exactly pi has an unavoidable sign ambiguity.
    """
    r=RigidTransform(rotation).rotation
    trace=sum(r[i][i] for i in range(3))
    if trace>0:
        scale=2*math.sqrt(1+trace)
        q=(scale/4,(r[2][1]-r[1][2])/scale,(r[0][2]-r[2][0])/scale,(r[1][0]-r[0][1])/scale)
    else:
        i=max(range(3),key=lambda j:r[j][j]);j=(i+1)%3;k=(i+2)%3
        scale=2*math.sqrt(max(0.,1+r[i][i]-r[j][j]-r[k][k]))
        xyz=[0.,0.,0.];xyz[i]=scale/4;xyz[j]=(r[i][j]+r[j][i])/scale;xyz[k]=(r[i][k]+r[k][i])/scale
        q=((r[k][j]-r[j][k])/scale,*xyz)
    if q[0]<0:q=tuple(-v for v in q)
    length=math.sqrt(sum(v*v for v in q[1:]))
    factor=2. if length<1e-15 else 2*math.atan2(length,q[0])/length
    return tuple(factor*v for v in q[1:])


def orientation_distance(target,current):
    """Global relative rotation: R_target R_current^T, independent of Euler charts."""
    return math.sqrt(sum(v*v for v in rotation_log(multiply(target,transpose(current)))))


def ordered_xy_axes(rotation,reference=IDENTITY,tolerance=1e-10):
    """Generators for the constrained chart R=Ry(beta) Rx(alpha) R_reference.

    Alpha is an ordered-chart coordinate, not an instantaneous Global Rx.
    Reject off-chart orientations rather than silently projecting away twist.
    """
    q=multiply(rotation,transpose(reference))
    beta=math.atan2(-q[2][0],q[0][0]);alpha=math.atan2(-q[1][2],q[1][1])
    y=axis_rotation('y',beta)
    if orientation_distance(q,multiply(y,axis_rotation('x',alpha)))>tolerance:
        raise ValueError('Orientation is outside the ordered Ry Rx reference chart')
    return matvec(y,(1.,0.,0.)),(0.,1.,0.)


def ordered_xy_increment(rotation,delta_alpha,delta_beta,reference=IDENTITY):
    """Exact finite chart retraction; no additive Euler state or twist removal."""
    x_axis,_=ordered_xy_axes(rotation,reference)
    q=multiply(axis_rotation('y',delta_beta),rotation_exp(tuple(delta_alpha*v for v in x_axis)))
    return multiply(q,rotation)
