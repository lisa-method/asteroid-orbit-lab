import math
import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selector_v4 import choose_v4, fit_selector_v4, features_v4
from selector_v3 import features_v3
import test_selector_v3 as v3fixtures
from ng_inputs_v2 import NGInput
from orbit_baselines import State
from planetary_dynamics import NonGravitationalParameters


MODELS = [
    {"model_id": "cheap", "planets": False, "solar_gr": False, "small_bodies": False, "earth_j2": False, "non_grav": "off"},
    {"model_id": "full", "planets": True, "solar_gr": True, "small_bodies": True, "earth_j2": True, "non_grav": "if_available"},
]


def row(oid, model, h=7.0, x=0.0, *, split="train", err=.2, ng="not_provided"):
    return {"object_id": oid, "split": split, "horizon_days": h, "model_id": model,
            "features": [math.log10(h), x, .1, 0., math.log10(2.), math.log10(3.), -1., -2., -30.],
            "feature_result": {"force_proxies_km": {"planet": 1., "gr": 2., "small_body": 3., "earth_j2": 4., "ng": 0.}, "ng_proxy_status": ng, "strong_encounter": False},
            "max_position_error_km": err if model == "cheap" else .05, "numerical_difference_km": .01,
            "runtime_median_seconds": 1. if model == "cheap" else 5.}


class SelectorV4Tests(unittest.TestCase):
    def _data(self):
        train=[]; cal=[]
        for i in range(4):
            for m in ("cheap", "full"):
                train.append(row(f"t{i}",m,x=float(i),err=1. + i*.1))
        for i in range(4,8):
            for m in ("cheap", "full"):
                cal.append(row(f"c{i}",m,x=float(i),split="calibration",err=1.))
        return train,cal

    def test_fit_and_choose_keeps_nine_vector_and_train_cost(self):
        train, cal = self._data(); art=fit_selector_v4(train,cal,MODELS)
        self.assertEqual(art["feature_dimension"],9)
        result=choose_v4(train[0]["feature_result"] | {"vector": train[0]["features"]},7.,100.,art)
        self.assertFalse(result["accuracy_guaranteed"])
        self.assertEqual(result["train_cost_seconds"],1.)
        self.assertEqual(set(result["rejected_forces"]["cheap"]),set(("planet","gr","small_body","earth_j2","ng")))

    def test_malformed_and_overlap_rejected(self):
        train,cal=self._data()
        with self.assertRaises(ValueError): fit_selector_v4([dict(train[0],features=[1.])],cal,MODELS)
        with self.assertRaises(ValueError): fit_selector_v4(train,[dict(cal[0],object_id="t0")],MODELS)

    def test_strong_and_ood_are_independent(self):
        train,cal=self._data(); art=fit_selector_v4(train,cal,MODELS)
        fr=dict(train[0]["feature_result"], vector=[math.log10(7.),99.,.1,0.,math.log10(2.),math.log10(3.),-1.,-2.,-30.], strong_encounter=True)
        out=choose_v4(fr,7.,100.,art)
        self.assertTrue(out["outside_training_support"])
        self.assertEqual(out["status"],"strong_encounter_unvalidated")
        self.assertTrue(out["strong_encounter"])

    def test_hybrid_caps_cover_scale(self):
        train,cal=self._data(); art=fit_selector_v4(train,cal,MODELS)
        out=choose_v4(train[0]["feature_result"] | {"vector":train[0]["features"]},7.,100.,art)
        self.assertGreaterEqual(out["predicted_error_caps_km"]["cheap"], 10.1)

    def test_all_four_force_omissions_and_sb_cannot_be_ignored(self):
        models = [dict(MODELS[0], model_id="B2"),
                  dict(MODELS[1], model_id="P", solar_gr=False, small_bodies=False),
                  dict(MODELS[1], model_id="PGR", small_bodies=False),
                  dict(MODELS[1])]
        train = [row(f"t{i}", m["model_id"], x=i) for i in range(4) for m in models]
        cal = [row(f"c{i}", m["model_id"], x=i+.1, split="calibration") for i in range(4) for m in models]
        art = fit_selector_v4(train, cal, models)
        feature = train[0]["feature_result"] | {"vector": train[0]["features"]}
        for method in ("physics_v4", "hybrid_v4"):
            result = choose_v4(feature, 7., 1., art, method)
            self.assertEqual(result["rejected_forces"], {"B2": ["planet", "gr", "small_body", "earth_j2", "ng"],
                             "P": ["gr", "small_body"], "PGR": ["small_body"], "full": []})
            self.assertEqual(result["model_id"], "full")
            self.assertGreaterEqual(result["predicted_error_caps_km"]["P"], 5.1)
            self.assertGreaterEqual(result["predicted_error_caps_km"]["PGR"], 3.1)

    def test_calibration_cannot_change_trees_floors_support_or_costs(self):
        train, cal = self._data()
        first = fit_selector_v4(train, cal, MODELS)
        changed = copy.deepcopy(cal)
        for r in changed:
            r["features"][1] = 100.
            r["runtime_median_seconds"] = 1e6
            r["max_position_error_km"] = 100.
        second = fit_selector_v4(train, changed, MODELS)
        for key in ("trees", "baseline_floor_km", "support_bounds", "median_cost_seconds"):
            self.assertEqual(first[key], second[key])
        self.assertGreater(second["physics_alpha"]["full"], first["physics_alpha"]["full"])
        self.assertAlmostEqual(second["physics_alpha"]["full"], 1000.)

    def test_same_horizon_support_and_no_candidate_are_separate(self):
        train, cal = self._data()
        for target in (train, cal):
            additions = copy.deepcopy(target)
            for r in additions:
                r["horizon_days"] = 30.
                r["features"][0] = math.log10(30.)
                r["features"][1] += 100.
            target.extend(additions)
        art = fit_selector_v4(train, cal, MODELS)
        feature = copy.deepcopy(train[0]["feature_result"])
        feature["vector"] = list(train[0]["features"])
        feature["vector"][1] = 50.  # Inside global range; outside this horizon.
        result = choose_v4(feature, 7., 100., art)
        self.assertTrue(result["outside_training_support"])
        self.assertFalse(result["no_candidate_predicted"])
        self.assertEqual(result["status"], "outside_training_support")
        feature["strong_encounter"] = True
        result = choose_v4(feature, 7., .000001, art)
        self.assertTrue(all(result[k] for k in ("strong_encounter", "outside_training_support", "no_candidate_predicted")))
        self.assertEqual(set(result["warning_reasons"]), {"strong_encounter_unvalidated", "outside_training_support", "no_candidate_predicted"})

    def test_bad_training_targets_missing_cases_and_force_spec_rejected(self):
        train, cal = self._data()
        for key, value in (("max_position_error_km", -1.), ("numerical_difference_km", math.nan),
                           ("runtime_median_seconds", -1.)):
            bad = copy.deepcopy(train)
            bad[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                fit_selector_v4(bad, cal, MODELS)
        with self.assertRaises(ValueError):
            fit_selector_v4(train[:-1], cal, MODELS)
        with self.assertRaises(ValueError):
            fit_selector_v4(train, cal, [dict(MODELS[0], planets="False"), MODELS[1]])
        with self.assertRaises(ValueError):
            fit_selector_v4(train, cal, MODELS, 2.)

    def test_inconsistent_features_and_missing_artifact_support_rejected(self):
        train, cal = self._data()
        art = fit_selector_v4(train, cal, MODELS)
        feature = train[0]["feature_result"] | {"vector": train[0]["features"]}
        for key, value in (("ng", 1.), ("earth_j2", math.nan), ("small_body", 10.)):
            bad = copy.deepcopy(feature)
            bad["force_proxies_km"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                choose_v4(bad, 7., 100., art)
        bad = copy.deepcopy(art)
        del bad["support_bounds"]["7.0"]
        with self.assertRaises(ValueError):
            choose_v4(feature, 7., 100., bad)
        bad = copy.deepcopy(train)
        bad[0]["features"][0] = 0.
        with self.assertRaises(ValueError):
            fit_selector_v4(bad, cal, MODELS)

    def test_causal_builder_preserves_v3_vector_and_ng_availability(self):
        context = v3fixtures.SelectorV3Tests._context()
        initial = State((1., 0., 0.), (0., math.sqrt(context["mu"]), 0.))
        config = {"earth_j2": {"reference_radius_km": 6378.1366, "j2": .00108262545, "pole_model": "iau"}}
        params = NonGravitationalParameters(a2_au_d2=1e-12)
        for availability in (2450000., 2450001.):
            ng = NGInput(parameters=params, source="synthetic", available_from_jd_tdb=availability, availability_basis="test")
            old = features_v3(initial, 2450000., 1., context, {}, ng)
            new = features_v4(initial, 2450000., 1., context, {}, config, ng)
            self.assertEqual(new["vector"], old["vector"])
            self.assertGreater(new["force_proxies_km"]["earth_j2"], 0.)
            self.assertEqual(new["ng_proxy_status"] == "available", availability == 2450000.)
            self.assertEqual(new["force_proxies_km"]["ng"] > 0., availability == 2450000.)
        without_earth = {**context, "planets": context["planets"][1:]}
        with self.assertRaises(ValueError):
            features_v4(initial, 2450000., 1., without_earth, {}, config)


if __name__ == "__main__": unittest.main()
