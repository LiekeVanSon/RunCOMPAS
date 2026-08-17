#!/usr/bin/env python3
"""
Profile the pure-stroopwafel work, with no COMPAS in the loop.

Everything here is what runs on the ONE core between COMPAS batches, which is
what we are trying to explain. Sizes are set to be representative of a large
production run and are scaled down by --scale for quick iteration.
"""
import argparse
import cProfile
import io
import os
import pstats
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import setup  # noqa: E402
setup(need_interface=True)

from stroopwafel import classes, distributions, prior, sampler, constants  # noqa: E402
import stroopwafel_interface as SI  # noqa: E402


def timed(label, fn, *a, **kw):
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    dt = time.perf_counter() - t0
    print(f"  {label:<52} {dt:8.2f} s")
    return dt, out


def make_hits(dimensions, n):
    """n plausible hit locations, as adapt() would produce."""
    init = distributions.InitialDistribution(dimensions)
    locs, mask = init.run_sampler(int(n * 3))
    locs = np.asarray(locs)[mask][:n]
    return list(locs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-hits', type=int, default=200,
                    help='number of hits = number of adapted Gaussians')
    ap.add_argument('--n-samples', type=int, default=20000,
                    help='number of samples for the postprocess weight step')
    ap.add_argument('--profile', action='store_true', help='cProfile the worst step')
    args = ap.parse_args()

    dimensions = SI.create_dimensions()
    print(f"\ndimensions : {[d.name for d in dimensions]}")
    print(f"n_hits     : {args.n_hits}   (= number of Gaussians)")
    print(f"n_samples  : {args.n_samples}")
    print(f"TOTAL_REJECTION_SAMPLES = {constants.TOTAL_REJECTION_SAMPLES:g}, "
          f"REJECTION_SAMPLES_PER_BATCH = {constants.REJECTION_SAMPLES_PER_BATCH:g}\n")

    results = {}

    # ---- 1. prior rejection rate: once, at the start of explore -------------
    init = distributions.InitialDistribution(dimensions)
    results['prior rejection rate (1x, 1e6 samples)'] = timed(
        "InitialDistribution.calculate_rejection_rate", init.calculate_rejection_rate,
        SI.update_properties, SI.rejected_systems, dimensions)[0]

    # ---- 2. build the Gaussians (adapt) ------------------------------------
    hits = make_hits(dimensions, args.n_hits)
    [h.revert_variables_to_original_scales() for h in hits]
    [h.transform_variables_to_new_scales() for h in hits]
    average_density_one_dim = 1.0 / np.power(len(hits), 1.0 / len(dimensions))
    dt, gaussians = timed("Gaussian.draw_distributions",
                          distributions.Gaussian.draw_distributions,
                          hits, average_density_one_dim)
    results['draw_distributions'] = dt

    # ---- 3. rejection rate of the adapted distributions --------------------
    # loops over EVERY Gaussian, drawing REJECTION_SAMPLES_PER_BATCH each
    results['distribution rejection rate (per Gaussian!)'] = timed(
        "Gaussian.calculate_rejection_rate", distributions.Gaussian.calculate_rejection_rate,
        gaussians, SI.update_properties, SI.rejected_systems, dimensions)[0]

    # ---- 4. mixture weights (postprocess) ----------------------------------
    samples = make_hits(dimensions, args.n_samples)
    [s.transform_variables_to_new_scales() for s in samples]
    results['mixture weights: pdf over all locations x Gaussians'] = timed(
        "calculate_probability_of_locations_from_distribution",
        distributions.Gaussian.calculate_probability_of_locations_from_distribution,
        samples, gaussians)[0]

    # ---- 5. the per-location python loops ----------------------------------
    results['revert_variables_to_original_scales (n_samples)'] = timed(
        "revert_variables_to_original_scales",
        lambda: [s.revert_variables_to_original_scales() for s in samples])[0]
    results['calculate_prior_probability (n_samples)'] = timed(
        "calculate_prior_probability",
        lambda: [s.calculate_prior_probability() for s in samples])[0]

    total = sum(results.values())
    print(f"\n{'':<54}{'seconds':>8}  {'share':>7}")
    for k, v in sorted(results.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<52} {v:8.2f}  {100*v/total:6.1f}%")
    print(f"  {'TOTAL':<52} {total:8.2f}")

    if args.profile:
        worst = max(results, key=results.get)
        print(f"\n=== cProfile of the worst step: {worst} ===")
        pr = cProfile.Profile()
        pr.enable()
        if 'distribution rejection' in worst:
            distributions.Gaussian.calculate_rejection_rate(
                gaussians, SI.update_properties, SI.rejected_systems, dimensions)
        elif 'mixture weights' in worst:
            [s.transform_variables_to_new_scales() for s in samples]
            distributions.Gaussian.calculate_probability_of_locations_from_distribution(
                samples, gaussians)
        else:
            init.calculate_rejection_rate(SI.update_properties, SI.rejected_systems, dimensions)
        pr.disable()
        st = pstats.Stats(pr, stream=io.StringIO())
        st.sort_stats('cumulative')
        buf = io.StringIO()
        pstats.Stats(pr, stream=buf).sort_stats('tottime').print_stats(14)
        print('\n'.join(buf.getvalue().splitlines()[4:26]))


if __name__ == '__main__':
    main()
