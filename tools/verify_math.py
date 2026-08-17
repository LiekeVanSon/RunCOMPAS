#!/usr/bin/env python3
"""
Prove the vectorised rejection logic is numerically identical to the original.

Compares, over a wide random sample of masses/metallicities/separations:
  * _zams_radius        vs stroopwafel.utils.get_zams_radius
  * _roche_lobe_radius  vs stroopwafel.utils.calculate_roche_lobe_radius
  * rejected_systems    vs a faithful re-implementation of the original loop
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import setup  # noqa: E402
setup(need_interface=True)

from stroopwafel import utils, constants, classes  # noqa: E402
import stroopwafel_interface as SI  # noqa: E402

rng = np.random.default_rng(12345)
N = 200000

mass_1 = 10 ** rng.uniform(np.log10(0.5), np.log10(150), N)
mass_2 = mass_1 * rng.uniform(0.01, 1.0, N)
Z = 10 ** rng.uniform(-4, np.log10(0.03), N)
sep = 10 ** rng.uniform(-2, 3, N)
ecc = np.zeros(N)

print(f"comparing over {N:,} random systems\n")

# ---- 1. ZAMS radius -------------------------------------------------------
scalar = np.array([utils.get_zams_radius(m, z) for m, z in zip(mass_1, Z)])
vector = SI._zams_radius(mass_1, Z)
exact = np.array_equal(scalar, vector)
maxrel = np.nanmax(np.abs(vector - scalar) / np.abs(scalar))
print(f"  _zams_radius        bitwise identical: {exact}   max rel diff: {maxrel:.3e}")

# ---- 2. Roche lobe radius -------------------------------------------------
scalar_rl = np.array([utils.calculate_roche_lobe_radius(a, b) for a, b in zip(mass_1, mass_2)])
vector_rl = SI._roche_lobe_radius(mass_1, mass_2)
exact_rl = np.array_equal(scalar_rl, vector_rl)
maxrel_rl = np.nanmax(np.abs(vector_rl - scalar_rl) / np.abs(scalar_rl))
print(f"  _roche_lobe_radius  bitwise identical: {exact_rl}   max rel diff: {maxrel_rl:.3e}")


# ---- 3. the whole rejection decision --------------------------------------
class FakeDim:
    def __init__(self, name):
        self.name = name


def build_locations():
    m1d, qd, ad = FakeDim('--initial-mass-1'), FakeDim('q'), FakeDim('--semi-major-axis')
    locs = []
    for i in range(N):
        loc = classes.Location({m1d: mass_1[i], qd: mass_2[i] / mass_1[i], ad: sep[i]}, {})
        loc.properties['--initial-mass-2'] = mass_2[i]
        loc.properties['--metallicity'] = Z[i]
        loc.properties['--eccentricity'] = ecc[i]
        locs.append(loc)
    return locs, [m1d, qd, ad]


def original_rejected_systems(locations, dimensions):
    """Verbatim the pre-optimisation loop."""
    m1, q, a = dimensions[0], dimensions[1], dimensions[2]
    mass_1_ = [loc.dimensions[m1] for loc in locations]
    mass_2_ = [loc.properties['--initial-mass-2'] for loc in locations]
    metallicity_ = [loc.properties['--metallicity'] for loc in locations]
    eccentricity_ = [loc.properties['--eccentricity'] for loc in locations]
    num_rejected = 0
    flags = []
    for index, location in enumerate(locations):
        radius_1 = utils.get_zams_radius(mass_1_[index], metallicity_[index])
        radius_2 = utils.get_zams_radius(mass_2_[index], metallicity_[index])
        s = location.dimensions[a] * (1 - eccentricity_[index])
        rl_1 = radius_1 / (s * utils.calculate_roche_lobe_radius(mass_1_[index], mass_2_[index]))
        rl_2 = radius_2 / (s * utils.calculate_roche_lobe_radius(mass_2_[index], mass_1_[index]))
        flag = 0
        if (mass_2_[index] < constants.MINIMUM_SECONDARY_MASS
                or location.dimensions[a] <= (radius_1 + radius_2)
                or rl_1 > 1 or rl_2 > 1):
            flag = 1
            num_rejected += 1
        flags.append(flag)
    return num_rejected, np.array(flags)


locs, dims = build_locations()
n_old, flags_old = original_rejected_systems(locs, dims)

locs2, dims2 = build_locations()
n_new = SI.rejected_systems(locs2, dims2)
flags_new = np.array([loc.properties['is_rejected'] for loc in locs2])

print(f"\n  rejected count   original: {n_old:,}   vectorised: {n_new:,}   "
      f"equal: {n_old == n_new}")
print(f"  per-system flags identical: {np.array_equal(flags_old, flags_new)}   "
      f"(disagreements: {int((flags_old != flags_new).sum())})")
print(f"  rejection rate: {n_old / N:.6f}")

# Bitwise identity is not the right bar: numpy's ** and C's pow() are separate
# implementations of the same operation and may differ in the last bit. What
# must hold is that every REJECTION DECISION is identical, and that the radii
# agree to within a few ULP (float64 epsilon = 2.2e-16).
EPS = np.finfo(float).eps
radii_ok = maxrel < 10 * EPS and maxrel_rl < 10 * EPS
decisions_ok = n_old == n_new and np.array_equal(flags_old, flags_new)
print(f"\n  radii agree to within 10 ULP ({10*EPS:.2e}): {radii_ok}")
print(f"  every rejection decision identical:      {decisions_ok}")
ok = radii_ok and decisions_ok
print(f"\n  {'PASS -- math is unchanged' if ok else 'FAIL -- results differ'}")
sys.exit(0 if ok else 1)
