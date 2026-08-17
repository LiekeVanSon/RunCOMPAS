# tools/

Diagnostics for the stroopwafel path. Not needed to run a simulation — these
exist so that a performance claim or an optimisation can be **checked** rather
than believed.

Run them from the repo root; paths are resolved from your cluster profile, so
no environment setup is needed:

```bash
python3 tools/verify_math.py
```

Override with `STROOPWAFEL_PATH` / `INTERFACE_DIR` to test a different clone or
a modified interface.

| script | what it does |
|---|---|
| `verify_math.py` | `rejected_systems()` and the ZAMS/Roche radii in `masterfolder/MainRun/stroopwafel_interface.py`, against the original per-system loop |
| `verify_utils.py` | the vectorised `get_zams_radius` / `calculate_roche_lobe_radius` in stroopwafel, against the original scalar formulas (reproduced verbatim in the script) |
| `verify_pdf.py` | the diagonal-covariance fast path in `calculate_probability_of_locations_from_distribution`, against the original `scipy.stats.multivariate_normal.pdf` call |
| `profile_sw.py` | times each pure-stroopwafel step with COMPAS taken out of the loop, and can `--profile` the worst one |
| `scaling.py` | how the two O(N) steps grow with number of hits and number of samples |

## Why these matter

Each optimisation in the stroopwafel path was made because one step dominated
the single core between COMPAS batches. Every one of them touches code that
decides which systems are sampled or what weight they carry, so "it is faster"
is only half the claim — the other half is that nothing moved.

The verifiers compare against the **original implementation reproduced verbatim
inside the script**, not against a description of it, so they stay honest if the
library changes underneath.

What they check, and what they found:

- **Decisions, not just numbers.** `verify_math.py` compares every per-system
  rejection flag over 200 000 random systems, not merely the total count.
- **Fallbacks stay exact.** `verify_pdf.py` confirms the cases that must not
  take the fast path — a full (correlated) covariance, and a singular one —
  are *bitwise* identical to scipy.
- **The end quantity.** It also checks `mixture_weight` itself, which is what
  actually reaches your science.

Expect agreement at the 1e-15..1e-13 level, not bitwise, wherever a computation
was reassociated: numpy's `**` and C's `pow()` are separate implementations of
the same operation and may differ in the last bit. The pass criteria are written
in those terms.

Two things these scripts caught that reading the code did not:

- passing a covariance as a numpy array crashed stroopwafel's `Gaussian`
  constructor (`if cov == None` on an array), which only ever worked because
  `genais` happens to pass a nested list;
- a batch that formed no double compact objects took the whole run down, and
  then corrupted `samples.csv` in a way that surfaced hours later in
  `postprocess`.

If you optimise anything in this path, add a case here first.
