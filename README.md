# RunCOMPAS

Ready-to-use scripts for running [COMPAS](https://compas.readthedocs.io) population
synthesis simulations on a SLURM cluster, in two ways:

| | what it does | when to use it |
|---|---|---|
| **[Mode 1: grid run](docs/02_grid_runs.md)** | evolves N systems split across a SLURM **array job** | you want a big, straightforward population |
| **[Mode 2: stroopwafel](docs/03_stroopwafel_runs.md)** | **adaptive importance sampling** — spends samples where the rare systems are | your systems of interest are rare (DNS, massive WDWD, ...) |

Both modes then run the same two optional stages:
**postProcessing** (merge the output into one file) and
**CosmicIntegration** (turn the population into merger rates).

---

## Quickstart

```bash
git clone <this repo> && cd RunCOMPAS

# 1. Make your own python environment
python3 -m venv ~/venvs/runcompas_venv
source ~/venvs/runcompas_venv/bin/activate
pip install -r requirements.txt

# 2. Point your cluster profile at it, and check COMPAS is built
#    -> clusters/<yourcluster>.yaml, and docs/01_setup.md

# 3. Edit the SETTINGS block at the top of a driver script
vim Submit_GridRun.py          # or Submit_StroopwafelRun.py

# 4. Look before you leap: writes every directory and job script, submits nothing
python Submit_GridRun.py --dry-run

# 5. Submit for real
python Submit_GridRun.py

# 6. Check on it
python <run_dir>/postProcessing/check_status.py <run_dir>
```

**Always `--dry-run` first.** It creates the full run directory and all the
generated `.sbatch` scripts so you can read exactly what would be submitted.

---

## What you get

A run produces one self-contained directory per simulation:

```
<output_root>/<RUN_NAME>/
├── MainRun/                    COMPAS output
│   ├── batch_0/ batch_1/ ...     one per array task (or per stroopwafel batch)
│   ├── compasConfig.yaml         the exact physics settings this run used
│   ├── COMPAS_grid.sbatch        the exact job script that was submitted
│   ├── COMPAS_Output.h5          <- merged, after postProcessing
│   └── samples.csv               <- stroopwafel weights (mode 2 only)
├── postProcessing/
├── CosmicIntegration/
├── logs/                       slurm .out / .err for every job
├── job_ids.txt                 what was submitted
└── Submit_GridRun.py           a copy of the driver, so the run is reproducible
```

Everything needed to understand or reproduce the run ends up next to the data.

---

## Repository map

```
RunCOMPAS/
├── Submit_GridRun.py           MODE 1 driver  <- edit the SETTINGS block, run
├── Submit_StroopwafelRun.py    MODE 2 driver  <- edit the SETTINGS block, run
├── requirements.txt            python dependencies -> your venv
├── archive_run.py              move finished runs to long-term storage
├── benchmark_run.py            what did a run actually cost?
│
├── clusters/                   machine-specific settings
│   ├── coma.yaml                 partitions, module loads, paths
│   ├── rusty.yaml
│   ├── TEMPLATE.yaml             copy this to add your cluster
│   └── BENCHMARKS.md             measured cost of real runs
│
├── masterfolder/               copied into every run directory
│   ├── compasConfig.yaml         COMPAS physics settings  <- your science lives here
│   ├── simulation_variations.json  physics variations to sweep over
│   ├── MainRun/
│   │   ├── runSubmit.py            yaml -> COMPAS command line
│   │   ├── stroopwafel_interface.py  the AIS run (edit what counts as a "hit")
│   │   └── make_grid_file.py       generate a BSE_grid.txt
│   ├── postProcessing/
│   │   ├── h5copy.py               merge per-batch h5 files
│   │   ├── append_weights.py       attach stroopwafel weights
│   │   └── check_status.py         did my run work?
│   ├── CosmicIntegration/
│   └── slurms/                   job script templates (@@PLACEHOLDER@@ filled at submit)
│
├── docs/                       ← start here
└── legacy/                     the original project-specific scripts, for reference
```

The two driver scripts deliberately **duplicate** their submission machinery
rather than sharing a module, so that each one is self-contained: you can copy a
driver plus `masterfolder/` and `clusters/` into a project folder and it works,
with no package to install.

---

## Documentation

| | |
|---|---|
| **[01 Setup](docs/01_setup.md)** | build COMPAS, python packages, stroopwafel, your first test run |
| **[02 Grid runs](docs/02_grid_runs.md)** | Mode 1: SLURM arrays, grid files, choosing batch sizes |
| **[03 Stroopwafel runs](docs/03_stroopwafel_runs.md)** | Mode 2: AIS, weights, defining "interesting", variations |
| **[04 Post-processing](docs/04_postprocessing.md)** | merging output, weights, cosmic integration, reading the h5 |
| **[05 Clusters](docs/05_clusters.md)** | adding a new machine |
| **[06 Troubleshooting](docs/06_troubleshooting.md)** | when things go wrong (read this before panicking) |

## The one thing to remember

For **stroopwafel runs, every system carries a `mixture_weight`** and you must use
it in any population statistic. Grid runs sample from the birth distribution, so they need no weights.
See [docs/04_postprocessing.md](docs/04_postprocessing.md).

---

## Next

- [01 Setup](docs/01_setup.md) — COMPAS, your python environment, first test run
