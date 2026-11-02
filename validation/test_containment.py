"""Independent frame and joint-design checks for unchanged-envelope fixtures."""
import unittest
import numpy as np
from geometry.transforms import rotation_exp
from validation.telescope_closure.containment import corners_in_module,ellipsoid_offsets
from validation.telescope_closure.rotation import injected_vector,labels

class PhysicalContainment(unittest.TestCase):
    def test_parent_frame_and_world_motion_do_not_change_clearance(self):
        center=np.array([2.,3.,4.]);rotation=np.array(rotation_exp([.1,-.03,.02]));parent=np.eye(3)
        a=corners_in_module(center,rotation,center,parent,np.array([3.,.2,2.]),np.array([4.,2.,4.]))
        q=np.array(rotation_exp([.3,.2,-.1]));d=np.array([5.,-4.,2.])
        b=corners_in_module(q@center+d,q@rotation,q@center+d,q@parent,np.array([3.,.2,2.]),np.array([4.,2.,4.]))
        np.testing.assert_allclose(a['clearance_mm'],b['clearance_mm'],atol=3e-15,rtol=0)

    def test_small_tilts_obey_first_order_radial_extent(self):
        r0=np.array([[0.,-1.,0.],[0.,0.,1.],[-1.,0.,0.]])
        w=[0.,-3e-5,2e-5];r=np.array(rotation_exp(w))@r0
        exact=(np.abs(r)@np.array([50.,.025,50.]))[0]
        self.assertAlmostEqual(exact,.025+50*(abs(w[1])+abs(w[2])),delta=1e-9)

    def test_sampled_offsets_respect_joint_information(self):
        root=np.array([[.2,0,0,0],[.03,.04,0,0],[.02,-.01,.4,0],[.01,.02,.05,.3]])
        covariance=root@root.T
        for d in ellipsoid_offsets(covariance):self.assertAlmostEqual(float(d@np.linalg.solve(covariance,d)),25.,places=11)
        with self.assertRaises(ValueError):ellipsoid_offsets(np.eye(3))

    def test_original_fixture_and_four_labels_are_not_redefined(self):
        self.assertEqual(injected_vector('l1_y_rx_ry_rz'),[.0018,.0025,-.0025])
        self.assertEqual(injected_vector('l1_y_rx_ry_rz_contained'),[.0018,.001,-.001])
        self.assertEqual(injected_vector('l1_y_rx_ry_rz_contained_negative'),[-.0018,-.001,.001])
        self.assertEqual(labels('l1_y_rx_ry_rz_contained'),[700102,700104,700105,700106])
