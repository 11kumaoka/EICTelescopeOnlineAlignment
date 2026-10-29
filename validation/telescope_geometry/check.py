"""Check independent barrel-compatible telescope Geometry gates from probe reports (read-only)."""
import argparse
import itertools
import json
import math
from pathlib import Path
import xml.etree.ElementTree as E


def check(report_path, fixture_path):
    report = json.loads(report_path.read_text())
    assert report['passed'] and report['tracking_geometry_available']
    planes = report['planes']
    layers = sorted(report['cylinder_layers'], key=lambda x:x['bounds'][0])
    assert len(planes)==len(layers)==report['official_surface_map_size']==7
    ids = [p['selected']['geometry_id'] for p in planes]
    assert len(set(ids))==7 and all(ids)
    assert len({p['volume_id'] for p in planes})==7
    root = E.parse(fixture_path).getroot()
    nominal = Path(__file__).resolve().parents[2]/'geometry/nominal/seven_layer_telescope.xml'
    def structural(element):
        return (element.tag,element.attrib,(element.text or '').strip(),tuple(structural(c) for c in element))
    assert structural(root.find('readouts'))==structural(E.parse(nominal).getroot().find('readouts'))
    previous_outer = 0.
    rows = []
    for plane, layer in zip(planes,layers):
        assert plane['equivalent'] and plane['bounds_equivalent'] and plane['is_plane_surface']
        assert plane['official_detector_identifier']==plane['volume_id']
        assert (plane['selected']['geometry_id'] >> 8) & ((1<<20)-1)
        number = plane['layer']
        thickness = .05 if number<5 else (3. if number==5 else .35)
        assert abs(plane['official_thickness_mm']-thickness)<1e-12
        half = plane['physical_half_lengths_mm']
        assert max(abs(a-b) for a,b in zip(half,[50,thickness/2,50]))<1e-10
        # axis_definitions=zXy gives u=-sensor Z, v=sensor X, n=-sensor Y.
        sensor_from_acts = ((0,0,-1),(1,0,0),(0,-1,0))
        selected = plane['local']['rotation']
        expected = [[sum(selected[i][k]*sensor_from_acts[k][j] for k in range(3))
                     for j in range(3)] for i in range(3)]
        physical = plane['physical_world_rotation']
        assert max(abs(physical[3*i+j]-expected[i][j]) for i in range(3) for j in range(3))<1e-12
        center = plane['local']['center_mm']
        corners = [[center[i]+sum(expected[i][k]*sign[k]*half[k] for k in range(3))
                    for i in range(3)] for sign in itertools.product((-1,1),repeat=3)]
        rmin = min(p[0] for p in corners)  # Conservative lower bound for positive x.
        rmax = max(math.hypot(p[0],p[1]) for p in corners)
        radius = layer['bounds'][0]
        inner, outer = radius-layer['thickness_mm']/2, radius+layer['thickness_mm']/2
        assert 0<inner<rmin and rmax<outer and previous_outer<inner
        assert all(abs(p[2])<layer['bounds'][1] for p in corners)
        group = 'SiMAPS' if number<5 else ('MPGDPlane' if number==5 else 'ACLGADPlane')
        detector = root.find(f"./detectors/detector[@name='{group}']")
        envelope = detector.find('./module/module_envelope')
        module_half = [float(envelope.get(a).split('*')[0])/2 for a in ('width','length','thickness')]
        # Nominal barrel module axes: X=-Global Y, Y=-Global Z, Z=Global X.
        btranspose = ((0,-1,0),(0,0,-1),(1,0,0))
        delta = (.2,.1,0) if report['scenario']=='translated' else (0,0,0)
        for sign in itertools.product((-1,1),repeat=3):
            global_offset = [sum(expected[i][k]*sign[k]*half[k] for k in range(3)) for i in range(3)]
            module_offset = [sum(btranspose[i][k]*global_offset[k] for k in range(3))+delta[i] for i in range(3)]
            assert all(abs(module_offset[i])<module_half[i] for i in range(3))
        air_corners = [[250+50*number+sign[2]*module_half[2],-sign[0]*module_half[0],-sign[1]*module_half[1]]
                       for sign in itertools.product((-1,1),repeat=3)]
        tube = detector.find(f"./layer[@id='{number}']/barrel_envelope")
        tube_values = [float(tube.get(a).split('*')[0]) for a in ('inner_r','outer_r','z_length')]
        assert min(p[0] for p in air_corners)>tube_values[0]
        assert max(math.hypot(p[0],p[1]) for p in air_corners)<tube_values[1]
        assert max(abs(p[2]) for p in air_corners)<tube_values[2]/2
        previous_outer = outer
        rows.append({'layer':number,'surface_radius_mm':math.hypot(center[0],center[1]),
                     'cylinder_radius_mm':radius,'cylinder_thickness_mm':layer['thickness_mm']})
    navigation = {n['name']:n for n in report['navigation']}
    for name in ('central','off_axis'):
        assert navigation[name]['hits']==list(range(7))
        assert navigation[name]['terminated'] and navigation[name]['end_of_world']
    for name in ('z_miss','opposite_phi'):
        assert not navigation[name]['hits'] and navigation[name]['terminated']
    assert not navigation['outside_world']['hits'] and navigation['outside_world']['end_of_world']
    print(json.dumps({'scenario':report['scenario'],'passed':True,'layers':rows,
                      'outside_world_entry_supported':False},indent=2))


def check_integration(report_path):
    report=json.loads(report_path.read_text())
    assert report['provider']=='official' and report['experimental_metadata']=='none'
    assert report['original_hierarchy']==report['selected_hierarchy']
    propagation={row['name']:row for row in report['propagation']}
    for name in ('inside_annulus','before_first_plane','transformed_gun',
                 'on_actual_entry_boundary','entry_with_interior_margin'):
        assert propagation[name]['ok'] and propagation[name]['hits']==list(range(7)),name
    outside=propagation['outside_inner_boundary']
    assert not outside['ok'] and not outside['hits']
    assert outside['error']=='No start volume could be resolved'
    assert report['entry_transport']['ok'] and not report['entry_transport']['material_aware']
    assert abs(report['entry_transport']['path_length_mm']-7.975)<1e-9
    intersections={row['name']:row['hits'] for row in report['sensitive_intersections']}
    assert intersections['outside_start']==list(range(7)) and not intersections['bounded_miss']
    surfaces=report['material_inventory']['surfaces']
    volumes=report['material_inventory']['volumes']
    assert len(surfaces)==56 and len(volumes)==5
    assert all(not row['material_assigned'] for row in surfaces+volumes)
    from collections import Counter
    roles=Counter(role['role'] for row in surfaces for role in row['roles'])
    assert roles=={'sensitive':7,'approach':14,'layer_representation':17,'boundary':20}
    assert all(plane['dd4hep_material']=='Silicon' for plane in report['planes'])
    print(json.dumps({'integration_passed':True,'diagnostic_metadata_adapter_used':False,
                     'material_model_complete':False,'material_surface_count':len(surfaces),
                     'material_roles':dict(roles),'explicit_boundary_transport_passed':True},indent=2))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--integration',action='store_true',help='Check normal initialization, full propagation and material inventory')
    args = parser.parse_args()
    check(args.report,args.fixture)
    if args.integration:check_integration(args.report)
