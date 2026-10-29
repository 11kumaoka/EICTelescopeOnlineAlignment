"""Create independent barrel-compatible telescope fixtures; never modifies canonical Geometry.

Run from the repository root. Only writes a new child directory of result/.
The installed DD4hep_SiTrackerBarrel constructor is used without changes.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as E

REPO = Path(__file__).resolve().parents[2]


def prepare(output, initialize_metadata=False):
    output = output.resolve()
    if not output.is_relative_to(REPO / 'result') or output == REPO / 'result':
        raise ValueError('Output must be a new child of result/')
    nominal = REPO / 'geometry/nominal/seven_layer_telescope.xml'
    output.mkdir(parents=True, exist_ok=False)
    for scenario in ('nominal', 'translated', 'tilted'):
        root = E.parse(nominal).getroot()
        detectors = root.find('detectors')
        detectors.clear()
        # These logical compounds order real radial subdetectors by their IDs.
        for identifier, name, child in ((25, 'Vertex', 'SiMAPS'),
                                        (40, 'Tracker', 'MPGDPlane'),
                                        (44, 'TOF', 'ACLGADPlane')):
            detector = E.SubElement(detectors, 'detector', id=str(identifier),
                                    name=name, type='DD4hep_SubdetectorAssembly')
            E.SubElement(detector, 'composite', name=child)
        groups = (('SiMAPS', 31, 'SiMAPSHits', 'SiMAPSVis', range(5), .05),
                  ('MPGDPlane', 100, 'MPGDHits', 'MPGDVis', (5,), 3.),
                  ('ACLGADPlane', 92, 'ACLGADHits', 'ACLGADVis', (6,), .35))
        for name, identifier, readout, vis, layers, thickness in groups:
            detector = E.SubElement(detectors, 'detector', id=str(identifier), name=name,
                                    type='DD4hep_SiTrackerBarrel', readout=readout)
            module = E.SubElement(detector, 'module', name=name+'Module', vis=vis)
            E.SubElement(module, 'module_envelope', width='100.5*mm',
                         length='100.5*mm', thickness=f'{thickness+.6}*mm')
            # Keep the original sensor X/Y/Z and CartesianGridXZ/XY definitions.
            component = E.SubElement(module, 'module_component', width='100*mm',
                                     length=f'{thickness}*mm', thickness='100*mm',
                                     material='Silicon', sensitive='true', vis=vis)
            E.SubElement(component, 'position', x='.2*mm' if scenario=='translated' else '0',
                         y='.1*mm' if scenario=='translated' else '0', z='0')
            # DD4hep's XML RotationZYX conversion is checked against actual matrices.
            E.SubElement(component, 'rotation', x='-pi/2+.005' if scenario=='tilted' else '-pi/2',
                         y='-pi/2', z='0')
            for layer_id in layers:
                radius = 250 + 50*layer_id
                layer = E.SubElement(detector, 'layer', id=str(layer_id),
                                     module=name+'Module', vis='PlaneLayerVis')
                E.SubElement(layer, 'barrel_envelope', inner_r=f'{radius-thickness/2-1}*mm',
                             outer_r=f'{math.hypot(radius+thickness/2+.4,50.3)+1}*mm',
                             z_length='102*mm')
                E.SubElement(layer, 'rphi_layout', phi0='0', phi_tilt='0', nphi='1',
                             rc=f'{radius}*mm', dr='0')
                E.SubElement(layer, 'z_layout', dr='0', z0='0', nz='1')
        # Optional normal XML initialization; the default retains the previous
        # diagnostic-only fixture for reproducible negative controls.
        plugins=root.find('plugins')
        plugins.clear()
        if initialize_metadata:
            for name, *_ in groups:
                plugin=E.SubElement(plugins,'plugin',name='TestbenchBarrelMetadata')
                E.SubElement(plugin,'argument',value=name)
                E.SubElement(plugin,'argument',value='zXy')
                plugin=E.SubElement(plugins,'plugin',name='DD4hep_ParametersPlugin')
                E.SubElement(plugin,'argument',value=name)
                E.SubElement(plugin,'argument',value='layer_pattern: str=^layer[0-6]$')
        E.indent(root)
        E.ElementTree(root).write(output / (scenario+'.xml'), encoding='unicode')
    manifest = {'canonical_geometry': str(nominal.relative_to(REPO)),
                'canonical_sha256': hashlib.sha256(nominal.read_bytes()).hexdigest(),
                'rigid_rotation': [[0,0,1],[-1,0,0],[0,-1,0]],
                'rigid_translation_mm': [250,0,0],
                'scenarios': ['nominal','translated','tilted'],
                'normal_initialization_metadata': initialize_metadata,
                'barrel_field': 0, 'production_geometry_activated': False}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--initialize-metadata',action='store_true',help='Include normal DD4hep XML metadata plugins')
    args=parser.parse_args()
    prepare(args.output_dir,args.initialize_metadata)
