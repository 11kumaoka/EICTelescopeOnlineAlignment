"""Approved canonical layout, exact Air growth and physical refusal contracts."""
import copy
import math
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from geometry.operations import GeometrySafetyError, displaced_xml, validate_geometry
from geometry.safety import normalize_air_envelopes
from geometry.transforms import rotation_exp


class NativeGeometrySafety(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name);self.nominal=self.directory/'nominal.xml'
        self.root=ET.Element('lccdd');detectors=ET.SubElement(self.root,'detectors')
        world=ET.SubElement(self.root,'world',material='Air')
        ET.SubElement(world,'shape',type='Box',dx='1000*mm',dy='1000*mm',dz='1000*mm')
        for name,layers,thickness in (('SiMAPS',range(5),.05),('MPGDPlane',(5,),3.),('ACLGADPlane',(6,),.35)):
            detector=ET.SubElement(detectors,'detector',name=name)
            for layer in layers:
                module=ET.SubElement(detector,'module',name=f'module_{layer}')
                ET.SubElement(module,'module_envelope',width='100.5*mm',length='100.5*mm',thickness=f'{thickness+.6}*mm')
                sensor=ET.SubElement(module,'module_component',material='Silicon',sensitive='true',
                                     width='100*mm',length=f'{thickness}*mm',thickness='100*mm')
                ET.SubElement(sensor,'position',x='0',y='0',z='0')
                ET.SubElement(sensor,'rotation',x='-pi/2',y='-pi/2',z='0')
                radius=250+50*layer;placement=ET.SubElement(detector,'layer',id=str(layer),module=module.get('name'))
                ET.SubElement(placement,'barrel_envelope',inner_r=f'{radius-thickness/2-1}*mm',
                    outer_r=f'{math.hypot(radius+thickness/2+.4,50.3)+1}*mm',z_length='102*mm')
                ET.SubElement(placement,'rphi_layout',phi0='0',phi_tilt='0',nphi='1',rc=f'{radius}*mm',dr='0')
                ET.SubElement(placement,'z_layout',z0='0',nz='1',dr='0')
        self.sensors=[{'layer':layer,'volume_id':layer+1,'geometry_id':layer+101,
            'center_mm':[250.+50*layer,0.,0.],
            'dd4hep_rotation':[[0.,-1.,0.],[0.,0.,1.],[-1.,0.,0.]],
            'acts_rotation':[[0.,0.,1.],[-1.,0.,0.],[0.,-1.,0.]]} for layer in range(7)]
        ET.ElementTree(self.root).write(self.nominal,encoding='unicode')

    def write(self,root):
        path=self.directory/'modified.xml';ET.ElementTree(root).write(path,encoding='unicode');return path

    def layer(self,root,number=1):return root.find(f"./detectors/detector/layer[@id='{number}']")

    def module(self,root,number=1):
        layer=self.layer(root,number)
        return next(m for m in root.findall('./detectors/detector/module') if m.get('name')==layer.get('module'))

    def test_actual_pose_contract_covers_all_seven_layers(self):
        report=validate_geometry(self.sensors,geometry_xml=self.nominal)
        self.assertTrue(report['passed']);self.assertEqual(len(report['sensors']),7)
        self.assertEqual(len(report['layer_radial_gaps_mm']),6)

    def test_noncanonical_layouts_are_refused_instead_of_using_wrong_parent_axes(self):
        for tag,key,value in [('rphi_layout','phi0','.1'),('rphi_layout','phi_tilt','.1'),
                              ('rphi_layout','nphi','2'),('rphi_layout','dr','.1*mm'),
                              ('z_layout','z0','1*mm'),('z_layout','nz','2'),('z_layout','dr','.1*mm')]:
            with self.subTest(tag=tag,key=key):
                root=copy.deepcopy(self.root);self.layer(root).find(tag).set(key,value)
                with self.assertRaisesRegex(GeometrySafetyError,'Unsupported telescope layout'):
                    validate_geometry(self.sensors,geometry_xml=self.write(root))

    def test_nonpositive_nonfinite_and_reversed_dimensions_are_refused(self):
        for tag,key,value in [('module_envelope','width','0'),('module_component','length','-1*mm'),
                              ('module_envelope','thickness','1e309'),('barrel_envelope','inner_r','0'),
                              ('barrel_envelope','outer_r','200*mm')]:
            with self.subTest(tag=tag,key=key,value=value):
                root=copy.deepcopy(self.root)
                node=self.layer(root).find(tag) if tag=='barrel_envelope' else self.module(root).find(tag)
                node.set(key,value)
                with self.assertRaises(GeometrySafetyError):validate_geometry(self.sensors,geometry_xml=self.write(root))

    def test_actual_module_layer_world_and_overlap_failures_are_distinct(self):
        root=copy.deepcopy(self.root);sensors=copy.deepcopy(self.sensors)
        self.module(root).find('module_component/position').set('x','-.5*mm');sensors[1]['center_mm'][1]=.5
        with self.assertRaisesRegex(GeometrySafetyError,'module containment failure on layer 1'):
            validate_geometry(sensors,geometry_xml=self.write(root))
        root=copy.deepcopy(self.root);self.module(root).find('module_envelope').set('width','200*mm')
        with self.assertRaisesRegex(GeometrySafetyError,'layer containment failure on layer 1'):
            validate_geometry(self.sensors,geometry_xml=self.write(root))
        root=copy.deepcopy(self.root);self.layer(root).find('barrel_envelope').set('outer_r','355*mm')
        with self.assertRaisesRegex(GeometrySafetyError,'layer Air envelopes overlap'):
            validate_geometry(self.sensors,geometry_xml=self.write(root))
        root=copy.deepcopy(self.root);root.find('world/shape').set('dz','50*mm')
        with self.assertRaisesRegex(GeometrySafetyError,'world containment failure'):
            validate_geometry(self.sensors,geometry_xml=self.write(root))

    def test_wrong_actual_pose_and_act_sensor_axes_are_refused(self):
        sensors=copy.deepcopy(self.sensors);sensors[1]['center_mm'][0]+=.01
        with self.assertRaisesRegex(GeometrySafetyError,'center disagrees'):
            validate_geometry(sensors,geometry_xml=self.nominal)
        sensors=copy.deepcopy(self.sensors);sensors[1]['acts_rotation']=(np.asarray(sensors[1]['acts_rotation'])@np.diag([-1.,-1.,1.])).tolist()
        with self.assertRaisesRegex(GeometrySafetyError,'sensor axes disagree'):
            validate_geometry(sensors,geometry_xml=self.nominal)

    def test_air_growth_cannot_replace_a_different_world_material(self):
        root=copy.deepcopy(self.root);root.find('world').set('material','Vacuum')
        with self.assertRaisesRegex(GeometrySafetyError,'unchanged Air world'):
            validate_geometry(self.sensors,geometry_xml=self.write(root))

    def test_growth_preserves_previous_clearance_even_when_original_box_contains_sensor(self):
        output=self.directory/'grown.xml';displaced_xml(self.nominal,output,{1:.08})
        candidate=ET.parse(output).getroot();before=self.module(self.root);after=self.module(candidate)
        self.assertAlmostEqual(float(after.find('module_envelope').get('width').replace('*mm','')),100.66,places=12)
        self.assertEqual(before.find('module_component').attrib,after.find('module_component').attrib)
        normalize_air_envelopes(before,after,self.layer(self.root),self.layer(candidate))

    def test_gratuitous_growth_and_shrinkage_are_refused_at_one_binary64_ulp(self):
        output=self.directory/'grown.xml';displaced_xml(self.nominal,output,{1:.08})
        base=ET.parse(output).getroot()
        for tag,key,direction in [('module_envelope','width',math.inf),('module_envelope','width',-math.inf),
                                  ('barrel_envelope','outer_r',math.inf)]:
            with self.subTest(tag=tag,key=key,direction=direction):
                candidate=copy.deepcopy(base);module=self.module(candidate);layer=self.layer(candidate)
                node=module.find(tag) if tag=='module_envelope' else layer.find(tag)
                old=float(node.get(key).replace('*mm',''));node.set(key,f'{math.nextafter(old,direction):.17g}*mm')
                with self.assertRaisesRegex(ValueError,'Unexpected Air-envelope growth'):
                    normalize_air_envelopes(self.module(self.root),module,self.layer(self.root),layer)

    def test_twenty_mrad_compound_pose_can_be_contained_without_an_angle_cut(self):
        vector=[.018,.02,-.015];output=self.directory/'finite.xml'
        displaced_xml(self.nominal,output,{1:.08},rotations={1:vector},rotation_axis='xyz')
        sensors=copy.deepcopy(self.sensors);rotation=np.array(rotation_exp(vector))
        sensors[1]['center_mm'][1]=.08
        for key in ('dd4hep_rotation','acts_rotation'):
            sensors[1][key]=(rotation@np.array(sensors[1][key])).tolist()
        self.assertTrue(validate_geometry(sensors,geometry_xml=output)['passed'])


if __name__=='__main__':unittest.main()
