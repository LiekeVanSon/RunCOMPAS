#!/usr/bin/env python3
"""
=============================================================================
 Submit_GridRun.py  --  MODE 1: a large COMPAS run, split over a SLURM array
=============================================================================
 basically run COMPAS the normal command-line way, (but big)
 It evolves N systems by splitting them into N_BATCHES equal slices and handing
 one slice to each task of a single slurm array job.

 It creates:

     <OUTPUT_ROOT>/<RUN_NAME>/
         MainRun/             <- COMPAS output: batch_0/, batch_1/, ...
         postProcessing/      <- merge scripts, run after MainRun succeeds
         CosmicIntegration/   <- rate calculation, run after postProcessing
         logs/                <- slurm .out/.err for every job
         job_ids.txt          <- what was submitted, for check_status.py

 and submits the chain   MainRun --> postProcessing --> CosmicIntegration,
 each stage waiting on the previous one via --dependency=afterok.

 USAGE
     python3 Submit_GridRun.py --dry-run    # write everything, submit nothing
     python3 Submit_GridRun.py              # for real

 Edit the SETTINGS block below. Physics settings live in
 masterfolder/compasConfig.yaml, not here.

 See docs/02_grid_runs.md
=============================================================================
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys

import yaml

REPO = os.path.dirname(os.path.abspath(__file__))

# ===========================================================================
#                                                                           #
#                          S E T T I N G S                                  #
#                        (this is the part you edit)                        #
#                                                                           #
# ===========================================================================

# --- what to call this run, and where it goes ------------------------------
RUN_NAME = "small_test"
# Leave as None to use output_root from your cluster profile.
OUTPUT_ROOT = None

# --- which cluster profile to use ------------------------------------------
# None = auto-detect from hostname against clusters/*.yaml
CLUSTER = 'coma'

# --- how big is the run, and how is it chopped up? -------------------------
N_BINARIES = int(200)     # total systems to evolve
N_BATCHES = 4           # = number of slurm array tasks
MAX_CONCURRENT = 20       # cap on tasks running at once (be kind to the queue)
SEED_BASE = 0             # COMPAS random seed of the very first system

# --- optional grid file ----------------------------------------------------
# A grid file gives you one line of COMPAS options per binary (see
# masterfolder/MainRun/make_grid_file.py).
#   GRID_FILE = None      -> no grid; each task just runs --number-of-systems N
#   GRID_FILE = "BSE_grid.txt"  -> use this file, split by line
# If the file does not exist yet it is generated with MAKE_GRID_SAMPLING.
GRID_FILE = None
MAKE_GRID_SAMPLING = "metallicity"    # "metallicity" or "zams"

# --- resources per array task ----------------------------------------------
# One task evolves N_BINARIES/N_BATCHES systems. Time it on a small run first:
# submit with N_BINARIES=1000, N_BATCHES=1 and look at the log.
MAINRUN_WALLTIME = "0-00:30:00"
MAINRUN_MEMORY = "4G"

# --- which stages to submit -------------------------------------------------
RUN_MAINRUN = True
RUN_POSTPROCESSING = True
RUN_COSMIC_INTEGRATION = False

PP_WALLTIME = "0-03:00:00"
PP_MEMORY = "16G"

CI_WALLTIME = "0-02:00:00"
CI_MEMORY = "16G"
# Arguments to FastCosmicIntegration.py. Defaults are the van Son et al. (2022)
# best-fit metallicity-specific star formation history.
CI_ARGS = ("--weight mixture_weight --keepRLOF_postCE --zstep 0.01 --sens O3 "
           "--m1min 5.0 --dco_type all --maxzdet 8 "
           "--mu0 0.025 --muz -0.049 --sigma0 1.129 --sigmaz 0.048 --alpha -1.79 "
           "--aSF 0.017 --bSF 1.487 --cSF 4.442 --dSF 5.886")


# ===========================================================================
#                                                                           #
#         You should not need to change anything below this line.           #
#                                                                           #
# ===========================================================================

def load_cluster(name=None):
    """Read clusters/<name>.yaml, or auto-detect one from the hostname."""
    cdir = os.path.join(REPO, 'clusters')
    if name:
        path = os.path.join(cdir, f'{name}.yaml')
        if not os.path.isfile(path):
            raise SystemExit(f"No cluster profile {path}\n"
                             f"Available: {', '.join(_available(cdir))}")
    else:
        host = socket.gethostname()
        path = None
        for candidate in sorted(os.listdir(cdir)):
            if not candidate.endswith('.yaml') or candidate == 'TEMPLATE.yaml':
                continue
            with open(os.path.join(cdir, candidate)) as f:
                prof = yaml.safe_load(f) or {}
            if any(m in host for m in (prof.get('hostname_match') or [])):
                path = os.path.join(cdir, candidate)
                break
        if path is None:
            raise SystemExit(
                f"Could not match hostname {host!r} to a cluster profile.\n"
                f"Available: {', '.join(_available(cdir))}\n"
                f"Either set CLUSTER = \"<name>\" at the top of this script, or add "
                f"your hostname to hostname_match in the profile.\n"
                f"To add a new machine: cp clusters/TEMPLATE.yaml clusters/<name>.yaml")

    with open(path) as f:
        profile = yaml.safe_load(f)
    profile['_path'] = path
    print(f"cluster profile : {path}")
    return profile


def _available(cdir):
    return [f[:-5] for f in sorted(os.listdir(cdir))
            if f.endswith('.yaml') and f != 'TEMPLATE.yaml']


def render(template_path, out_path, subs):
    """Fill every @@PLACEHOLDER@@ in a template and write the result."""
    with open(template_path) as f:
        text = f.read()

    for key, val in subs.items():
        text = text.replace(f'@@{key}@@', str(val))

    left = set(re.findall(r'@@([A-Z_]+)@@', text))
    if left:
        raise SystemExit(f"Unfilled placeholders {sorted(left)} in {out_path}. "
                         f"This is a bug in the driver script.")

    # Drop #SBATCH lines whose value came out empty (e.g. no mail_user set)
    text = '\n'.join(l for l in text.split('\n')
                     if not re.match(r'^#SBATCH\s+--[\w-]+=\s*$', l))

    with open(out_path, 'w') as f:
        f.write(text)
    os.chmod(out_path, 0o755)
    return out_path


def build_setup(profile):
    """
    The shell block pasted at the top of every job script: activate the venv
    (if the profile names one), then whatever else the profile asks for.
    """
    lines = []
    venv = (profile.get('venv') or '').strip()
    if venv:
        venv = os.path.expanduser(venv)
        activate = os.path.join(venv, 'bin', 'activate')
        if not os.path.isfile(activate):
            raise SystemExit(
                f"The cluster profile points at a venv that does not exist:\n"
                f"    {venv}\n"
                f"(expected an activate script at {activate})\n\n"
                f"Create it with:\n"
                f"    python3 -m venv {venv}\n"
                f"    source {venv}/bin/activate\n"
                f"    pip install -r {os.path.join(REPO, 'requirements.txt')}\n\n"
                f"Or clear `venv:` in {profile['_path']} to use the system python.")
        lines.append(f'source "{activate}"')
    lines.append((profile.get('setup') or '').rstrip())
    return '\n'.join(l for l in lines if l)


def sbatch_common(profile, run_dir, cluster_name):
    """Placeholder values shared by every job script."""
    extra = list(profile.get('extra_sbatch') or [])
    if profile.get('account'):
        extra.insert(0, f"--account={profile['account']}")
    return {
        'CLUSTER': cluster_name,
        'PARTITION': profile['partition'],
        'MAIL_USER': profile.get('mail_user') or '',
        'MAIL_TYPE': profile.get('mail_type') or 'NONE',
        'EXTRA_SBATCH': '\n'.join(f'#SBATCH {e}' for e in extra),
        'SETUP': build_setup(profile),
        'PYTHON': profile.get('python') or 'python3',
        'COMPAS_ROOT': profile['compas_root'],
        'SCRATCH_ROOT': (profile.get('scratch_root') or '').strip(),
        'DATA_DIR': run_dir,
        'LOG_DIR': os.path.join(run_dir, 'logs'),
    }


def submit(script, dependency=None, dry_run=False):
    """sbatch one script, optionally after another job. Returns the job id."""
    cmd = ['sbatch', '--parsable']
    if dependency:
        cmd += ['--kill-on-invalid-dep=yes', f'--dependency=afterok:{dependency}']
    cmd.append(script)

    if dry_run:
        print(f"  [dry-run] {' '.join(cmd)}")
        return None

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"sbatch failed for {script}:\n{proc.stderr}")
    job_id = proc.stdout.strip()
    print(f"  submitted {os.path.basename(script)} -> job {job_id}"
          + (f" (after {dependency})" if dependency else ""))
    return job_id


def check_walltime(requested, maximum, label):
    """Warn if a walltime exceeds what the partition allows."""
    def secs(t):
        days, _, rest = t.partition('-')
        if not rest:
            days, rest = 0, t
        h, m, s = (list(map(int, rest.split(':'))) + [0, 0])[:3]
        return int(days) * 86400 + h * 3600 + m * 60 + s
    try:
        if secs(requested) > secs(maximum):
            print(f"  WARNING: {label} walltime {requested} exceeds the partition "
                  f"limit {maximum}; slurm will reject this job.")
    except (ValueError, AttributeError):
        pass


def stage_files(run_dir, grid_file):
    """Create the run directory tree and copy masterfolder into it."""
    master = os.path.join(REPO, 'masterfolder')
    for sub in ('MainRun', 'postProcessing', 'CosmicIntegration', 'logs'):
        os.makedirs(os.path.join(run_dir, sub), exist_ok=True)

    for sub in ('MainRun', 'postProcessing', 'CosmicIntegration'):
        shutil.copytree(os.path.join(master, sub), os.path.join(run_dir, sub),
                        dirs_exist_ok=True)
    shutil.copy(os.path.join(master, 'compasConfig.yaml'),
                os.path.join(run_dir, 'MainRun', 'compasConfig.yaml'))
    # keep a copy of the driver next to the data, so a run is reproducible
    shutil.copy(os.path.abspath(__file__), os.path.join(run_dir, os.path.basename(__file__)))

    if grid_file:
        dest = os.path.join(run_dir, 'MainRun', os.path.basename(grid_file))
        if os.path.abspath(grid_file) != os.path.abspath(dest):
            shutil.copy(grid_file, dest)
        return dest
    return None


def count_lines(path):
    with open(path) as f:
        return sum(1 for line in f if line.strip())


def main():
    ap = argparse.ArgumentParser(description="Submit a COMPAS grid run as a slurm array.")
    ap.add_argument('--dry-run', action='store_true',
                    help='create the run directory and job scripts, but do not submit')
    ap.add_argument('--force', action='store_true',
                    help='reuse an existing run directory instead of refusing')
    ap.add_argument('--cluster', default=CLUSTER, help='override the cluster profile')
    ap.add_argument('--run-name', default=RUN_NAME, help='override RUN_NAME')
    ap.add_argument('--output-root', default=None, help='override OUTPUT_ROOT')
    args = ap.parse_args()

    profile = load_cluster(args.cluster)
    cluster_name = profile['name']
    output_root = args.output_root or OUTPUT_ROOT or profile['output_root']
    run_dir = os.path.join(output_root, args.run_name)

    print(f"run directory   : {run_dir}")

    if os.path.exists(run_dir) and not args.force:
        raise SystemExit(
            f"\n{run_dir} already exists.\n"
            "Pick a new RUN_NAME, delete it, or pass --force to reuse it.\n"
            "(Refusing by default so a finished run is never silently overwritten.)")

    # ---- grid file ---------------------------------------------------------
    grid_src = None
    if GRID_FILE:
        grid_src = GRID_FILE if os.path.isabs(GRID_FILE) else os.path.join(REPO, GRID_FILE)
        if not os.path.isfile(grid_src):
            print(f"grid file       : {grid_src} does not exist -- generating it")
            sys.path.insert(0, os.path.join(REPO, 'masterfolder', 'MainRun'))
            from make_grid_file import write_grid_file
            os.makedirs(os.path.dirname(grid_src) or '.', exist_ok=True)
            write_grid_file(N_BINARIES, grid_src, MAKE_GRID_SAMPLING)

    # ---- lay out the run directory -----------------------------------------
    grid_dest = stage_files(run_dir, grid_src)

    # ---- work out the array geometry ---------------------------------------
    n_total = count_lines(grid_dest) if grid_dest else N_BINARIES
    if grid_dest and n_total != N_BINARIES:
        print(f"note            : grid file has {n_total} lines, using that "
              f"instead of N_BINARIES={N_BINARIES}")

    batch_size = -(-n_total // N_BATCHES)          # ceiling division
    n_tasks = -(-n_total // batch_size)            # may be < N_BATCHES if it divides unevenly

    print(f"systems         : {n_total}")
    print(f"array           : {n_tasks} tasks x {batch_size} systems "
          f"(max {MAX_CONCURRENT} at once)")
    if n_tasks * batch_size != n_total:
        print(f"                  last task takes the remainder "
              f"({n_total - (n_tasks - 1) * batch_size})")

    check_walltime(MAINRUN_WALLTIME, profile.get('max_walltime', '99-00:00:00'), 'MainRun')

    # ---- render the job scripts --------------------------------------------
    slurms = os.path.join(REPO, 'masterfolder', 'slurms')
    common = sbatch_common(profile, run_dir, cluster_name)

    if grid_dest:
        slice_args = (f'--grid {grid_dest} '
                      f'--grid-start-line ${{START}} --grid-lines-to-process ${{N_THIS}}')
    else:
        slice_args = '--number-of-systems ${N_THIS}'

    main_script = render(
        os.path.join(slurms, 'COMPAS_grid.sbatch'),
        os.path.join(run_dir, 'MainRun', 'COMPAS_grid.sbatch'),
        {**common,
         'JOB_NAME': f'grid_{args.run_name}'[:60],
         'ARRAY_SPEC': f'0-{n_tasks - 1}%{MAX_CONCURRENT}',
         'WALLTIME': MAINRUN_WALLTIME,
         'MEMORY': MAINRUN_MEMORY,
         'BATCH_SIZE': batch_size,
         'N_TOTAL': n_total,
         'SEED_BASE': SEED_BASE,
         'SLICE_ARGS': slice_args})

    pp_script = render(
        os.path.join(slurms, 'COMPAS_PP.sbatch'),
        os.path.join(run_dir, 'postProcessing', 'COMPAS_PP.sbatch'),
        {**common,
         'JOB_NAME': f'pp_{args.run_name}'[:60],
         'WALLTIME': PP_WALLTIME,
         'MEMORY': PP_MEMORY,
         # a grid run samples from the birth distribution, so there are no
         # stroopwafel weights to attach
         'APPEND_WEIGHTS': 'echo ">>> grid run: no stroopwafel weights to append"'})

    ci_script = render(
        os.path.join(slurms, 'CosmicIntegration.sbatch'),
        os.path.join(run_dir, 'CosmicIntegration', 'CosmicIntegration.sbatch'),
        {**common,
         'JOB_NAME': f'ci_{args.run_name}'[:60],
         'WALLTIME': CI_WALLTIME,
         'MEMORY': CI_MEMORY,
         'INPUT_H5': 'COMPAS_Output.h5',
         'CI_ARGS': CI_ARGS.replace('--weight mixture_weight ', '')})

    # ---- submit the chain ---------------------------------------------------
    print("\nsubmitting:")
    job_ids = {}
    dep = None

    if RUN_MAINRUN:
        dep = submit(main_script, dry_run=args.dry_run)
        job_ids['MainRun'] = dep
    if RUN_POSTPROCESSING:
        pid = submit(pp_script, dependency=dep, dry_run=args.dry_run)
        job_ids['postProcessing'] = pid
        dep = pid or dep
    if RUN_COSMIC_INTEGRATION:
        cid = submit(ci_script, dependency=dep, dry_run=args.dry_run)
        job_ids['CosmicIntegration'] = cid

    if not args.dry_run:
        with open(os.path.join(run_dir, 'job_ids.txt'), 'w') as f:
            json.dump({k: v for k, v in job_ids.items() if v}, f, indent=2)

    print(f"\ndone. check on it with:\n"
          f"    python3 {os.path.join(run_dir, 'postProcessing', 'check_status.py')} {run_dir}")
    if args.dry_run:
        print("\n(dry run -- nothing was submitted. The job scripts above are real "
              "and can be inspected or sbatch'd by hand.)")


if __name__ == '__main__':
    main()
