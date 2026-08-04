# Benchmarks

Measured cost of real runs, per machine. The point is to answer "how long will
this take, how much will it cost me, and what should I request?" without
guessing — and to notice when a COMPAS upgrade or a config change makes things
suddenly slower.

**Please add a row when you finish a production run.** One command:

```bash
python3 benchmark_run.py <run_dir>          # readable summary
python3 benchmark_run.py <run_dir> --row    # markdown row to paste below
```

It reads slurm accounting (`sacct`) and the output directory, so the numbers are
what actually happened, not what was requested.

---

## coma

COMPAS v03.29.05 · `normal` partition · 1 core per array task · output on `$HOME`,
simulation I/O on node-local `/scratch`.

| run | systems | tasks | CPU-h | wall-h | peak mem | run dir | merged h5 | notes |
|---|---|---|---|---|---|---|---|---|
| `N1e6_test_fid` | 1e6 | 100 | ~7.8 | ~0.35 | 307 MB | ~0.6 GB | — | grid mode, fiducial, no grid file. See below. |
| `N200_test` | 200 | 4 | <0.01 | <0.01 | ~300 MB | 50 MB | 0.8 MB | smoke test |

### `N1e6_test_fid` — the useful one

Fiducial grid run, 10⁶ systems, `N_BATCHES = 100`, `MAX_CONCURRENT = 25`.

**Measured per task** (over the first 25 completed tasks, on `coma35`/`coma39`):

| | |
|---|---|
| systems per task | 10 000 |
| wallclock per task | **282 s mean**, 290 s longest |
| CPU per task | ~4.5 min (≈97% of wallclock — single core, no idle) |
| peak memory per task | **307 MB** |
| output per task | **5.6 MB** |

**Projected totals** (100 tasks; replace with `benchmark_run.py --row` once the
run finishes):

| | |
|---|---|
| total CPU | ~7.8 CPU-hours |
| wallclock | ~19 min of compute — 4 waves of 25 tasks — plus queue time |
| throughput | **~35 systems / s / core**, i.e. ~28 ms per binary |
| per-batch output | ~560 MB total |

---

## What these numbers tell you

### Ask for far less memory

`MAINRUN_MEMORY = "4G"` but the tasks peaked at **307 MB** — a 13× over-request.
Slurm reserves what you ask for, so an over-request means your jobs sit in the
queue waiting for memory nobody will use. **`1G` is plenty for a fiducial grid
run**, and will start sooner.

Check yours after any run:

```bash
python3 benchmark_run.py <run_dir>    # prints "over-requested Nx" when it matters
```

Stroopwafel is different — it forks many COMPAS workers in one job, so its
memory scales with `NUM_CORES`. Benchmark it separately before trusting a number.

### Sizing batches

At ~35 systems/s/core, a task takes roughly

```
wallclock per task  ≈  (N_BINARIES / N_BATCHES) / 35   seconds
```

Aiming at the 1–4 hour target from
[02_grid_runs.md](../docs/02_grid_runs.md#choosing-n_batches-and-walltime) means
**1.3 × 10⁵ – 5 × 10⁵ systems per task**. The 10⁴ used here gives ~5 min tasks,
which is finer-grained than necessary — fine for a test, but for a big run
prefer fewer, longer tasks so the queue and the merge step have less to do.

Wallclock for the whole array is roughly

```
(N_BATCHES / MAX_CONCURRENT) x (time per task)   + queue time
```

so `MAX_CONCURRENT` is what actually sets your turnaround, up to what the
partition will give you.

### Disk

~5.6 MB per 10⁴ systems in the per-batch files, so **~0.6 KB per system**.
For 10⁶ systems that is ~0.6 GB of batches, plus the merged file.

Note the merged file carries HDF5 chunk overhead of roughly
`n_datasets x chunk_size x 8 bytes` — with ~840 datasets and the chunk size the
driver picks, tens of MB. Negligible for a large run, dominant for a small one;
see [06_troubleshooting.md](../docs/06_troubleshooting.md#a-small-run-produces-an-absurdly-large-merged-file).

These figures are for the **fiducial** physics with default logging. Turning on
`--detailed-output`, or logging RLOF/CE/SNe you do not need, changes them a lot.

---

## rusty (Flatiron)

Historical, from the WDWD/NSNS project — stroopwafel mode on the `cca`
partition, not re-measured with this repo.

| systems | cores | per core | MainRun | postProcessing | CosmicIntegration |
|---|---|---|---|---|---|
| 1e6 | 40 | 1e4 | ~75 min | 11 min | 1 min |
| 5e6 | 40 | 1e4 | ~7 h | 40 min | 4 min |
| 1e7 | 100 | 1.5e4 | ~8 h | 1 h | 10 min |

---

## Adding a machine

Copy the `coma` section, run a 10⁶-system fiducial grid, and record it. Even one
row is worth having: it tells the next person whether an overnight job is
plausible, and it is the only way to notice a regression.
