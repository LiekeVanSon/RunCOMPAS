# 05 — Adding a cluster

Everything machine-specific lives in `clusters/<name>.yaml`. Nothing else in the
repo needs to know what machine you are on, so moving to a new cluster means
writing one file.

---

## How a profile is chosen

1. `--cluster NAME` on the command line, or `CLUSTER = "name"` in the driver, wins.
2. Otherwise each `clusters/*.yaml` is checked and the first whose
   `hostname_match` entries appear as a substring of your `hostname` is used.
3. If nothing matches, the driver stops and lists what is available.

```bash
hostname                                  # what will be matched against
python3 Submit_GridRun.py --dry-run       # prints the profile it picked
```

---

## Adding one

```bash
cp clusters/TEMPLATE.yaml clusters/mycluster.yaml
```

Fill it in — the template has a comment for every field:

```yaml
name: mycluster
hostname_match: ["login", "head"]   # substring match on `hostname`

partition: "normal"
max_walltime: "2-00:00:00"
account: null                       # some clusters need --account=<project>
mail_type: "FAIL"
mail_user: "you@example.com"
extra_sbatch: []                    # raw #SBATCH lines, without the "#SBATCH "

compas_root: "/path/to/COMPAS"      # must contain src/COMPAS
output_root: "/big/scratch/you"     # NOT your home directory
stroopwafel_path: ""                # "" if pip-installed

venv: "~/venvs/runcompas_venv"      # built from requirements.txt
python: "python"                    # correct once the venv is active

setup: |
  export QT_QPA_PLATFORM=offscreen
  module load gcc boost gsl hdf5
```

### Where to find each value

| field | how to find it |
|---|---|
| `hostname_match` | `hostname` |
| `partition`, `max_walltime` | `sinfo -s` |
| `account` | `sacctmgr show assoc user=$USER` (often not needed) |
| `compas_root` | `echo $COMPAS_ROOT_DIR`, or wherever you built it |
| `output_root` | your group's scratch/data filesystem — check quotas with `df -h` |
| `venv` | wherever you built it — see [01_setup.md §2](01_setup.md#2-make-your-own-python-environment) |
| `setup` | `module avail`, and whatever COMPAS was compiled against |

---

## Where jobs write: `output_root` and `scratch_root`

Two rules decide these:

1. **`output_root` must be writable from the compute nodes**, not just from the
   login node. This is not automatic — check it:

   ```bash
   srun -p <partition> -n1 -t 2 bash -c 'touch <output_root>/probe && echo OK || echo NO'
   ```

   If it isn't, every job dies instantly with slurm `ExitCode 0:53` and an empty
   `logs/` directory. (`mount` can report `rw` and writes still fail — an NFS
   server may export read-only to particular clients.)

2. **`scratch_root`** is node-local disk for the actual simulation I/O. When
   set, COMPAS writes there and each finished batch is copied back to
   `output_root` when the task ends — including on failure or timeout, so
   partial results survive. Scratch is always removed afterwards.

   Set it when your shared filesystem is ordinary NFS, or when your cluster
   documentation asks you to (many do — dozens of array tasks writing
   concurrently to NFS is exactly what it is worst at). Leave it `""` on a fast
   parallel filesystem such as ceph or lustre, where writing directly is fine.

```yaml
# coma: NFS shared storage, local disk per node
output_root:  "/home/you/CompasOutput"
scratch_root: "/scratch"

# rusty: ceph is fast and parallel, no staging needed
output_root:  "/mnt/home/you/ceph/CompasOutput"
scratch_root: ""
```

---

## Python: `venv` and `python`

Set `venv` to a virtualenv built from `requirements.txt`. The drivers emit

```bash
source <venv>/bin/activate
```

at the top of every job script, before the `setup` block, and **refuse to
submit** if that path does not exist — so a typo fails immediately rather than
in every array task an hour later.

With a venv active, `python:` should just be `"python"`. Set `venv: ""` only if
your `setup` block already puts a suitable python on the PATH (e.g. via
`module load python`), as `rusty.yaml` does.

---

## The `setup` block

Pasted verbatim at the top of every generated job script, **after** the venv is
activated. It has one job: leave the shell able to run COMPAS.

**The module versions must match what COMPAS was compiled against.** A COMPAS
built against HDF5 1.12.3 will not run with 1.10 loaded. Check with:

```bash
$COMPAS_ROOT_DIR/src/COMPAS --version
# COMPAS v03.29.05 (gsl v2.7.1, boost v1.83.0, HDF5 v1.10.10)
```

Two real examples from this repo:

```yaml
# coma: COMPAS built against system libraries, nothing to load
setup: |
  export QT_QPA_PLATFORM=offscreen
```

```yaml
# rusty: an explicit module stack pinned to the COMPAS build
setup: |
  export QT_QPA_PLATFORM=offscreen
  module purge
  module load modules/2.3-20240529
  module load gcc/11.4.0 boost/1.84.0 gsl/2.7.1 hdf5/1.12.3 python/3.10.13
```

`export QT_QPA_PLATFORM=offscreen` avoids matplotlib's X-display error on
headless compute nodes. Keep it.

---

## Testing a new profile

```bash
python3 Submit_GridRun.py --dry-run --cluster mycluster --run-name profiletest
```

Then read the generated script — it is the real thing:

```bash
cat <output_root>/profiletest/MainRun/COMPAS_grid.sbatch
```

Check the `#SBATCH` header, that `setup` looks right, and that `COMPAS_ROOT_DIR`
points somewhere real. Then run a 200-system test
([01_setup.md §6](01_setup.md#6-your-first-run)) before trusting it with a
production run.

---

## Notes

- Empty fields are handled: if `mail_user` is blank, the `#SBATCH --mail-user=`
  line is dropped rather than emitted broken.
- If `account` is set it becomes `#SBATCH --account=<value>` automatically.
- If your requested walltime exceeds `max_walltime`, the driver warns you before
  submitting — slurm would otherwise reject the job.
- `python:` is the interpreter used inside jobs. Point it at a venv's `python` if
  you use one.

**Please commit new profiles back to the repo** so the next person on that
machine doesn't have to work it out again.

---

## Previous

- [00 README](../README.md) — overview, repository map, quickstart
- [01 Setup](01_setup.md) — COMPAS, your python environment, first test run

## Next

- [06 Troubleshooting](06_troubleshooting.md) — when things go wrong
