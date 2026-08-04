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
#                        (the part you edit)                                #
#                                                                           #
# ===========================================================================

# --- what to call this run, and where it goes ------------------------------
RUN_NAME = "N1e6_test_fid"
# Leave as None to use output_root from your cluster profile.
OUTPUT_ROOT = "/home/lvanson/CompasOutput"

# --- optional extra directory level ----------------------------------------
# Runs land in  <output_root>/<RUN_SUBDIR>/<RUN_NAME>.
#   "auto"      -> the COMPAS version, e.g. "v03.29.05"  (recommended)
#   "some/name" -> a literal path
#   None        -> no extra level
# archive_run.py mirrors this structure into archive_root, so the working and
# archived layouts always match.
RUN_SUBDIR = "auto"

# --- which cluster profile to use ------------------------------------------
# None = auto-detect from hostname against clusters/*.yaml
CLUSTER = 'coma'

# --- how big is the run, and how is it chopped up? -------------------------
N_BINARIES = int(1e6)     # total systems to evolve
N_BATCHES = 100           # = number of slurm array tasks
MAX_CONCURRENT = 25       # cap on tasks running at once (be kind to the queue)
SEED_BASE = 0             # COMPAS random seed of the very first system

# --- optional grid file ----------------------------------------------------
# A grid file gives you one line of COMPAS options per binary (see
# masterfolder/MainRun/make_grid_file.py).
#   GRID_FILE = None      -> no grid; each task just runs --number-of-systems N
#   GRID_FILE = "BSE_grid.txt"  -> use this file, split by line
#
# You usually do NOT need one: COMPAS samples ZAMS parameters itself
# (--initial-mass-function, --metallicity-distribution, ...) and builds
# parameter scans with ranges/sets. Use a grid file only for explicit
# per-system values -- e.g. re-running systems from a previous run.
# See docs/02_grid_runs.md and masterfolder/MainRun/make_grid_file.py
GRID_FILE = None

# --- resources per array task ----------------------------------------------
# One task evolves N_BINARIES/N_BATCHES systems. Time it on a small run first:
# submit with N_BINARIES=1000, N_BATCHES=1 and look at the log.
MAINRUN_WALLTIME = "0-04:30:00"
MAINRUN_MEMORY = "2G"

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

# --- physics variations -----------------------------------------------------
# Run the same setup several times with different COMPAS physics. Each entry
# gets its own output directory, <RUN_NAME>_<simname>, and its own copy of
# compasConfig.yaml with those flags overridden.
#
# This is the preferred way to build a grid of physics variations: the file
# lists only what CHANGES per variation, so you can see the difference at a
# glance instead of re-stating every flag. It also sidesteps the ranges/sets
# limitation (COMPAS ignores --number-of-systems when those are present, which
# breaks splitting the run across array tasks -- see docs/02_grid_runs.md).
#
# Set to None to run a single unvaried simulation.
VARIATIONS_FILE = None
# VARIATIONS_FILE = "masterfolder/simulation_variations.json"

# Applied to every variation, before its own overrides.
BASE_OVERRIDES = {}


# ===========================================================================
#                                                                           #
#         You should not need to change anything below this line.           #
#                                                                           #
# ===========================================================================

RANGE_SET_RE = re.compile(r"^\s*(?:r|range|s|set)?\[[^\]]*\]\s*$", re.I)


def check_for_ranges_and_sets(config_path, using_grid_file):
    """
    Refuse to split a run across array tasks when the config uses ranges or sets.

    COMPAS ignores --number-of-systems as soon as any range or set is given: the
    number of systems is then the product of the range/set sizes. Our array
    tasks each pass --number-of-systems <slice>, so that value would be silently
    dropped and EVERY task would evolve the WHOLE grid -- N_BATCHES copies of it,
    differing only by random seed. Nothing would error; you would just get a
    duplicated population.

    Verified against COMPAS v03.29.05: `-n 1000 --metallicity [0.0001,5,0.0013]`
    evolves 5 systems, not 1000.
    """
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return

    offenders = []
    for section in config.values():
        for key, val in (section or {}).items():
            if isinstance(val, str) and RANGE_SET_RE.match(val):
                offenders.append(f"{key}: {val}")

    if not offenders:
        return
    if using_grid_file:
        # with a grid file we slice by line, so ranges/sets still work --
        # they are applied to every line. Just say what will happen.
        print("note            : config uses ranges/sets; COMPAS applies them to "
              "EVERY grid line,\n                  so the run is "
              "(grid lines) x (range/set combinations).")
        return

    raise SystemExit(
        "\nYour compasConfig.yaml uses COMPAS ranges/sets:\n"
        + '\n'.join(f"    {o}" for o in offenders)
        + "\n\nCOMPAS ignores --number-of-systems when a range or set is present, so "
          "splitting\nthe run across array tasks would give every task the FULL grid "
          "instead of a\nslice -- silently duplicating your population.\n\n"
          "Either:\n"
          "  * set N_BATCHES = 1 (the grid is usually small enough), or\n"
          "  * write the combinations to a grid file and set GRID_FILE, which slices "
          "by line.\n"
          "See docs/02_grid_runs.md")


def resolve_subdir(subdir, profile):
    """
    Optional directory level between output_root and the run name.

    "auto" resolves to the COMPAS version that will actually run, e.g.
    "v03.29.05". Filing data under the code version that produced it is worth
    the extra level: COMPAS renames and removes options between releases, so a
    run is only really reproducible against the build it came from.

    Set a literal string to pin it, or None for no extra level.
    """
    if not subdir:
        return ''
    if subdir != 'auto':
        return subdir.strip('/')

    exe = os.path.join(profile['compas_root'], 'src/COMPAS')
    try:
        out = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=60)
        match = re.search(r'v\d+\.\d+\.\d+', out.stdout)
        if match:
            return match.group(0)
    except (OSError, subprocess.SubprocessError):
        pass
    raise SystemExit(
        f"RUN_SUBDIR is 'auto' but the COMPAS version could not be read from:\n"
        f"    {exe} --version\n"
        f"Set RUN_SUBDIR to a literal string (e.g. \"v03.29.05\") or to None.")


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


def h5_chunk_size(n_systems):
    """
    Chunk size for h5copy, scaled to the run.

    h5copy allocates one full chunk per dataset, and COMPAS output has ~840
    datasets. At the h5copy default of 100000 that is ~670 MB of empty chunks
    even for a 200-system test run. Too small is bad too -- many tiny chunks
    slow reads down -- so aim for ~100 chunks across the run, clamped to a
    sane range.
    """
    return int(min(100000, max(1000, n_systems // 100)))


def update_config_file(path, overrides):
    """
    Apply COMPAS flag overrides to a compasConfig.yaml, in place.

    Uncomments the entry if needed and replaces its value, at whatever indent
    the file already uses for its uncommented entries. YAML rejects a mapping
    whose keys sit at inconsistent indentation, and people uncomment by hand in
    different ways -- deleting just the '#' leaves 4 spaces, deleting '# '
    leaves 3 -- so we match the file instead of imposing a width.

    Raises if a flag is not in the file at all, which almost always means a
    typo or an option that your COMPAS version does not have.
    """
    if not overrides:
        return

    with open(path) as f:
        lines = f.readlines()

    # Match the indent already used by uncommented entries; fall back to 4,
    # which is what you get by deleting only the '#' from a commented line.
    existing = [re.match(r'^( +)--', l) for l in lines]
    indents = [m.group(1) for m in existing if m]
    indent = indents[0] if indents else '    '

    applied = set()
    for i, line in enumerate(lines):
        for flag, value in overrides.items():
            if flag in applied:
                continue
            if re.match(rf"^\s*#?\s*{re.escape(flag)}:\s*.*", line):
                if isinstance(value, bool):
                    formatted = 'True' if value else 'False'
                elif isinstance(value, str):
                    formatted = f"'{value}'"
                else:
                    formatted = str(value)
                lines[i] = f"{indent}{flag}: {formatted}\n"
                applied.add(flag)
                break

    missing = set(overrides) - applied
    if missing:
        raise SystemExit(
            f"These options are not in {path}:\n"
            + '\n'.join(f"    {m}" for m in sorted(missing))
            + "\n\nEither they are misspelled, or your COMPAS version does not have "
              "them.\nCheck with:  $COMPAS_ROOT_DIR/src/COMPAS --help | grep <option>")

    with open(path, 'w') as f:
        f.writelines(lines)

    # fail loudly here rather than inside a job three hours from now
    try:
        with open(path) as f:
            yaml.safe_load(f)
    except yaml.YAMLError as err:
        raise SystemExit(f"Applying overrides broke {path}:\n{err}")


def load_variations(only=None):
    """Read the variations file, or return a single unvaried run."""
    if not VARIATIONS_FILE:
        return [{'simname': None, 'overrides': {}}]

    path = (VARIATIONS_FILE if os.path.isabs(VARIATIONS_FILE)
            else os.path.join(REPO, VARIATIONS_FILE))
    if not os.path.isfile(path):
        raise SystemExit(f"VARIATIONS_FILE not found: {path}")

    with open(path) as f:
        variations = json.load(f)

    if only:
        wanted = set(only)
        variations = [v for v in variations if v['simname'] in wanted]
        unknown = wanted - {v['simname'] for v in variations}
        if unknown:
            raise SystemExit(f"No such variation(s): {', '.join(sorted(unknown))}")
    return variations


def stage_files(run_dir, grid_file, overrides=None):
    """Create the run directory tree and copy masterfolder into it."""
    master = os.path.join(REPO, 'masterfolder')
    for sub in ('MainRun', 'postProcessing', 'CosmicIntegration', 'logs'):
        os.makedirs(os.path.join(run_dir, sub), exist_ok=True)

    for sub in ('MainRun', 'postProcessing', 'CosmicIntegration'):
        shutil.copytree(os.path.join(master, sub), os.path.join(run_dir, sub),
                        dirs_exist_ok=True)
    config_dest = os.path.join(run_dir, 'MainRun', 'compasConfig.yaml')
    shutil.copy(os.path.join(master, 'compasConfig.yaml'), config_dest)
    update_config_file(config_dest, overrides)
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


def submit_one(variation, profile, args, output_root, subdir,
               n_binaries, n_batches, grid_src):
    """Set up and submit the full chain for one variation."""
    simname = variation.get('simname')
    run_name = f"{args.run_name}_{simname}" if simname else args.run_name
    run_dir = os.path.join(output_root, subdir, run_name)

    print(f"\n--- {simname or run_name} ---")
    print(f"  {run_dir}")

    if os.path.exists(run_dir) and not args.force:
        print(f"  SKIPPED: already exists (pass --force to reuse)")
        return None

    overrides = {**BASE_OVERRIDES, **(variation.get('overrides') or {})}
    for k, v in (variation.get('overrides') or {}).items():
        print(f"  override  {k}: {v}")

    grid_dest = stage_files(run_dir, grid_src, overrides)

    # ---- work out the array geometry ---------------------------------------
    n_total = count_lines(grid_dest) if grid_dest else n_binaries
    if grid_dest and n_total != n_binaries:
        print(f"  note: grid file has {n_total} lines, using that instead of "
              f"N_BINARIES={n_binaries}")

    batch_size = -(-n_total // n_batches)      # ceiling division
    n_tasks = -(-n_total // batch_size)        # may be < n_batches if uneven

    print(f"  {n_total} systems -> {n_tasks} tasks x {batch_size} "
          f"(max {MAX_CONCURRENT} at once)")
    if n_tasks * batch_size != n_total:
        print(f"  last task takes the remainder "
              f"({n_total - (n_tasks - 1) * batch_size})")

    # ---- render the job scripts --------------------------------------------
    slurms = os.path.join(REPO, 'masterfolder', 'slurms')
    common = sbatch_common(profile, run_dir, profile['name'])

    if grid_dest:
        slice_args = (f'--grid {grid_dest} '
                      f'--grid-start-line ${{START}} --grid-lines-to-process ${{N_THIS}}')
    else:
        slice_args = '--number-of-systems ${N_THIS}'

    main_script = render(
        os.path.join(slurms, 'COMPAS_grid.sbatch'),
        os.path.join(run_dir, 'MainRun', 'COMPAS_grid.sbatch'),
        {**common,
         'JOB_NAME': f'grid_{run_name}'[:60],
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
         'JOB_NAME': f'pp_{run_name}'[:60],
         'WALLTIME': PP_WALLTIME,
         'MEMORY': PP_MEMORY,
         'H5_CHUNK': h5_chunk_size(n_total),
         # a grid run samples from the birth distribution, so there are no
         # stroopwafel weights to attach
         'APPEND_WEIGHTS': 'echo ">>> grid run: no stroopwafel weights to append"'})

    ci_script = render(
        os.path.join(slurms, 'CosmicIntegration.sbatch'),
        os.path.join(run_dir, 'CosmicIntegration', 'CosmicIntegration.sbatch'),
        {**common,
         'JOB_NAME': f'ci_{run_name}'[:60],
         'WALLTIME': CI_WALLTIME,
         'MEMORY': CI_MEMORY,
         'INPUT_H5': 'COMPAS_Output.h5',
         'CI_ARGS': CI_ARGS.replace('--weight mixture_weight ', '')})

    # ---- submit the chain ---------------------------------------------------
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
        job_ids['CosmicIntegration'] = submit(ci_script, dependency=dep,
                                              dry_run=args.dry_run)

    if not args.dry_run:
        with open(os.path.join(run_dir, 'job_ids.txt'), 'w') as f:
            json.dump({k: v for k, v in job_ids.items() if v}, f, indent=2)

    return run_dir


def main():
    ap = argparse.ArgumentParser(description="Submit a COMPAS grid run as a slurm array.")
    ap.add_argument('--dry-run', action='store_true',
                    help='create the run directory and job scripts, but do not submit')
    ap.add_argument('--force', action='store_true',
                    help='reuse an existing run directory instead of refusing')
    ap.add_argument('--cluster', default=CLUSTER, help='override the cluster profile')
    ap.add_argument('--run-name', default=RUN_NAME, help='override RUN_NAME')
    ap.add_argument('--output-root', default=None, help='override OUTPUT_ROOT')
    ap.add_argument('--subdir', default=None,
                    help="override RUN_SUBDIR ('auto', a literal path, or '' for none)")
    ap.add_argument('--n-binaries', type=float, default=None,
                    help='override N_BINARIES (handy for a quick test run)')
    ap.add_argument('--n-batches', type=int, default=None,
                    help='override N_BATCHES')
    ap.add_argument('--only', nargs='+', metavar='SIMNAME',
                    help='run only these variations (by simname)')
    ap.add_argument('--list', action='store_true',
                    help='list the available variations and exit')
    args = ap.parse_args()

    if args.list:
        for v in load_variations():
            n = len(v.get('overrides') or {})
            print(f"  {v['simname'] or '(no variations file)':<35} {n} override(s)")
        return

    n_binaries = int(args.n_binaries) if args.n_binaries else N_BINARIES
    n_batches = args.n_batches or N_BATCHES

    profile = load_cluster(args.cluster)
    output_root = args.output_root or OUTPUT_ROOT or profile['output_root']
    subdir = resolve_subdir(args.subdir if args.subdir is not None else RUN_SUBDIR, profile)
    variations = load_variations(args.only)

    if subdir:
        print(f"sub-directory   : {subdir}")
    print(f"output root     : {os.path.join(output_root, subdir)}")
    if VARIATIONS_FILE:
        print(f"variations      : {len(variations)}")

    # ---- grid file ---------------------------------------------------------
    grid_src = None
    if GRID_FILE:
        grid_src = GRID_FILE if os.path.isabs(GRID_FILE) else os.path.join(REPO, GRID_FILE)
        if not os.path.isfile(grid_src):
            raise SystemExit(
                f"GRID_FILE is set but does not exist:\n    {grid_src}\n\n"
                f"Create it first, e.g. from a previous run:\n"
                f"    python3 masterfolder/MainRun/make_grid_file.py \\\n"
                f"        --from-h5 <previous>/MainRun/COMPAS_Output.h5 -o {grid_src}\n\n"
                f"Or set GRID_FILE = None and use COMPAS's built-in samplers "
                f"(see docs/02_grid_runs.md).")

    # ---- ranges/sets are incompatible with slicing by --number-of-systems ---
    check_for_ranges_and_sets(os.path.join(REPO, 'masterfolder', 'compasConfig.yaml'),
                              using_grid_file=bool(grid_src))

    check_walltime(MAINRUN_WALLTIME, profile.get('max_walltime', '99-00:00:00'), 'MainRun')

    submitted = [r for v in variations
                 if (r := submit_one(v, profile, args, output_root, subdir,
                                     n_binaries, n_batches, grid_src))]

    print(f"\ndone. {len(submitted)} run(s) set up.")
    if submitted:
        print(f"check on one with:\n"
              f"    python3 {os.path.join(submitted[0], 'postProcessing', 'check_status.py')} "
              f"{submitted[0]}")
    if args.dry_run:
        print("\n(dry run -- nothing was submitted. The job scripts above are real "
              "and can be inspected or sbatch'd by hand.)")


if __name__ == '__main__':
    main()
