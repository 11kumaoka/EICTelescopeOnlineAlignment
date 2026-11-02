"""Validated sensitive-center SO(3) serialization and physical pose checks."""
import math
import copy
import numpy as np
from geometry.transforms import rotation_exp, rotation_log, multiply, transpose
from geometry.placements import xml_number

def rotate_component(component,increment,axis='x'):
    """Restricted left-Global Exp(dw), current sensitive-center pivot.

    Actual DD4hep probes establish Rx = -(XML y + pi/2) and
    Ry = -(XML x + pi/2) on their separate one-axis charts. Compositions
    outside each subgroup are rejected. Rz uses the independently verified
    alternate chart x=0, y=-pi/2-Rz, z=pi/2; this is not a general serializer.
    """
    if axis in ('yz','xyz'):return rotate_coupled_component(component,increment,full=axis=='xyz')
    if axis not in ('x','y','z') or not math.isfinite(increment):
        raise ValueError('Only finite separate sensor Global Rx, Ry or Rz enabled')
    r=component.find('rotation')
    x,y,z=[xml_number(r.get(a,'0'),{'pi':math.pi}) for a in 'xyz']
    if axis=='z':
        # The alternate zero chart is physically identical to the nominal chart.
        # Only the pure-Rz subgroup is admitted; never decompose a general pose.
        nominal=abs(x+math.pi/2)<1e-12 and abs(y+math.pi/2)<1e-12 and abs(z)<1e-12
        selected=abs(x)<1e-12 and abs(z-math.pi/2)<1e-12
        if not (nominal or selected):raise ValueError('Unsupported composition outside the selected Rz chart')
        current=0. if nominal else -(y+math.pi/2)
        vector=rotation_log(multiply(rotation_exp((0.,0.,increment)),rotation_exp((0.,0.,current))))
        if abs(vector[0])+abs(vector[1])>1e-12:raise ValueError('Outside pure-Rz subgroup')
        r.set('x','0');r.set('y',f'{-math.pi/2-vector[2]:.17g}');r.set('z',f'{math.pi/2:.17g}')
        return
    if abs(z)>1e-12 or abs((x if axis=='x' else y)+math.pi/2)>1e-12:
        raise ValueError('Unsupported composition outside the selected one-axis chart')
    index=0 if axis=='x' else 1
    current=[0.,0.,0.];current[index]=-((y if axis=='x' else x)+math.pi/2)
    delta=[0.,0.,0.];delta[index]=increment
    vector=rotation_log(multiply(rotation_exp(delta),rotation_exp(current)))
    if sum(abs(v) for i,v in enumerate(vector) if i!=index)>1e-12:
        raise ValueError('Outside selected one-axis subgroup')
    r.set('y' if axis=='x' else 'x',f'{-math.pi/2-vector[index]:.17g}')


def pose_checks(before, after, shifts, rx, axis='x'):
    """Check actual DD4hep/ACTS sensor identity and rigid transforms."""
    if len(before) != len(after):
        raise ValueError("Sensor count changed")

    for a, b in zip(before, after):
        layer = a['layer']
        vector = [0., 0., 0.]

        if axis == 'xyz':
            vector = rx.get(layer, [0., 0., 0.])
        elif axis == 'yz':
            vector[1:] = rx.get(layer, [0., 0.])
        else:
            vector['xyz'.index(axis)] = rx.get(layer, 0.)

        q = np.array(rotation_exp(vector))

        assert a['layer'] == b['layer']
        assert a['volume_id'] == b['volume_id']
        assert a['geometry_id'] == b['geometry_id']

        np.testing.assert_allclose(
            b['center_mm'],
            np.array(a['center_mm']) + [0, shifts.get(layer, 0.), 0],
            atol=1e-9,
            rtol=0
        )

        np.testing.assert_allclose(
            b['acts_rotation'],
            q @ np.array(a['acts_rotation']),
            atol=2e-12,
            rtol=0
        )

        np.testing.assert_allclose(
            b['dd4hep_rotation'],
            q @ np.array(a['dd4hep_rotation']),
            atol=2e-12,
            rtol=0
        )


def component_matrix(component):
    """Effective sensitive-to-module basis, checked against actual pinned DD4hep.

    This telescope parent/module frame is fixed by its existing geometry fixture.
    It is not a replacement geometry provider or a generic detector adapter.
    """
    r=component.find('rotation')
    x,y,z=[xml_number(r.get(a,'0'),{'pi':math.pi}) for a in 'xyz']
    return np.array(multiply(rotation_exp((x,0,0)),multiply(rotation_exp((0,y,0)),rotation_exp((0,0,z)))))


def rotate_coupled_component(component,increment,full=False):
    """Serialize a finite physical left-Global rotation-vector update.

    The two-axis scope retains known commutator Rx but never fits Rx. The explicit
    full scope permits all three components of a selected sensor. General singular Euler
    extraction is refused; exact one-axis targets use validated restricted charts.
    """
    from geometry.operations import MODULE_TO_GLOBAL
    parent=np.array(MODULE_TO_GLOBAL)
    delta=np.array(increment,dtype=float)
    if delta.shape != ((3,) if full else (2,)) or not np.isfinite(delta).all():
        raise ValueError('Invalid selected sensor Global rotation-vector increment')
    nominal=np.array(multiply(rotation_exp((-math.pi/2,0,0)),rotation_exp((0,-math.pi/2,0))))
    current=component_matrix(component)
    pose=parent@current@nominal.T@parent.T
    w=np.array(rotation_log(pose.tolist()))
    if not np.isfinite(w).all() or (not full and abs(w[0]) > 1e-4):
        raise ValueError('Invalid current rotation or unsupported two-axis scope')
    target=np.array(rotation_exp(delta if full else [0.,*delta]))@parent@current
    local=parent.T@target
    cy=math.hypot(local[0,0],local[0,1])
    if cy<1e-6:
        physical=np.array(rotation_log((target@nominal.T@parent.T).tolist()))
        if full and abs(physical[1])+abs(physical[2])<1e-12:
            angles=(-math.pi/2,-math.pi/2-physical[0],0.)
        elif abs(physical[0])+abs(physical[2])<1e-12:
            angles=(-math.pi/2-physical[1],-math.pi/2,0.)
        elif abs(physical[0])+abs(physical[1])<1e-12:
            angles=(0.,-math.pi/2-physical[2],math.pi/2)
        else:raise ValueError('Ambiguous near-singular coupled serialization chart')
    else:
        angles=(math.atan2(-local[1,2],local[2,2]),
                math.atan2(local[0,2],cy),math.atan2(-local[0,1],local[0,0]))
    candidate=copy.deepcopy(component)
    for name,value in zip('xyz',angles):candidate.find('rotation').set(name,f'{value:.17g}')
    if not np.allclose(parent@component_matrix(candidate),target,atol=5e-13,rtol=0):
        raise ValueError('Coupled target matrix cannot be serialized safely')
    # Mutation follows successful target-matrix and chart validation.
    for name in 'xyz':component.find('rotation').set(name,candidate.find('rotation').get(name))

