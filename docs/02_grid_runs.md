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
--n-binaries 200      # override N_BINARIES  (quick test without editing)
--n-batches 4         # override N_BATCHES
--output-root PATH    # write somewhere else
--subdir NAME         # override RUN_SUBDIR ('' for none)
--cluster NAME        # force a cluster profile
--force               # reuse an existing run directory
```

### Where the run lands

```
<output_root>/<RUN_SUBDIR>/<RUN_NAME>/
```

`RUN_SUBDIR` defaults to `"auto"`, which resolves to the COMPAS version that
will actually run:

```
/home/you/CompasOutput/v03.29.05/N1e6_fiducial
```

Filing data under the code version that produced it is worth the extra level:
COMPAS renames and removes options between releases, so a run is only really
reproducible against the build it came from. Set `RUN_SUBDIR` to a literal
string to pin it, or `None` for no extra level.

`archive_run.py` mirrors this structure into `archive_root`, so your working and
archived layouts never drift apart.

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

## Choosing your initial conditions

There are a few ways to say what systems to evolve, in increasing order of
effort. **Use the first one that can express what you want.**

1. built-in samplers — for drawing from a distribution
2.  A: a variations file — for a grid of *physics* variations (usually this one)
    B: ranges and sets — for a quick parameter scan on the command line
3. grid files — for explicit per-system values nothing else can express

### 1. Built-in samplers 

COMPAS samples the ZAMS parameters itself. If you want to deviate from default sampling setting, you can achieve most common functionalities with settings in `compasConfig.yaml`:

```yaml
    --initial-mass-function: 'KROUPA'
    --initial-mass-function-min: 5.0            # note: -function-, not --initial-mass-min
    --initial-mass-function-max: 150.0
    --metallicity-distribution: 'LOGUNIFORM'    # default is ZSOLAR (all solar!)
    --metallicity-min: 0.0001
    --metallicity-max: 0.03
    --semi-major-axis-distribution: 'FLATINLOG'
    --semi-major-axis-min: 0.01                 # AU
    --semi-major-axis-max: 1000.0
    --mass-ratio-distribution: 'FLAT'
    --eccentricity-distribution: 'ZERO'
```

find the options in the [online docs](https://compas.readthedocs.io/en/latest/pages/User%20guide/Program%20options/program-options-list-defaults.html), or in `compasConfig.yaml` with `grep -n "initial-mass-function" masterfolder/compasConfig.yaml` and uncomment/select what you need.

This is faster than a grid file, better tested, and self-documenting — the
choices land in `Run_Details` in the output.

Available distributions in v03.29.05 (check yours with `COMPAS --help`):

| option | choices |
|---|---|
| `--metallicity-distribution` | `ZSOLAR`, `LOGUNIFORM` |
| `--initial-mass-function` | `KROUPA`, `SALPETER`, `UNIFORM`, `POWERLAW` |
| `--semi-major-axis-distribution` | `FLATINLOG`, `SANA2012`, `DUQUENNOYMAYOR1991` |
| `--orbital-period-distribution` | `FLATINLOG` |
| `--mass-ratio-distribution` | `FLAT`, `SANA2012`, `DUQUENNOYMAYOR1991` |
| `--eccentricity-distribution` | `ZERO`, `THERMAL`, `FLAT`, `SANA2012`, `DUQUENNOYMAYOR1991`, `GELLER+2013` |

> **`--metallicity-distribution` defaults to `ZSOLAR`**, which gives *every*
> system solar metallicity. If you want a spread you must ask for `LOGUNIFORM`.


### 2a. Physics variations (the usual way to build a grid)

To run the same population several times with different physics, list the flags
that **change** in `masterfolder/simulation_variations.json`:

```json
[
  { "simname": "fid",        "overrides": {} },
  { "simname": "CE_alpha05", "overrides": {"--common-envelope-alpha": 0.5} },
  { "simname": "AM_circumbinary",
    "overrides": {"--mass-transfer-angular-momentum-loss-prescription": "CIRCUMBINARY"} }
]
```

Then point the driver at it:

```python
VARIATIONS_FILE = "masterfolder/simulation_variations.json"
```

```bash
python3 Submit_GridRun.py --list                    # see them all
python3 Submit_GridRun.py --only fid CE_alpha05     # run a subset
python3 Submit_GridRun.py                           # run all of them
```

Each variation gets its own output directory, `<RUN_NAME>_<simname>`, with its
own copy of `compasConfig.yaml` patched with those flags — and its own array
job, so **splitting across tasks keeps working**, which is exactly what ranges
and sets break.

The reason to prefer this over sets: the file records only what *differs*
per variation, so you can see the physics difference at a glance instead of
re-reading a full option list. It doubles as a record of what a paper's grid
actually was.

Overrides are validated against `compasConfig.yaml` before anything is
submitted — a flag that does not exist stops the run rather than being silently
ignored, which would otherwise give you fiducial physics under a variation's
name. The same file and the same mechanism drive
[Mode 2](03_stroopwafel_runs.md#physics-variations); see there for the full
list of shipped variations and for `BASE_OVERRIDES`.


### 2b. Ranges and sets (for parameter scans)

To scan parameters rather than sample them, COMPAS has the option to build the grid on the
command line — no file involved. But the behaviour is a bit annoying at times (see below), so this is not always preferred for grids of variations.

```bash
--metallicity r[0.0001,10,0.0013]        # range: start, count, increment -> 10 values
--common-envelope-alpha s[0.1,0.5,1.0]   # set: 3 explicit values
```

`r[...]`/`range[...]` works for any numeric option; `s[...]`/`set[...]` works for
any type, including strings such as `s[THERMAL,FLAT,ZERO]`. No spaces inside the
brackets. **They multiply**: the two lines above together evolve 10 × 3 = 30
systems.

> ### Ranges and sets do not combine with array splitting
> When any range or set is present, **COMPAS ignores `--number-of-systems`** —
> the count comes from the grid instead. Verified on v03.29.05:
> `-n 1000 --metallicity [0.0001,5,0.0013]` evolves 5 systems, not 1000.
>
> Each array task passes `--number-of-systems <slice>`, so that value would be
> dropped and every task would evolve the *whole* grid — `N_BATCHES` duplicate
> copies, differing only by seed, with no error anywhere. `Submit_GridRun.py`
> detects ranges/sets in your config and refuses to submit.

Because of that, ranges and sets are a poor fit for a grid of **physics
variations**. Use the variations file instead — see above.



### 3. Grid files (when you want something specific per binary)

A grid file gives one line of options per binary. Anything not on a line falls
back to defaults (as in your `compasConfig.yaml`).

```
--random-seed 0 --initial-mass-1 10.78 --initial-mass-2 0.37 --metallicity 0.000357
--random-seed 1 --initial-mass-1 13.60 --initial-mass-2 9.25 --metallicity 0.000164
```

Options on a grid line **override** the command line for that system, which is
why a per-line `--random-seed` wins over the one the job script passes.

Use one when you need explicit per-system values that no distribution describes:

- **re-running specific systems** from a previous run, with detailed output on
  or different physics, without re-evolving the whole population
- initial conditions from an observed sample or another code
- correlations COMPAS has no option for, e.g. a metallicity-dependent IMF

```bash
# re-run the systems that formed double compact objects
python3 masterfolder/MainRun/make_grid_file.py \
    --from-h5 <run_dir>/MainRun/COMPAS_Output.h5 --dco-only -o BSE_grid.txt

# or just a few interesting SEEDs
python3 masterfolder/MainRun/make_grid_file.py \
    --from-h5 COMPAS_Output.h5 --seeds 12,57,993 -o rerun.txt
```

That reproduces masses, separation, metallicity, eccentricity **and the kick
random numbers**, so the supernovae come out the same. Drop the kick columns
from `GRID_COLUMNS` if you want kicks re-drawn.

For genuinely custom sampling, edit `custom_line()` in that script and use
`--custom`. Then point the driver at the file:

```python
GRID_FILE = "BSE_grid.txt"
```

The number of lines then determines the run size and `N_BINARIES` is ignored
(the driver says so when this happens).

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

- [03 Stroopwafel runs](03_stroopwafel_runs.md) — Mode 2: adaptive importance sampling
- [04 Post-processing](04_postprocessing.md) — merging, weights, cosmic integration
- [05 Clusters](05_clusters.md) — adding a new machine
- [06 Troubleshooting](06_troubleshooting.md) — when things go wrong
