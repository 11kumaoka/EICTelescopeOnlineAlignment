"""Independent safeguards for read-only residual attribution."""
import unittest
import numpy as np
from validation.telescope_closure.bias import projected_parts


class BiasAttribution(unittest.TestCase):
    def test_local_offsets_cannot_change_profiled_global_scores(self):
        random=np.random.default_rng(84);a=random.normal(size=(10,4));g=random.normal(size=(10,3));r=random.normal(size=10)
        h,b=projected_parts(a,g,{'r':r,'offset':r+a@np.array([2.,-1.,.3,.5])})
        self.assertEqual(np.linalg.matrix_rank(h),3)
        np.testing.assert_allclose(b['r'],b['offset'],atol=1e-13)

    def test_additive_attribution_matches_joint_least_squares(self):
        random=np.random.default_rng(18);a=random.normal(size=(10,4));g=random.normal(size=(10,3));parts={k:random.normal(size=10) for k in ('pixel','finite','reference')};r=sum(parts.values())
        h,b=projected_parts(a,g,parts)
        result=np.linalg.solve(h,sum(b.values()))
        joint=np.linalg.lstsq(np.column_stack((a,g)),r,rcond=None)[0][4:]
        np.testing.assert_allclose(result,joint,atol=1e-12)

    def test_local_rank_defect_is_refused(self):
        with self.assertRaises(AssertionError):projected_parts(np.zeros((10,4)),np.ones((10,3)),{'r':np.zeros(10)})
