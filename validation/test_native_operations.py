"""Translation-only XML spelling compatibility without permitting extra motion."""
from pathlib import Path
import math
import tempfile
import unittest
import xml.etree.ElementTree as ET

from calibration.native_operations import LABELS, _xml_identity, displaced_xml


class NativeOperationXMLIdentity(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root=Path(directory.name)
        self.nominal=self.root/'nominal.xml'
        self.candidate=self.root/'candidate.xml'
        root=ET.Element('lccdd')
        detector=ET.SubElement(ET.SubElement(root,'detectors'),'detector',name='SiMAPS',id='31')
        module=ET.SubElement(detector,'module',name='shared')
        ET.SubElement(module,'module_envelope',width='100.5*mm',length='100.5*mm',thickness='0.65*mm')
        component=ET.SubElement(module,'module_component',material='Silicon',sensitive='true',
                                width='100*mm',length='0.05*mm',thickness='100*mm')
        # These literal zeros reproduce the actual nominal compact XML.
        ET.SubElement(component,'position',x='0',y='0',z='0')
        ET.SubElement(component,'rotation',x='-pi/2',y='-pi/2',z='0')
        for layer in range(5):
            radius=250+50*layer
            placement=ET.SubElement(detector,'layer',id=str(layer),module='shared')
            ET.SubElement(placement,'barrel_envelope',inner_r=f'{radius-1.025}*mm',
                          outer_r=f'{math.hypot(radius+.425,50.3)+1}*mm',z_length='102*mm')
            ET.SubElement(placement,'rphi_layout',phi0='0',phi_tilt='0',nphi='1',
                          rc=f'{radius}*mm',dr='0')
            ET.SubElement(placement,'z_layout',z0='0',nz='1',dr='0')
        ET.ElementTree(root).write(self.nominal,encoding='unicode')
        displaced_xml(self.nominal,self.candidate,{1:.14914,2:.20318})

    def component(self,tree,layer):
        detector=tree.getroot().find("./detectors/detector[@name='SiMAPS']")
        name=detector.find(f"./layer[@id='{layer}']").get('module')
        return detector.find(f"./module[@name='{name}']/module_component")

    def write(self,tree):
        tree.write(self.candidate,encoding='unicode')

    def check(self):
        _xml_identity(self.nominal,self.candidate,LABELS,'l1_l2_y')

    def test_original_zero_unit_serialization_passes_without_rewriting_xml(self):
        before=self.candidate.read_bytes()
        tree=ET.parse(self.candidate)
        for layer in (1,2):
            position=self.component(tree,layer).find('position')
            self.assertEqual(position.get('y'),'0*mm')
            self.assertEqual(position.get('z'),'0*mm')
        self.check()
        self.assertEqual(self.candidate.read_bytes(),before)

    def test_equivalent_zero_units_preserve_the_physical_position(self):
        tree=ET.parse(self.candidate)
        self.component(tree,1).find('position').set('y','0.0*cm')
        self.write(tree)
        self.check()

    def test_actual_nonselected_motion_is_refused_without_a_tolerance(self):
        original=self.candidate.read_bytes()
        for attribute in ('y','z'):
            with self.subTest(attribute=attribute):
                self.candidate.write_bytes(original)
                tree=ET.parse(self.candidate)
                self.component(tree,1).find('position').set(attribute,'1e-30*mm')
                self.write(tree)
                with self.assertRaises(AssertionError):self.check()

    def test_rotation_change_is_refused(self):
        tree=ET.parse(self.candidate)
        self.component(tree,2).find('rotation').set('z','0.0001')
        self.write(tree)
        with self.assertRaises(AssertionError):self.check()

    def test_material_or_shape_change_is_refused(self):
        original=self.candidate.read_bytes()
        for key,value in (('material','Air'),('width','99*mm')):
            with self.subTest(attribute=key):
                self.candidate.write_bytes(original)
                tree=ET.parse(self.candidate)
                self.component(tree,1).set(key,value)
                self.write(tree)
                with self.assertRaises(AssertionError):self.check()

    def test_fixed_sensor_definition_change_is_refused(self):
        tree=ET.parse(self.candidate)
        self.component(tree,0).find('position').set('x','0.001*mm')
        self.write(tree)
        with self.assertRaises(AssertionError):self.check()

    def test_unrelated_detector_attribute_change_is_refused(self):
        tree=ET.parse(self.candidate)
        tree.getroot().find('./detectors/detector').set('id','32')
        self.write(tree)
        with self.assertRaises(AssertionError):self.check()


if __name__=='__main__':unittest.main()
