"""Safety boundaries for the experimentally validated native one-axis XML charts."""
import math
import unittest
import xml.etree.ElementTree as ET
from validation.telescope_closure.rotation import rotate_component


class NativeRotationScope(unittest.TestCase):
    def component(self):
        return ET.fromstring('<module_component><rotation x="-pi/2" y="-pi/2" z="0"/></module_component>')

    def test_signed_increment_and_current_orientation(self):
        for axis,attribute in [('x','y'),('y','x')]:
            component=self.component()
            rotate_component(component,.004,axis)
            rotate_component(component,-.005,axis)
            self.assertAlmostEqual(float(component.find('rotation').get(attribute)),-math.pi/2+.001,places=14)
            self.assertEqual(component.find('rotation').get('z'),'0')

    def test_coupled_rotation_is_rejected(self):
        for first,second in [(a,b) for a in 'xyz' for b in 'xyz' if a!=b]:
            component=self.component();rotate_component(component,.002,first)
            before=ET.tostring(component)
            with self.assertRaises(ValueError):rotate_component(component,.003,second)
            self.assertEqual(ET.tostring(component),before)

    def test_unsupported_or_nonfinite_update_is_rejected(self):
        for angle,axis in [(.001,'w'),(math.inf,'y'),(math.nan,'x')]:
            component=self.component();before=ET.tostring(component)
            with self.assertRaises(ValueError):rotate_component(component,angle,axis)
            self.assertEqual(ET.tostring(component),before)

    def test_rz_equivalent_chart_and_signed_composition(self):
        component=self.component()
        rotate_component(component,-.005,'z')
        rotate_component(component,.002,'z')
        r=component.find('rotation')
        self.assertEqual(r.get('x'),'0')
        self.assertAlmostEqual(float(r.get('y')),-math.pi/2+.003,places=14)
        self.assertAlmostEqual(float(r.get('z')),math.pi/2,places=14)

    def test_rz_rejects_inexact_zero_chart(self):
        component=self.component();component.find('rotation').set('y',str(-math.pi/2+.0001))
        before=ET.tostring(component)
        with self.assertRaises(ValueError):rotate_component(component,.003,'z')
        self.assertEqual(ET.tostring(component),before)

    def test_coupled_update_keeps_noncommuting_matrix_terms(self):
        import numpy as np
        from validation.telescope_closure.rotation import component_matrix
        from validation.telescope_closure.closure import MODULE_TO_GLOBAL
        from geometry.transforms import rotation_exp,rotation_log
        component=self.component();parent=np.array(MODULE_TO_GLOBAL)
        start=parent@component_matrix(component)
        a=[0.,.0025,-.0025];b=[0.,-.001,.0015]
        rotate_component(component,a[1:],'yz');rotate_component(component,b[1:],'yz')
        actual=parent@component_matrix(component)
        expected=np.array(rotation_exp(b))@rotation_exp(a)@start
        self.assertTrue(np.allclose(actual,expected,atol=5e-13,rtol=0))
        self.assertGreater(abs(rotation_log((actual@start.T).tolist())[0]),1e-7)
        rotate_component(component,[-b[1],-b[2]],'yz')
        rotate_component(component,[-a[1],-a[2]],'yz')
        self.assertTrue(np.allclose(parent@component_matrix(component),start,atol=5e-13,rtol=0))

    def test_coupled_singular_chart_refusal_is_atomic(self):
        component=self.component();before=ET.tostring(component)
        with self.assertRaises(ValueError):rotate_component(component,[.002,1e-7],'yz')
        self.assertEqual(ET.tostring(component),before)

    def test_coupled_scope_refuses_free_rx_and_invalid_increment(self):
        for increment in ([math.nan,0.],[math.inf,0.],[0.,0.,0.]):
            component=self.component();before=ET.tostring(component)
            with self.assertRaises(ValueError):rotate_component(component,increment,'yz')
            self.assertEqual(ET.tostring(component),before)
        component=self.component();rotate_component(component,.002,'x')
        before=ET.tostring(component)
        with self.assertRaises(ValueError):rotate_component(component,[.002,-.002],'yz')
        self.assertEqual(ET.tostring(component),before)

    def test_coupled_nominal_microradian_probes_use_validated_axis_charts(self):
        import numpy as np
        from validation.telescope_closure.rotation import component_matrix
        from validation.telescope_closure.closure import MODULE_TO_GLOBAL
        from geometry.transforms import rotation_exp
        parent=np.array(MODULE_TO_GLOBAL)
        for delta in ([1e-6,0.],[-1e-6,0.],[0.,1e-6],[0.,-1e-6]):
            component=self.component();before=parent@component_matrix(component)
            rotate_component(component,delta,'yz')
            self.assertTrue(np.allclose(parent@component_matrix(component),np.array(rotation_exp([0.,*delta]))@before,atol=5e-13,rtol=0))

    def test_partial_per_track_information_can_form_full_global_rank(self):
        import numpy as np
        from validation.telescope_alignment.check import profile
        def rows(a,b):
            result=[]
            for layer in range(5):
                for coordinate in range(2):
                    local={1+coordinate:1.,3+coordinate:float(layer)}
                    v=(.3,.4)[coordinate] if layer==1 else 0.
                    result.append((0.,1.,local,{700105:a*v,700106:b*v}))
            return result
        with self.assertRaises(AssertionError):profile(rows(1.,2.),[700105,700106])
        first=profile(rows(1.,2.),[700105,700106],require_global_rank=False)[0]
        second=profile(rows(2.,1.),[700105,700106],require_global_rank=False)[0]
        self.assertEqual(np.linalg.matrix_rank(first),1)
        self.assertEqual(np.linalg.matrix_rank(first+second),2)
        self.assertTrue(np.all(np.linalg.eigvalsh(first+second)>0))

    def test_repeating_a_weak_track_does_not_create_global_observability(self):
        import numpy as np
        from validation.telescope_alignment.check import profile
        rows=[]
        for layer in range(5):
            for j in range(2):
                value=1. if layer==1 else 0.
                rows.append((0.,1.,{1+j:1.,3+j:float(layer)},{700105:value,700106:2*value}))
        normal=profile(rows,[700105,700106],require_global_rank=False)[0]
        self.assertEqual(np.linalg.matrix_rank(4096*normal),1)

    def test_three_axis_target_and_noncommuting_inverse(self):
        import numpy as np
        from validation.telescope_closure.rotation import component_matrix
        from validation.telescope_closure.closure import MODULE_TO_GLOBAL
        from geometry.transforms import rotation_exp
        parent=np.array(MODULE_TO_GLOBAL);component=self.component()
        start=parent@component_matrix(component)
        a=np.array([.0018,.0025,-.0025]);b=np.array([-.0007,-.001,.0015])
        rotate_component(component,a,'xyz');rotate_component(component,b,'xyz')
        self.assertTrue(np.allclose(parent@component_matrix(component),np.array(rotation_exp(b))@rotation_exp(a)@start,atol=5e-13,rtol=0))
        rotate_component(component,-b,'xyz');rotate_component(component,-a,'xyz')
        self.assertTrue(np.allclose(parent@component_matrix(component),start,atol=5e-13,rtol=0))

    def test_three_axis_nominal_derivative_charts(self):
        import numpy as np
        from validation.telescope_closure.rotation import component_matrix
        from validation.telescope_closure.closure import MODULE_TO_GLOBAL
        from geometry.transforms import rotation_exp
        parent=np.array(MODULE_TO_GLOBAL)
        for index in range(3):
            for sign in (-1.,1.):
                component=self.component();start=parent@component_matrix(component)
                delta=np.zeros(3);delta[index]=sign*1e-6
                rotate_component(component,delta,'xyz')
                self.assertTrue(np.allclose(parent@component_matrix(component),np.array(rotation_exp(delta))@start,atol=5e-13,rtol=0))

    def test_three_axis_invalid_and_ambiguous_updates_are_atomic(self):
        for increment in ([math.nan,0.,0.],[math.inf,0.,0.],[0.,0.],[0.,.002,1e-7]):
            component=self.component();before=ET.tostring(component)
            with self.assertRaises(ValueError):rotate_component(component,increment,'xyz')
            self.assertEqual(ET.tostring(component),before)

    def test_three_axis_old_coupled_scope_stays_restricted(self):
        component=self.component();rotate_component(component,[.0018,.0025,-.0025],'xyz')
        before=ET.tostring(component)
        with self.assertRaises(ValueError):rotate_component(component,[.001,-.001],'yz')
        self.assertEqual(ET.tostring(component),before)

    def test_finite_updates_above_ten_mrad_keep_exact_rotation_composition(self):
        import numpy as np
        from geometry.transforms import rotation_exp
        from validation.telescope_closure.closure import MODULE_TO_GLOBAL
        from validation.telescope_closure.rotation import component_matrix
        parent=np.array(MODULE_TO_GLOBAL)
        cases=[('x',.02,[.02,0.,0.]),('y',-.02,[0.,-.02,0.]),
               ('z',.02,[0.,0.,.02]),('yz',[.02,-.015],[0.,.02,-.015]),
               ('xyz',[.018,.02,-.015],[.018,.02,-.015])]
        for axis,increment,vector in cases:
            with self.subTest(axis=axis):
                component=self.component();before=parent@component_matrix(component)
                rotate_component(component,increment,axis)
                np.testing.assert_allclose(parent@component_matrix(component),
                    np.array(rotation_exp(vector))@before,atol=5e-13,rtol=0)

    def test_three_partial_tracks_can_provide_rank_three(self):
        import numpy as np
        from validation.telescope_alignment.check import profile
        labels=[700104,700105,700106];matrices=[]
        for selected in range(3):
            rows=[]
            for layer in range(5):
                for j in range(2):
                    rows.append((0.,1.,{1+j:1.,3+j:float(layer)},
                        {k:(1. if layer==1 and index==selected else 0.) for index,k in enumerate(labels)}))
            matrices.append(profile(rows,labels,require_global_rank=False)[0])
        self.assertTrue(all(np.linalg.matrix_rank(m)==1 for m in matrices))
        self.assertEqual(np.linalg.matrix_rank(sum(matrices)),3)
        self.assertEqual(np.linalg.matrix_rank(4096*matrices[0]),1)
