"""Independent four-local-parameter projection for residual-attribution validation."""
import numpy as np


def projected_parts(a,g,parts):
    """Use the existing four-local-parameter QR projection for offline attribution."""
    assert np.linalg.matrix_rank(a)==4
    q,_=np.linalg.qr(a);p=np.eye(len(a))-q@q.T
    return g.T@p@g,{name:g.T@p@value for name,value in parts.items()}
