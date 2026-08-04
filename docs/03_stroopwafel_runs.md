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

## Which stroopwafel?

**Use a git clone of stroopwafel, not the PyPI package, and do not use the
interface that ships with COMPAS.** Both were tested on 2026-08-04 against
COMPAS v03.29.05:

| | result |
|---|---|
| clone + `masterfolder/MainRun/stroopwafel_interface.py` | **works** — explore, adapt, refine, weights |
| `pip install stroopwafel` + `$COMPAS_ROOT_DIR/compas_python_utils/preprocessing/stroopwafelInterface.py` | **does not run** |

The COMPAS-shipped interface fails in four independent ways:

1. **It cannot start.** `main()` reads module-level `debug`, `mc_only`,
   `userunSubmit`, … and also assigns them, so Python treats them as local:
   `UnboundLocalError`. It was refactored into a function without `global` and
   evidently not run since.
2. **Its grid files are not COMPAS input.** Its dimensions are named `Mass_1`,
   `Separation`, `Mass_2`. Stroopwafel writes dimension names verbatim, so the
   grid reads `Mass_1 33.4 Separation 5.7 ...` — none of which are COMPAS
   options. COMPAS rejects every line and the run finishes in 0 seconds having
   evolved nothing. Dimensions must be named `--initial-mass-1`,
   `--semi-major-axis`, `--initial-mass-2`.
3. **It reads CSV** (`BSE_System_Parameters.csv`), while COMPAS now defaults to
   HDF5.
4. **It has no AIS.** `mc_only = True` is hardcoded, commented "currently not
   implemented, leave set to True" — so even fully repaired it is plain Monte
   Carlo, which is the one thing stroopwafel exists to improve on.

Points 1–3 are fixable; point 4 means it would still not do what you want. The
clone at `/vol/astro8/lvanson/lvanson/Programs/stroopwafel` (branch
`updates_and_comments_lieke`) carries the fixes that matter — chiefly creating
the batch folder before moving the grid into it, tolerating
`is_hit` written as a float, and not falling over when a phase finds zero hits.

---

## Running one

Requires stroopwafel — see [01_setup.md §3](01_setup.md#3-stroopwafel-only-for-mode-2).

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

### Does it actually help? (measured)

A 4000-system BBH run on coma, 8 cores, 250 per batch:

| phase | hits | tried | hit rate |
|---|---|---|---|
| explore (plain Monte Carlo) | 20 | 3000 | 0.67% |
| refine (adapted) | 203 | 1000 | **20.3%** |

Getting 203 BBH mergers by plain Monte Carlo would have taken ~30 000 systems;
AIS got them from 1000. That is the ~30x you are buying, and it grows the rarer
your target is.

Sanity check on the weights: `mixture_weight` averaged **0.998** over all 4000
samples. It should average to 1 — if yours does not, something is wrong with the
sampling, and any rate you compute from it will be wrong too.

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

## Performance: where the single core goes

A stroopwafel run alternates between COMPAS batches (parallel, fast) and
stroopwafel's own bookkeeping (**one core, and it does not parallelise**). On a
large run the second part dominates, which is why the job looks idle with one
core pinned. Profiled on coma, COMPAS entirely out of the loop:

| step | when | cost |
|---|---|---|
| prior rejection rate | once, before explore | fixed, `TOTAL_REJECTION_SAMPLES` = 1e6 samples |
| **distribution rejection rate** | after adapt | **linear in the number of hits** |
| **mixture weights** | postprocess | **N_samples x N_gaussians** |

### What was fixed

`rejected_systems()` and `update_properties()` in
`masterfolder/MainRun/stroopwafel_interface.py` are called once per sampled
system -- 1e6 times for the prior rejection rate, and
`REJECTION_SAMPLES_PER_BATCH` times *for every adapted Gaussian*. They used to
loop in python, calling `utils.get_zams_radius` twice per system; that single
function was 56% of total runtime, and it re-derives a 9x5 metallicity
polynomial on every call.

Both are now array-at-a-time. Measured on the same profile:

| | before | after |
|---|---|---|
| prior rejection rate (1e6 samples) | 35.5 s | 11.0 s |
| distribution rejection rate (200 hits) | 40.1 s | 14.2 s |
| **total pure-stroopwafel** | **77.3 s** | **26.9 s** |

The maths is unchanged, and that is checked rather than asserted: over 200 000
random systems every rejection decision is identical, and the radii agree to
within 1 ULP (the last-bit difference between numpy's `**` and C's `pow`).
The check is worth keeping if you touch this code again.

### What is still slow, and what to do about it

**Distribution rejection rate — ~71 ms per hit.** Linear in the number of hits,
so 5000 hits is ~6 minutes (it was ~17). Annoying, not fatal. The loop over
Gaussians is embarrassingly parallel if it ever becomes the problem.

**Mixture weights — ~400 ns per (sample x Gaussian) pair.** This is the one to
watch, because it is a product, not a sum:

| samples | Gaussians | pairs | time |
|---|---|---|---|
| 1e5 | 500 | 5e7 | ~20 s |
| 1e6 | 1000 | 1e9 | ~7 min |
| 5e6 | 3000 | 1.5e10 | ~1.7 h |
| 1e7 | 5000 | 5e10 | **~6 h** |

If your postprocess step seems to hang at the end of a big run, this is why.
Two ways out, in order of effort:

1. **The covariances are diagonal** (`np.diagflat(sigma**2)` in
   `distributions.py`), but `calculate_probability_of_locations_from_distribution`
   calls the general `scipy.stats.multivariate_normal.pdf`, which does a
   Cholesky solve per call. For a diagonal covariance the pdf is just a product
   of 1-D Gaussians and can be evaluated directly on the whole array -- a large
   constant-factor win with no change to the maths.
2. **Parallelise over Gaussians.** The weights are a sum of independent pdf
   contributions, so splitting the Gaussian list across processes and summing is
   exact. Do it after (1), and only if you still need to.

Neither is done yet. If you hit this, measure first --
`~/sw_profile/profile_sw.py` isolates each step with no COMPAS in the loop.

---

## Choosing what is "interesting"

`SYS_INT` selects one of the masks defined in
`masterfolder/MainRun/stroopwafel_interface.py`:

| value | hits |
|---|---|
| `DNS` | double neutron stars |
| `BBH` | binary black holes |
| `BHNS` | black hole–neutron star |
| `AnyDCO` | any double compact object, white dwarfs included |

All of these additionally require the binary to merge within a Hubble time.

The file also carries three white-dwarf masks (`WDWD`, `MassiveWDWD`,
`MassiveWDWD_NSNS`) under an **EXAMPLE** heading. They are not defaults — they
are worked examples of the two things you are most likely to want, a mass cut
and a union of two populations. Delete them if they are not yours.

> **If you target white dwarfs, change `create_dimensions()` too.** The default
> samples `--initial-mass-1` from 5 M⊙, which is right for compact objects and
> useless for WD progenitors — drop it to ~1 M⊙ or you will never sample them.

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
