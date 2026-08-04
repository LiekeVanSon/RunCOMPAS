# 01 — Setup

Work through this once. At the end you will have run a small COMPAS simulation
end to end, which is the only real proof that your setup works.

---

## 1. You need a working COMPAS

COMPAS is a C++ program you compile yourself. Follow the
[official install guide](https://compas.readthedocs.io/en/latest/pages/Getting%20started/getting-started.html).

if you set everything up correcly, you can check it works with the following commands:

```bash
echo $COMPAS_ROOT_DIR
$COMPAS_ROOT_DIR/src/COMPAS --version
```

You should see something like:

```
COMPAS v03.29.05 (gsl v2.7.1, boost v1.83.0, HDF5 v1.10.10)
```

**Write down that version number.** COMPAS options get added, renamed and removed
between versions, and a config file from an older version will not necessarily
work with a newer binary. See [§5](#5-make-your-config-match-your-compas-version).
You might even want to make a branch of the specific COMPAS version you are using if you are running production runs, so you know for sure you can get back to this version easily. 

> On `coma` my build is at
> `/vol/astro8/lvanson/lvanson/Programs/COMPAS` and this is already set in
> `clusters/coma.yaml`, so you do not need your own build to get started.

(TODO: create a shared build at /vol/optcoma)
---

## 2. Make your own python environment

**Make your own venv.** Don't rely on the system python: it works today, but it
is upgraded without warning, and the day it changes your jobs start failing in
ways that are hard to trace. A venv is yours, and `requirements.txt` records
exactly what is in it.

```bash
python3 -m venv ~/venvs/runcompas_venv
source ~/venvs/runcompas_venv/bin/activate
pip install -r requirements.txt
```

Check it worked:

```bash
python -c "
for m in ['numpy','h5py','yaml','astropy','scipy','pandas','matplotlib']:
    try: __import__(m); print('  OK  ', m)
    except ImportError: print('  MISS', m)
"
```

All seven should say OK. What each is for is documented in
[`requirements.txt`](../requirements.txt); the one that surprises people is
**matplotlib**, which `FastCosmicIntegration.py` imports at module level, so it
is required even if you never make a plot.

Then point your cluster profile at it, so the compute nodes use it too:

```yaml
# clusters/coma.yaml
venv: "/home/<you>/venvs/runcompas_venv"
python: "python"
```

The drivers put `source <venv>/bin/activate` at the top of every generated job
script, and refuse to submit if the path doesn't exist.

> **If you use `uv`** (`uv venv`) — it's much faster, but it does **not** install
> `pip` into the venv, so `pip install -r requirements.txt` will fail with
> `No module named pip`. Use `uv pip install -r requirements.txt` instead, or
> add pip with `python -m ensurepip`.


---

## 3. Stroopwafel (only for Mode 2)

Skip this if you only want plain grid runs.

Two options. **Pip** is simpler:

```bash
pip install stroopwafel        # with your venv active
python -c "import stroopwafel; print('stroopwafel OK')"
```

and leave `stroopwafel_path: ""` in your cluster profile.

**A git clone** is better if you want to modify the sampler, or need something
newer than the PyPI release (1.1.0, which is not always current):

```bash
git clone https://github.com/lokiysh/stroopwafel ~/Programs/stroopwafel
```

then set the path in your cluster profile:

```yaml
stroopwafel_path: "~/Programs/stroopwafel"
```

The job scripts export this as `STROOPWAFEL_PATH` and
`stroopwafel_interface.py` puts it on `sys.path`, so the clone does not need
installing.


---

## 4. Your cluster profile

Machine-specific settings — partitions, module loads, where output goes — live in
`clusters/<name>.yaml`, so nothing else in the repo has to know what machine you
are on. The profile is picked automatically by matching your hostname.

Check the one for your machine is right:

```bash
hostname                      # which profile will be matched
cat clusters/coma.yaml        # is compas_root / output_root correct for YOU?
```

The fields you almost certainly need to change are **`output_root`** and
**`archive_root`** — they default to Lieke's directories:

```yaml
output_root:  "/home/<you>/CompasOutput"                  # jobs write here
archive_root: "/vol/astro8/<you>/CompasOutput"            # you move runs here
```

**`output_root` must be writable from the compute nodes**, which is not the same
as being writable from the login node. On coma the big `/vol/astro*` volumes are
exported read-only to the nodes, so jobs must write to `$HOME` and finished runs
are moved to astro8 afterwards with `archive_run.py`. Check yours:

```bash
srun -p normal -n1 -t 2 bash -c 'touch <output_root>/probe && echo OK || echo NO'
```

Simulation output is **large** (tens to hundreds of GB) and `$HOME` on coma is
only ~25 GB, so archive between runs. See
[06_troubleshooting.md](06_troubleshooting.md) for the full picture, and
[05_clusters.md](05_clusters.md) for a new machine.

---

## 5. Make your config match your COMPAS version

`masterfolder/compasConfig.yaml` holds every COMPAS physics option. The one in
this repo was generated from **v03.29.05**. If your COMPAS is a different
version, regenerate it:

```bash
cp $COMPAS_ROOT_DIR/compas_python_utils/preprocessing/compasConfigDefault.yaml masterfolder/compasConfig.yaml
```

This writes a file with every option your build supports, all commented out (so
every option keeps its COMPAS default). To change a setting, delete the `#` and
edit the value:

```yaml
#    --common-envelope-alpha: 1.000000       # before
    --common-envelope-alpha: 0.5             # after
```

> **Watch the indentation.** Deleting only the `#` leaves 4 spaces, which is what
> the scripts also write. Don't re-indent by hand — YAML rejects a file whose
> keys sit at inconsistent indentation.

To find out what an option does:

```bash
$COMPAS_ROOT_DIR/src/COMPAS --help | grep -A2 "common-envelope-alpha"
```

---

## 6. Your first run

Do a tiny run to prove the whole chain works. Edit the top of `Submit_GridRun.py`:

```python
RUN_NAME   = "my_first_test"
N_BINARIES = 200
N_BATCHES  = 4
MAINRUN_WALLTIME = "0-00:30:00"
```

Then:

```bash
python3 Submit_GridRun.py --dry-run     # read what it would do
python3 Submit_GridRun.py               # submit
squeue -u $USER                         # watch it
```

When it finishes:

```bash
python3 <output_root>/my_first_test/postProcessing/check_status.py <output_root>/my_first_test
```

You should see 4 batch directories with output, and a merged
`MainRun/COMPAS_Output.h5`.

> A 200-system run merges to well under a megabyte. If yours is hundreds of MB,
> the merge used too large an HDF5 chunk size — see
> [06_troubleshooting.md](06_troubleshooting.md). The drivers scale this
> automatically, so it should not happen.

---

## Previous

- [00 README](../README.md) — overview, repository map, quickstart

## Next

- [02 Grid runs](02_grid_runs.md) — Mode 1: large runs via a SLURM array
- [03 Stroopwafel runs](03_stroopwafel_runs.md) — Mode 2: adaptive importance sampling
