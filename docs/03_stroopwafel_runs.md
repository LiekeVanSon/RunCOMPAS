# 03 — Stroopwafel runs (Mode 2)

## Why

Gravitational-wave sources are rare, so to avoid running a ridiculously large simulation, or your rate estimates to be dominated by Poisson noise, we use "Adaptive importance sampling". 
Specifically we use the Stroopwafel algorythm by [Broekgaarden 2019](https://arxiv.org/abs/1905.00910), and updated by lokiy: [Stroopwafel](https://github.com/lokiysh/stroopwafel)

**adaptive importance sampling**: finds where in ZAMS parameter space the systems you care about
come from, and concentrates its samples there. For rare outcomes this is worth orders of magnitude in effective sample size for the same CPU time.

It runs in four phases:

| phase | what happens |
|---|---|
| **explore** | plain Monte Carlo, to locate the hits |
| **adapt** | fit Gaussian instrumental distributions around those hits |
| **refine** | draw the remaining samples from those distributions |
| **postprocess** | compute the `mixture_weight` that undoes the sampling bias |

> ### The catch
> Because samples are no longer drawn from the birth distribution, **every system
> carries a `mixture_weight`**, and any population statistic must use it. A
> histogram of raw counts from an AIS run is meaningless. Weights are attached
> by `append_weights.py` during post-processing, giving
> `COMPAS_Output_wWeights.h5` — that is the file you analyse.

---

## Running one

Requires stroopwafel installed — see [01_setup.md §3](01_setup.md#3-stroopwafel-only-for-mode-2).

Edit the `SETTINGS` block at the top of `Submit_StroopwafelRun.py`:

```python
RUN_NAME     = "N1e6_DNS"
NUM_SYSTEMS  = int(1e6)   # total across all four phases
NUM_CORES    = 20         # COMPAS processes in parallel inside the one job
NUM_PER_CORE = int(1e4)   # systems per batch
SYS_INT      = "DNS"      # what counts as a hit
```

```bash
python3 Submit_StroopwafelRun.py --dry-run
python3 Submit_StroopwafelRun.py
```

**This is one long job, not an array.** Stroopwafel is adaptive — it must see the
explore results before it knows where to draw the refine samples — so it runs as
a single job that itself spawns `NUM_CORES` COMPAS processes. The job script
therefore requests `NUM_CORES + 1` tasks (the workers plus stroopwafel itself).

### Timing guide

Measured on Flatiron's `cca` partition:

| systems | cores | per core | MainRun | postProcessing | CosmicIntegration |
|---|---|---|---|---|---|
| 1e6 | 40 | 1e4 | ~75 min | 11 min | 1 min |
| 5e6 | 40 | 1e4 | ~7 h | 40 min | 4 min |
| 1e7 | 100 | 1.5e4 | ~8 h | 1 h | 10 min |

Set `MAINRUN_WALLTIME` generously — losing an 8-hour adaptive run to the walltime
is painful, and there is no partial-restart.

---

## Choosing what is "interesting"

`SYS_INT` selects one of the masks defined in
`masterfolder/MainRun/stroopwafel_interface.py`:

| value | hits |
|---|---|
| `WDWD` | any double white dwarf |
| `MassiveWDWD` | COWD > 0.6 M⊙ paired with a WD or NS |
| `BBH` | binary black holes |
| `DNS` | double neutron stars |
| `BHNS` | black hole–neutron star |
| `MassiveWDWD_NSNS` | either of MassiveWDWD or DNS |
| `AnyDCO` | any double compact object |

All of these additionally require the binary to merge within a Hubble time.

To add your own, write a mask function and register it in `SYSTEMS_OF_INTEREST`:

```python
def _mask_my_systems(st1, st2, m1, m2):
    # stellar types: 10 HeWD, 11 COWD, 12 ONeWD, 13 NS, 14 BH
    return np.logical_and(st1 == 14, m1 > 30)      # heavy BH primaries

SYSTEMS_OF_INTEREST = {
    ...,
    'HeavyBH': _mask_my_systems,
}
```

Then set `SYS_INT = "HeavyBH"`.

> **Don't make the target too rare.** If the explore phase finds almost no hits,
> stroopwafel has nothing to adapt to and you get a slow Monte Carlo run.
> Combining two very rare populations is especially fragile — pushing the
> `MassiveWDWD` mass cut from 0.6 to 0.8 M⊙ makes it hard to target massive WDWDs
> and DNSs at the same time.

You can also change **what is sampled** in `create_dimensions()` (the default is
M1 with a Kroupa IMF, uniform mass ratio, log-uniform separation; metallicity and
eccentricity are set in `update_properties()`).

---

## Physics variations

The real workhorse: run the same setup many times with different COMPAS physics.
Variations live in `masterfolder/simulation_variations.json`:

```json
[
  { "simname": "fid",         "overrides": {} },
  { "simname": "CE_alpha05",  "overrides": {"--common-envelope-alpha": 0.5} },
  { "simname": "AM_circumbinary",
    "overrides": {"--mass-transfer-angular-momentum-loss-prescription": "CIRCUMBINARY"} }
]
```

Each entry gets its own output directory, `<RUN_NAME>_<simname>`, with its own
copy of `compasConfig.yaml` patched with those overrides. The shipped file has
19 variations covering angular momentum loss, common envelope, mass transfer and
remnant-mass prescriptions.

```bash
python3 Submit_StroopwafelRun.py --list                    # see them all
python3 Submit_StroopwafelRun.py --only fid CE_alpha05     # run a subset
python3 Submit_StroopwafelRun.py                           # run all of them
```

To run a single unvaried simulation, set `VARIATIONS_FILE = None`.

### Overrides are validated

If an option in your variations file is **not present** in `compasConfig.yaml`,
the driver stops with an error before submitting anything. This is deliberate —
it catches typos, and it catches options that were renamed between COMPAS
versions. For example, in v03.29.05:

```
--mass-transfer-jloss-macleod-linear-fraction-degen   (old)
--mass-transfer-jloss-linear-fraction-degen           (new)
```

Silently ignoring the unknown option would have run the fiducial physics under a
variation's name — a bug you might not notice for months.

To check an option exists in your build:

```bash
$COMPAS_ROOT_DIR/src/COMPAS --help | grep <option>
```

### BASE_OVERRIDES

Applied to every variation, set at the top of the driver:

```python
BASE_OVERRIDES = {
    "--add-options-to-sysparms": "NEVER",
    "--logfile-definitions": "COMPAS_Output_Definitions.txt",
    "--logfile-type": "HDF5",
}
```

`--add-options-to-sysparms: NEVER` matters. Stroopwafel writes one grid line per
system, and the `GRID` default would then append a copy of every program option
to the SysParms table for every system — enormous files, for no benefit.

---

## Output

```
<run_dir>/MainRun/
├── batch_0/batch_0.h5      one per stroopwafel batch
├── batch_1/batch_1.h5
├── samples.csv             every sampled system + its mixture_weight
├── distributions.csv       the adapted instrumental distributions
└── log.txt                 stroopwafel's own log
```

After post-processing you get `COMPAS_Output_wWeights.h5`.
See [04_postprocessing.md](04_postprocessing.md).

---

## Previous

- [00 README](../README.md) — overview, repository map, quickstart
- [01 Setup](01_setup.md) — COMPAS, your python environment, first test run
- [02 Grid runs](02_grid_runs.md) — Mode 1: large runs via a SLURM array

## Next

- [04 Post-processing](04_postprocessing.md) — merging, weights, cosmic integration
- [05 Clusters](05_clusters.md) — adding a new machine
- [06 Troubleshooting](06_troubleshooting.md) — when things go wrong
