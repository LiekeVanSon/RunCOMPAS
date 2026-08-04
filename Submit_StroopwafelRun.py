#!/usr/bin/env python3
"""
=============================================================================
 Submit_StroopwafelRun.py  --  MODE 2: COMPAS with adaptive importance sampling
=============================================================================

 Use this when the systems you care about are RARE (double neutron stars,
 massive double white dwarfs, ...). Stroopwafel spends the explore phase finding
 where those systems live in ZAMS parameter space, then concentrates the
 remaining samples there. Every system comes out carrying a `mixture_weight`
 that undoes the sampling bias -- you MUST use it in any population statistic.

 It creates, for each variation:

     <OUTPUT_ROOT>/<RUN_NAME>_<simname>/
         MainRun/             <- COMPAS output + samples.csv (the weights)
         postProcessing/      <- merge + attach weights
         CosmicIntegration/   <- rate calculation
         logs/                <- slurm .out/.err
         job_ids.txt

 and submits  MainRun --> postProcessing --> CosmicIntegration  for each,
 chained with --dependency=afterok.

 USAGE
     python3 Submit_StroopwafelRun.py --dry-run     # write, don't submit
     python3 Submit_StroopwafelRun.py               # for real
     python3 Submit_StroopwafelRun.py --only fid    # just one variation

 Requires stroopwafel:  pip install --user stroopwafel
                        https://github.com/lokiysh/stroopwafel

 See docs/03_stroopwafel_runs.md
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
# Each variation appends its simname:  N1e6_DNS_fid, N1e6_DNS_CE_alpha05, ...
RUN_NAME = "N1e6_DNS"
OUTPUT_ROOT = None        # None = output_root from the cluster profile

# --- optional extra directory level ----------------------------------------
# Runs land in  <output_root>/<RUN_SUBDIR>/<RUN_NAME>.
#   "auto"      -> the COMPAS version, e.g. "v03.29.05"  (recommended)
#   "some/name" -> a literal path
#   None        -> no extra level
# archive_run.py mirrors this structure into archive_root, so the working and
# archived layouts always match.
RUN_SUBDIR = "auto"

# --- which cluster profile to use ------------------------------------------
CLUSTER = None            # None = auto-detect from hostname

# --- the sampling ----------------------------------------------------------
NUM_SYSTEMS = int(1e6)    # total systems, across all four stroopwafel phases
NUM_CORES = 20            # COMPAS processes in parallel INSIDE the one job
NUM_PER_CORE = int(1e4)   # systems per batch
SEED_BASE = 0
MC_ONLY = False           # True = plain Monte Carlo, skip adapt/refine (no AIS)

# What counts as a "hit". Must be one of the keys in SYSTEMS_OF_INTEREST in
# masterfolder/MainRun/stroopwafel_interface.py:
#   WDWD  MassiveWDWD  BBH  DNS  BHNS  MassiveWDWD_NSNS  AnyDCO
SYS_INT = "DNS"

# --- resources for the (single, long) stroopwafel job -----------------------
# Rules of thumb from previous runs (Flatiron, cca partition):
#   1e6 systems,  40 cores, 1e4 per core  -> ~75 min  + 11 min PP + 1 min CI
#   5e6 systems,  40 cores, 1e4 per core  -> ~7 h     + 40 min PP + 4 min CI
#   1e7 systems, 100 cores, 1.5e4 per core-> ~8 h     + 1 h    PP + 10 min CI
MAINRUN_WALLTIME = "1-00:00:00"
MAINRUN_MEMORY = "32G"

# --- which stages to submit -------------------------------------------------
RUN_MAINRUN = True
RUN_POSTPROCESSING = True
RUN_COSMIC_INTEGRATION = False

PP_WALLTIME = "0-04:00:00"
PP_MEMORY = "32G"

CI_WALLTIME = "0-02:00:00"
CI_MEMORY = "16G"
CI_ARGS = ("--weight mixture_weight --keepRLOF_postCE --zstep 0.01 --sens O3 "
           "--m1min 5.0 --dco_type all --maxzdet 8 "
           "--mu0 0.025 --muz -0.049 --sigma0 1.129 --sigmaz 0.048 --alpha -1.79 "
           "--aSF 0.017 --bSF 1.487 --cSF 4.442 --dSF 5.886")

# --- variations -------------------------------------------------------------
# Run the same setup several times with different COMPAS physics. Each entry
# gets its own output directory. Set to None to run a single unvaried run.
# The file is a list of {"simname": ..., "overrides": {"--flag": value, ...}}.
VARIATIONS_FILE = "masterfolder/simulation_variations.json"
# VARIATIONS_FILE = None

# Overrides applied to EVERY variation. Stroopwafel needs these two: it writes
# one grid line per system, so leaving --add-options-to-sysparms at its 'GRID'
# default would balloon the SysParms table with a copy of every option.
BASE_OVERRIDES = {
    "--add-options-to-sysparms": "NEVER",
    "--logfile-definitions": "COMPAS_Output_Definitions.txt",
    "--logfile-type": "HDF5",
}


# ===========================================================================
#                                                                           #
#         You should not need to change anything below this line.           #
#                                                                           #
#  (This machinery is deliberately duplicated in Submit_GridRun.py so that   #
#   each driver is self-contained and can be copied on its own.)             #
# ===========================================================================

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
              "them.\nCheck with:  $COMPAS_ROOT_DIR/src/COMPAS --help | grep <option>\n"
              "Regenerate a matching config with:\n"
              "    $COMPAS_ROOT_DIR/src/COMPAS --create-YAML-file compasConfig.yaml")

    with open(path, 'w') as f:
        f.writelines(lines)

    # Fail loudly here rather than inside a job three hours from now.
    try:
        with open(path) as f:
            yaml.safe_load(f)
    except yaml.YAMLError as err:
        raise SystemExit(f"Applying overrides broke {path}:\n{err}")


def stage_files(run_dir, overrides):
    """Create the run directory tree, copy masterfolder in, apply overrides."""
    master = os.path.join(REPO, 'masterfolder')
    for sub in ('MainRun', 'postProcessing', 'CosmicIntegration', 'logs'):
        os.makedirs(os.path.join(run_dir, sub), exist_ok=True)

    for sub in ('MainRun', 'postProcessing', 'CosmicIntegration'):
        shutil.copytree(os.path.join(master, sub), os.path.join(run_dir, sub),
                        dirs_exist_ok=True)

    config_dest = os.path.join(run_dir, 'MainRun', 'compasConfig.yaml')
    shutil.copy(os.path.join(master, 'compasConfig.yaml'), config_dest)
    update_config_file(config_dest, overrides)

    shutil.copy(os.path.abspath(__file__), os.path.join(run_dir, os.path.basename(__file__)))
    return config_dest


def load_variations(only=None):
    """Read the variations file, or return a single unvaried run."""
    if not VARIATIONS_FILE:
        return [{'simname': 'fid', 'overrides': {}}]

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


def submit_one(variation, profile, args, output_root, subdir=''):
    """Set up and submit the full chain for one variation."""
    simname = variation['simname']
    run_name = f"{args.run_name}_{simname}"
    run_dir = os.path.join(output_root, subdir, run_name)

    print(f"\n--- {simname} ---")
    print(f"  {run_dir}")

    if os.path.exists(run_dir) and not args.force:
        print(f"  SKIPPED: already exists (pass --force to reuse)")
        return None

    overrides = {**BASE_OVERRIDES, **(variation.get('overrides') or {})}
    stage_files(run_dir, overrides)
    if variation.get('overrides'):
        for k, v in variation['overrides'].items():
            print(f"  override  {k}: {v}")

    slurms = os.path.join(REPO, 'masterfolder', 'slurms')
    common = sbatch_common(profile, run_dir, profile['name'])

    main_script = render(
        os.path.join(slurms, 'COMPAS_stroopwafel.sbatch'),
        os.path.join(run_dir, 'MainRun', 'COMPAS_stroopwafel.sbatch'),
        {**common,
         'JOB_NAME': f'sw_{run_name}'[:60],
         # stroopwafel forks NUM_CORES workers and needs a core for itself
         'NTASKS': NUM_CORES + 1,
         'WALLTIME': MAINRUN_WALLTIME,
         'MEMORY': MAINRUN_MEMORY,
         'STROOPWAFEL_PATH': profile.get('stroopwafel_path') or '',
         'NUM_SYSTEMS': NUM_SYSTEMS,
         'NUM_CORES': NUM_CORES,
         'NUM_PER_CORE': NUM_PER_CORE,
         'SYS_INT': SYS_INT,
         'SEED_BASE': SEED_BASE,
         'MC_ONLY': '--mc_only' if MC_ONLY else ''})

    pp_script = render(
        os.path.join(slurms, 'COMPAS_PP.sbatch'),
        os.path.join(run_dir, 'postProcessing', 'COMPAS_PP.sbatch'),
        {**common,
         'JOB_NAME': f'pp_{run_name}'[:60],
         'WALLTIME': PP_WALLTIME,
         'MEMORY': PP_MEMORY,
         'H5_CHUNK': h5_chunk_size(NUM_SYSTEMS),
         'APPEND_WEIGHTS': (
             'echo ">>> attaching stroopwafel mixture weights"\n'
             f'{common["PYTHON"]} append_weights.py --data-dir "${{DATA_DIR}}/MainRun/"')})

    ci_script = render(
        os.path.join(slurms, 'CosmicIntegration.sbatch'),
        os.path.join(run_dir, 'CosmicIntegration', 'CosmicIntegration.sbatch'),
        {**common,
         'JOB_NAME': f'ci_{run_name}'[:60],
         'WALLTIME': CI_WALLTIME,
         'MEMORY': CI_MEMORY,
         'INPUT_H5': 'COMPAS_Output_wWeights.h5',
         'CI_ARGS': CI_ARGS})

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
        job_ids['CosmicIntegration'] = submit(ci_script, dependency=dep, dry_run=args.dry_run)

    if not args.dry_run:
        with open(os.path.join(run_dir, 'job_ids.txt'), 'w') as f:
            json.dump({k: v for k, v in job_ids.items() if v}, f, indent=2)

    return run_dir


def main():
    ap = argparse.ArgumentParser(
        description="Submit COMPAS stroopwafel (adaptive importance sampling) runs.")
    ap.add_argument('--dry-run', action='store_true',
                    help='create run directories and job scripts, but do not submit')
    ap.add_argument('--force', action='store_true',
                    help='reuse existing run directories instead of skipping them')
    ap.add_argument('--cluster', default=CLUSTER, help='override the cluster profile')
    ap.add_argument('--run-name', default=RUN_NAME, help='override RUN_NAME')
    ap.add_argument('--output-root', default=None, help='override OUTPUT_ROOT')
    ap.add_argument('--subdir', default=None,
                    help="override RUN_SUBDIR ('auto', a literal path, or '' for none)")
    ap.add_argument('--only', nargs='+', metavar='SIMNAME',
                    help='run only these variations (by simname)')
    ap.add_argument('--list', action='store_true',
                    help='list the available variations and exit')
    args = ap.parse_args()

    if args.list:
        for v in load_variations():
            n = len(v.get('overrides') or {})
            print(f"  {v['simname']:<35} {n} override(s)")
        return

    profile = load_cluster(args.cluster)
    output_root = args.output_root or OUTPUT_ROOT or profile['output_root']
    subdir = resolve_subdir(args.subdir if args.subdir is not None else RUN_SUBDIR, profile)
    variations = load_variations(args.only)

    print(f"output root     : {os.path.join(output_root, subdir)}")
    print(f"variations      : {len(variations)}")
    print(f"sampling        : {NUM_SYSTEMS:.3g} systems, {NUM_CORES} cores, "
          f"{NUM_PER_CORE} per batch, hits = {SYS_INT}"
          + (" (MC only, no AIS)" if MC_ONLY else ""))
    check_walltime(MAINRUN_WALLTIME, profile.get('max_walltime', '99-00:00:00'), 'MainRun')

    submitted = [r for v in variations
                 if (r := submit_one(v, profile, args, output_root, subdir))]

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
