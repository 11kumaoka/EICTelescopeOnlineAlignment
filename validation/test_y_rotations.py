"""Physical-center and scope checks for the explicit L1 Y/SO(3) extension."""
import math
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from geometry.transforms import rotation_exp
from validation.telescope_closure.closure import displaced_xml,MODULE_TO_GLOBAL
from validation.telescope_closure.rotation import component_matrix,labels,rotation_labels,fixture_shifts


class NativeYRotations(unittest.TestCase):
    def test_world_translation_and_full_rotation_have_independent_centers(self):
        xml=('<lccdd><detectors><detector name="SiMAPS"><module name="original">'
             '<module_envelope width="100.5*mm" length="100.5*mm" thickness=".65*mm"/>'
             '<module_component material="Silicon" sensitive="true" width="100*mm" '
             'length=".05*mm" thickness="100*mm"><position x="0*mm" y="0*mm" z="0*mm"/>'
             '<rotation x="-pi/2" y="-pi/2" z="0"/></module_component></module>'
             '<layer id="1" module="original"><barrel_envelope inner_r="298.975*mm" '
             f'outer_r="{math.hypot(300.425,50.3)+1}*mm" z_length="102*mm"/>'
             '<rphi_layout phi0="0" phi_tilt="0" nphi="1" rc="300*mm" dr="0"/>'
             '<z_layout z0="0" nz="1" dr="0"/></layer></detector></detectors></lccdd>')
        def active(path):
            detector=ET.parse(path).getroot().find('./detectors/detector');name=detector.find('layer').get('module')
            return detector.find(f"./module[@name='{name}']/module_component")
        with tempfile.TemporaryDirectory() as tmp:
            a,b,c=[Path(tmp)/n for n in ('a.xml','b.xml','c.xml')];a.write_text(xml)
            w=np.array([.0018,.0025,-.0025]);m=np.array(MODULE_TO_GLOBAL);start=m@component_matrix(active(a))
            displaced_xml(a,b,{1:.150},rotations={1:w},rotation_axis='xyz')
            component=active(b);pos=np.array([float(component.find('position').get(k).replace('*mm','')) for k in 'xyz'])
            np.testing.assert_allclose(m@pos,[0,.150,0],atol=1e-15,rtol=0)
            np.testing.assert_allclose(m@component_matrix(component),np.array(rotation_exp(w))@start,atol=5e-13,rtol=0)
            displaced_xml(b,c,{1:-.150},rotations={1:-w},rotation_axis='xyz')
            component=active(c);pos=np.array([float(component.find('position').get(k).replace('*mm','')) for k in 'xyz'])
            np.testing.assert_allclose(m@pos,[0,0,0],atol=1e-15,rtol=0)
            np.testing.assert_allclose(m@component_matrix(component),start,atol=5e-13,rtol=0)

    def test_translation_cannot_be_treated_as_a_fourth_rotation_component(self):
        for mode in ('l1_y_rx_ry_rz','l1_y_rx_ry_rz_negative'):
            self.assertEqual(labels(mode),[700102,700104,700105,700106])
            self.assertEqual(rotation_labels(mode),[700104,700105,700106])
            self.assertEqual(fixture_shifts(mode),{1:.150})
        self.assertEqual(labels('l1_rx_ry_rz'),[700104,700105,700106])
        self.assertEqual(fixture_shifts('l1_rx_ry_rz'),{})

    def test_parameter_configuration_declares_mixed_units_and_fixed_axes(self):
        import json
        config=json.loads((Path(__file__).resolve().parents[1]/'calibration/alignment/config/l1_global_y_rx_ry_rz.json').read_text())
        self.assertEqual([p['label'] for p in config['parameters']],[700102,700104,700105,700106])
        self.assertEqual([p['unit'] for p in config['parameters']],['mm','rad','rad','rad'])
        self.assertTrue(all(p['layer']==1 and p['system']==31 for p in config['parameters']))

    def test_refinement_requires_explicit_four_parameter_scope(self):
        from types import SimpleNamespace
        from validation.telescope_closure.rotation import refine
        for mode,opt_in in [('l1_rx_ry_rz',True),('l1_y_rx_ry_rz',False)]:
            with self.assertRaisesRegex(ValueError,'explicit four-parameter opt-in'):
                refine(SimpleNamespace(mode=mode,refine_once=opt_in))

    def test_refinement_refuses_incomplete_or_modified_parent(self):
        from types import SimpleNamespace
        from validation.telescope_closure.rotation import refine
        from research_common import stage,done
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);args=SimpleNamespace(mode='l1_y_rx_ry_rz',refine_once=True,result=root)
            with self.assertRaisesRegex(RuntimeError,'Complete postsolve first'):
                refine(args)
            parent=stage(root,'postsolve');data=parent/'millepede.res';data.write_text('preserved parent')
            done(parent,passed=True);data.write_text('modified parent')
            with self.assertRaisesRegex(RuntimeError,'Modified stage output'):
                refine(args)
            self.assertFalse((root/'refinement').exists())
