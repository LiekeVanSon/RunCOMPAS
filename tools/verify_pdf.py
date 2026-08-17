#!/usr/bin/env python3
"""
Verify the diagonal-covariance fast path against the original scipy call.

The original is reproduced verbatim below as `original_pdf_sum` so the
comparison is against the real prior behaviour, not against my description of it.

Covers: several dimensionalities, wide dynamic range, deliberate underflow,
biased_weight, and the cases that MUST fall back (full covariance, zero
variance). Finally checks the quantity that actually matters -- the
mixture_weight each location ends up with.
"""
import os
import sys
import time

import numpy as np
from scipy.stats import multivariate_normal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import setup  # noqa: E402
setup(need_interface=False)
from stroopwafel import distributions, classes  # noqa: E402

EPS = np.finfo(float).eps
rng = np.random.default_rng(20260804)
failures = []


def original_pdf_sum(locations, gaussians):
    """Verbatim the pre-optimisation implementation."""
    samples = []
    for location in locations:
        samples.append(location.to_array())
    pdfs = np.zeros(len(locations))
    for index, gaussian in enumerate(gaussians):
        pdf = multivariate_normal.pdf(samples, gaussian.mean.to_array(),
                                      gaussian.cov, allow_singular=True)
        pdf = pdf * gaussian.biased_weight
        pdfs += pdf
    return pdfs


class Dim:
    def __init__(self, name):
        self.name = name


def make(n_loc, n_gauss, ndim, sigma_scale=1.0, cov_mode='diag', biased=False,
         far=False, seed=0):
    r = np.random.default_rng(seed)
    dims = [Dim(f'd{i}') for i in range(ndim)]
    locs = [classes.Location({d: v for d, v in zip(dims, r.normal(0, 1, ndim))}, {})
            for _ in range(n_loc)]
    if far:   # push locations far away so the pdf underflows to zero
        for loc in locs:
            for d in dims:
                loc.dimensions[d] += 400.0

    gaussians = []
    for g in range(n_gauss):
        mean = classes.Location({d: v for d, v in zip(dims, r.normal(0, 1, ndim))}, {})
        sig = np.abs(r.normal(0, 1, ndim)) * sigma_scale + 1e-3
        if cov_mode == 'diag':
            cov = np.diagflat(sig ** 2)
        elif cov_mode == 'full':                      # correlated -> must fall back
            A = r.normal(0, 1, (ndim, ndim))
            cov = A @ A.T + ndim * np.eye(ndim)
        elif cov_mode == 'zerovar':                   # singular -> must fall back
            cov = np.diagflat(sig ** 2)
            cov[0, 0] = 0.0
        gauss = distributions.Gaussian(mean, cov=cov)
        if biased:
            gauss.biased_weight = float(r.uniform(0.1, 3.0))
        gaussians.append(gauss)
    return locs, gaussians


def check(label, locs, gaussians, tol=1e-12):
    expected = original_pdf_sum(locs, gaussians)
    distributions.Gaussian.calculate_probability_of_locations_from_distribution(locs, gaussians)
    got = np.array([loc.properties['q_pdf'] for loc in locs])

    both_zero = (expected == 0) & (got == 0)
    with np.errstate(divide='ignore', invalid='ignore'):
        rel = np.where(both_zero, 0.0, np.abs(got - expected) / np.abs(expected))
    maxrel = float(np.nanmax(rel)) if len(rel) else 0.0
    ok = maxrel <= tol and np.array_equal(np.isfinite(expected), np.isfinite(got))
    print(f"  {'ok  ' if ok else 'FAIL'} {label:<46} max rel diff {maxrel:.3e}")
    if not ok:
        failures.append(label)
    return maxrel


print("\n--- fast path (diagonal covariance) ---")
for ndim in (1, 2, 3, 5):
    locs, g = make(2000, 30, ndim, seed=ndim)
    check(f"{ndim}-D, 2000 locations x 30 gaussians", locs, g)

locs, g = make(2000, 30, 3, sigma_scale=1e-3, seed=11)
check("very narrow gaussians (sigma ~1e-3)", locs, g)
locs, g = make(2000, 30, 3, sigma_scale=1e3, seed=12)
check("very wide gaussians (sigma ~1e3)", locs, g)
locs, g = make(1000, 20, 3, biased=True, seed=13)
check("biased_weight != 1", locs, g)
locs, g = make(500, 10, 3, far=True, seed=14)
check("locations far away (pdf underflows to 0)", locs, g)
locs, g = make(1, 5, 3, seed=15)
check("single location", locs, g)

print("\n--- must fall back to scipy (bitwise identical) ---")
locs, g = make(1000, 10, 3, cov_mode='full', seed=21)
check("full (correlated) covariance", locs, g, tol=0.0)
locs, g = make(1000, 10, 3, cov_mode='zerovar', seed=22)
check("singular covariance (zero variance)", locs, g, tol=0.0)

print("\n--- mixed: diagonal and full gaussians in one call ---")
locs, g1 = make(1000, 8, 3, cov_mode='diag', seed=31)
_, g2 = make(1000, 8, 3, cov_mode='full', seed=32)
check("half diagonal, half full", locs, g1 + g2)

print("\n--- the quantity that matters: mixture_weight ---")
locs, g = make(5000, 50, 3, seed=41)
prior = rng.uniform(0.1, 2.0, len(locs))
expected_q = original_pdf_sum(locs, g)
distributions.Gaussian.calculate_probability_of_locations_from_distribution(locs, g)
got_q = np.array([loc.properties['q_pdf'] for loc in locs])
f_expl, pi_norm, q_norm = 0.7, 1.05, 1.02
def weight(q):
    Q = f_expl * prior * pi_norm + (1 - f_expl) * (q / len(g)) * q_norm
    return prior * pi_norm / Q
w_old, w_new = weight(expected_q), weight(got_q)
maxrel = float(np.max(np.abs(w_new - w_old) / np.abs(w_old)))
ok = maxrel <= 1e-12
print(f"  {'ok  ' if ok else 'FAIL'} mixture_weight over 5000 locations         "
      f"max rel diff {maxrel:.3e}")
if not ok:
    failures.append('mixture_weight')

print("\n--- speed ---")
locs, g = make(20000, 200, 3, seed=99)
t = time.perf_counter(); original_pdf_sum(locs, g); t_old = time.perf_counter() - t
t = time.perf_counter()
distributions.Gaussian.calculate_probability_of_locations_from_distribution(locs, g)
t_new = time.perf_counter() - t
print(f"  20000 locations x 200 gaussians: {t_old:.2f} s -> {t_new:.2f} s  "
      f"({t_old / t_new:.1f}x)")

print(f"\n  {'PASS -- all results unchanged' if not failures else 'FAIL: ' + ', '.join(failures)}")
sys.exit(0 if not failures else 1)
