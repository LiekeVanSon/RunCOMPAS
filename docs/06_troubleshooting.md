# 06 — Troubleshooting

## Where the logs go

Everything a job prints goes to **`<run_dir>/logs/`**:

```
<run_dir>/logs/
├── grid_1937174_0.out      MainRun array task 0   (%A = array job id, %a = task id)
├── grid_1937174_0.err
├── grid_1937174_1.out      ... one pair per task
├── postProcessing_1937175.out
└── cosmicIntegration_*.out
```

`.out` is stdout (including COMPAS's own output), `.err` is stderr. The
`--dry-run` output tells you the run directory, and it is also printed at submit.

> If `logs/` is **empty** and the jobs failed instantly, slurm could not create
> the log files at all — see [the 0:53 error](#jobs-fail-instantly-with-exitcode-053-and-an-empty-logs-directory).

## SLURM commands you actually need

```bash
squeue -u $USER                       # what is queued/running right now
squeue -u $USER -t PENDING            # ... and why it hasn't started (see NODELIST(REASON))
scancel <jobid>                       # cancel a job
scancel -u $USER                      # cancel everything you have queued
scancel <jobid>_3                     # cancel one array task

sacct -j <jobid>                      # what happened, after it left the queue
sacct -j <jobid> --format=JobID,State,ExitCode,Elapsed,MaxRSS
sacct -u $USER --starttime today      # everything you ran today

sinfo -s                              # partitions, limits, how busy
scontrol show job <jobid>             # everything slurm knows about a job
```

`squeue` only shows **pending and running** jobs — a finished or failed job
disappears from it, which is why an empty `squeue` right after submitting looks
alarming. Use `sacct` for anything that already ended.

Reading `sacct` state and `ExitCode` (`exit_code:signal`):

| | meaning |
|---|---|
| `COMPLETED  0:0` | fine |
| `FAILED     1:0` | your script/COMPAS returned non-zero — read the `.err` |
| `FAILED     0:53` | slurm could not open the output file — see below |
| `TIMEOUT` | hit the walltime; raise it or use more batches |
| `OUT_OF_MEMORY` | raise `MAINRUN_MEMORY` |
| `CANCELLED` | you cancelled it, or a dependency failed |

---

## Start here

```bash
python3 <run_dir>/postProcessing/check_status.py <run_dir>
```

Tells you the slurm state of every job, how many batches produced output, which
ones are empty, and whether the merged files exist:

```
SLURM jobs:
  MainRun            4823914      COMPLETED x98  FAILED x2
  postProcessing     4823915      PENDING

Batches:
  100 batch directories, 98 with output, 2 empty
  empty: 37,64
  resubmit with: sbatch --array=37,64 /path/to/MainRun/COMPAS_grid.sbatch

Output files:
  absent   MainRun/COMPAS_Output.h5
```

Then read the logs. Every job writes to `<run_dir>/logs/`:

```bash
ls <run_dir>/logs/
tail -50 <run_dir>/logs/grid_4823914_37.err     # a specific failed array task
```

---

## Common problems

### "My 200-system test produced a 700 MB file"

Not a bug. COMPAS allocates HDF5 chunks of **100000 entries** by default, so even
a tiny run allocates full-size chunks. For small test runs, set in
`compasConfig.yaml`:

```yaml
    --hdf5-chunk-size: 1000
```

Leave it at the default for production runs — it is much faster there.

### Jobs fail instantly with ExitCode `0:53` and an empty `logs/` directory

Every task starts and ends in the same second, `sacct` shows `FAILED 0:53`, and
nothing was written to `logs/`:

```
1937163_0|FAILED|0:53|2026-08-03T15:16:59|2026-08-03T15:16:59|coma47|
```

**Slurm could not create the job's output file**, so your script never ran. The
run directory is on a filesystem the compute nodes cannot write to.

Check it directly from a compute node:

```bash
srun -p <partition> -n1 -t 2 bash -c 'touch <your_output_root>/probe && echo WRITABLE || echo NOT'
```

A filesystem can be mounted read-write and still refuse writes — an NFS server
can export read-only to specific clients, in which case `mount` says `rw` but
writes return `Read-only file system`.

**On coma this is the normal state of affairs:** all `/vol/astro*` volumes are
exported **read-only to the compute nodes**. You can write there from the login
node, so creating the run directory succeeds and the jobs then die. See
[Storage on coma](#storage-on-coma).

### `The cluster profile points at a venv that does not exist`

`venv:` in your cluster profile is wrong, or you haven't built it yet. The error
prints the exact commands to create it. Set `venv: ""` if you deliberately want
the system python.

### `No module named pip` when installing requirements

Your venv was created with `uv venv`, which does not install pip. Either:

```bash
uv pip install -r requirements.txt        # use uv's installer
python -m ensurepip                       # or add pip to the venv
```

### `ModuleNotFoundError: No module named 'matplotlib'` in CosmicIntegration

`FastCosmicIntegration.py` imports `matplotlib.pyplot` at module level, so it is
required even if you never make a plot. It is in `requirements.txt` — this means
your venv predates it, or you installed packages by hand:

```bash
pip install -r requirements.txt
```

### `Could not match hostname 'x' to a cluster profile`

Either set `CLUSTER = "name"` at the top of the driver, pass `--cluster name`, or
add your hostname to `hostname_match` in the profile. See
[05_clusters.md](05_clusters.md).

### `COMPAS_ROOT_DIR is not set` / `No COMPAS executable at ...`

`compas_root` in your cluster profile is wrong, or COMPAS isn't built. Check:

```bash
ls $COMPAS_ROOT_DIR/src/COMPAS
$COMPAS_ROOT_DIR/src/COMPAS --version
```

### `These options are not in compasConfig.yaml: --some-option`

Your variations file names an option your COMPAS build doesn't have — usually a
typo, or an option renamed between versions. Check:

```bash
$COMPAS_ROOT_DIR/src/COMPAS --help | grep some-option
```

and regenerate a matching config:

```bash
cd masterfolder && $COMPAS_ROOT_DIR/src/COMPAS --create-YAML-file compasConfig.yaml
```

This error is deliberate. Silently skipping the option would run fiducial physics
under a variation's name.

### `<run_dir> already exists`

Refusing by default so a finished run is never silently overwritten. Pick a new
`RUN_NAME`, delete the old directory, or pass `--force` to reuse it.

### Jobs die instantly with a module or library error

The `setup` block in your cluster profile doesn't match what COMPAS was compiled
against. Compare `COMPAS --version` against your `module load` lines — see
[05_clusters.md](05_clusters.md#the-setup-block).

### Array tasks hit the walltime

Each task's walltime covers `N_BINARIES / N_BATCHES` systems, not the whole run.
Either raise `MAINRUN_WALLTIME` or raise `N_BATCHES` so each task does less. Then
resubmit only the failed tasks with `sbatch --array=...`.

### postProcessing never runs / is stuck PENDING

It waits on `afterok` — the MainRun must *succeed*. If any array task failed, the
dependency is never satisfied and `--kill-on-invalid-dep=yes` cancels the job.
Fix and resubmit the failed tasks, then resubmit post-processing by hand:

```bash
sbatch <run_dir>/postProcessing/COMPAS_PP.sbatch
```

### `sorted SEEDs dont match up!!` in append_weights

The COMPAS output and stroopwafel's `samples.csv` disagree about which systems
exist — almost always because some batches failed and are missing from the merge.
Run `check_status.py`, fix the missing batches, re-merge, then retry.

### Stroopwafel: `Could not import stroopwafel`

```bash
python3 -m pip install --user stroopwafel
```

or clone it and set `stroopwafel_path` in your cluster profile.
See [01_setup.md §3](01_setup.md#3-stroopwafel-only-for-mode-2).

### Stroopwafel finds almost no hits

The explore phase found too few interesting systems to adapt to, so you're
effectively running slow Monte Carlo. Either loosen `SYS_INT`, or raise
`NUM_SYSTEMS` so the explore phase sees more. Combining two very rare populations
in one run is especially fragile — see
[03_stroopwafel_runs.md](03_stroopwafel_runs.md#choosing-what-is-interesting).

### `Error evolving binary: not evolved`

Individual binaries that COMPAS declined to evolve — usually touching at birth or
already overflowing their Roche lobe at ZAMS. A few percent is normal, especially
with the `zams` grid sampler which draws separations down to 0.01 AU. Only worry
if it's most of your systems.

---

## Storage on coma

coma is unusual, and worth understanding before you plan a big run.

| location | writable from a compute node? | shared between nodes? | size |
|---|---|---|---|
| `/vol/astro*` | **no** — exported read-only | yes | ~123 TB free |
| `$HOME` | yes | yes (NFS) | **~25 GB** |
| `/scratch` | yes | **no** — local disk per node | ~3.4 TB per node |

The cluster documentation asks that jobs do their I/O on `/scratch`, because the
`/vol` NFS volumes have high latency and cope badly with many jobs writing at
once. This repo does that for you: with `scratch_root: "/scratch"` in the
cluster profile, COMPAS writes to node-local scratch and each finished batch is
copied back to `output_root` when the task ends (including on failure, so
partial output survives). Scratch is always cleaned up afterwards.

That leaves the question of where `output_root` points:

- **`$HOME` (current setting)** — works today, shared and writable from nodes.
  But it is only ~25 GB, and a COMPAS h5 is much bigger than you expect
  (a 200-system test produces ~700 MB, see the chunk-size note above). Fine for
  tests and small runs; **not enough for a production population.**

- **`/vol/astro8` via `scp`** — the cluster docs suggest staging results to the
  fileserver by name (`scp out.h5 astro8-srv:/vol/astro8/users/me/`) rather than
  through the read-only mount. This needs passwordless SSH to `astro8-srv`,
  which is not set up by default (`Permission denied (publickey...)`). If you
  get that working it is the right home for production data.

- **Ask the sysadmin** whether `/vol/astro8` can be exported read-write to the
  compute nodes. If it can, set `output_root` back to astro8 and the storage
  problem disappears entirely.

Until one of the last two is sorted, keep production runs small enough for
`$HOME`, or move each finished run off to `/vol/astro8` **from the login node**
(which can write there) before starting the next one.

---

## Debugging technique

**Always `--dry-run` first.** It creates the whole run directory and every job
script without submitting, so you can read exactly what would happen:

```bash
python3 Submit_GridRun.py --dry-run --output-root /tmp/test --run-name debug
cat /tmp/test/debug/MainRun/COMPAS_grid.sbatch
```

**Run one task by hand** — the generated scripts are ordinary sbatch files:

```bash
cd <run_dir>/MainRun
export COMPAS_ROOT_DIR=/path/to/COMPAS
python3 runSubmit.py compasConfig.yaml --number-of-systems 20 \
    --output-path "$PWD" --output-container test --random-seed 0
```

**See the COMPAS command without running it:**

```bash
python3 runSubmit.py compasConfig.yaml --dry-run
```

**Shrink the problem.** Most bugs reproduce at 200 systems in 4 batches in under
a minute. Debug there, not in an 8-hour production run.

---

## Getting help

- COMPAS documentation: https://compas.readthedocs.io
- COMPAS option reference: `$COMPAS_ROOT_DIR/src/COMPAS --help`
- Stroopwafel: https://github.com/lokiysh/stroopwafel
- Original project-specific scripts, for reference: `legacy/`

When asking for help, include: the COMPAS version, your cluster profile, the
generated `.sbatch`, and the relevant `logs/*.err`.

---

## Previous

- [00 README](../README.md) — overview, repository map, quickstart
- [01 Setup](01_setup.md) — COMPAS, your python environment, first test run
- [02 Grid runs](02_grid_runs.md) — Mode 1: large runs via a SLURM array
- [03 Stroopwafel runs](03_stroopwafel_runs.md) — Mode 2: adaptive importance sampling
- [04 Post-processing](04_postprocessing.md) — merging, weights, cosmic integration
- [05 Clusters](05_clusters.md) — adding a new machine
