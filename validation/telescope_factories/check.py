#!/usr/bin/env python3
"""Check the real factory audit, persisted EDM links, and standalone reference."""
import argparse
import json
import math
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output must be new')
    report = json.loads(args.audit.read_text())
    reference = json.loads(args.reference.read_text())
    assert report['passed'] and reference['passed']
    assert report['anisotropic_xz_covariance_control_passed']
    assert report['unchanged_xy_covariance_control_passed']
    assert report['seed_inside_tracking_geometry']
    assert (report['native_tracks'], report['edm_tracks'], report['measurements'], report['outliers'], report['holes']) == (1,1,5,0,2)
    # EDM parameters/locations are float32; the standalone reference uses double
    # curvilinear parameters. Compare the observable fit, not identical bits.
    delta_chi2 = abs(report['chi2'] - reference['track']['chi2'])
    assert delta_chi2 < 0.01, delta_chi2
    expected_measurements = {m['geometry_id']: m for m in reference['measurements']}
    for measurement in report['measurement_details']:
        expected = expected_measurements[measurement['geometry_id']]
        assert measurement['cell_id'] == expected['cell_id']
        assert measurement['volume_id'] == expected['volume_id']
        assert max(abs(a-b) for a,b in zip(measurement['loc_mm'],expected['loc_mm'])) < 1e-6
        a,b,c = measurement['covariance_mm2']
        assert abs(a-expected['covariance_mm2'][0][0]) < 1e-10
        assert abs(b-expected['covariance_mm2'][1][1]) < 1e-10
        assert abs(c-expected['covariance_mm2'][0][1]) < 1e-10
    assert set(report['hole_geometry_ids']) == set(reference['excluded_readout_holes'])
    expected_states = {state['geometry_id']: state for state in reference['states']}
    max_position_difference = 0.
    for state in report['smoothed_states']:
        expected = expected_states[state['geometry_id']]
        difference = max(abs(a-b) for a,b in zip(state['position_mm'],expected['position_mm']))
        max_position_difference = max(max_position_difference,difference)
        assert difference < 1e-5, difference
        assert max(abs(a-b) for a,b in zip(state['residual_mm'],expected['residual_mm'])) < 1e-5
        assert all(x > 0 and math.isfinite(x) for x in state['local_covariance_diagonal_mm2'])
    residuals = [abs(x) for state in report['smoothed_states'] for x in state['residual_mm']]
    assert len(residuals) == 10 and max(residuals) < 0.02
    import ROOT
    from podio import reading
    reader = reading.get_reader(str(args.root))
    events = reader.get('events')
    frames = list(events)
    assert len(frames) == 1
    frame = frames[0]
    tracks = frame.get('TelescopeTracks')
    hits = frame.get('TelescopeHits')
    measurements = frame.get('TelescopeMeasurements')
    seeds = frame.get('TelescopeSeeds')
    assert len(tracks)==1 and len(hits)==5 and len(measurements)==5 and len(seeds)==1
    track = tracks[0]
    assert len(track.getMeasurements()) == 5
    assert track.getTrajectory().isAvailable() and seeds[0].getParams().isAvailable()
    trajectory = track.getTrajectory()
    assert trajectory.getNMeasurements()==5 and trajectory.getNOutliers()==0 and trajectory.getNHoles()==2
    assert trajectory.getSeed() == seeds[0]
    assert len(trajectory.getTrackParameters())==1
    assert trajectory.getTrackParameters()[0].isAvailable()
    assert all(m.getHits()[0].isAvailable() for m in measurements)
    assert len(frame.get('TelescopeHitAssociations')) == 0
    assert len(frame.get('TelescopeTrackAssociations')) == 0
    assert track.getNdf() == 10 and track.getPdg() == 11 and track.getCharge() == -1
    assert abs(track.getChi2()-report['chi2']) < 1e-4
    result = {'passed':True, 'one_event_read_back':True, 'measurement_and_seed_links_valid':True,
              'empty_associations_verified':True, 'chi2_difference_from_standalone':delta_chi2,
              'max_abs_smoothed_residual_mm':max(residuals), 'max_smoothed_position_difference_mm':max_position_difference, 'material_free':True,
              'rawhit_digitization_tested':False, 'alignment_tested':False}
    with args.output.open('x') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
