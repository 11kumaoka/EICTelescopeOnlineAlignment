"""Mathematical six-DOF foundation; these tests do not enable rotation alignment."""
import math
import unittest
from geometry.transforms import RigidTransform,axis_rotation


class TransformTests(unittest.TestCase):
    def close(self,a,b,tolerance=1e-12):
        for x,y in zip(a,b):self.assertAlmostEqual(x,y,delta=tolerance)
    def test_identity_translation(self):
        self.close(RigidTransform().apply((1,2,3)),(1,2,3))
        self.close(RigidTransform(translation=(4,5,6)).apply((1,2,3)),(5,7,9))
    def test_axes_and_small_angle(self):
        for axis,point,expected in [('x',(0,1,0),(0,0,1)),('y',(0,0,1),(1,0,0)),('z',(1,0,0),(0,1,0))]:
            self.close(RigidTransform(axis_rotation(axis,math.pi/2)).apply(point),expected)
        self.close(RigidTransform(axis_rotation('z',1e-8)).apply((1,0,0)),(1,1e-8,0),1e-15)
    def test_composition_inverse_round_trip(self):
        first=RigidTransform(axis_rotation('x',.2),(1,2,3),'global','parent')
        second=RigidTransform(axis_rotation('y',-.3),(-2,4,1),'parent','sensor')
        combined=first@second;point=(5,-4,2)
        self.close(combined.apply(point),first.apply(second.apply(point)))
        self.close(combined.inverse().apply(combined.apply(point)),point)
    def test_pivot_and_update_order(self):
        base=RigidTransform(translation=(2,0,0))
        moved=base.updated(axis_rotation('z',math.pi/2),pivot=(1,0,0))
        self.close(moved.translation,(1,1,0));self.close(base.translation,(2,0,0))
        local=RigidTransform(axis_rotation('z',math.pi/2)).updated(translation=(1,0,0),frame='local')
        self.close(local.translation,(0,1,0))
    def test_invalid_frames_and_reflections(self):
        with self.assertRaises(ValueError):RigidTransform()@RigidTransform()
        with self.assertRaises(ValueError):RigidTransform(((-1,0,0),(0,1,0),(0,0,1)))



    def test_noncommutative_rotation_and_immutable_inputs(self):
        rotation=[list(row) for row in axis_rotation('x',math.pi/2)]
        first=RigidTransform(rotation,parent='global',child='global')
        second=RigidTransform(axis_rotation('z',math.pi/2),parent='global',child='global')
        self.assertNotEqual((first@second).apply((0,1,0)),(second@first).apply((0,1,0)))
        rotation[0][0]=99
        self.assertEqual(first.rotation[0][0],1)


class GaugeTests(unittest.TestCase):
    def rank(self,rows):
        matrix=[list(map(float,row)) for row in rows];rank=0
        for column in range(len(matrix[0])):
            pivot=next((i for i in range(rank,len(matrix)) if abs(matrix[i][column])>1e-10),None)
            if pivot is None:continue
            matrix[rank],matrix[pivot]=matrix[pivot],matrix[rank]
            scale=matrix[rank][column];matrix[rank]=[v/scale for v in matrix[rank]]
            for i in range(len(matrix)):
                if i!=rank:
                    scale=matrix[i][column];matrix[i]=[a-scale*b for a,b in zip(matrix[i],matrix[rank])]
            rank+=1
        return rank
    def test_transverse_translation_gauges(self):
        rows=[]
        for track in range(2):
            for layer,z in enumerate((-100,-50,0,50,100)):
                local=[0.]*4;local[2*track]=1;local[2*track+1]=z
                global_parameters=[float(i==layer) for i in range(5)]
                rows.append(local+global_parameters)
        self.assertEqual(9-self.rank(rows),2)
        fixed=[[row[i] for i in (0,1,2,3,5,6,7)] for row in rows]
        self.assertEqual(self.rank(fixed),7)

if __name__=='__main__':unittest.main()
