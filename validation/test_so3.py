"""Rotation mathematics only; passing does not certify an alignment mode."""
import math
import unittest
from geometry.transforms import (IDENTITY, RigidTransform, axis_rotation, multiply,
    transpose, rotation_exp, rotation_log, orientation_distance,
    ordered_xy_axes, ordered_xy_increment)


class SO3Tests(unittest.TestCase):
    def test_exp_log_and_relative(self):
        for vector in ((0.,0.,0.),(1e-12,-2e-12,3e-12),(.03,-.025,.017),(2.,-1.,.5),(math.pi,0.,0.)):
            r=rotation_exp(vector)
            self.assertLess(orientation_distance(r,rotation_exp(rotation_log(r))),1e-12)
        r=multiply(axis_rotation('z',.017),axis_rotation('x',.03))
        target=multiply(rotation_exp((.001,-.002,.003)),r)
        self.assertAlmostEqual(orientation_distance(target,r),math.sqrt(14)*.001,places=13)
        self.assertLess(orientation_distance(multiply(rotation_exp(rotation_log(multiply(target,transpose(r)))),r),target),1e-12)

    def test_chart_retraction_reference_and_pivot(self):
        reference=multiply(axis_rotation('z',.017),axis_rotation('y',-.013))
        r=reference
        for da,db in ((.03,-.025),(-.02,.01),(.004,-.003)):
            r=ordered_xy_increment(r,da,db,reference)
        target=multiply(multiply(axis_rotation('y',-.018),axis_rotation('x',.014)),reference)
        self.assertLess(orientation_distance(r,target),1e-12)
        t=RigidTransform(reference,(1.25,-.75,52.))
        updated=t.updated(rotation=multiply(target,transpose(reference)),pivot=t.translation)
        for a,b in zip(updated.translation,t.translation):self.assertAlmostEqual(a,b,places=12)
        self.assertLess(orientation_distance(updated.rotation,target),1e-12)

    def test_chart_transported_derivatives_and_order(self):
        for a,b in ((.03,-.025),(-.03,.025)):
            r=multiply(axis_rotation('y',b),axis_rotation('x',a))
            axes=ordered_xy_axes(r)
            for index,axis in enumerate(axes):
                eps=1e-6
                plus=ordered_xy_increment(r,eps if index==0 else 0.,eps if index==1 else 0.)
                minus=ordered_xy_increment(r,-eps if index==0 else 0.,-eps if index==1 else 0.)
                fd=tuple(tuple((plus[i][j]-minus[i][j])/(2*eps) for j in range(3)) for i in range(3))
                x,y,z=axis;k=((0.,-z,y),(z,0.,-x),(-y,x,0.));analytic=multiply(k,r)
                self.assertLess(max(abs(fd[i][j]-analytic[i][j]) for i in range(3) for j in range(3)),1e-10)
            reverse=multiply(axis_rotation('x',a),axis_rotation('y',b))
            self.assertGreater(orientation_distance(r,reverse),.0007)
            with self.assertRaises(ValueError):ordered_xy_axes(reverse)
            with self.assertRaises(ValueError):ordered_xy_axes(multiply(axis_rotation('z',.017),r))

    def test_repeated_so3_updates(self):
        target=multiply(axis_rotation('x',-.025),multiply(axis_rotation('z',.017),axis_rotation('y',.03)))
        current=axis_rotation('z',-.01)
        for _ in range(20):
            relative=rotation_log(multiply(target,transpose(current)))
            current=multiply(rotation_exp(tuple(.8*v for v in relative)),current)
        self.assertLess(orientation_distance(target,current),1e-12)

    def test_invalid(self):
        for vector in ((0.,0.),(float('nan'),0.,0.)):
            with self.assertRaises(ValueError):rotation_exp(vector)
        with self.assertRaises(ValueError):rotation_log(((1.,0.,0.),(0.,1.,0.),(0.,0.,-1.)))


if __name__=='__main__':unittest.main()
