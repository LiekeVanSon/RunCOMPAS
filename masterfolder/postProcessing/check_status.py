#!/usr/bin/env python3
"""
check_status.py -- did my run actually work?

Run this any time, from anywhere:

    python3 check_status.py /path/to/my/run

It reports, for one run directory:
  * the slurm state of every job in the chain (from job_ids.txt, via sacct)
  * how many batch_* directories exist and how many hold a readable h5 file
  * which batches are missing or empty, so you know exactly what to resubmit
  * whether the merged / weighted output files exist

Note this does NOT block. The job chain is already wired with
--dependency=afterok, so slurm holds post-processing until the run succeeds.
This script is for when you want to know what happened.

See ../../docs/06_troubleshooting.md
"""
import argparse
import glob
import json
import os
import subprocess
import sys

GREEN, RED, YELLOW, DIM, RESET = '\033[92m', '\033[91m', '\033[93m', '\033[2m', '\033[0m'
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = RESET = ''


def sacct_state(job_id):
    """Return the slurm state of a job id, or None if sacct knows nothing."""
    try:
        out = subprocess.run(
            ['sacct', '-j', str(job_id), '--format=JobID,State,Elapsed,MaxRSS',
             '--parsable2', '--noheader'],
            capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None

    rows = []
    for line in out.stdout.strip().splitlines():
        parts = line.split('|')
        if len(parts) >= 2 and '.' not in parts[0]:   # skip .batch/.extern steps
            rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ''))
    return rows or None


def colour_state(state):
    if state.startswith('COMPLETED'):
        return f"{GREEN}{state}{RESET}"
    if state.startswith(('FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY', 'NODE_FAIL')):
        return f"{RED}{state}{RESET}"
    return f"{YELLOW}{state}{RESET}"


def check_jobs(run_dir):
    ids_file = os.path.join(run_dir, 'job_ids.txt')
    if not os.path.isfile(ids_file):
        print(f"  {DIM}no job_ids.txt -- was this run submitted from Submit_*.py?{RESET}")
        return

    with open(ids_file) as f:
        jobs = json.load(f)

    for stage, job_id in jobs.items():
        rows = sacct_state(job_id)
        if rows is None:
            print(f"  {stage:<18} {job_id:<12} {DIM}(sacct has no record){RESET}")
            continue
        # Summarise array jobs rather than printing hundreds of lines
        states = {}
        for _, state, _ in rows:
            states[state] = states.get(state, 0) + 1
        summary = '  '.join(f"{colour_state(s)} x{n}" if n > 1 else colour_state(s)
                            for s, n in sorted(states.items()))
        print(f"  {stage:<18} {job_id:<12} {summary}")


def check_batches(run_dir):
    main_run = os.path.join(run_dir, 'MainRun')
    if not os.path.isdir(main_run):
        print(f"  {RED}no MainRun/ directory{RESET}")
        return

    batches = sorted(glob.glob(os.path.join(main_run, 'batch_*')),
                     key=lambda p: int(p.rsplit('_', 1)[-1]) if p.rsplit('_', 1)[-1].isdigit() else -1)
    if not batches:
        print(f"  {RED}no batch_* directories found{RESET}")
        return

    good, empty = [], []
    for b in batches:
        h5s = glob.glob(os.path.join(b, '*.h5'))
        h5s = [h for h in h5s if os.path.getsize(h) > 0]
        (good if h5s else empty).append(os.path.basename(b))

    print(f"  {len(batches)} batch directories, "
          f"{GREEN}{len(good)} with output{RESET}, "
          f"{(RED if empty else DIM)}{len(empty)} empty{RESET}")

    if empty:
        # Report as a slurm array spec so it can be resubmitted directly
        idx = sorted(int(n.rsplit('_', 1)[-1]) for n in empty if n.rsplit('_', 1)[-1].isdigit())
        print(f"  {RED}empty:{RESET} {','.join(map(str, idx))}")
        print(f"  {DIM}resubmit with: sbatch --array={','.join(map(str, idx))} "
              f"{os.path.join(main_run, 'COMPAS_grid.sbatch')}{RESET}")


def check_outputs(run_dir):
    for rel in ('MainRun/COMPAS_Output.h5', 'MainRun/COMPAS_Output_wWeights.h5',
                'MainRun/samples.csv'):
        path = os.path.join(run_dir, rel)
        if os.path.isfile(path):
            size = os.path.getsize(path) / 1e9
            print(f"  {GREEN}present{RESET}  {rel}  ({size:.2f} GB)")
        else:
            print(f"  {DIM}absent   {rel}{RESET}")


def check_seeds(run_dir):
    """
    Sanity-check the merged file: every system should appear exactly once, and
    the total should match the sum over the batches.

    A merged file containing each SEED twice means post-processing ran on top of
    a previous merge (h5copy scans MainRun/, and the merged file lives there).
    It is silent and it doubles every population statistic, so it is worth
    catching here rather than in a paper.
    """
    try:
        import h5py
        import numpy as np
    except ImportError:
        return

    merged = None
    for name in ('COMPAS_Output_wWeights.h5', 'COMPAS_Output.h5'):
        p = os.path.join(run_dir, 'MainRun', name)
        if os.path.isfile(p):
            merged = p
            break
    if merged is None:
        return

    try:
        with h5py.File(merged, 'r') as f:
            if 'BSE_System_Parameters' not in f:
                return
            seeds = f['BSE_System_Parameters']['SEED'][:]
    except Exception as err:
        print(f"  {RED}could not read {os.path.basename(merged)}: {err}{RESET}")
        return

    n_rows = len(seeds)
    n_uniq = len(np.unique(seeds))

    batch_total = 0
    for b in glob.glob(os.path.join(run_dir, 'MainRun', 'batch_*', '*.h5')):
        try:
            with h5py.File(b, 'r') as bf:
                if 'BSE_System_Parameters' in bf:
                    batch_total += len(bf['BSE_System_Parameters']['SEED'])
        except Exception:
            pass

    if n_rows == n_uniq:
        print(f"  {GREEN}ok{RESET}       {n_rows} systems, all SEEDs unique")
    else:
        rep = n_rows // n_uniq if n_uniq else 0
        print(f"  {RED}CORRUPT{RESET}  {n_rows} rows but only {n_uniq} unique SEEDs "
              f"(each appears ~{rep}x)")
        print(f"  {RED}         post-processing merged a previous merged file.{RESET}")
        print(f"  {DIM}         fix: rm {merged} and resubmit "
              f"{os.path.join(run_dir, 'postProcessing', 'COMPAS_PP.sbatch')}{RESET}")

    if batch_total and batch_total != n_uniq:
        print(f"  {YELLOW}note{RESET}     batches hold {batch_total} systems but the "
              f"merged file has {n_uniq} unique -- some batches may be missing")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('run_dir', nargs='?', default='..',
                   help='the run output directory (default: parent of cwd)')
    args = p.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        raise SystemExit(f"No such directory: {run_dir}")

    print(f"\n=== {run_dir} ===\n")
    print("SLURM jobs:")
    check_jobs(run_dir)
    print("\nBatches:")
    check_batches(run_dir)
    print("\nOutput files:")
    check_outputs(run_dir)
    print("\nData integrity:")
    check_seeds(run_dir)
    print()


if __name__ == '__main__':
    main()
