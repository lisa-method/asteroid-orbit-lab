import math
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from build_trajectory_showcase import orthonormal_basis, project_vector, select_indices, choose_amplification, build_scene

class ShowcaseTests(unittest.TestCase):
    def test_basis_is_orthonormal_and_projection(self):
        b=orthonormal_basis((1,0,0),(0,1,0))
        self.assertAlmostEqual(project_vector((2,3,4),b)[0],2)
        self.assertAlmostEqual(project_vector((2,3,4),b)[1],3)
        for x in b: self.assertAlmostEqual(math.sqrt(sum(v*v for v in x)),1)
    def test_sampling_keeps_exact_endpoints(self):
        ix=select_indices(list(range(500)),120)
        self.assertEqual((ix[0],ix[-1],len(ix)),(0,499,120))
    def test_amplification_is_power_ten_and_scene_keeps_true_errors(self):
        ref=[[i,0] for i in range(20)]; pred=[[i+1e-6,0] for i in range(20)]
        gain=choose_amplification(pred,ref); self.assertEqual(gain,10.0**round(math.log10(gain)))
        scene=build_scene("x","m",list(range(20)),pred,ref,[3.0]*20)
        self.assertEqual(scene["error_km"][0],3.0); self.assertEqual(len(scene["times_days"]),20)
    def test_invalid_epochs_rejected(self):
        with self.assertRaises(ValueError): select_indices([0,1,1])
    def test_detail_contains_interior_error_peak_instead_of_last_days(self):
        from build_trajectory_showcase import detail_rows_near_max_error
        rows=[{'display_time_days':float(t)} for t in range(366)]
        errors={float(t):1.0/(1+abs(t-100)) for t in range(366)}
        detail=detail_rows_near_max_error(rows,errors,365.)
        self.assertEqual([r['display_time_days'] for r in detail],list(map(float,range(55,146))))
        detail=detail_rows_near_max_error(rows[:31],errors,30.)
        self.assertEqual(detail,rows[:31])


class SceneIntegrationTests(unittest.TestCase):
    """Synthetic frozen trace catches frame, provenance and epoch mistakes."""
    def test_scene_reproduces_3d_error_and_applies_shared_body_frame(self):
        import json
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        from build_trajectory_showcase import assemble_confirmation, _sha, PLOT_ASPECT
        from orbit_baselines import State, norm, subtract
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            def save(path,value):
                p=root/path; p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text(json.dumps(value)); return _sha(p)
            start=2451545.0
            rows=[dict(epoch_jd_tdb=start+t,epoch_tdb=f'2000-01-{t+1:02d}T12:00:00',
                       r=(1+1e-8*t,float(t),2e-8*t),v=(1e-8,1.,2e-8)) for t in range(11)]
            sample_path='data/processed/selector_confirmation100/sample.json'
            raw_path='data/raw/selector_confirmation100/asteroids/asteroid_X_daily.json'
            refined_path=raw_path.replace('_daily','_refined')
            config_path='configs/force_models_v2.json'
            trace_path='outputs/selector_confirmation100/traces/test.json'
            hashes={sample_path:save(sample_path,{'objects':[dict(id='X',name='Synthetic',event={'body_id':'399'})]}),
                    raw_path:save(raw_path,{}),refined_path:save(refined_path,{}),config_path:save(config_path,{})}
            th=save(trace_path,{'accepted_states':[
                {'time_days':0.,'state':{'r':[1.,0.,0.],'v':[0.,1.,0.]}},
                {'time_days':10.,'state':{'r':[1.,10.,0.],'v':[0.,1.,0.]}}]})
            from run_development_benchmark import _interpolate_endpoints
            at=_interpolate_endpoints([{'time_days':t,'state':State((1.,t,0.),(0.,1.,0.))} for t in (0.,10.)])
            errs=[dict(time_days=float(t),position_error_km=norm(subtract(at(t).position,r['r']))*149597870.7) for t,r in enumerate(rows)]
            record=dict(object_id='X',model_id='V2-P',horizon_days=10.,start_jd_tdb=start,
                        initial_state={'r':[1.,0.,0.],'v':[0.,1.,0.]},trace_paths=[trace_path],trace_hashes={trace_path:th},
                        record={'error_rows':errs,'max_position_error_km':errs[-1]['position_error_km']})
            choice=dict(object_id='X',method='physics_v4',model_id='V2-P',horizon_days=10.,tolerance_km=1.,status='fixture',warning=False,actual_eligible=False)
            mp='outputs/selector_confirmation100/matrix.json'
            mh=save(mp,dict(provenance={'source_hashes':hashes},records=[record],choices=[choice]))
            save('outputs/selector_confirmation100/verification.json',{'passed':True,'matrix_sha256':mh})
            body=SimpleNamespace(body_id='399',name='Earth',ephemeris=SimpleNamespace(state_at=lambda jd: State((.2*(jd-start),0.,0.),(0.,0.,0.))))
            def parse(path,*args): return {}, rows[3:8] if 'refined' in str(path) else rows
            with patch('build_trajectory_showcase.parse_horizons',side_effect=parse), patch('run_development_benchmark.load_development_context',return_value={'planets':[body]}):
                scenes,audit=assemble_confirmation(root,'X','physics_v4',10.)
                self.assertEqual(scenes['overview']['times_days'],list(map(float,range(11))))
                self.assertEqual(scenes['zoom']['times_days'],list(map(float,range(3,8))))
                self.assertEqual(scenes['divergence']['times_days'],list(map(float,range(11))))
                self.assertEqual(audit['scenes']['divergence']['common_center_name'],'Sun')
                self.assertEqual(scenes['divergence']['error_km'][-1],errs[-1]['position_error_km'])
                self.assertAlmostEqual(audit['scenes']['zoom']['common_center_au'][0][0],.6)
                self.assertEqual(scenes['overview']['error_km'],[e['position_error_km'] for e in errs])
                z=scenes['zoom']; a=audit['scenes']['zoom']
                self.assertGreater(a['amplification_gain'],1.)
                self.assertGreater(z['error_km'][-1],abs(a['projected_residual_xy'][-1][0]))
                for p,r,d in zip(z['prediction_xy'],z['reference_xy'],a['projected_residual_xy']):
                    self.assertAlmostEqual(p[0]-r[0],a['amplification_gain']*d[0],places=7)
                limits=z['limits']; self.assertAlmostEqual((limits[1]-limits[0])/(limits[3]-limits[2]),PLOT_ASPECT)
                (root/trace_path).write_text('{}')
                with self.assertRaisesRegex(ValueError,'source hash'):
                    assemble_confirmation(root,'X','physics_v4',10.)
            # A verification without an exact matching matrix hash is not sufficient.
            save('outputs/selector_confirmation100/verification.json',{'passed':True})
            with self.assertRaisesRegex(ValueError,'verified current'):
                assemble_confirmation(root,'X','physics_v4',10.)
