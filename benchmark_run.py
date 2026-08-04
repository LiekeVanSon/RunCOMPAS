#!/usr/bin/env python3
"""
benchmark_run.py -- summarise what a finished run actually cost.

Reads slurm accounting and the output directory, and prints a row you can paste
straight into clusters/BENCHMARKS.md. The point is to make recording a
benchmark cheap enough that it actually happens.

    python3 benchmark_run.py <run_dir>
    python3 benchmark_run.py <run_dir> --row      # just the markdown row

It reports, per stage: wallclock, CPU time, peak memory, and how far the memory
you REQUESTED was from what was used -- over-requesting memory makes your jobs
queue longer for no benefit, and is the most common thing to get wrong.
"""
import argparse
import json
import os
import re
import subprocess
import sys


def parse_slurm_time(t):
    """slurm times: 'MM:SS.mmm', 'HH:MM:SS', 'D-HH:MM:SS'. -> seconds."""
    if not t or t in ('', 'INVALID'):
        return 0.0
    days = 0
    if '-' in t:
        d, _, t = t.partition('-')
        days = int(d)
    parts = [float(p) for p in t.split(':')]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, sec = parts
    return days * 86400 + h * 3600 + m * 60 + sec


def parse_mem(v):
    """slurm MaxRSS like '307M', '1.5G', '512K' -> MB."""
    if not v:
        return 0.0
    m = re.match(r'^([\d.]+)([KMGT]?)', v)
    if not m:
        return 0.0
    val = float(m.group(1))
    return val * {'': 1 / 1e6, 'K': 1 / 1024, 'M': 1, 'G': 1024, 'T': 1024 ** 2}[m.group(2)]


def sacct(job_id):
    """One dict per job step that matters (array tasks, not .extern)."""
    out = subprocess.run(
        ['sacct', '-j', str(job_id), '--units=M', '-np',
         '-o', 'JobID,JobName,State,ElapsedRaw,TotalCPU,MaxRSS,ReqMem,AllocCPUS,NodeList'],
        capture_output=True, text=True, timeout=120)
    rows = {}
    for line in out.stdout.strip().splitlines():
        f = line.split('|')
        if len(f) < 9:
            continue
        jid = f[0]
        base = jid.split('.')[0]
        rec = rows.setdefault(base, {'name': f[1], 'state': f[2], 'elapsed': 0.0,
                                     'cpu': 0.0, 'rss': 0.0, 'req': f[6],
                                     'cpus': f[7], 'nodes': f[8]})
        if '.' not in jid:                       # the task itself
            rec['state'] = f[2]
            rec['elapsed'] = float(f[3] or 0)
            rec['cpu'] = parse_slurm_time(f[4])
            rec['nodes'] = f[8]
        else:                                    # .batch step carries MaxRSS
            rec['rss'] = max(rec['rss'], parse_mem(f[5]))
    return rows


def du_mb(path):
    try:
        out = subprocess.run(['du', '-sm', path], capture_output=True, text=True, timeout=900)
        return float(out.stdout.split()[0])
    except Exception:
        return 0.0


def summarise(run_dir, want_row):
    ids_file = os.path.join(run_dir, 'job_ids.txt')
    if not os.path.isfile(ids_file):
        raise SystemExit(f"No job_ids.txt in {run_dir}")
    with open(ids_file) as f:
        jobs = json.load(f)

    stages = {}
    for stage, jid in jobs.items():
        rows = sacct(jid)
        if not rows:
            continue
        done = [r for r in rows.values() if r['state'].startswith('COMPLETED')]
        if not done:
            continue
        stages[stage] = {
            'ntasks': len(rows),
            'ndone': len(done),
            'wall_max': max(r['elapsed'] for r in done),
            'wall_mean': sum(r['elapsed'] for r in done) / len(done),
            'cpu_total': sum(r['cpu'] for r in done),
            'rss_max': max(r['rss'] for r in done),
            'req': done[0]['req'],
            'nodes': sorted({r['nodes'] for r in done if r['nodes']}),
        }

    # how many systems, and how much disk
    n_systems = 0
    merged_mb = 0.0
    main = os.path.join(run_dir, 'MainRun')
    for name in ('COMPAS_Output_wWeights.h5', 'COMPAS_Output.h5'):
        p = os.path.join(main, name)
        if os.path.isfile(p):
            merged_mb = os.path.getsize(p) / 1e6
            try:
                import h5py
                with h5py.File(p, 'r') as f:
                    n_systems = len(f['BSE_System_Parameters']['SEED'])
            except Exception:
                pass
            break
    total_mb = du_mb(run_dir)

    cpu_h = sum(s['cpu_total'] for s in stages.values()) / 3600
    wall_h = sum(s['wall_max'] for s in stages.values()) / 3600

    if want_row:
        mr = stages.get('MainRun', {})
        print(f"| {os.path.basename(run_dir)} | {n_systems or '?'} | "
              f"{mr.get('ntasks','?')} | {cpu_h:.1f} | {wall_h:.2f} | "
              f"{mr.get('rss_max',0):.0f} MB | {total_mb/1024:.2f} GB | "
              f"{merged_mb/1024:.2f} GB | |")
        return

    print(f"\n=== {run_dir} ===\n")
    if n_systems:
        print(f"systems        : {n_systems:,}")
    print(f"total CPU      : {cpu_h:.2f} CPU-hours")
    print(f"total wallclock: {wall_h:.2f} h  (sum of each stage's longest task)")
    print(f"disk, whole run: {total_mb/1024:.2f} GB")
    if merged_mb:
        print(f"     merged h5 : {merged_mb/1024:.2f} GB")
        if n_systems:
            print(f"     per system: {merged_mb*1e6/n_systems:,.0f} bytes")
    print()

    for stage, s in stages.items():
        print(f"{stage}:")
        print(f"    tasks      : {s['ndone']}/{s['ntasks']} completed"
              + (f"   nodes: {', '.join(s['nodes'][:4])}" if s['nodes'] else ""))
        print(f"    wallclock  : {s['wall_mean']:.0f} s mean, {s['wall_max']:.0f} s longest")
        print(f"    CPU        : {s['cpu_total']/3600:.2f} CPU-hours total")
        print(f"    peak memory: {s['rss_max']:.0f} MB used, {s['req']} requested", end='')
        req = parse_mem(s['req'].rstrip('nc'))
        if req and s['rss_max']:
            factor = req / s['rss_max']
            if factor > 3:
                print(f"   <- over-requested {factor:.0f}x, queues slower than it needs to")
            else:
                print()
        else:
            print()
        print()

    print("Paste into clusters/BENCHMARKS.md with:  --row")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run_dir')
    ap.add_argument('--row', action='store_true',
                    help='print only the markdown table row')
    args = ap.parse_args()
    summarise(os.path.abspath(args.run_dir), args.row)


if __name__ == '__main__':
    main()
