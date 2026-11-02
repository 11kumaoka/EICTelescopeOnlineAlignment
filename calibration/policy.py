"""Offline quality policy; numerical and detector integrity remain mandatory."""
import math
import re


class QualityGateError(RuntimeError, AssertionError):
    """Valid numerical output blocked by an established quality policy."""


def solver_diagnostics(log, status, diagnostic=False):
    """Permit only the recognized high-chi2 advisory in diagnostic mode.

    The caller must separately check the process exit, matrix rank, finite
    estimates, errors and agreement with the independently profiled equations.
    Rejected local fits are never allowed: that would invalidate the all-record
    comparison. No steering value or reported uncertainty is changed here.
    """
    rejected = []
    for line in log.splitlines():
        parts = line.split()
        if (len(parts) >= 7 and parts[0].isdigit() and parts[1].isdigit()
                and parts[-1] in ('FMS', 'FM', 'F', 'MS') and parts[-2].isdigit()):
            rejected.append(int(parts[-3]))
    warnings = [line for line in log.splitlines()
                if re.search(r'warning|multiply all input standard deviations', line, re.I)]
    if not rejected or max(rejected) != 0:
        raise RuntimeError('Pede missing/rejected local fits: ' + status)
    if re.fullmatch(r'0\s+Ended normally', status) and not warnings:
        return {'status': status, 'rejected_local_fits': rejected, 'warnings': [],
                'diagnostic_continuation': False, 'error_rescaling_applied': False}
    if not re.fullmatch(r'1\s+Ended with warnings \(bad measurements\)', status):
        raise RuntimeError('Unsupported Pede diagnostic status: ' + status)

    def recognized(line):
        value = line.strip().lower()
        banner = 'warning' * (len(value) // 7 + 2)
        if len(value) >= 20 and any(value == banner[i:i + len(value)] for i in range(7)):
            return True
        return re.fullmatch(r'=>\s*multiply all input standard deviations by factor\s+'
                            r'[0-9.Ee+\-]+', value) is not None

    factors = re.findall(r'multiply all input standard deviations by factor\s+([0-9.Ee+\-]+)', log)
    ratios = re.findall(r'Chi\^2/Ndf\s*=\s*([0-9.Ee+\-]+)\s+\(should be close to 1\)', log)
    if (not warnings or not all(recognized(line) for line in warnings)
            or len(factors) != 1 or len(ratios) != 1
            or not math.isfinite(float(factors[0])) or float(factors[0]) <= 1
            or not math.isfinite(float(ratios[0])) or float(ratios[0]) <= 1):
        raise RuntimeError('Unrecognized Pede warning; diagnostic continuation refused')
    if not diagnostic:
        raise QualityGateError('Pede warning/failure/rejected local fits: ' + status)
    return {'status': status, 'rejected_local_fits': rejected, 'warnings': warnings,
            'diagnostic_continuation': True, 'quality_warning': 'High Pede chi2/NDF',
            'suggested_error_scale_not_applied': float(factors[0]),
            'error_rescaling_applied': False}


def require_precision(passed, message, diagnostic, warnings):
    """Separate a declared resolution target from identifiability checks."""
    if not passed:
        if not diagnostic:
            raise QualityGateError(message)
        warnings.append(message)
