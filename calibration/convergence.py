"""Provisional demonstration gates; not detector-calibration certification."""
import math


def evaluate(delta_mm,chi2_ratio,tracks,policy):
    if not all(math.isfinite(x) for x in (delta_mm,chi2_ratio)):raise RuntimeError('Non-finite calibration metric')
    if abs(delta_mm)>policy['max_update_mm']:raise RuntimeError('L2 update exceeds configured demonstration bound')
    if tracks<policy['min_tracks']:raise RuntimeError('Insufficient tracks for configured demonstration policy')
    if chi2_ratio>policy['max_track_chi2_ratio']:raise RuntimeError('Track chi2 ratio exceeds configured demonstration bound')
    return {'converged':abs(delta_mm)<=policy['delta_tolerance_mm'],
            'delta_mm':delta_mm,'track_chi2_ratio':chi2_ratio,'tracks':tracks,
            'policy_label':policy['policy_label']}


def evaluate_rotation(delta_rad,chi2_ratio,tracks,policy):
    """Apply angular bounds in radians; never compare angles with an mm tolerance."""
    if not math.isfinite(delta_rad):raise RuntimeError('Non-finite angular update')
    if abs(delta_rad)>policy['max_update_rad']:raise RuntimeError('Rotation update exceeds configured demonstration bound')
    metrics=evaluate(0.,chi2_ratio,tracks,policy)
    metrics.pop('delta_mm')
    metrics.update(converged=abs(delta_rad)<=policy['delta_tolerance_rad'],delta_rad=delta_rad)
    return metrics


def evaluate_native(labels, delta, initial_normal, initial_sigma, *, iteration,
                    minimum_corrections=1, maximum_corrections=3, chi2_ratio,
                    residual_norm, initial_chi2_ratio, initial_residual_norm,
                    tracks, rejected, initial_tracks, initial_rejected, rank,
                    condition, association_identity, solver_ok, geometry_ok,
                    policy=None):
    """Evaluate same-data convergence without injected or Truth Geometry.

    ``iteration`` counts corrections already applied to the current Geometry.
    The initial data-only information and formal errors remain fixed across
    iterations; covariance matrices from successive fits are never added.
    Existing strict component limits are Y < 2e-5 mm and angles < sigma0/4.
    The additional joint limit sqrt(delta.T H0 delta) < 0.25 is an explicitly
    configurable demonstration criterion, not an uncertainty-coverage claim.
    Policies may tighten these limits but cannot relax the validated bounds.

    Current-data rank/sensitivity checks belong to the native solve stage.
    Here the supplied current rank/condition and execution/Geometry checks must
    pass before any convergence decision. Invalid state raises; exhaustion is
    a separate nonconverged result, leaving checkpoint management to the caller.
    """
    import numpy as np

    defaults = {'max_abs_y_mm': 2e-5, 'max_angular_sigma_ratio': .25,
                'max_joint_sigma': .25, 'max_scaled_condition': 100.,
                'policy_label': 'demonstration: same-data native calibration'}
    if policy is not None and not isinstance(policy, dict):
        raise ValueError('Native convergence policy must be a dictionary')
    unknown = set(policy or {}) - set(defaults)
    if unknown:
        raise ValueError('Unknown native convergence policy keys: '+str(sorted(unknown)))
    selected_policy = dict(defaults, **(policy or {}))
    for key in ('max_abs_y_mm', 'max_angular_sigma_ratio', 'max_joint_sigma',
                'max_scaled_condition'):
        value = selected_policy[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Native convergence threshold must be finite: '+key)
        lower = 1. if key == 'max_scaled_condition' else 0.
        if not lower < value <= defaults[key]:
            raise ValueError('Native convergence threshold exceeds its validated range: '+key)
    if not isinstance(selected_policy['policy_label'], str) or not selected_policy['policy_label'].strip():
        raise ValueError('Native convergence policy_label must be nonempty')
    for key, value in (('iteration', iteration), ('minimum_corrections', minimum_corrections),
                       ('maximum_corrections', maximum_corrections)):
        if type(value) is not int:
            raise ValueError('Native correction counts must be integers: '+key)
    if not 1 <= minimum_corrections <= maximum_corrections <= 3 or not 0 <= iteration <= maximum_corrections:
        raise ValueError('Require 1 <= minimum <= maximum <= 3 and 0 <= iteration <= maximum')

    labels = list(labels)
    if not labels or any(type(k) is not int for k in labels) or len(set(labels)) != len(labels):
        raise ValueError('Native labels must be nonempty unique integer physical labels')
    components = []
    for label in labels:
        layer, component = divmod(label-700000, 100)
        if layer not in (1, 2, 3) or component not in (2, 4, 5, 6):
            raise ValueError('Native convergence supports selected L1/L2/L3 Y/Rx/Ry/Rz labels only')
        components.append((layer, component))
    for layer in {layer for layer, _ in components}:
        rotations = {component for sensor, component in components if sensor == layer and component != 2}
        if rotations and rotations != {4, 5, 6}:
            raise ValueError('A selected native SO(3) update requires all three rotation components')

    size = len(labels)
    remaining = np.asarray(delta, dtype=float)
    information = np.asarray(initial_normal, dtype=float)
    sigma = np.asarray(initial_sigma, dtype=float)
    if remaining.shape != (size,) or sigma.shape != (size,) or information.shape != (size, size):
        raise ValueError('Native correction, initial information and sigma shapes must match labels')
    if not all(np.isfinite(value).all() for value in (remaining, information, sigma)) or not np.all(sigma > 0):
        raise ValueError('Native corrections/information must be finite and initial sigmas positive')
    if not np.allclose(information, information.T, rtol=1e-12, atol=0):
        raise ValueError('Initial native information must be symmetric')
    try:
        np.linalg.cholesky(information)
    except np.linalg.LinAlgError as error:
        raise ValueError('Initial native information must be positive definite') from error

    for key, value in (('chi2_ratio', chi2_ratio), ('residual_norm', residual_norm),
                       ('initial_chi2_ratio', initial_chi2_ratio), ('initial_residual_norm', initial_residual_norm)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('Native reconstruction quality must be finite and nonnegative: '+key)
    for key, value in (('tracks', tracks), ('rejected', rejected),
                       ('initial_tracks', initial_tracks), ('initial_rejected', initial_rejected)):
        if type(value) is not int or value < (0 if 'rejected' in key else 1):
            raise ValueError('Invalid native track count: '+key)
    if tracks != initial_tracks or rejected != initial_rejected:
        raise RuntimeError('Native accepted/rejected track counts changed')
    if type(rank) is not int or rank != size:
        raise RuntimeError('Native selected information is rank deficient')
    if isinstance(condition, bool) or not isinstance(condition, (int, float)) or not math.isfinite(condition) or not 1 <= condition < selected_policy['max_scaled_condition']:
        raise RuntimeError('Native scaled information condition failed')
    for key, value in (('association identity', association_identity), ('official Pede status', solver_ok),
                       ('physical Geometry validation', geometry_ok)):
        if value is not True:
            raise RuntimeError('Native convergence requires valid '+key)
    if iteration >= 1 and (chi2_ratio > initial_chi2_ratio or residual_norm > initial_residual_norm):
        raise RuntimeError('Native reconstruction quality worsened relative to the initial Geometry')

    per_parameter = {}
    for label, (_, component), value, error in zip(labels, components, remaining, sigma):
        limit = selected_policy['max_abs_y_mm'] if component == 2 else selected_policy['max_angular_sigma_ratio']*error
        per_parameter[str(label)] = {'unit': 'mm' if component == 2 else 'rad',
            'remaining_correction': float(value), 'initial_formal_sigma': float(error),
            'correction_over_initial_sigma': float(value/error),
            'strict_absolute_limit': float(limit), 'passed': bool(abs(value) < limit)}
    joint_squared = float(remaining @ information @ remaining)
    if not math.isfinite(joint_squared) or joint_squared < 0:
        raise RuntimeError('Invalid native covariance-aware correction significance')
    joint_sigma = math.sqrt(joint_squared)
    checks = {'component_updates': all(p['passed'] for p in per_parameter.values()),
              'joint_correction_significance': joint_sigma < selected_policy['max_joint_sigma'],
              'minimum_corrections': iteration >= minimum_corrections,
              'chi2_improved': chi2_ratio < initial_chi2_ratio,
              'residuals_improved': residual_norm < initial_residual_norm}
    converged = all(checks.values())
    exhausted = not converged and iteration == maximum_corrections
    status = 'converged' if converged else ('iteration_limit' if exhausted else 'continue')
    reason = ('all_truth_independent_gates_passed' if converged else
              ('maximum_corrections_exhausted' if exhausted else
               ','.join(key for key, passed in checks.items() if not passed)))
    return {'status': status, 'converged': converged, 'reason': reason,
            'corrections_applied': iteration, 'minimum_corrections': minimum_corrections,
            'maximum_corrections': maximum_corrections, 'checks': checks,
            'per_parameter': per_parameter, 'joint_correction_sigma': joint_sigma,
            'joint_correction_significance_squared': joint_squared,
            'rank': rank, 'condition_diagonal_scaled': condition,
            'tracks': tracks, 'rejected': rejected, 'association_identity': True,
            'solver_ok': True, 'geometry_ok': True,
            'chi2_ratio': chi2_ratio, 'initial_chi2_ratio': initial_chi2_ratio,
            'residual_norm': residual_norm, 'initial_residual_norm': initial_residual_norm,
            'policy': selected_policy, 'truth_used': False, 'covariance_combined': False,
            'interpretation': 'Same-data self-consistency; not Truth recovery or uncertainty coverage'}
