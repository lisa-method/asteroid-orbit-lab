import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from prepare_apophis_shape import mesh_moments


class MeshMomentsTests(unittest.TestCase):
    def tetra(self,shift=(0,0,0)):
        vertices=[(0,0,0),(1,0,0),(0,1,0),(0,0,1)]
        text='\n'.join('v '+' '.join(str(a+b) for a,b in zip(v,shift)) for v in vertices)
        return text+'\nf 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n'

    def test_analytic_tetrahedron_moments(self):
        m,_,_=mesh_moments(self.tetra())
        self.assertAlmostEqual(m['volume_km3'],1/6)
        for c in m['uniform_density_centroid_km']:self.assertAlmostEqual(c,.25)
        for i in range(3):
            for j in range(3):self.assertAlmostEqual(m['second_moment_km2'][i][j],3/80 if i==j else -1/80)

    def test_translation_leaves_central_moment_unchanged(self):
        a,_,_=mesh_moments(self.tetra());b,_,_=mesh_moments(self.tetra((2,-3,4)))
        for i in range(3):
            for j in range(3):self.assertAlmostEqual(a['second_moment_km2'][i][j],b['second_moment_km2'][i][j],places=12)

    def test_open_mesh_is_rejected(self):
        with self.assertRaises(ValueError):mesh_moments(self.tetra().rsplit('f 2 3 4',1)[0])
