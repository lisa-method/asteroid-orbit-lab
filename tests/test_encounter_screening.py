import inspect
import math
import random
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from encounter_screening import forecast_encounters, scan_body, uniform_times
from encounter_geometry import segment_closest_approach
from orbit_baselines import State, propagate_variable_step
from planetary_dynamics import EphemerisInterpolator, Perturber
from run_encounter_screening import compare_minimum, forecast_feature_record


class EncounterScreeningTests(unittest.TestCase):
    def test_feature_export_excludes_future_event_design_metadata(self):
        case = {"case_id":"x", "object_id":"99942", "start_date":"2029-01-01", "horizon_days":365,
                "lead_to_anchor_days":102, "group":"known_event", "reference_distance_km":38000, "event_date":"2029-04-13"}
        exported = forecast_feature_record(case,"daily_hermite",100,{"distance_km":50000})
        self.assertFalse({"lead_to_anchor_days","group","reference_distance_km","event_date"} & exported.keys())
        self.assertEqual(exported["distance_km"],50000)

    def test_cubic_minimum_matches_independent_dense_curves(self):
        rng = random.Random(20260907)
        for _ in range(40):
            coefficients = [[rng.uniform(-2,2) for _ in range(4)] for _ in range(3)]
            def position(u):
                return tuple(sum(c[k]*u**k for k in range(4)) for c in coefficients)
            def velocity(u):
                return tuple(sum(k*c[k]*u**(k-1) for k in range(1,4)) for c in coefficients)
            fraction,state = segment_closest_approach(State(position(0),velocity(0)),State(position(1),velocity(1)),1)
            dense_min = min(sum(x*x for x in position(i/1000)) for i in range(1001))
            self.assertLessEqual(sum(x*x for x in state.position),dense_min+1e-11)
            for actual,expected in zip(state.position,position(fraction)):
                self.assertAlmostEqual(actual,expected,places=12)

    def body(self, states, times=(0.0, 1.0), body_id="399"):
        return Perturber(body_id, "synthetic", 0.001, EphemerisInterpolator(tuple(times), tuple(states)))

    def test_dense_minimum_detects_flyby_missed_by_daily_nodes(self):
        planets = [State((2.0, 0.0, 0.0), (0.0, 0.0, 0.0))] * 2
        asteroid = [State((1.0, 0.001, 0.0), (2.0, 0.0, 0.0)), State((3.0, 0.001, 0.0), (2.0, 0.0, 0.0))]
        body = self.body(planets)
        coarse = scan_body([0,1], asteroid, planets, body, 1, 1, 1, refine=False)
        refined = scan_body([0,1], asteroid, planets, body, 1, 1, 1, refine=True)
        self.assertGreater(coarse["distance_au"], 0.01)
        self.assertAlmostEqual(refined["distance_au"], 0.001, places=13)
        self.assertAlmostEqual(refined["time_days"], 0.5, places=13)
        self.assertEqual(compare_minimum(coarse, refined, [0.01])["outcomes"]["0.01"], "FN")
        self.assertEqual(compare_minimum(refined, refined, [0.01])["outcomes"]["0.01"], "TP")

    def test_crossing_paths_at_different_times_are_not_an_encounter(self):
        # Both lines pass (2,2), but the asteroid arrives at t=1 and planet at t=2.
        asteroid = [State((1,2,0), (1,0,0)), State((4,2,0), (1,0,0))]
        planets = [State((2,0,0), (0,1,0)), State((2,3,0), (0,1,0))]
        minimum = scan_body([0,3], asteroid, planets, self.body(planets,(0,3)), 1, 1, 1, refine=True)
        self.assertAlmostEqual(minimum["distance_au"], math.sqrt(0.5), places=12)
        self.assertAlmostEqual(minimum["time_days"], 1.5, places=12)
        self.assertGreater(minimum["distance_au"], 0.05)

    def test_boundary_minimum_and_moon_hill_proxy_are_explicit(self):
        planet = State((2,0,0), (0,0,0))
        asteroid = [State((3,0,0), (1,0,0)),State((4,0,0), (1,0,0))]
        minimum = scan_body([0,1], asteroid, [planet]*2, self.body([planet]*2,body_id="301"), 1, 1, 1, refine=True)
        self.assertTrue(minimum["boundary_minimum"])
        self.assertIsNone(minimum["rho_sun_proxy_at_closest"])
        self.assertEqual(minimum["time_days"], 0)

    def test_confusion_has_false_positives_and_true_negatives(self):
        reference = {"distance_au": 0.1, "distance_km": 1, "time_days": 1,
                     "relative_speed_km_s": 1, "bracket_gap_days_at_minimum": 1, "boundary_minimum": False}
        predicted = {**reference,"distance_au":0.001}
        result = compare_minimum(predicted, reference, [0.01,0.0001])
        self.assertEqual(result["outcomes"], {"0.01":"FP", "0.0001":"TN"})

    def test_forecast_requires_no_reference_and_grid_does_not_use_event_dates(self):
        parameters = set(inspect.signature(forecast_encounters).parameters)
        self.assertFalse({"reference", "event", "asteroid_rows", "labels"} & parameters)
        self.assertEqual(uniform_times(1.1,0.5),[0,0.5,1,1.1])
        initial = State((1,0,0),(0,0.017,0))
        p = State((2,0,0),(0,0,0))
        body = self.body([p,p],(100,102))
        args = dict(cadence_days=.5,refine=True,default_step_days=.0625,step_scale=.5,au_km=1,day_s=1)
        one = forecast_encounters(initial,100,1,(body,),.0003,**args)
        two = forecast_encounters(initial,100,1,(body,),.0003,**args)
        self.assertEqual(one["minima"],two["minima"])
        self.assertEqual(one["output_samples"],3)
        self.assertGreaterEqual(one["runtime_seconds"],one["propagation_seconds"])
        self.assertEqual(one["force_evaluations"],4*one["rk4_steps"])

    def test_explicit_b2_uniform_is_backward_compatible(self):
        initial = State((1, 0, 0), (0, 0.017, 0))
        p = State((2, 0, 0), (0, 0, 0))
        body = self.body([p, p], (100, 102))
        args = dict(cadence_days=.5, refine=True, default_step_days=.0625,
                    step_scale=.5, au_km=1, day_s=1)
        implicit = forecast_encounters(initial, 100, 1, (body,), .0003, **args)
        explicit = forecast_encounters(initial, 100, 1, (body,), .0003,
                                       force_model="B2", sampling="uniform",
                                       refinement_distance_au=.01, **args)
        self.assertEqual(implicit["minima"], explicit["minima"])
        self.assertEqual(implicit["output_samples"], explicit["output_samples"])
        self.assertEqual(explicit["nominal_output_samples"], 3)
        self.assertEqual(explicit["sampling"], "uniform")
        self.assertEqual(explicit["force_model"], "B2")

    def test_b3_applies_passed_perturber_force(self):
        initial = State((1.0, 0, 0), (0, 0, 0))
        p = State((1.1, 0, 0), (0, 0, 0))
        body = self.body([p, p], (100, 101))
        args = dict(cadence_days=.01, refine=False, default_step_days=.01,
                    step_scale=1, au_km=1, day_s=1)
        b2 = forecast_encounters(initial, 100, .01, (body,), .0003, **args)
        b3 = forecast_encounters(initial, 100, .01, (body,), .0003,
                                 force_model="B3", **args)
        # At short times the relative displacement is set by the asteroid's
        # initial B3 acceleration.  This checks both direct and indirect signs.
        acceleration_x = -.0003 + .001 / .1**2 - .001 / 1.1**2
        expected_distance = .1 - .5 * acceleration_x * .01**2
        self.assertAlmostEqual(b3["minima"][0]["distance_au"], expected_distance,
                               delta=1.0e-8)
        self.assertLess(b3["minima"][0]["distance_au"], b2["minima"][0]["distance_au"])
        self.assertEqual(b3["force_model"], "B3")

    def test_on_step_observer_sees_each_accepted_step_without_changing_results(self):
        initial = State((1, 0, 0), (0.1, 0, 0))
        acceleration = lambda _time, _position, _velocity: (0.0, 0.0, 0.0)
        observed = []
        with_observer, with_steps = propagate_variable_step(
            initial, [0, 1], acceleration, lambda _t, _s: .3,
            on_step=lambda lt, ls, rt, rs: observed.append((lt, ls, rt, rs)),
        )
        plain, plain_steps = propagate_variable_step(
            initial, [0, 1], acceleration, lambda _t, _s: .3,
        )
        self.assertEqual(with_observer, plain)
        self.assertEqual(with_steps, plain_steps)
        self.assertEqual(len(observed), with_steps)
        self.assertAlmostEqual(with_observer[1].position[0], 1.1, places=14)
        self.assertAlmostEqual(with_observer[1].position[1], 0.0, places=14)
        self.assertEqual(observed[0][0], 0.0)
        self.assertEqual(observed[-1][2], 1.0)
        for left, right in zip(observed, observed[1:]):
            self.assertEqual(left[2], right[0])
            self.assertEqual(left[3], right[1])

    def test_encounter_steps_adds_forecast_driven_knots_and_keeps_endpoints(self):
        initial = State((1.0, 0, 0), (0.1, 0, 0))
        p = State((1.005, 0, 0), (0, 0, 0))
        body = self.body([p, p], (100, 101))
        result = forecast_encounters(
            initial, 100, 1, (body,), .0000001,
            cadence_days=1, refine=True, default_step_days=.25,
            step_scale=1, au_km=1, day_s=1,
            sampling="encounter_steps", refinement_distance_au=.01,
        )
        self.assertGreater(result["output_samples"], result["nominal_output_samples"])
        self.assertEqual(result["minima"][0]["sample_count"], result["output_samples"])
        self.assertAlmostEqual(result["minima"][0]["max_knot_gap_days"], .75)
        self.assertEqual(result["sampling"], "encounter_steps")
        self.assertEqual(result["nominal_output_samples"], 2)

    def test_invalid_force_sampling_and_refinement_options_rejected(self):
        initial = State((1, 0, 0), (0, .017, 0)); p = State((2, 0, 0), (0, 0, 0))
        body = self.body([p, p], (100, 102))
        args = dict(cadence_days=1, refine=True, default_step_days=.1,
                    step_scale=1, au_km=1, day_s=1)
        for kwargs in ({"force_model": "B4"}, {"sampling": "daily"},
                       {"refinement_distance_au": 0},
                       {"refinement_distance_au": math.inf},
                       {"refinement_distance_au": math.nan}):
            with self.assertRaises(ValueError):
                forecast_encounters(initial, 100, 1, (body,), .0003,
                                    **args, **kwargs)

    def test_invalid_grids_and_missing_planet_coverage_rejected(self):
        for horizon,cadence in [(0,1),(1,0),(math.nan,1),(1,math.inf)]:
            with self.assertRaises(ValueError):
                uniform_times(horizon,cadence)
        initial = State((1,0,0),(0,.017,0)); p=State((2,0,0),(0,0,0))
        with self.assertRaises(ValueError):
            forecast_encounters(initial,0,2,(self.body([p,p]),),.0003,
                cadence_days=1,refine=True,default_step_days=.0625,step_scale=.5,au_km=1,day_s=1)


if __name__ == "__main__":
    unittest.main()
