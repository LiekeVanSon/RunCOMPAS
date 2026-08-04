#!/usr/bin/env python3
"""
make_grid_file.py -- write a COMPAS grid file (one line of options per binary).

    ####################################################################
    #  BEFORE YOU USE THIS: you probably do not need a grid file.      #
    ####################################################################

COMPAS already samples the ZAMS parameters for you, and can build parameter
grids on the command line. Reach for a grid file only when neither of those
can express what you want. See ../../docs/02_grid_runs.md for the full story;
the short version:

  1. Sampling a distribution?  Use the built-in samplers.

         --initial-mass-function KROUPA
         --initial-mass-function-min 5 --initial-mass-function-max 150
         --metallicity-distribution LOGUNIFORM --metallicity-min 1e-4 --metallicity-max 0.03
         --semi-major-axis-distribution FLATINLOG
         --mass-ratio-distribution FLAT
         --eccentricity-distribution ZERO

     Put those in compasConfig.yaml and set N with --number-of-systems.
     This is faster, better tested, and self-documenting in Run_Details.

  2. Scanning a parameter grid?  Use ranges and sets, no file needed.

         --metallicity r[0.0001,10,0.0013]      # 10 values, step 0.0013
         --common-envelope-alpha s[0.1,0.5,1.0] # 3 explicit values

     These multiply: the above two together evolve 30 systems.

  3. A grid file is the right tool when you need EXPLICIT per-system values
     that no distribution describes:

       * re-running specific systems from a previous run  (--from-h5 below)
       * initial conditions from an observed sample or another code
       * correlated parameters COMPAS cannot express, e.g. a metallicity
         dependent IMF, or a mass-dependent separation distribution

USAGE

    # re-run systems of interest from a previous COMPAS output
    python3 make_grid_file.py --from-h5 COMPAS_Output.h5 -o BSE_grid.txt
    python3 make_grid_file.py --from-h5 COMPAS_Output.h5 --seeds 12,57,993 -o rerun.txt
    python3 make_grid_file.py --from-h5 COMPAS_Output.h5 --dco-only -o dcos.txt

    # write a grid from your own sampling function (edit custom_line below)
    python3 make_grid_file.py --custom -n 10000 -o BSE_grid.txt

A grid file has one line per binary. Options on a line override both
compasConfig.yaml and the command line for that system; anything absent falls
back to them.

    --random-seed 0 --initial-mass-1 10.78 --initial-mass-2 0.37 --metallicity 0.0004
"""
import argparse
import math
import random
import sys


# ---------------------------------------------------------------------------
# Re-running systems from a previous run
# ---------------------------------------------------------------------------
# COMPAS h5 column -> the option that reproduces it. Kick random numbers are
# included so that a re-run reproduces the same supernovae; drop them if you
# want to re-draw kicks.
GRID_COLUMNS = {
    'SEED': '--random-seed',
    'Mass@ZAMS(1)': '--initial-mass-1',
    'Mass@ZAMS(2)': '--initial-mass-2',
    'SemiMajorAxis@ZAMS': '--semi-major-axis',
    'Metallicity@ZAMS(1)': '--metallicity',
    'Eccentricity@ZAMS': '--eccentricity',
    'SN_Kick_Magnitude_Random_Number(1)': '--kick-magnitude-random-1',
    'SN_Kick_Magnitude_Random_Number(2)': '--kick-magnitude-random-2',
}


def from_h5(h5_path, out_path, seeds=None, dco_only=False, columns=None):
    """
    Write a grid file reproducing systems from a previous COMPAS run.

    This is the main honest use of a grid file: you found something
    interesting, and now you want to re-run exactly those systems -- with
    detailed output on, or with different physics, without evolving the whole
    population again.

    seeds     : only these SEEDs (list of int). None = all.
    dco_only  : only systems that appear in BSE_Double_Compact_Objects.
    """
    import h5py
    import numpy as np

    columns = columns or GRID_COLUMNS

    with h5py.File(h5_path, 'r') as f:
        if 'BSE_System_Parameters' not in f:
            raise SystemExit(f"{h5_path} has no BSE_System_Parameters group.")
        sp = f['BSE_System_Parameters']

        available = {c: o for c, o in columns.items() if c in sp}
        missing = set(columns) - set(available)
        if missing:
            print(f"note: not in this file, skipping: {', '.join(sorted(missing))}",
                  file=sys.stderr)
        if 'SEED' not in available:
            raise SystemExit("No SEED column -- cannot build a grid file.")

        data = {c: sp[c][:] for c in available}
        mask = np.ones(len(data['SEED']), dtype=bool)

        if dco_only:
            if 'BSE_Double_Compact_Objects' not in f:
                raise SystemExit("--dco-only given but the file has no "
                                 "BSE_Double_Compact_Objects group.")
            mask &= np.isin(data['SEED'], f['BSE_Double_Compact_Objects']['SEED'][:])

        if seeds is not None:
            wanted = np.asarray(sorted(set(seeds)))
            found = np.isin(wanted, data['SEED'])
            if not found.all():
                raise SystemExit("These SEEDs are not in the file: "
                                 f"{', '.join(map(str, wanted[~found]))}")
            mask &= np.isin(data['SEED'], wanted)

    n = int(mask.sum())
    if n == 0:
        raise SystemExit("No systems selected -- nothing to write.")

    with open(out_path, 'w') as out:
        for i in np.flatnonzero(mask):
            parts = []
            for col, opt in available.items():
                val = data[col][i]
                # SEED must stay an integer; everything else is a float
                parts.append(f"{opt} {int(val)}" if col == 'SEED' else f"{opt} {val:.10g}")
            out.write(' '.join(parts) + '\n')

    print(f"Wrote {n} systems to {out_path}")
    print(f"  reproducing: {', '.join(available.values())}")
    return out_path


# ---------------------------------------------------------------------------
# Custom sampling  --  EDIT ME
# ---------------------------------------------------------------------------
def custom_line(i, rng):
    """
    One grid line per system, for sampling COMPAS cannot express itself.

    The example below draws a METALLICITY-DEPENDENT IMF upper limit -- the kind
    of correlation between parameters that has no built-in option. If your
    sampling does NOT need that kind of coupling, delete this and use the
    built-in distributions instead (see the header of this file).
    """
    # log-uniform metallicity, as --metallicity-distribution LOGUNIFORM would do
    Z = 10 ** rng.uniform(math.log10(1e-4), math.log10(0.03))

    # ... but with an upper mass limit that depends on it, which is the part
    # COMPAS has no option for
    m_max = 150.0 if Z < 0.005 else 100.0

    alpha, m_min = -2.3, 5.0
    u = rng.random()
    p = alpha + 1.0
    m1 = (m_min ** p + u * (m_max ** p - m_min ** p)) ** (1.0 / p)

    q = rng.uniform(0.01, 1.0)
    sep = 10 ** rng.uniform(math.log10(0.01), math.log10(1000.0))

    return (f"--random-seed {i} --initial-mass-1 {m1:.6g} "
            f"--initial-mass-2 {m1 * q:.6g} --semi-major-axis {sep:.6g} "
            f"--metallicity {Z:.6g} --eccentricity 0")


def write_custom(n, out_path, seed=42):
    """
    n    : number of grid lines (= number of binaries)
    seed : seed of the PYTHON rng that draws the grid, for reproducibility.
           Not the COMPAS seed -- that is --random-seed on each line.
    """
    rng = random.Random(seed)
    with open(out_path, 'w') as f:
        for i in range(n):
            f.write(custom_line(i, rng) + '\n')
    print(f"Wrote {n} grid lines to {out_path}  (custom sampler, rng seed={seed})")
    return out_path


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('-o', '--output', default='BSE_grid.txt', help='output filename')

    mode = p.add_argument_group('mode (pick one)')
    mode.add_argument('--from-h5', metavar='FILE',
                      help='reproduce systems from a previous COMPAS output')
    mode.add_argument('--custom', action='store_true',
                      help='use custom_line() in this file')

    sel = p.add_argument_group('--from-h5 options')
    sel.add_argument('--seeds', help='comma-separated SEEDs to keep (default: all)')
    sel.add_argument('--dco-only', action='store_true',
                     help='only systems that formed a double compact object')

    cus = p.add_argument_group('--custom options')
    cus.add_argument('-n', '--n-binaries', type=float, default=1e4,
                     help='number of grid lines (default 1e4)')
    cus.add_argument('--seed', type=int, default=42, help='rng seed (default 42)')

    args = p.parse_args()

    if args.from_h5 and args.custom:
        raise SystemExit("Pick one of --from-h5 or --custom, not both.")
    if args.from_h5:
        seeds = [int(s) for s in args.seeds.split(',')] if args.seeds else None
        from_h5(args.from_h5, args.output, seeds, args.dco_only)
    elif args.custom:
        write_custom(int(args.n_binaries), args.output, args.seed)
    else:
        p.print_help()
        raise SystemExit(
            "\nNo mode given. If you just want to sample a distribution, you do not "
            "need\na grid file at all -- use COMPAS's built-in samplers. See the "
            "header above\nand ../../docs/02_grid_runs.md")


if __name__ == '__main__':
    main()
