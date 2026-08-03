#!/bin/bash
# =====================================================================
#  COMPAS stroopwafel (adaptive importance sampling) run.
# =====================================================================
#  GENERATED FILE. Written by Submit_StroopwafelRun.py from
#  masterfolder/slurms/COMPAS_stroopwafel.sbatch.
#
#  This is deliberately NOT an array job. Stroopwafel is adaptive: it has to
#  see the results of the explore phase before it knows where to draw the
#  refine samples. So it runs as ONE job that itself spawns --num_cores COMPAS
#  processes in parallel. That is why --ntasks below must be >= num_cores.
# =====================================================================
#SBATCH --job-name=@@JOB_NAME@@
#SBATCH --nodes=1
#SBATCH --ntasks=@@NTASKS@@
#SBATCH --output=@@LOG_DIR@@/stroopwafel_%j.out
#SBATCH --error=@@LOG_DIR@@/stroopwafel_%j.err
#SBATCH --time=@@WALLTIME@@
#SBATCH --mem=@@MEMORY@@
#SBATCH --partition=@@PARTITION@@
#SBATCH --mail-user=@@MAIL_USER@@
#SBATCH --mail-type=@@MAIL_TYPE@@
@@EXTRA_SBATCH@@

set -euo pipefail

echo "=============================================="
echo " job id  : ${SLURM_JOB_ID}"
echo " node    : $(hostname)"
echo " started : $(date)"
echo "=============================================="

# ---- cluster setup (from clusters/@@CLUSTER@@.yaml) ----------------------
@@SETUP@@

export COMPAS_ROOT_DIR="@@COMPAS_ROOT@@"
export STROOPWAFEL_PATH="@@STROOPWAFEL_PATH@@"

DATA_DIR="@@DATA_DIR@@"
MAIN_RUN="${DATA_DIR}/MainRun"
cd "${MAIN_RUN}"

# ---- where to do the actual I/O ------------------------------------------
# See the note in COMPAS_grid.sbatch. Stroopwafel forks many COMPAS workers on
# this one node, so they all share this node's local scratch. Everything it
# produces (batch_*/, samples.csv, distributions.csv) is copied back at the end.
SCRATCH_ROOT="@@SCRATCH_ROOT@@"

if [ -n "${SCRATCH_ROOT}" ]; then
    WORK_DIR="${SCRATCH_ROOT}/${USER:-$(whoami)}/compas_${SLURM_JOB_ID}"
    mkdir -p "${WORK_DIR}"

    stage_out() {
        echo ">>> copying results back to ${MAIN_RUN}"
        cp -a "${WORK_DIR}/." "${MAIN_RUN}/" \
            || echo "WARNING: copy-back from ${WORK_DIR} failed"
        rm -rf "${WORK_DIR}"
    }
    trap stage_out EXIT

    echo "working on node-local scratch : ${WORK_DIR}"
else
    WORK_DIR="${MAIN_RUN}"
    echo "working directly in : ${WORK_DIR}"
fi

@@PYTHON@@ stroopwafel_interface.py \
    --output_folder "${WORK_DIR}" \
    --config_file compasConfig.yaml \
    --num_systems @@NUM_SYSTEMS@@ \
    --num_cores @@NUM_CORES@@ \
    --num_per_core @@NUM_PER_CORE@@ \
    --sys_int @@SYS_INT@@ \
    --random_seed_base @@SEED_BASE@@ @@MC_ONLY@@

echo "finished : $(date)"
