#!/usr/bin/env python3
"""
make_grid_file.py -- write a COMPAS grid file (BSE_grid.txt).

A COMPAS grid file has ONE LINE PER BINARY. Each line holds the COMPAS options
that differ between systems; anything not on the line falls back to the value in
compasConfig.yaml. For example:

    --random-seed 0 --metallicity 0.0142
    --random-seed 1 --metallicity 0.0031
    ...

The grid run (Submit_GridRun.py) hands each slurm array task a slice of this file
using --grid-start-line and --grid-lines-to-process, so the file is written once
and read by every task.

Usage:
    python3 make_grid_file.py -n 1000000 -o BSE_grid.txt
    python3 make_grid_file.py -n 1000 --sampling zams   # M1, q, a and Z sampled

Edit sample_line() below to change what varies between systems.
"""
import argparse
import math
import random


def sample_line_metallicity_only(i, rng):
    """One random metallicity per system, log-uniform in [1e-4, 0.03]."""
    Z = 10 ** rng.uniform(math.log10(1e-4), math.log10(0.03))
    return f"--random-seed {i} --metallicity {Z:.6g}"


def sample_line_zams(i, rng):
    """
    Full ZAMS draw: Kroupa IMF in M1, uniform mass ratio, log-uniform separation,
    log-uniform metallicity. A reasonable starting point for a population run.
    """
    # Kroupa (2001) IMF with alpha = -2.3 between 5 and 150 Msun, sampled by
    # inverting the CDF of a single power law.
    a, m_min, m_max = -2.3, 5.0, 150.0
    u = rng.random()
    p = a + 1.0
    m1 = (m_min ** p + u * (m_max ** p - m_min ** p)) ** (1.0 / p)

    q = rng.uniform(0.01, 1.0)
    m2 = m1 * q
    sep = 10 ** rng.uniform(math.log10(0.01), math.log10(1000.0))   # AU
    Z = 10 ** rng.uniform(math.log10(1e-4), math.log10(0.03))

    return (f"--random-seed {i} --initial-mass-1 {m1:.6g} --initial-mass-2 {m2:.6g} "
            f"--semi-major-axis {sep:.6g} --metallicity {Z:.6g} --eccentricity 0")


SAMPLERS = {
    'metallicity': sample_line_metallicity_only,
    'zams': sample_line_zams,
}


def write_grid_file(n, out_path, sampling='metallicity', seed=42):
    """
    n        : number of grid lines (= number of binaries)
    out_path : file to write
    sampling : which sample_line_* function to use (see SAMPLERS)
    seed     : seed of the *python* RNG that draws the grid, for reproducibility.
               This is NOT the COMPAS random seed -- that is the --random-seed on
               each line, which increments so every binary is distinguishable.
    """
    rng = random.Random(seed)
    sampler = SAMPLERS[sampling]

    with open(out_path, 'w') as f:
        for i in range(n):
            f.write(sampler(i, rng) + "\n")

    print(f"Wrote {n} grid lines to {out_path}  (sampling='{sampling}', rng seed={seed})")
    return out_path


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('-n', '--n-binaries', type=float, default=1e6,
                   help='number of grid lines to write (default 1e6)')
    p.add_argument('-o', '--output', default='BSE_grid.txt',
                   help='output filename (default BSE_grid.txt)')
    p.add_argument('--sampling', choices=sorted(SAMPLERS), default='metallicity',
                   help="what to vary per line (default 'metallicity')")
    p.add_argument('--seed', type=int, default=42,
                   help='seed for the grid-drawing RNG (default 42)')
    args = p.parse_args()

    write_grid_file(int(args.n_binaries), args.output, args.sampling, args.seed)


if __name__ == '__main__':
    main()
