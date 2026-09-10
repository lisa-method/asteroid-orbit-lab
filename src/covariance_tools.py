"""Small, dependency-free covariance and sigma-point utilities.

The functions operate on nested tuples/lists of finite real numbers.  Units
are deliberately supplied by the caller: this module never combines position
and velocity blocks or silently rescales a covariance.  Cholesky routines
require strict positive definiteness; zero and merely positive-semidefinite
covariances are rejected because their sigma-point factorization is otherwise
ambiguous without an explicit rank policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence


Matrix = tuple[tuple[float, ...], ...]
Vector = tuple[float, ...]


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _matrix(value: Sequence[Sequence[object]], name: str, *, square: bool | None = None) -> Matrix:
    try:
        rows = tuple(tuple(_finite(item, f"{name}[{i}][{j}]") for j, item in enumerate(row))
                     for i, row in enumerate(value))
    except TypeError as exc:
        raise ValueError(f"{name} must be a rectangular finite matrix") from exc
    if not rows or not rows[0]:
        raise ValueError(f"{name} must be non-empty")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError(f"{name} must be rectangular")
    if square is True and len(rows) != width:
        raise ValueError(f"{name} must be square")
    return rows


def _symmetrize(matrix: Matrix) -> Matrix:
    return tuple(tuple((matrix[i][j] + matrix[j][i]) / 2.0 for j in range(len(matrix)))
                   for i in range(len(matrix)))


def validate_covariance(
    covariance: Sequence[Sequence[object]],
    *,
    symmetry_atol: float = 0.0,
    symmetry_rtol: float = 1e-12,
    require_positive_diagonal: bool = True,
) -> Matrix:
    """Validate and return a square finite symmetric covariance matrix.

    This checks representation and symmetry only.  Use :func:`scaled_cholesky`
    when a strict SPD check is required.  ``symmetry_atol`` and
    ``symmetry_rtol`` are comparison tolerances, never a matrix modification.
    """

    matrix = _matrix(covariance, "covariance", square=True)
    atol = _finite(symmetry_atol, "symmetry_atol")
    rtol = _finite(symmetry_rtol, "symmetry_rtol")
    if atol < 0.0 or rtol < 0.0:
        raise ValueError("symmetry tolerances must be non-negative")
    for i in range(len(matrix)):
        if require_positive_diagonal and matrix[i][i] <= 0.0:
            raise ValueError("covariance must have strictly positive diagonal")
        for j in range(i):
            difference = abs(matrix[i][j] - matrix[j][i])
            scale = max(abs(matrix[i][j]), abs(matrix[j][i]))
            if difference > atol + rtol * scale:
                raise ValueError("covariance must be symmetric")
    return matrix


def _residual(covariance: Matrix, factor: Matrix) -> float:
    n = len(covariance)
    maximum = 0.0
    for i in range(n):
        for j in range(n):
            reconstructed = math.fsum(factor[i][k] * factor[j][k] for k in range(n))
            scale = max(abs(covariance[i][j]), math.sqrt(abs(covariance[i][i] * covariance[j][j])), 1e-300)
            maximum = max(maximum, abs(reconstructed - covariance[i][j]) / scale)
    return maximum


@dataclass(frozen=True)
class CholeskyResult:
    """Scaled lower-triangular factor and its relative reconstruction residual."""

    factor: Matrix
    residual: float
    scales: Vector


def scaled_cholesky(
    covariance: Sequence[Sequence[object]],
    *,
    pivot_tolerance: float = 1e-14,
    residual_tolerance: float = 1e-12,
) -> CholeskyResult:
    """Return a strict-SPD Cholesky factor using diagonal scaling.

    The matrix is factored as ``C = D R D`` where ``D`` contains the square
    roots of the diagonal and ``R`` is a correlation-scale matrix.  No jitter,
    eigenvalue clipping, or other covariance modification is performed.
    """

    matrix = validate_covariance(covariance)
    n = len(matrix)
    pivot_tol = _finite(pivot_tolerance, "pivot_tolerance")
    residual_tol = _finite(residual_tolerance, "residual_tolerance")
    if pivot_tol < 0.0 or residual_tol < 0.0:
        raise ValueError("Cholesky tolerances must be non-negative")
    scales = tuple(math.sqrt(matrix[i][i]) for i in range(n))
    correlation = tuple(tuple(matrix[i][j] / (scales[i] * scales[j]) for j in range(n)) for i in range(n))
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            remainder = math.fsum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                pivot = correlation[i][i] - remainder
                if not math.isfinite(pivot) or pivot <= pivot_tol:
                    raise ValueError("covariance is not strictly positive definite")
                lower[i][j] = math.sqrt(pivot)
            else:
                lower[i][j] = (correlation[i][j] - remainder) / lower[j][j]
    factor = tuple(tuple(scales[i] * lower[i][j] if j <= i else 0.0 for j in range(n)) for i in range(n))
    residual = _residual(matrix, factor)
    if not math.isfinite(residual) or residual > residual_tol:
        raise ValueError(f"Cholesky reconstruction residual {residual:g} exceeds tolerance")
    return CholeskyResult(factor=factor, residual=residual, scales=scales)


def transform_covariance(
    jacobian: Sequence[Sequence[object]],
    covariance: Sequence[Sequence[object]],
) -> Matrix:
    """Compute ``J C Jᵀ`` with pairwise symmetric, compensated sums."""

    jac = _matrix(jacobian, "jacobian")
    cov = validate_covariance(covariance, require_positive_diagonal=False)
    input_dimension = len(cov)
    if len(jac[0]) != input_dimension:
        raise ValueError("jacobian column count must equal covariance dimension")
    intermediate = tuple(tuple(math.fsum(jac[i][k] * cov[k][l] for k in range(input_dimension))
                               for l in range(input_dimension)) for i in range(len(jac)))
    output_dimension = len(jac)
    result = [[0.0] * output_dimension for _ in range(output_dimension)]
    for i in range(output_dimension):
        for j in range(i + 1):
            value = math.fsum(intermediate[i][k] * jac[j][k] for k in range(input_dimension))
            result[i][j] = value
            result[j][i] = value
    return tuple(tuple(row) for row in result)


def sigma_points(
    mean: Iterable[object],
    covariance: Sequence[Sequence[object]],
) -> tuple[tuple[Vector, ...], tuple[float, ...]]:
    """Return symmetric ``±sqrt(n)`` column sigma points and equal weights."""

    central = tuple(_finite(value, f"mean[{i}]") for i, value in enumerate(mean))
    if not central:
        raise ValueError("mean must be non-empty")
    cov = validate_covariance(covariance)
    if len(cov) != len(central):
        raise ValueError("mean and covariance dimensions differ")
    factor = scaled_cholesky(cov).factor
    n = len(central)
    radius = math.sqrt(n)
    points: list[Vector] = []
    for column in range(n):
        direction = tuple(radius * factor[row][column] for row in range(n))
        points.append(tuple(central[i] + direction[i] for i in range(n)))
        points.append(tuple(central[i] - direction[i] for i in range(n)))
    return tuple(points), tuple(1.0 / (2.0 * n) for _ in points)


def sigma_point_moments(
    points: Sequence[Sequence[object]],
    weights: Sequence[object],
) -> tuple[Vector, Matrix]:
    """Return weighted mean and covariance using compensated sums.

    Weights must be finite and non-negative.  They are normalized by their
    finite sum, allowing callers to pass equivalent weights with a different
    common scale while preserving the stated sigma-point convention.
    """

    if not points or len(points) != len(weights):
        raise ValueError("points and weights must be non-empty and equally sized")
    converted = tuple(tuple(_finite(value, f"points[{i}][{j}]") for j, value in enumerate(point))
                      for i, point in enumerate(points))
    dimension = len(converted[0])
    if dimension == 0 or any(len(point) != dimension for point in converted):
        raise ValueError("points must be a non-empty rectangular collection")
    converted_weights = tuple(_finite(weight, f"weights[{i}]") for i, weight in enumerate(weights))
    if any(weight < 0.0 for weight in converted_weights):
        raise ValueError("weights must be non-negative")
    total = math.fsum(converted_weights)
    if total <= 0.0:
        raise ValueError("weights must have a positive sum")
    normalized = tuple(weight / total for weight in converted_weights)
    mean = tuple(math.fsum(weight * point[i] for point, weight in zip(converted, normalized, strict=True))
                 for i in range(dimension))
    result = [[0.0] * dimension for _ in range(dimension)]
    for i in range(dimension):
        for j in range(i + 1):
            value = math.fsum(weight * (point[i] - mean[i]) * (point[j] - mean[j])
                              for point, weight in zip(converted, normalized, strict=True))
            result[i][j] = value
            result[j][i] = value
    return mean, tuple(tuple(row) for row in result)


@dataclass(frozen=True)
class JacobiEigenResult:
    eigenvalues: Vector
    eigenvectors: Matrix
    residual: float
    sweeps: int


def jacobi_eigh_3x3(
    matrix: Sequence[Sequence[object]],
    *,
    tolerance: float = 1e-14,
    max_sweeps: int = 50,
) -> JacobiEigenResult:
    """Diagonalize a finite symmetric 3×3 matrix by Jacobi rotations.

    Eigenvectors are returned as columns, sorted by descending eigenvalue, with
    deterministic signs.  The residual is the maximum relative norm of
    ``A V - V diag(values)``; no eigenvalue clipping is applied.
    """

    source = validate_covariance(matrix, symmetry_atol=0.0, symmetry_rtol=1e-12,
                                 require_positive_diagonal=False)
    if len(source) != 3:
        raise ValueError("jacobi_eigh_3x3 requires a 3x3 matrix")
    tol = _finite(tolerance, "tolerance")
    if tol < 0.0 or isinstance(max_sweeps, bool) or not isinstance(max_sweeps, int) or max_sweeps <= 0:
        raise ValueError("invalid Jacobi tolerance or sweep budget")
    scale = max(abs(value) for row in source for value in row)
    if scale == 0.0:
        return JacobiEigenResult((0.0, 0.0, 0.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), 0.0, 0)
    a = [[source[i][j] / scale for j in range(3)] for i in range(3)]
    vectors = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    sweeps = 0
    for sweep in range(1, max_sweeps + 1):
        sweeps = sweep
        p, q = max(((i, j) for i in range(3) for j in range(i + 1, 3)),
                   key=lambda pair: abs(a[pair[0]][pair[1]]))
        if abs(a[p][q]) <= tol:
            break
        app, aqq, apq = a[p][p], a[q][q], a[p][q]
        tau = (aqq - app) / (2.0 * apq)
        t = math.copysign(1.0 / (abs(tau) + math.sqrt(1.0 + tau * tau)), tau)
        c = 1.0 / math.sqrt(1.0 + t * t)
        s = t * c
        for k in range(3):
            if k not in (p, q):
                akp, akq = a[k][p], a[k][q]
                a[k][p] = a[p][k] = c * akp - s * akq
                a[k][q] = a[q][k] = s * akp + c * akq
        a[p][p] = c * c * app - 2.0 * s * c * apq + s * s * aqq
        a[q][q] = s * s * app + 2.0 * s * c * apq + c * c * aqq
        a[p][q] = a[q][p] = 0.0
        for k in range(3):
            vkp, vkq = vectors[k][p], vectors[k][q]
            vectors[k][p] = c * vkp - s * vkq
            vectors[k][q] = s * vkp + c * vkq
    else:
        raise ValueError("Jacobi eigensolver exceeded sweep budget")
    order = sorted(range(3), key=lambda i: a[i][i], reverse=True)
    values = tuple(a[i][i] * scale for i in order)
    columns = [[vectors[row][column] for row in range(3)] for column in order]
    for column in columns:
        pivot = max(range(3), key=lambda i: abs(column[i]))
        if column[pivot] < 0.0:
            for i in range(3):
                column[i] = -column[i]
    eigenvectors = tuple(tuple(columns[column][row] for column in range(3)) for row in range(3))
    residual = 0.0
    for row in range(3):
        for column in range(3):
            left = math.fsum(source[row][k] * eigenvectors[k][column] for k in range(3))
            right = eigenvectors[row][column] * values[column]
            residual = max(residual, abs(left - right) / scale)
    if not math.isfinite(residual) or residual > max(100.0 * tol, 1e-13):
        raise ValueError(f"Jacobi residual {residual:g} exceeds tolerance")
    return JacobiEigenResult(values, eigenvectors, residual, sweeps)


@dataclass(frozen=True)
class PrincipalPositionSigmas:
    eigenvalues: Vector
    eigenvectors: Matrix
    offsets: tuple[Vector, ...]
    residual: float
    units: str | None


def principal_position_sigmas(
    covariance: Sequence[Sequence[object]],
    *,
    units: str | None = None,
    eigenvalue_tolerance: float = 1e-12,
) -> PrincipalPositionSigmas:
    """Return principal 3D position offsets ``sqrt(lambda) * eigenvector``.

    The input must be a standalone 3×3 position covariance.  Every negative
    eigenvalue is rejected explicitly, including tiny numerical negatives;
    this helper never silently clips an unresolved PSD mode to zero.
    """

    source = validate_covariance(covariance, symmetry_atol=0.0, symmetry_rtol=1e-12,
                                 require_positive_diagonal=False)
    result = jacobi_eigh_3x3(source)
    scale = max(max(abs(value) for row in source for value in row), 1e-300)
    tolerance = _finite(eigenvalue_tolerance, "eigenvalue_tolerance")
    if tolerance < 0.0:
        raise ValueError("eigenvalue_tolerance must be non-negative")
    values = []
    for value in result.eigenvalues:
        # Do not hide a negative mode by clipping it to zero.  Even a tiny
        # negative mode is an unresolved PSD/numerical issue for the caller.
        if value < 0.0:
            raise ValueError("position covariance has a negative eigenvalue; PSD is unresolved")
        values.append(value)
    offsets = tuple(tuple(math.sqrt(values[column]) * result.eigenvectors[row][column] for row in range(3))
                    for column in range(3))
    return PrincipalPositionSigmas(tuple(values), result.eigenvectors, offsets, result.residual, units)


__all__ = [
    "CholeskyResult", "JacobiEigenResult", "Matrix", "PrincipalPositionSigmas", "Vector",
    "jacobi_eigh_3x3", "principal_position_sigmas", "scaled_cholesky", "sigma_point_moments",
    "sigma_points", "transform_covariance", "validate_covariance",
]
