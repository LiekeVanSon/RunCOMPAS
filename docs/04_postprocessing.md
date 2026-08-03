# 04 — Post-processing and cosmic integration

Both run modes leave you with one output file per batch. These two stages turn
that into something you can analyse.

```
MainRun/batch_*/  ──h5copy──▶  COMPAS_Output.h5  ──append_weights──▶  COMPAS_Output_wWeights.h5
                                                       (AIS only)              │
                                                                               ▼
                                                                    FastCosmicIntegration
                                                                       (merger rates)
```

Both stages are submitted automatically by the driver scripts, chained with
`--dependency=afterok` so each waits for the previous one to *succeed*. If the
MainRun fails, post-processing never runs (and `--kill-on-invalid-dep=yes` means
it is cancelled rather than left pending forever).

---

## Stage 1: merging (`h5copy.py`)

Walks `MainRun/` two directory levels deep and concatenates every matching
dataset from each `batch_*/` into one file:

```bash
python3 h5copy.py <run_dir>/MainRun/ -r 2 -o <run_dir>/MainRun/COMPAS_Output.h5
```

Controlled by `RUN_POSTPROCESSING` in the driver. Give it enough memory —
`PP_MEMORY = "16G"` is a sane starting point, more for runs above ~5e6 systems.

To run it by hand later:

```bash
cd <run_dir>/postProcessing
sbatch COMPAS_PP.sbatch          # the exact script that was generated
```

---

## Stage 2: weights (`append_weights.py`) — stroopwafel only

Sorts the merged file by SEED, matches it against stroopwafel's `samples.csv`,
and adds a `mixture_weight` column to `BSE_System_Parameters` and
`BSE_Double_Compact_Objects`. Output: `COMPAS_Output_wWeights.h5`.

Grid runs skip this — they sample from the birth distribution, so every system
already has an implicit weight of 1.

If it raises `sorted SEEDs dont match up!!`, the COMPAS output and the stroopwafel
sample file disagree about which systems exist. That usually means some batches
failed and are missing from the merge — check `check_status.py` first.

### Using the weights

This is the part people get wrong. **Every** population statistic from an AIS run
must be weighted:

```python
import h5py, numpy as np

with h5py.File('COMPAS_Output_wWeights.h5', 'r') as f:
    dco = f['BSE_Double_Compact_Objects']
    m1 = dco['Mass(1)'][:]
    w  = dco['mixture_weight'][:]

# WRONG - counts raw samples, which are not drawn from the birth distribution
counts, edges = np.histogram(m1, bins=50)

# RIGHT
counts, edges = np.histogram(m1, bins=50, weights=w)
```

The same applies to means, rates, and fractions. An unweighted mean over AIS
samples is not the population mean.

---

## Stage 3: cosmic integration (optional)

Convolves the population with a metallicity-specific star formation history to
get intrinsic and detectable merger rates as a function of redshift, applying
detector selection effects. Off by default:

```python
RUN_COSMIC_INTEGRATION = True
```

Arguments are set with `CI_ARGS` in the driver. The defaults are the
van Son et al. (2022) best-fit SFH:

```
--mu0 0.025 --muz -0.049 --sigma0 1.129 --sigmaz 0.048 --alpha -1.79
--aSF 0.017 --bSF 1.487 --cSF 4.442 --dSF 5.886
--sens O3 --m1min 5.0 --dco_type all --maxzdet 8 --zstep 0.01
```

Useful knobs:

| flag | meaning |
|---|---|
| `--dco_type` | `all`, `BBH`, `BHNS`, `BNS`, `WDWD` |
| `--sens` | detector sensitivity, e.g. `O1`, `O3`, `design` |
| `--m1min` | minimum primary mass to include |
| `--maxzdet` | maximum redshift for the detectable rate |
| `--weight` | the weights column (`mixture_weight` for AIS; dropped for grid runs) |

Results are written back into the input h5 as new groups.

---

## Reading the output

```python
import h5py
with h5py.File('COMPAS_Output.h5', 'r') as f:
    print(list(f.keys()))
```

```
BSE_System_Parameters       one row per binary: ZAMS masses, separation, Z, SEED
BSE_Double_Compact_Objects  one row per DCO formed: masses, stellar types, merger time
BSE_Common_Envelopes        one row per CE event
BSE_Supernovae              one row per supernova
BSE_RLOF                    Roche-lobe overflow episodes
Run_Details                 the options this run used
```

`SEED` is the key that links them: a binary in `System_Parameters` has the same
SEED as its entry in `Double_Compact_Objects`, if it made one.

Which columns appear is controlled by
`masterfolder/MainRun/COMPAS_Output_Definitions.txt` — the shipped version adds
kick magnitudes, mass transfer histories and CE flags to the defaults, so runs
can be reproduced from their output.

To check what physics a file was produced with:

```python
with h5py.File('COMPAS_Output.h5','r') as f:
    print(list(f['Run_Details'].keys()))
```

You also always have the exact `compasConfig.yaml` and job scripts sitting in the
run directory next to the data.

---

## Previous

- [02 Grid runs](02_grid_runs.md) — Mode 1: large runs via a SLURM array
- [03 Stroopwafel runs](03_stroopwafel_runs.md) — Mode 2: adaptive importance sampling

## Next

- [05 Clusters](05_clusters.md) — adding a new machine
- [06 Troubleshooting](06_troubleshooting.md) — when things go wrong
