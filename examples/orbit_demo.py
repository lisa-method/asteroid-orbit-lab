"""A portable two-body smoke example with an exact circular-orbit reference.

This synthetic example demonstrates the numerical code. It is not an asteroid
benchmark, a v4 selector replay, or a reproduction of the confirmation100 score.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orbit_baselines import (  # noqa: E402
    State,
    norm,
    propagate,
    specific_energy,
    specific_angular_momentum,
    subtract,
    two_body_acceleration,
)


def main() -> int:
    # Fixed illustrative constants; distance in AU, time in days.
    mu = 0.0002959122082855911
    au_metres = 149_597_870_700.0
    omega = math.sqrt(mu)
    initial = State((1.0, 0.0, 0.0), (0.0, omega, 0.0))
    days = [0.0, 7.0, 30.0, 90.0, 180.0, 365.0]
    states = propagate(initial, days, two_body_acceleration(mu), max_step_days=0.25)
    initial_energy = specific_energy(initial, mu)
    initial_momentum = specific_angular_momentum(initial)
    print("ASTEROID ORBITS | two-body numerical example")
    print("Synthetic circular orbit at 1 AU; exact analytic reference.")
    print("This is a code smoke check, not the 100-body research benchmark.\n")
    print(f"{'Day':>6}  {'Position error (m)':>20}  {'Relative energy drift':>23}")
    errors = []
    momenta = []
    for day, state in zip(days, states, strict=True):
        reference = (math.cos(omega * day), math.sin(omega * day), 0.0)
        error = norm(subtract(state.position, reference)) * au_metres
        drift = abs((specific_energy(state, mu) - initial_energy) / initial_energy)
        errors.append(error)
        momenta.append(abs(specific_angular_momentum(state) / initial_momentum - 1.0))
        print(f"{day:6.0f}  {error:20.6f}  {drift:23.3e}")
    if max(errors) > 100.0 or max(momenta) > 1e-8:
        raise AssertionError("Synthetic two-body smoke budget exceeded")
    print("\nPassed: position error < 100 m; relative angular-momentum drift < 1e-8.")
    print("For the measured selector result, see docs/SELECTOR_CONFIRMATION100_REPORT.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
