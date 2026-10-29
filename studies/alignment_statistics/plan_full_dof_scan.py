#!/usr/bin/env python3
"""Generate a deterministic cohort plan, not an alignment reconstruction.

Use only after a quality-selected, immutable master sample contains >=20000
accepted tracking events. Ordinals index that master sample; event IDs and
SHA256 must be bound by the actual experiment runner before execution.
"""
import argparse
import json
from pathlib import Path

POINTS = (100, 180, 320, 580, 1050, 1900, 3420, 6160, 11100, 20000)


def make_plan(accepted, mode):
    if type(accepted) is not int or accepted < 20000:
        raise ValueError('A master sample with at least 20,000 accepted tracking events is required')
    cases = []
    for n in POINTS:
        all_repetitions = 20000 // n
        if mode == 'pilot':
            repetitions = 1
        elif mode == 'balanced':
            repetitions = all_repetitions if n == 100 else min(10, all_repetitions)
        elif mode == 'full':
            repetitions = all_repetitions
        else:
            raise ValueError(mode)
        for rep in range(repetitions):
            start = rep * n
            cases.append({
                'case_id': f'n{n:05d}_r{rep:03d}',
                'N': n,
                'repeat': rep,
                'start_ordinal': start,
                'stop_ordinal_exclusive': start + n,
                'source': 'first 20,000 positions of immutable accepted-event master',
                'status': 'planned_not_run',
            })
    for n in POINTS:
        rows = [c for c in cases if c['N'] == n]
        intervals = sorted((c['start_ordinal'], c['stop_ordinal_exclusive']) for c in rows)
        assert all(a[1] <= b[0] for a, b in zip(intervals, intervals[1:])), 'Cohort overlap within N'
        assert all(b <= 20000 for a,b in intervals), 'Outside 20k cohort window'
    assert len([c for c in cases if c['N'] == 100]) == (200 if mode != 'pilot' else 1)
    return {
        'schema': 'full-dof-cohort-plan-v1',
        'purpose': 'Planning only. Does not generate events, modify geometry or fit alignment.',
        'master_required_accepted_events': 20000,
        'source_events_before_selection': 'unspecified',
        'source_root_sha256': None,
        'accepted_master_sha256': None,
        'study_mode': mode,
        'N_values': list(POINTS),
        'case_count': len(cases),
        'cohorts': cases,
        'warnings': [
            'No calibration was performed by this plan generator.',
            'The existing workflow has 5 SiMAPS measurements, not full 7-layer alignment information.',
            'All-layer all-6DOF unconstrained fits contain gauge freedoms and must be rank-tested before running.',
            'Different N cohorts overlap; only repeats at the same N are disjoint.',
            'Master selection must not use simulation Truth or alignment-closure quality to cherry-pick events.',
            'Bind real event IDs and checksums to every cohort before fitting.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accepted-events', type=int, default=20000,
                        help='Number of available accepted events; a minimum of 20000 is required')
    parser.add_argument('--mode', choices=['pilot', 'balanced', 'full'], default='pilot')
    parser.add_argument('--output', type=Path, required=True,
                        help='A new JSON file; refuse overwrites')
    args = parser.parse_args()
    plan = make_plan(args.accepted_events, args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f'Created {args.output} with {plan["case_count"]} planned cohorts; no fits were run.')
    print('N : repeats = ' + ', '.join(
        f'{n}:{sum(c["N"] == n for c in plan["cohorts"])}' for n in POINTS))


if __name__ == '__main__':
    main()
