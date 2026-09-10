"""Deterministic, group-aware CART regression tree.

The tree is deliberately small and dependency-free.  ``object_ids`` are used
only to enforce the minimum number of distinct objects in each child; they are
never exposed as split features.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


_SCHEMA_VERSION = 1


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _positive_int(value: object, name: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < (0 if allow_zero else 1):
        requirement = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {requirement}")
    return value


def _features_and_targets(
    features: Sequence[Sequence[object]],
    targets: Sequence[object],
    object_ids: Sequence[object],
) -> tuple[tuple[tuple[float, ...], ...], tuple[float, ...], tuple[str, ...], int]:
    if isinstance(features, (str, bytes)) or not isinstance(features, Sequence) or not features:
        raise ValueError("features must be a non-empty sequence of rows")
    if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
        raise ValueError("targets must be a sequence")
    if isinstance(object_ids, (str, bytes)) or not isinstance(object_ids, Sequence):
        raise ValueError("object_ids must be a sequence")
    if len(features) != len(targets) or len(features) != len(object_ids):
        raise ValueError("features, targets and object_ids must have equal lengths")

    first = features[0]
    if isinstance(first, (str, bytes)) or not isinstance(first, Sequence) or not first:
        raise ValueError("feature rows must be non-empty sequences")
    dimension = len(first)
    converted_features: list[tuple[float, ...]] = []
    for row_index, row in enumerate(features):
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence) or len(row) != dimension:
            raise ValueError(f"features[{row_index}] has inconsistent dimension")
        converted_features.append(tuple(
            _finite(value, f"features[{row_index}][{column}]")
            for column, value in enumerate(row)
        ))

    converted_targets = tuple(_finite(value, f"targets[{index}]") for index, value in enumerate(targets))
    converted_ids: list[str] = []
    for index, identifier in enumerate(object_ids):
        if not isinstance(identifier, str):
            raise ValueError(f"object_ids[{index}] must be a string")
        converted_ids.append(identifier)
    return tuple(converted_features), converted_targets, tuple(converted_ids), dimension


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _sse(values: Sequence[float]) -> float:
    mean = _mean(values)
    return math.fsum((value - mean) ** 2 for value in values)


def _leaf(targets: Sequence[float], ids: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "leaf",
        "prediction": _mean(targets),
        "sample_count": len(targets),
        "object_count": len(set(ids)),
    }


def _midpoint(left: float, right: float) -> float | None:
    # Halving before adding avoids overflow for opposite-sign extreme values.
    threshold = left / 2.0 + right / 2.0
    if not math.isfinite(threshold) or not left < threshold < right:
        return None
    return threshold


def _build(
    features: tuple[tuple[float, ...], ...],
    targets: tuple[float, ...],
    ids: tuple[str, ...],
    indices: tuple[int, ...],
    depth: int,
    max_depth: int,
    min_leaf_objects: int,
) -> dict[str, Any]:
    local_targets = tuple(targets[index] for index in indices)
    local_ids = tuple(ids[index] for index in indices)
    parent_sse = _sse(local_targets)
    if depth >= max_depth or len(set(local_ids)) < 2:
        return _leaf(local_targets, local_ids)

    best: tuple[float, int, float, tuple[int, ...], tuple[int, ...]] | None = None
    dimension = len(features[0])
    for feature_index in range(dimension):
        distinct = sorted({features[index][feature_index] for index in indices})
        for left_value, right_value in zip(distinct, distinct[1:]):
            threshold = _midpoint(left_value, right_value)
            if threshold is None:
                continue
            left_indices = tuple(index for index in indices if features[index][feature_index] <= threshold)
            right_indices = tuple(index for index in indices if features[index][feature_index] > threshold)
            if not left_indices or not right_indices:
                continue
            if len({ids[index] for index in left_indices}) < min_leaf_objects:
                continue
            if len({ids[index] for index in right_indices}) < min_leaf_objects:
                continue
            split_sse = _sse(tuple(targets[index] for index in left_indices)) + _sse(
                tuple(targets[index] for index in right_indices)
            )
            candidate = (split_sse, feature_index, threshold, left_indices, right_indices)
            # Iteration order is feature, then threshold.  Strict comparison
            # keeps the first deterministic split on an exact SSE tie.
            if best is None or split_sse < best[0]:
                best = candidate

    if best is None or not best[0] < parent_sse:
        return _leaf(local_targets, local_ids)
    split_sse, feature_index, threshold, left_indices, right_indices = best
    return {
        "type": "split",
        "feature_index": feature_index,
        "threshold": threshold,
        "sample_count": len(indices),
        "object_count": len(set(local_ids)),
        "sse": split_sse,
        "left": _build(features, targets, ids, left_indices, depth + 1, max_depth, min_leaf_objects),
        "right": _build(features, targets, ids, right_indices, depth + 1, max_depth, min_leaf_objects),
    }


def fit_tree(
    features: list[list[float]],
    targets: list[float],
    object_ids: list[str],
    max_depth: int = 3,
    min_leaf_objects: int = 4,
) -> dict[str, Any]:
    """Fit a deterministic squared-error CART tree and return JSON data."""
    converted_features, converted_targets, converted_ids, dimension = _features_and_targets(
        features, targets, object_ids
    )
    depth = _positive_int(max_depth, "max_depth", allow_zero=True)
    minimum_objects = _positive_int(min_leaf_objects, "min_leaf_objects")
    return {
        "schema_version": _SCHEMA_VERSION,
        "model": "cart_regression_tree",
        "n_features": dimension,
        "max_depth": depth,
        "min_leaf_objects": minimum_objects,
        "root": _build(
            converted_features,
            converted_targets,
            converted_ids,
            tuple(range(len(converted_targets))),
            0,
            depth,
            minimum_objects,
        ),
    }


def _validate_node(node: object, dimension: int, path: str) -> Mapping[str, Any]:
    if not isinstance(node, Mapping):
        raise ValueError(f"{path} must be a tree node")
    node_type = node.get("type")
    if node_type == "leaf":
        _finite(node.get("prediction"), f"{path}.prediction")
        return node
    if node_type != "split":
        raise ValueError(f"{path}.type must be leaf or split")
    feature_index = node.get("feature_index")
    if isinstance(feature_index, bool) or not isinstance(feature_index, int) or not 0 <= feature_index < dimension:
        raise ValueError(f"{path}.feature_index is invalid")
    _finite(node.get("threshold"), f"{path}.threshold")
    _validate_node(node.get("left"), dimension, f"{path}.left")
    _validate_node(node.get("right"), dimension, f"{path}.right")
    return node


def predict_tree(tree: dict, vector: list[float]) -> float:
    """Predict one target from a fitted JSON-compatible tree."""
    if not isinstance(tree, Mapping):
        raise ValueError("tree must be a mapping")
    if tree.get("schema_version") != _SCHEMA_VERSION or tree.get("model") != "cart_regression_tree":
        raise ValueError("unsupported tree schema")
    dimension = _positive_int(tree.get("n_features"), "tree.n_features")
    if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence) or len(vector) != dimension:
        raise ValueError("vector has the wrong dimension")
    values = tuple(_finite(value, f"vector[{index}]") for index, value in enumerate(vector))
    node = _validate_node(tree.get("root"), dimension, "tree.root")
    while node.get("type") == "split":
        child = "left" if values[node["feature_index"]] <= float(node["threshold"]) else "right"
        node = _validate_node(node[child], dimension, f"tree.root.{child}")
    return _finite(node.get("prediction"), "tree prediction")


__all__ = ["fit_tree", "predict_tree"]
