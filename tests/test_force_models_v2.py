"""Integration checks for physical composition and forecast input boundaries."""
from dataclasses import replace
import inspect
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from earth_oblateness import earth_j2_acceleration
from force_models_v2 import ForceModelSpec, build_force_model_v2, forecast_candidate_v2
from ng_inputs_v2 import NGInput
from orbit_baselines import State
from planetary_dynamics import EphemerisInterpolator, NonGravitationalParameters, Perturber, non_gravitational_acceleration
from run_development_benchmark import forecast_candidate


class ForceModelsV2Tests(unittest.TestCase):
    def setUp(self):
        self.earth = Perturber("399", "Earth", 1e-8, EphemerisInterpolator(
            (100., 101., 102., 103.),
            tuple(State((2., 0.01*i, 0.), (0., 0.01, 0.)) for i in range(4))))
        self.context = {"mu": 0.0003, "c_au_d": 173., "au_km": 149597870.7,
                        "day_s": 86400., "planets": (self.earth,), "small": ()}
        self.config = {"default_step_days": 0.25,
                       "earth_j2": {"j2": .001, "reference_radius_km": 14959787.07, "pole_model": "iau"}}
        self.initial = State((1., 0., .05), (0., .017, 0.))
        self.spec = ForceModelSpec("arbitrary label", True, False, False, False, "off")

    def test_disabled_corrections_reproduce_frozen_rollouts(self):
        for label, flags in (("B2", (False, False, False)), ("B3", (True, False, False)),
                             ("B3+GR", (True, True, False)), ("B3+GR+SB16", (True, True, True))):
            with self.subTest(label=label):
                spec = ForceModelSpec("new name", *flags, False, "off")
                old = forecast_candidate(self.initial, 100., 2., {"model_id": label}, self.context, self.config, .5)
                new = forecast_candidate_v2(self.initial, 100., 2., spec, self.context, self.config, .5)
                self.assertEqual(old[0], new[0])
                self.assertEqual(old[1]["accepted_states"], new[1]["accepted_states"])
                self.assertEqual(old[1]["rk4_steps"], new[1]["rk4_steps"])

    def test_composition_adds_j2_and_ng_at_each_state_without_second_sun(self):
        ng = NGInput(parameters=NonGravitationalParameters(a2_au_d2=1e-8),
                     source="synthetic dated fixture", available_from_jd_tdb=99., availability_basis="fixture")
        base, _, _ = build_force_model_v2(100., self.spec, self.context, self.config)
        full, bodies, metadata = build_force_model_v2(100., replace(self.spec, earth_j2=True, non_grav="required"),
                                                     self.context, self.config, ng=ng)
        j2 = earth_j2_acceleration(self.earth, 100., .1, .001, pole_model="iau")
        ng_force = non_gravitational_acceleration(ng.parameters)
        for epoch, state in ((0., self.initial), (1., State((1.2, .3, .1), (-.003, .015, .002)))):
            expected = tuple(sum(values) for values in zip(base(epoch, state.position, state.velocity),
                             j2(epoch, state.position, state.velocity), ng_force(epoch, state.position, state.velocity)))
            self.assertEqual(full(epoch, state.position, state.velocity), expected)
        self.assertEqual(len(bodies), 1)
        self.assertIn("earth_j2", metadata["applied_terms"])
        self.assertIn("non_gravitational", metadata["applied_terms"])
        self.assertIn("ng_uncertainty_unknown", metadata["limitations"])

    def test_name_cannot_change_physics_but_flag_does(self):
        force, _, _ = build_force_model_v2(100., self.spec, self.context, self.config)
        renamed, _, _ = build_force_model_v2(100., replace(self.spec, model_id="613569"), self.context, self.config)
        corrected, _, _ = build_force_model_v2(100., replace(self.spec, earth_j2=True), self.context, self.config)
        args = (0., self.initial.position, self.initial.velocity)
        self.assertEqual(force(*args), renamed(*args))
        self.assertNotEqual(force(*args), corrected(*args))

    def test_future_ng_does_not_activate_during_rollout(self):
        ng = NGInput(parameters=NonGravitationalParameters(a2_au_d2=1e-5),
                     source="synthetic future", available_from_jd_tdb=101., availability_basis="fixture")
        disabled = forecast_candidate_v2(self.initial, 100., 2., self.spec, self.context, self.config, .5)
        future = forecast_candidate_v2(self.initial, 100., 2., replace(self.spec, non_grav="if_available"),
                                        self.context, self.config, .5, ng=ng)
        self.assertEqual(disabled[0], future[0])
        self.assertEqual(future[1]["force_metadata"]["ng_status"], "not_yet_available")
        with self.assertRaises(ValueError):
            build_force_model_v2(100., replace(self.spec, non_grav="required"), self.context, self.config, ng=ng)

    def test_unknown_input_does_not_claim_zero_or_accuracy(self):
        _, _, meta = build_force_model_v2(100., replace(self.spec, non_grav="if_available"), self.context, self.config)
        self.assertEqual(meta["ng_status"], "not_provided")
        self.assertFalse(meta["ng_applied"])
        self.assertFalse(meta["accuracy_guaranteed"])
        self.assertIsNone(meta["ng_sigma_au_d2"])

    def test_invalid_configuration_and_duplicate_bodies_fail_early(self):
        with self.assertRaises(ValueError):
            ForceModelSpec.from_mapping({"model_id": "B3", "earth_j2": True})
        with self.assertRaises(ValueError):
            replace(self.spec, planets=False, earth_j2=True)
        with self.assertRaises(ValueError):
            replace(self.spec, planets="true")
        with self.assertRaises(ValueError):
            build_force_model_v2(100., self.spec, {**self.context, "planets": (self.earth, self.earth)}, self.config)
        with self.assertRaises(ValueError):
            build_force_model_v2(100., replace(self.spec, earth_j2=True), {**self.context, "planets": ()}, self.config)
        with self.assertRaises(ValueError):
            forecast_candidate_v2(self.initial, 100., 4., self.spec, self.context, self.config, .5)

    def test_forecast_has_no_reference_or_identity_argument(self):
        self.assertEqual(set(inspect.signature(forecast_candidate_v2).parameters),
                         {"initial", "start_jd", "horizon_days", "model", "context", "config", "step_scale", "ng"})


if __name__ == "__main__":
    unittest.main()
