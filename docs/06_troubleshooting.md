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

### A small run produces an absurdly large merged file

Not COMPAS -- the merge step. `h5copy` allocates one full HDF5 chunk for **every
dataset**, and COMPAS output has hundreds of them. At h5copy's default chunk size
of 100000 entries, a 200-system run merged to **706 MB**; the same data with a
chunk size of 1000 is **778 KB**. Nearly a thousand-fold difference, all empty
chunks.

The drivers now scale the chunk size to the run (`h5_chunk_size()`, roughly
N/100 clamped to 1000-100000) and pass it to h5copy with `-c`. If you invoke
h5copy by hand, pass `-c` yourself:

```bash
python3 h5copy.py <run_dir>/MainRun/ -r 2 -c 1000 -o merged.h5
```

Too small is not free either -- many tiny chunks slow down reads -- so scale it
with the run rather than always using 1000.

Realistic sizes after this fix: roughly **4 KB per system**, so ~4 GB for 1e6
systems and ~20 GB for 5e6.

### `check_status.py` says CORRUPT: more rows than unique SEEDs

```
Data integrity:
  CORRUPT  400 rows but only 200 unique SEEDs (each appears ~2x)
```

Post-processing merged a **previous merged file** back in. `h5copy` scans
`MainRun/` two levels deep and the merged file lives in `MainRun/`, so a leftover
`COMPAS_Output.h5` becomes an input as well as the output. Every system is then
counted twice — silently, with no error, doubling every rate you compute.

The shipped `COMPAS_PP.sbatch` deletes previous merged files before merging, so
this should not happen. If you see it, you are probably running an older
generated job script, or invoked `h5copy.py` by hand. To fix:

```bash
rm -f <run_dir>/MainRun/COMPAS_Output.h5 <run_dir>/MainRun/COMPAS_Output_wWeights.h5
sbatch <run_dir>/postProcessing/COMPAS_PP.sbatch
```

Worth knowing because resubmitting a few failed array tasks and re-merging is a
completely normal workflow — which is exactly when this used to bite.

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

### Why `output_root` cannot be `/vol/astro8`

Every route from a compute node to astro8 is closed:

| route | status |
|---|---|
| write the NFS mount directly | **read-only export** to compute nodes |
| `scp` to `astro8-srv` (as the cluster docs suggest) | server refuses user logins |
| `ssh`/`scp` back to the coma login node | **coma requires 2FA** — `publickey` gets "partial success", then `keyboard-interactive` is demanded |

That last one is the decisive constraint: **no batch job on coma can ever ssh or
scp anywhere**, no matter how you set up keys. So automated stage-out to astro8
is impossible, and `$HOME` is the only shared filesystem the nodes can write.

### The two-tier workflow

Jobs write to `output_root` (`$HOME`, ~25 GB), and you move finished runs to
`archive_root` (astro8) yourself, **from the login node**:

```bash
python3 archive_run.py --list          # what is using space, and is it finished?
python3 archive_run.py my_run          # move it to astro8
python3 archive_run.py --all           # move everything that has finished
```

Whatever nesting you use under `output_root` is mirrored into `archive_root`,
so the two layouts always match:

```
$HOME/CompasOutput/v03.29.05/N1e6_fid
   ->  /vol/astro8/.../CompasOutput/v03.29.05/N1e6_fid
```

That version level comes from `RUN_SUBDIR` in the drivers. Name a run by its
full relative path or by its bare name when unambiguous, and use `--from` /
`--to` to override the profile's roots entirely.

It refuses to touch a run that still has jobs in the queue or has no merged
output yet, so you cannot archive a run out from under itself.

Keep an eye on `--list`. At ~4 KB/system, `$HOME` holds a few 1e6-system runs
comfortably, but a single 5e6-system run is ~20 GB and a 19-variation sweep is
hundreds of GB. Archive between runs, and for a big sweep archive as you go.

### This is really a sysadmin question

The evidence, if you need to make the case:

- `/vol/astro8` is read-only from **every** compute node -- verified on 17 nodes
  spanning the general pool and the `proj_bhc`, `proj_stev` and `proj_statm`
  ranges. It is not a node-selection problem.
- The mount reports `rw` in `mount`, but writes return `EROFS`, so the
  restriction is in the **NFS export on astro8-srv**, not in the client mount.
- The login node writes astro8 fine, so it is per-client, not a permission
  problem on your directories.
- `/scratch` (3.4 TB, node-local) and `$HOME` (25 GB, shared) are the only
  writable filesystems on a compute node.

Ask whether `/vol/astro8` can be exported read-write to the compute nodes, the
way `/vol/astro2` presumably was before it was phased out. If it can, point
`output_root` straight at astro8 and `archive_run.py` becomes unnecessary.

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
