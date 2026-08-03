# 02 — Grid runs (Mode 1)

The standard way to run a large COMPAS population: evolve N systems by splitting
them into equal slices and handing one slice to each task of a **single SLURM
array job**.

```
                    ┌── task 0 → batch_0/ ──┐
 Submit_GridRun.py ─┼── task 1 → batch_1/ ──┼→ postProcessing → CosmicIntegration
                    ├── task 2 → batch_2/ ──┤     (merge)         (rates)
                    └── ...                 ┘
                    one array job                 chained with --dependency=afterok
```

---

## Running one

Edit the `SETTINGS` block at the top of `Submit_GridRun.py`:

```python
RUN_NAME   = "N1e6_fiducial"     # the output directory name
N_BINARIES = int(1e6)            # total systems
N_BATCHES  = 100                 # = number of array tasks
MAX_CONCURRENT = 20              # how many tasks may run at once
MAINRUN_WALLTIME = "0-08:00:00"  # per task, not for the whole run
MAINRUN_MEMORY   = "4G"
```

Then:

```bash
python3 Submit_GridRun.py --dry-run    # writes everything, submits nothing
python3 Submit_GridRun.py              # submit
```

Useful flags:

```bash
--run-name NAME       # override RUN_NAME without editing the file
--output-root PATH    # write somewhere else
--cluster NAME        # force a cluster profile
--force               # reuse an existing run directory
```

---

## Choosing N_BATCHES and walltime

Each task evolves `N_BINARIES / N_BATCHES` systems. The trade-off:

- **too few batches** → each task runs for many hours and risks hitting the
  walltime, losing all of its work
- **too many batches** → thousands of tiny jobs, slow scheduling, and a merge
  step that has to open thousands of files

A reasonable target is **1–4 hours per task**.

To find the right number, time a small run first:

```python
N_BINARIES = 1000
N_BATCHES  = 1
```

Look at `logs/grid_*.out` for the COMPAS `Wall time` line, then scale up. As a
very rough guide, COMPAS evolves order 100–1000 binaries/second/core depending on
the physics, so 10⁴ systems per task is often about right.

`MAX_CONCURRENT` (the `%20` in `--array=0-99%20`) caps how many tasks run at
once. Keep it modest on a shared cluster — you are not the only user.

> The walltime you set applies to **each task**, not the whole array.
> The driver warns you if it exceeds your partition's limit
> (`max_walltime` in the cluster profile).

---

## Using a grid file

By default every system is drawn from the distributions in `compasConfig.yaml`.
A **grid file** instead gives you explicit control: one line per binary, listing
the COMPAS options that differ between systems.

```
--random-seed 0 --initial-mass-1 10.78 --initial-mass-2 0.37 --semi-major-axis 0.237 --metallicity 0.000357
--random-seed 1 --initial-mass-1 13.60 --initial-mass-2 9.25 --semi-major-axis 289.0 --metallicity 0.000164
...
```

Anything not on the line falls back to `compasConfig.yaml`. Options on a grid
line **override** the command line, which is why the per-system `--random-seed`
above wins over the one the job script passes.

To use one:

```python
GRID_FILE = "BSE_grid.txt"        # generated if it does not exist
MAKE_GRID_SAMPLING = "zams"       # "metallicity" or "zams"
```

The number of lines in the file then determines the run size — `N_BINARIES` is
ignored (the driver tells you when this happens).

Generate one by hand:

```bash
python3 masterfolder/MainRun/make_grid_file.py -n 1000000 -o BSE_grid.txt --sampling zams
```

Edit `sample_line_*()` in that script to change what varies. The built-in
`zams` sampler draws a Kroupa IMF, uniform mass ratio, log-uniform separation and
log-uniform metallicity.

> A few percent of systems from the `zams` sampler will report
> `Error evolving binary: not evolved`. Those are binaries touching at birth —
> a consequence of sampling separations down to 0.01 AU with massive stars, not
> a bug. Raise the minimum separation if you'd rather avoid them.

Grid files are also how you **rerun specific systems**: pull the initial
conditions of interesting binaries out of a previous run and write them to a new
grid file (there is an example of this in
`legacy/COMPASsubmitscripts/make_grid_file.ipynb`).

---

## How the slicing works

You do **not** need to edit any config per batch. Each array task computes its
own slice in the job script:

```bash
TASK=${SLURM_ARRAY_TASK_ID}
START=$(( TASK * BATCH_SIZE ))
# N_THIS = BATCH_SIZE, or the remainder on the last task
```

and passes it to COMPAS on the command line:

```bash
python3 runSubmit.py compasConfig.yaml \
    --grid BSE_grid.txt --grid-start-line ${START} --grid-lines-to-process ${N_THIS} \
    --output-path "${MAIN_RUN}" \
    --output-container "batch_${TASK}" \
    --random-seed $(( SEED_BASE + START ))
```

`runSubmit.py` reads the physics from the yaml and lets any option given on the
command line override it. So **the yaml is never edited per batch** — it stays a
single, readable record of the physics for the whole run.

The `--random-seed` offset guarantees no two batches reuse random numbers. You
can verify this after a run: the merged SEED column should have no duplicates.

If `N_BINARIES` doesn't divide evenly, batch size is rounded up and the last task
takes the remainder — e.g. 500 systems in 7 batches → 6 tasks of 72 plus one of 68.

---

## When a task fails

The generated array script is a normal sbatch file, so you can resubmit just the
tasks that failed:

```bash
sbatch --array=3,7,12 <run_dir>/MainRun/COMPAS_grid.sbatch
```

`check_status.py` prints exactly this command for you, listing the empty batches:

```bash
python3 <run_dir>/postProcessing/check_status.py <run_dir>
```

---

## Previous

- [00 README](../README.md) — overview, repository map, quickstart
- [01 Setup](01_setup.md) — COMPAS, your python environment, first test run

## Next

- [04 Post-processing](04_postprocessing.md) — merging, weights, cosmic integration
- [05 Clusters](05_clusters.md) — adding a new machine
- [06 Troubleshooting](06_troubleshooting.md) — when things go wrong
