"""Selector v4: causal force proxies plus an empirical, train-only cap model.

This module is deliberately independent of the frozen v3 implementation.  The
first nine features are obtained from ``features_v3`` unchanged; the two new
proxies are diagnostics and are never treated as physical error bounds.
"""
from __future__ import annotations

import math
import statistics
import time
from collections.abc import Mapping, Sequence
from typing import Any

from development_features import _weighted_trapezoid
from earth_oblateness import earth_j2_acceleration
from encounter_screening import uniform_times
from ng_inputs_v2 import NGInput
from orbit_baselines import State, norm, propagate_variable_step, two_body_acceleration
from planetary_dynamics import non_gravitational_acceleration, encounter_aware_step_selector
from selector_tree import fit_tree, predict_tree
from force_models_v2 import ForceModelSpec
from selector_v3 import FEATURE_NAMES, features_v3

_FLOOR = 1e-12
_NG_FLOOR = 1e-30
PROXY_NAMES = ("planet", "gr", "small_body", "earth_j2", "ng")
NG_STATUSES = ("available", "not_provided", "availability_unknown", "not_yet_available")


def _validate_feature(feature, vector, horizon):
    """Reject inconsistent cached or caller-provided forecast-time features."""
    if not isinstance(feature, Mapping):
        raise ValueError("feature_result must be a mapping")
    values = _row_vector({"features": vector}, "feature_result")
    if "vector" in feature and list(feature["vector"]) != values:
        raise ValueError("feature_result vector differs from row vector")
    if "feature_names" in feature and feature["feature_names"] != list(FEATURE_NAMES):
        raise ValueError("feature names/order differs")
    if "schema_version" in feature and feature["schema_version"] != 4:
        raise ValueError("require v4 feature schema")
    if not math.isclose(values[0], math.log10(horizon), rel_tol=0., abs_tol=1e-10):
        raise ValueError("feature vector horizon differs")
    if "horizon_days" in feature and _num(feature["horizon_days"], "feature horizon", True) != horizon:
        raise ValueError("feature horizon differs")
    proxies = feature.get("force_proxies_km")
    if not isinstance(proxies, Mapping) or set(proxies) != set(PROXY_NAMES):
        raise ValueError("require exactly the five named force proxies")
    if any(_num(proxies[k], f"proxy {k}") < 0. for k in PROXY_NAMES):
        raise ValueError("force proxies must be non-negative")
    _check_proxy_vector({"features": values, "feature_result": feature}, "feature_result")
    status = feature.get("ng_proxy_status")
    if status not in NG_STATUSES:
        raise ValueError("invalid NG availability status")
    if "ng" in feature and (not isinstance(feature["ng"], Mapping) or feature["ng"].get("status") != status):
        raise ValueError("NG metadata and proxy availability disagree")
    if status != "available" and (proxies["ng"] != 0. or values[8] != math.log10(_NG_FLOOR)):
        raise ValueError("unavailable NG proxy/vector must use explicit zero floor")
    if type(feature.get("strong_encounter")) is not bool:
        raise ValueError("strong_encounter must be an explicit boolean")
    if "geometry" in feature:
        geometry = feature["geometry"]
        if not isinstance(geometry, Mapping) or not geometry:
            raise ValueError("geometry must contain predicted per-body minima")
        strong = any(_num(g["distance_over_hill"], "distance/Hill", True) < .25 and
                     _num(g["scattering_strength"], "scattering strength") > .01 for g in geometry.values())
        if strong != feature["strong_encounter"]:
            raise ValueError("strong encounter flag differs from same-body geometry")
    return values


def _omitted(spec):
    groups = [name for name, flag in (("planet", "planets"), ("gr", "solar_gr"),
              ("small_body", "small_bodies"), ("earth_j2", "earth_j2")) if not spec[flag]]
    return groups + (["ng"] if spec["non_grav"] == "off" else [])


def _full_spec(spec):
    return all(spec[k] for k in ("planets", "solar_gr", "small_bodies", "earth_j2")) and spec["non_grav"] == "if_available"

def _num(x: Any, name: str, positive: bool = False) -> float:
    if isinstance(x, bool): raise ValueError(f"{name} must be finite")
    try: y = float(x)
    except (TypeError, ValueError, OverflowError) as exc: raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(y) or (positive and y <= 0): raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return y

def _row_vector(row: Mapping[str, Any], label: str) -> list[float]:
    v = row.get("features", row.get("vector"))
    if isinstance(v, (str, bytes)) or not isinstance(v, Sequence) or len(v) != 9:
        raise ValueError(f"{label}.features must be a 9-vector")
    return [_num(x, f"{label}.features[{i}]") for i, x in enumerate(v)]

def _effective(row: Mapping[str, Any], fraction: float, label: str) -> float:
    return max(_num(row.get("max_position_error_km"), label+".position"), _num(row.get("numerical_difference_km"), label+".numerical") / fraction, _FLOOR)

def _check_proxy_vector(row: Mapping[str, Any], label: str) -> None:
    p = row["feature_result"]["force_proxies_km"]
    for index, key in ((3, "planet"), (4, "gr"), (5, "small_body")):
        expected = math.log10(max(float(p[key]), _FLOOR))
        if not math.isclose(float(row["features"][index]), expected, rel_tol=0.0, abs_tol=1e-10):
            raise ValueError(f"{label} force proxy is inconsistent with v3 feature vector")

def _spec(model: Any) -> dict[str, Any]:
    if not isinstance(model, Mapping):
        raise ValueError("models must contain force-spec mappings")
    raw = model.get("force_spec", model)
    if not isinstance(raw, Mapping):
        raise ValueError("model force specification must be a mapping")
    try:
        spec = ForceModelSpec.from_mapping(dict(raw))
    except (TypeError, KeyError) as exc:
        raise ValueError("invalid complete force specification") from exc
    return {"planets": spec.planets, "solar_gr": spec.solar_gr,
            "small_bodies": spec.small_bodies, "earth_j2": spec.earth_j2,
            "non_grav": spec.non_grav}

def features_v4(initial: State, start_jd: float, horizon_days: float, context: dict, feature_config: dict, force_config: dict, ng: NGInput | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    base = features_v3(initial, start_jd, horizon_days, context, feature_config, ng=ng)
    mu = _num(context.get("mu"), "context.mu", True); au = _num(context.get("au_km"), "context.au_km", True)
    step = _num(feature_config.get("default_step_days", .0625), "default_step_days", True)
    scale = _num(feature_config.get("step_scale", 1.), "step_scale", True)
    times = uniform_times(float(horizon_days), 1.0)
    selector = encounter_aware_step_selector((), float(start_jd), default_step_days=step, scale_factor=scale)
    states, _ = propagate_variable_step(initial, times, two_body_acceleration(mu), selector)
    planets = tuple(context.get("planets", ()))
    def term_proxy(accs): return _weighted_trapezoid(times, [norm(a) for a in accs], float(horizon_days), au)
    proxies = dict(base.get("base_features", {}))
    # v3 already computed these on exactly the same daily B2 path.
    values = {"planet": _num(proxies.get("planet_proxy_km"), "planet proxy"), "gr": _num(proxies.get("gr_proxy_km"), "GR proxy"), "small_body": _num(proxies.get("small_body_proxy_km"), "small-body proxy"), "earth_j2": 0.0, "ng": 0.0}
    if any(value < 0.0 for value in values.values()):
        raise ValueError("force proxies must be finite and non-negative")
    earth = next((b for b in planets if getattr(b, "body_id", None) == "399"), None)
    cfg = force_config.get("earth_j2", {}) if isinstance(force_config, Mapping) else {}
    if not isinstance(cfg, Mapping):
        raise ValueError("force_config.earth_j2 must be a mapping")
    if earth is None:
        raise ValueError("features_v4 requires Earth body 399 for the J2 proxy")
    if earth is not None:
        radius = _num(cfg.get("reference_radius_km"), "earth_j2.reference_radius_km", True)
        j2 = _num(cfg.get("j2"), "earth_j2.j2", True)
        pole = cfg.get("pole_model")
        if pole not in ("iau", "fixed"):
            raise ValueError("Earth J2 pole model must be explicit")
        j2acc = earth_j2_acceleration(earth, float(start_jd), radius / au, j2, pole_model=pole)
        values["earth_j2"] = term_proxy([j2acc(t, s.position, s.velocity) for t, s in zip(times, states, strict=True)])
    status = base["ng"]["status"]
    if status != "available":
        values["ng"] = 0.0
    if status == "available":
        if ng is None or ng.parameters is None: raise ValueError("available NG status lacks parameters")
        ngacc = non_gravitational_acceleration(ng.parameters)
        values["ng"] = term_proxy([ngacc(t, s.position, s.velocity) for t, s in zip(times, states, strict=True)])
    base.update({"schema_version": 4, "feature_names": list(FEATURE_NAMES), "vector": list(base["vector"]),
                 "force_proxies_km": values, "ng_proxy_status": status,
                 "proxy_runtime_seconds": time.perf_counter()-started, "runtime_seconds": time.perf_counter()-started,
                 "accuracy_guaranteed": False})
    _validate_feature(base, base["vector"], float(horizon_days))
    return base

def _models(models: Sequence[Any]) -> list[str]:
    if isinstance(models, (str, bytes)) or not isinstance(models, Sequence) or not models: raise ValueError("models must be non-empty")
    out=[]
    for m in models:
        if not isinstance(m, Mapping):
            raise ValueError("models must be mappings with explicit force specs")
        ident = m.get("model_id")
        if not isinstance(ident, str) or not ident or ident in out: raise ValueError("models must have unique IDs")
        _spec(m)
        out.append(ident)
    if not _full_spec(_spec(models[-1])):
        raise ValueError("last candidate must be the complete nominal fallback")
    return out

def _check(rows, ids, label, fraction):
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows: raise ValueError(f"{label} must be non-empty")
    seen=set(); objects=set(); horizons=set(); checked=[]
    for i,r in enumerate(rows):
        if not isinstance(r, Mapping): raise ValueError(f"{label}[{i}] must be a mapping")
        oid=r.get("object_id"); mid=r.get("model_id"); h=_num(r.get("horizon_days"), f"{label}[{i}].horizon", True)
        if not isinstance(oid,str) or not oid or mid not in ids: raise ValueError(f"invalid {label} identity")
        split = r.get("split")
        if label=="training" and split != "train": raise ValueError("training rows must be labelled train")
        if label=="calibration" and split not in ("calibration", "validation", "holdout"): raise ValueError("calibration rows must have a calibration split")
        key=(oid,h,mid)
        if key in seen: raise ValueError("duplicate object/horizon/model row")
        seen.add(key); objects.add(oid); horizons.add(h); _row_vector(r, f"{label}[{i}]")
        if _num(r.get("max_position_error_km"), f"{label}[{i}].position") < 0 or _num(r.get("numerical_difference_km"), f"{label}[{i}].numerical") < 0:
            raise ValueError("errors must be non-negative")
        _effective(r, fraction, f"{label}[{i}]")
        if _num(r.get("runtime_median_seconds"), f"{label}[{i}].runtime") < 0: raise ValueError("runtime must be non-negative")
        if "feature_result" not in r or not isinstance(r["feature_result"], Mapping): raise ValueError("v4 rows require feature_result")
        p=r["feature_result"].get("force_proxies_km");
        if not isinstance(p, Mapping) or any(k not in p for k in PROXY_NAMES): raise ValueError("feature_result lacks force proxies")
        _validate_feature(r["feature_result"], r["features"], h)
        checked.append(r)
    expected={(oid,h,m) for oid in objects for h in horizons for m in ids}
    if seen != expected: raise ValueError(f"{label} must contain a complete object/horizon/model Cartesian product")
    cases={}
    for r in checked:
        case=(r["object_id"],float(r["horizon_days"]))
        fr = r["feature_result"]
        sig=(tuple(_row_vector(r,"case")), tuple(fr["force_proxies_km"][k] for k in PROXY_NAMES),
             fr["ng_proxy_status"], fr["strong_encounter"])
        if case in cases and cases[case] != sig: raise ValueError("feature_result must agree across candidate rows")
        cases[case]=sig
    return checked,objects,horizons

def fit_selector_v4(training, calibration, models, numerical_fraction=.1):
    ids=_models(models); frac=_num(numerical_fraction,"numerical_fraction",True)
    if frac > 1.:
        raise ValueError("numerical_fraction must be <= 1")
    tr,to,th=_check(training,ids,"training",frac); ca,co,ch=_check(calibration,ids,"calibration",frac)
    if to&co: raise ValueError("training and calibration object IDs overlap")
    if th != ch: raise ValueError("training and calibration horizons differ")
    if any(len(_row_vector(r,"row"))!=9 for r in tr+ca): raise ValueError("feature dimension must be 9")
    for r in tr+ca:
        p=r["feature_result"]["force_proxies_km"]
        if any(_num(p[k],"proxy")<0 for k in PROXY_NAMES): raise ValueError("proxies must be nonnegative")
    specs={m:_spec(next((x for x in models if (x.get("model_id") if isinstance(x,Mapping) else x)==m),{})) for m in ids}
    trees={}; margins={}; alphas={}; costs={m:{} for m in ids}
    fullrows=[r for r in tr if r["model_id"]==ids[-1]]
    baseline_floor={str(float(h)): max(_effective(r,frac,"train") for r in fullrows if float(r["horizon_days"])==h) for h in th}
    def scale_for(r,m):
        p=r["feature_result"]["force_proxies_km"]; s=specs[m]
        omitted = _omitted(s)
        return sum(float(p[k]) for k in omitted)
    for m in ids:
        baseline={h:baseline_floor[str(float(h))] for h in th}
        scales={id(r):scale_for(r,m)+baseline[float(r["horizon_days"])] for r in tr+ca}
        ratios=[_effective(r,frac,"train")/scales[id(r)] for r in tr if r["model_id"]==m]
        calrat=[_effective(r,frac,"cal")/scales[id(r)] for r in ca if r["model_id"]==m]
        alphas[m]=max(1.0,max(ratios),max(calrat))
        targets=[math.log10(_effective(r,frac,"train")/scales[id(r)]) for r in tr if r["model_id"]==m]
        trees[m]=fit_tree([_row_vector(r,"training") for r in tr if r["model_id"]==m],targets,[r["object_id"] for r in tr if r["model_id"]==m],3,4)
        residual=[math.log10(_effective(r,frac,"cal")/scales[id(r)])-predict_tree(trees[m],_row_vector(r,"calibration")) for r in ca if r["model_id"]==m]
        margins[m]=max(0.0,max(residual))
        for h in th:
            costs[m][str(float(h))]=statistics.median([_num(r["runtime_median_seconds"],"runtime") for r in tr if r["model_id"]==m and float(r["horizon_days"])==h])
    bounds={str(float(h)):{str(i): [min(r["features"][i] for r in tr if float(r["horizon_days"])==h),max(r["features"][i] for r in tr if float(r["horizon_days"])==h)] for i in (1,2,3,4,5,6,7)} for h in th}
    ng_bounds={str(float(h)): [min(r["features"][8] for r in tr if float(r["horizon_days"])==h and r["feature_result"].get("ng_proxy_status")=="available"),max(r["features"][8] for r in tr if float(r["horizon_days"])==h and r["feature_result"].get("ng_proxy_status")=="available")] for h in th if any(float(r["horizon_days"])==h and r["feature_result"].get("ng_proxy_status")=="available" for r in tr)}
    return {"schema_version":4,"method":"selector_v4","models":ids,"fallback_model_id":ids[-1],"feature_dimension":9,"feature_names":list(FEATURE_NAMES),"numerical_fraction":frac,"trees":trees,"calibration_margin_log10":margins,"physics_alpha":alphas,"baseline_floor_km":baseline_floor,"median_cost_seconds":costs,"horizons_days":sorted(th),"training_object_ids":sorted(to),"calibration_object_ids":sorted(co),"support_bounds":bounds,"ng_available_support":ng_bounds,"force_specs":specs,"accuracy_guaranteed":False}

def choose_v4(feature_result, horizon, tolerance, artifact, method="hybrid_v4"):
    if not isinstance(feature_result, Mapping): raise TypeError("feature_result must be a mapping")
    required = ("models", "fallback_model_id", "trees", "calibration_margin_log10", "physics_alpha", "baseline_floor_km", "median_cost_seconds", "horizons_days", "support_bounds", "ng_available_support", "force_specs")
    if not isinstance(artifact, Mapping) or artifact.get("schema_version")!=4 or artifact.get("method")!="selector_v4" or any(k not in artifact for k in required):
        raise ValueError("invalid incomplete selector v4 artifact")
    if method not in ("hybrid_v4","physics_v4"): raise ValueError("invalid selector method")
    h=_num(horizon,"horizon",True); tol=_num(tolerance,"tolerance",True)
    if h not in [float(x) for x in artifact["horizons_days"]]: raise ValueError("horizon is absent from selector artifact")
    vec=feature_result.get("vector")
    if not isinstance(vec,Sequence) or isinstance(vec,(str,bytes)) or len(vec)!=9: raise ValueError("feature vector must have dimension 9")
    v=[_num(x,"feature vector") for x in vec]; p=feature_result.get("force_proxies_km")
    _validate_feature(feature_result, v, h)
    ids = artifact["models"]
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids) or any(not isinstance(m, str) for m in ids):
        raise ValueError("invalid model IDs in artifact")
    if artifact.get("feature_dimension") != 9 or artifact.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("artifact feature schema differs")
    if artifact["fallback_model_id"] != ids[-1]:
        raise ValueError("fallback must be the last full candidate")
    for mid in ids:
        for field in ("trees", "calibration_margin_log10", "physics_alpha", "median_cost_seconds", "force_specs"):
            if not isinstance(artifact[field], Mapping) or mid not in artifact[field]:
                raise ValueError(f"artifact lacks {field} for {mid}")
        _spec({"model_id": mid, **artifact["force_specs"][mid]})
    if not _full_spec(artifact["force_specs"][ids[-1]]):
        raise ValueError("invalid fallback force specification")
    if not isinstance(p,Mapping) or any(k not in p for k in PROXY_NAMES): raise ValueError("feature_result lacks force proxies")
    if "horizon_days" in feature_result and _num(feature_result["horizon_days"], "feature_result.horizon_days") != h: raise ValueError("feature result horizon differs")
    if not math.isclose(v[0], math.log10(h), rel_tol=0.0, abs_tol=1e-10): raise ValueError("feature vector horizon differs")
    for key in PROXY_NAMES:
        value = _num(p[key], f"force_proxies_km.{key}")
        if value < 0.0: raise ValueError("force proxies must be non-negative")
    for index, key in ((3, "planet"), (4, "gr"), (5, "small_body")):
        if not math.isclose(v[index], math.log10(max(float(p[key]), _FLOOR)), rel_tol=0.0, abs_tol=1e-10): raise ValueError("force proxy differs from v3 vector")
    if feature_result.get("ng_proxy_status") != "available" and float(p["ng"]) != 0.0: raise ValueError("unavailable NG proxy must be zero")
    outside=[]
    hkey=str(float(h))
    support = artifact["support_bounds"].get(hkey)
    if not isinstance(support, Mapping) or set(support) != {str(i) for i in range(1, 8)}:
        raise ValueError("artifact lacks complete same-horizon support bounds")
    for k,b in support.items():
        i=int(k)
        if not isinstance(b, Sequence) or len(b) != 2 or _num(b[0], "support lower") > _num(b[1], "support upper"):
            raise ValueError("artifact has invalid support bounds")
        if v[i]<b[0] or v[i]>b[1]: outside.append(FEATURE_NAMES[i])
    if feature_result.get("ng_proxy_status")=="available":
        if hkey not in artifact.get("ng_available_support",{}):
            outside.append("ng_feature_no_training_support")
        else:
            b=artifact["ng_available_support"][hkey]
            if not isinstance(b, Sequence) or len(b) != 2 or _num(b[0], "NG support lower") > _num(b[1], "NG support upper"):
                raise ValueError("invalid available-NG support")
            if v[8]<b[0] or v[8]>b[1]: outside.append(FEATURE_NAMES[8])
    full=artifact["fallback_model_id"]; specs=artifact["force_specs"]; caps={}; rejected={}; statuses={}
    try:
        floor = _num(artifact["baseline_floor_km"][str(float(h))], "baseline floor", True)
    except (KeyError, TypeError) as exc:
        raise ValueError("artifact lacks finite positive floor for horizon") from exc
    for m in artifact["models"]:
        s=specs[m]; omitted=_omitted(s)
        scale=sum(float(p[k]) for k in omitted) + floor
        alpha = _num(artifact["physics_alpha"][m], f"physics alpha {m}")
        if alpha < 1.0: raise ValueError("physics alpha must be at least one")
        if method=="physics_v4": cap=alpha*scale
        else:
            margin = _num(artifact["calibration_margin_log10"][m], f"margin {m}")
            if margin < 0.:
                raise ValueError("calibration margin must be non-negative")
            pred=predict_tree(artifact["trees"][m],v)+margin
            try: cap=scale*10**pred
            except OverflowError: raise ValueError("hybrid cap overflow")
            if not math.isfinite(cap): raise ValueError("hybrid cap is not finite")
        if not math.isfinite(cap) or not math.isfinite(scale):
            raise ValueError("force scale/cap overflow; accuracy cannot be assessed")
        caps[m]=max(cap,scale) if method=="hybrid_v4" else cap; statuses[m]="predicted_feasible" if caps[m]<=tol else "predicted_infeasible"
        rejected[m]=omitted
        costs = artifact["median_cost_seconds"].get(m)
        if not isinstance(costs, Mapping) or str(float(h)) not in costs or _num(costs[str(float(h))], f"cost {m}") < 0: raise ValueError("artifact has invalid cost")
    strong=feature_result.get("strong_encounter") is True
    feasible=[m for m in artifact["models"] if caps[m]<=tol]
    reasons=[]
    if outside: reasons.append("outside_training_support")
    no_candidate = not feasible
    if strong: reasons.append("strong_encounter_unvalidated")
    if no_candidate: reasons.append("no_candidate_predicted")
    if strong: chosen=full; status="strong_encounter_unvalidated"; fallback=True
    elif outside: chosen=full; status="outside_training_support"; fallback=True
    elif feasible: chosen=min(feasible,key=lambda m:(artifact["median_cost_seconds"][m][str(float(h))],artifact["models"].index(m))); status="predicted_feasible"; fallback=False
    else: chosen=full; status="no_candidate_predicted"; fallback=True
    return {"schema_version":4,"method":method,"model_id":chosen,"horizon_days":h,"tolerance_km":tol,"predicted_error_caps_km":caps,"statuses":statuses,"status":status,"warning":status != "predicted_feasible","fallback":fallback,"strong_encounter":strong,"outside_training_support":bool(outside),"no_candidate_predicted":no_candidate,"train_cost_seconds":float(artifact["median_cost_seconds"][chosen][str(float(h))]),"accuracy_guaranteed":False,"conditional_on_initial_state":True,"covariance_calibrated":False,"outside_training_support_reasons":outside,"warning_reasons":reasons,"rejected_forces":rejected,"full_model_id":full,"full_cap_km":caps[full]}

__all__=["features_v4","fit_selector_v4","choose_v4"]
