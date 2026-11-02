#!/usr/bin/env python3
"""Profile native four-local-parameter equations for bounded SiMAPS sensor sets.

This read-only diagnostic consumes the actual native ACTS equation audit. It does
not construct measurements, alter uncertainties, add constraints, or run Pede.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_common import digest, write_json


SCOPE_A_LABELS = (700102, 700104, 700105, 700106, 700202)
SCOPE_B_LABELS = SCOPE_A_LABELS + (700204, 700205, 700206)
SCOPE_C_LABELS = SCOPE_B_LABELS + (700302,)
SCOPE_D_LABELS = SCOPE_C_LABELS + (700304, 700305, 700306)
AXES = ('X', 'Y', 'Z', 'Rx', 'Ry', 'Rz')


def parameter(label):
    """Decode the existing physical label convention, without guessing a surface."""
    label = int(label)
    layer, component = divmod(label - 700000, 100)
    if layer not in range(5) or component not in range(1, 7):
        raise ValueError('Unsupported native SiMAPS alignment label: ' + str(label))
    return {'label': label, 'layer': layer, 'column': component - 1,
            'axis': 'Global ' + AXES[component - 1],
            'unit': 'mm' if component <= 3 else 'rad'}


def _report(information):
    """Report rank in dimensionless coordinates; never invert a singular design."""
    h = (information + information.T) / 2
    diagonal = h.diagonal()
    if np.any(diagonal <= 0):
        return {'rank': 0, 'parameters': len(h), 'information': h.tolist(),
                'positive_information_diagonal': False, 'covariance_mixed_units': None,
                'sigma': None, 'correlation': None, 'diagonal_scaled_condition': None}
    scale = np.sqrt(diagonal)
    scaled = h / np.outer(scale, scale)
    eigenvalues, eigenvectors = np.linalg.eigh(scaled)
    tolerance = 1e-10 * max(1., float(np.max(np.abs(eigenvalues))))
    retained = eigenvalues > tolerance
    rank = int(np.count_nonzero(retained))
    report = {'rank': rank, 'raw_units_rank': int(np.linalg.matrix_rank(h)),
              'parameters': len(h), 'information': h.tolist(),
              'positive_information_diagonal': True,
              'diagonal_scaled_eigenvalues': eigenvalues.tolist(),
              'dimensionless_rank_tolerance': tolerance,
              'diagonal_scaled_condition': (float(eigenvalues[-1] / eigenvalues[0])
                                             if rank == len(h) else None),
              'null_directions_scaled_coordinates': eigenvectors[:, ~retained].T.tolist(),
              'single_parameter_sigma': (1 / scale).tolist()}
    if rank == len(h):
        covariance = np.linalg.inv(h)
        sigma = np.sqrt(covariance.diagonal())
        report.update(covariance_mixed_units=covariance.tolist(), sigma=sigma.tolist(),
                      correlation=(covariance / np.outer(sigma, sigma)).tolist())
    else:
        report.update(covariance_mixed_units=None, sigma=None, correlation=None)
    return report


def _columns(equations, definitions):
    rows = []
    for equation in equations:
        derivative = np.zeros((2, len(definitions)))
        for i, definition in enumerate(definitions):
            if equation['layer'] == definition['layer']:
                derivative[:, i] = np.asarray(equation['global'])[:, definition['column']]
        rows.append(np.asarray(equation['whitening']) @ derivative)
    return np.concatenate(rows)


def analyze(native, labels=SCOPE_A_LABELS):
    """Accumulate native information with all unselected sensor poses fixed.

    The nuisance diagnostic frees all six pose components on selected sensors.
    The thirty-column diagnostic releases the reference sensors too;
    its null directions are diagnostics, never constraints applied to a fit.
    """
    native = Path(native)
    selected = [parameter(label) for label in labels]
    if len({p['label'] for p in selected}) != len(selected):
        raise ValueError('Selected alignment labels must be unique')
    if not selected or any(p['layer'] not in (1, 2, 3) for p in selected):
        raise ValueError('Only selected L1/L2/L3 sensors may be free in this bounded study')
    aligned_layers = sorted({p['layer'] for p in selected})
    fixed_layers = sorted(set(range(5)) - set(aligned_layers))
    extended = [parameter(700001 + 100 * layer + column)
                for layer in aligned_layers for column in range(6)]
    released = [parameter(700001 + 100 * layer + column)
                for layer in range(5) for column in range(6)]
    data = json.loads(native.read_text())
    contributions, rhs, slopes, positions = [], [], [], []
    extended_total = np.zeros((len(extended), len(extended)))
    released_total = np.zeros((30, 30))
    identities, local_ranks, per_track_ranks = {}, Counter(), Counter()
    selected_fixed_rows_zero = True
    rigid_numerators = np.zeros(6)
    rigid_denominators = np.zeros(6)
    accepted = [event for event in data['events'] if event.get('accepted', True)]
    if not accepted:
        raise ValueError('No accepted native tracks')
    for event in accepted:
        equations = event['equations']
        if len(equations) != 5 or {e['layer'] for e in equations} != set(range(5)):
            raise ValueError('Expected exactly one native measurement on each SiMAPS layer')
        if len({e['source_index'] for e in equations}) != 5:
            raise ValueError('Native SourceLink indices are not unique within the track')
        for equation in equations:
            layer = equation['layer']
            cell = int(equation['cell_id'])
            if cell & 255 != 31 or (cell >> 11) & 63 != layer:
                raise ValueError('CellID system/layer disagrees with the native equation')
            identity = {'volume_id': equation['volume_id'],
                        'geometry_id': equation['geometry_id']}
            if layer in identities and identities[layer] != identity:
                raise ValueError('Native surface identity changed within the audit')
            identities[layer] = identity
            whitening = np.asarray(equation['whitening'])
            covariance = np.asarray(equation['covariance'])
            if not np.allclose(whitening @ covariance @ whitening.T, np.eye(2), atol=1e-12):
                raise ValueError('Native calibrated covariance/whitening mismatch')
        a = np.concatenate([np.asarray(e['whitening']) @ np.asarray(e['local'])
                            for e in equations])
        local_rank = int(np.linalg.matrix_rank(a))
        local_ranks[local_rank] += 1
        if local_rank != 4:
            raise ValueError('Four local track parameters are not independently measurable')
        q, _ = np.linalg.qr(a, mode='reduced')
        g = _columns(equations, selected)
        projected = g - q @ (q.T @ g)
        information = projected.T @ projected
        contributions.append(information)
        per_track_ranks[int(np.linalg.matrix_rank(projected))] += 1
        residual = np.concatenate([np.asarray(e['whitening']) @
            (np.asarray(e['measurement']).ravel() - np.asarray(e['prediction']).ravel())
            for e in equations])
        rhs.append(projected.T @ residual)
        fixed_rows = np.concatenate([np.full(2, e['layer'] in fixed_layers) for e in equations])
        selected_fixed_rows_zero &= not np.any(g[fixed_rows])
        for definitions, total in ((extended, extended_total), (released, released_total)):
            design = _columns(equations, definitions)
            profiled = design - q @ (q.T @ design)
            total += profiled.T @ profiled
            if definitions is released:
                # A common rigid displacement includes each current center's
                # displacement and a left-Global rotation about that center.
                rigid = np.zeros((30, 6))
                for equation in equations:
                    base = 6 * equation['layer']
                    center = np.asarray(equation['center_mm'])
                    rigid[base:base + 3, :3] = np.eye(3)
                    for axis in range(3):
                        rigid[base:base + 3, 3 + axis] = np.cross(np.eye(3)[axis], center)
                    rigid[base + 3:base + 6, 3:] = np.eye(3)
                rigid_numerators += np.sum((profiled @ rigid) ** 2, axis=0)
                rigid_denominators += np.sum((design @ rigid) ** 2, axis=0)
        direction = np.asarray(event['reference_direction'])
        if abs(direction[0]) < 1e-12:
            raise ValueError('Native reference track is outside the +X telescope chart')
        slopes.append(direction[1:] / direction[0])
        positions.append([np.asarray(next(e['measurement'] for e in equations
                                         if e['layer'] == layer)).ravel()
                          for layer in aligned_layers])
    n = len(contributions)
    if (len({identity['volume_id'] for identity in identities.values()}) != 5 or
            len({identity['geometry_id'] for identity in identities.values()}) != 5):
        raise ValueError('Distinct SiMAPS layers do not have distinct native surface identities')
    for definition in selected:
        definition.update(identities[definition['layer']])
    prefixes = {}
    for count in sorted({min(n, k) for k in (16, 32, 64, 128, 256, 512, 1024, 2048, 4096)} | {n}):
        report = _report(sum(contributions[:count]))
        report['tracks'] = count
        if report['rank'] == len(selected):
            report['linearized_displacement'] = (np.asarray(report['covariance_mixed_units']) @
                                                sum(rhs[:count])).tolist()
        prefixes[str(count)] = report
    slopes, positions = np.asarray(slopes), np.asarray(positions)
    subsets = {}
    quantities = [('slope_magnitude', np.linalg.norm(slopes, axis=1))] + [
        ('L' + str(layer) + '_impact_radius', np.linalg.norm(positions[:, i], axis=1))
        for i, layer in enumerate(aligned_layers)]
    for name, values in quantities:
        median = float(np.median(values))
        for side, mask in (('lower', values <= median), ('upper', values > median)):
            count = int(mask.sum())
            if not count:
                continue
            report = _report(sum(h for h, take in zip(contributions, mask) if take))
            report.update(tracks=count, median_split=median,
                          information_diagonal_per_track=(np.asarray(report['information']).diagonal() / count).tolist())
            subsets[name + '_' + side] = report
    full = prefixes[str(n)]
    selected_indices = [next(i for i, p in enumerate(released)
                             if p['label'] == definition['label']) for definition in selected]
    fixed_information = released_total[np.ix_(selected_indices, selected_indices)]
    information_identity = float(np.max(np.abs(fixed_information - full['information'])))
    if not np.allclose(fixed_information, full['information'], rtol=1e-12, atol=1e-8):
        raise ValueError('Fixed-reference information does not match the selected design')
    cross_sensor = None
    cross_pairs = {}
    if full['correlation'] is not None:
        for a, first_layer in enumerate(aligned_layers):
            for second_layer in aligned_layers[a + 1:]:
                first = [i for i, p in enumerate(selected) if p['layer'] == first_layer]
                second = [i for i, p in enumerate(selected) if p['layer'] == second_layer]
                pair = {'row_labels': [selected[i]['label'] for i in first],
                        'column_labels': [selected[i]['label'] for i in second],
                        'correlation': np.asarray(full['correlation'])[np.ix_(first, second)].tolist()}
                cross_pairs['L' + str(first_layer) + '_L' + str(second_layer)] = pair
        # Preserve the established L1/L2 report field exactly for old scopes.
        cross_sensor = cross_pairs.get('L1_L2')
    released_report = _report(released_total)
    released_report['parameter_definitions'] = released
    released_report['common_rigid_gauge_profiled_to_unprofiled_norm_ratio'] = np.sqrt(
        rigid_numerators / np.maximum(rigid_denominators, np.finfo(float).tiny)).tolist()
    released_report['gauge_order'] = ['Global X', 'Global Y', 'Global Z',
                                     'Global Rx about origin', 'Global Ry about origin',
                                     'Global Rz about origin']
    released_report['common_rigid_gauge_is_null'] = bool(np.all(
        np.sqrt(rigid_numerators / np.maximum(rigid_denominators, np.finfo(float).tiny)) < 1e-10))
    released_report['interpretation'] = (
        'All five sensitive poses are released only in this diagnostic. Common rigid '
        'motions are gauge directions. Additional null directions may depend on the '
        'parallel-plane geometry; no fixed nullity or constraint is imposed.')
    extended_report = _report(extended_total)
    extended_report['parameter_definitions'] = extended
    layer_name = '/'.join('L' + str(layer) for layer in aligned_layers)
    extended_report['scope'] = ('Read-only omitted-X/Z and other fixed ' + layer_name +
                               ' pose-component diagnostic; no additional alignment mode enabled')
    result = {'source': str(native), 'source_sha256': digest(native),
            'model_sha256': data['model_sha256'], 'accepted_tracks': n,
            'labels': [p['label'] for p in selected], 'parameter_definitions': selected,
            'surface_identities': {str(k): v for k, v in sorted(identities.items())},
            'local_information_rank_counts': {str(k): v for k, v in sorted(local_ranks.items())},
            'individual_selected_design_rank_counts': {str(k): v for k, v in sorted(per_track_ranks.items())},
            'prefix_designs': prefixes, 'selected': full,
            'cross_sensor_correlation': cross_sensor, 'slope_position_subsets': subsets,
            'slopes_min': slopes.min(axis=0).tolist(), 'slopes_max': slopes.max(axis=0).tolist(),
            'slopes_rms': np.sqrt(np.mean(slopes ** 2, axis=0)).tolist(),
            'impact_local_min_mm': positions.min(axis=0).tolist(),
            'impact_local_max_mm': positions.max(axis=0).tolist(),
            'fixed_reference_layers': fixed_layers,
            'fixed_reference_rows_have_no_selected_global_columns': bool(selected_fixed_rows_zero),
            'fixed_reference_selected_design_full_rank': full['rank'] == len(selected),
            'fixed_reference_principal_information_max_abs_difference': information_identity,
            'omitted_X_Z_diagnostic': extended_report,
            'released_reference_gauge_diagnostic': released_report,
            'method': ('Original calibrated covariance and native ACTS global columns; QR profiling of '
                       'four local nuisance parameters independently per track. Global observability '
                       'is evaluated only after accumulating tracks; no priors or gauge constraints added.'),
            'uncertainty_coverage_validated': False, 'truth_used_for_inference': False}
    if 3 in aligned_layers:
        result['aligned_sensor_layers'] = aligned_layers
        result['cross_sensor_pair_correlations'] = cross_pairs
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--labels', type=int, nargs='+', default=SCOPE_A_LABELS)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output must be new')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, analyze(args.native, args.labels))


if __name__ == '__main__':
    main()
