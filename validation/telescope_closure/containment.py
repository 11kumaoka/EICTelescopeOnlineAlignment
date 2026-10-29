"""Read-only physical box diagnostics; never constrain or change a Pede fit."""
import itertools
import numpy as np


def corners_in_module(center, rotation, module_center, module_rotation,
                      sensitive_half, module_half):
    """Transform all eight sensitive corners into the actual parent frame."""
    corners=np.array(list(itertools.product((-1.,1.),repeat=3)))*sensitive_half
    world=corners@np.asarray(rotation).T+center
    local=(world-module_center)@np.asarray(module_rotation)
    maximum=np.max(np.abs(local),axis=0)
    return {'global_corners_mm':world.tolist(),'module_corners_mm':local.tolist(),
            'maximum_absolute_module_mm':maximum.tolist(),
            'clearance_mm':(np.asarray(module_half)-maximum).tolist(),
            'contained':bool(np.all(maximum<module_half))}


def ellipsoid_offsets(covariance, radius=5.):
    """Deterministic sampled joint-error directions plus coordinate extrema.

    Sampling is an engineering preflight, not proof of containment everywhere
    in the ellipsoid or a statement of calibrated statistical coverage.
    """
    covariance=np.asarray(covariance)
    if covariance.shape!=(4,4) or not np.isfinite(covariance).all():
        raise ValueError('Expected finite four-parameter covariance')
    root=np.linalg.cholesky(covariance)
    directions=[np.array(v,dtype=float) for v in itertools.product((-1,0,1),repeat=4) if any(v)]
    for i in range(4):
        # Maximize each physical coordinate, retaining covariance correlations.
        directions.extend([root[i],-root[i]])
    return [radius*root@(v/np.linalg.norm(v)) for v in directions]
