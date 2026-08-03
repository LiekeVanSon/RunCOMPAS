#!/bin/bash
# =====================================================================
#  COMPAS grid run -- ONE SLURM ARRAY JOB, one task per batch.
# =====================================================================
#  GENERATED FILE. Written by Submit_GridRun.py from
#  masterfolder/slurms/COMPAS_grid.sbatch. Every double-at placeholder below
#  is filled in at submit time. Edit the template, not the generated copy --
#  but the generated copy is a normal sbatch script, so you can resubmit it
#  by hand.
#
#  Why an array instead of N separate sbatch calls: one job ID for the whole
#  run, %N caps how many run at once so you stay friendly on a shared queue,
#  and a single --dependency=afterok on the array waits for ALL tasks.
#
#  Rerun only the tasks that failed:
#      sbatch --array=3,7,12 this_script.sbatch
# =====================================================================
#SBATCH --job-name=@@JOB_NAME@@
#SBATCH --array=@@ARRAY_SPEC@@
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --output=@@LOG_DIR@@/grid_%A_%a.out
#SBATCH --error=@@LOG_DIR@@/grid_%A_%a.err
#SBATCH --time=@@WALLTIME@@
#SBATCH --mem=@@MEMORY@@
#SBATCH --partition=@@PARTITION@@
#SBATCH --mail-user=@@MAIL_USER@@
#SBATCH --mail-type=@@MAIL_TYPE@@
@@EXTRA_SBATCH@@

set -euo pipefail

echo "=============================================="
echo " job id     : ${SLURM_ARRAY_JOB_ID}"
echo " array task : ${SLURM_ARRAY_TASK_ID}"
echo " node       : $(hostname)"
echo " started    : $(date)"
echo "=============================================="

# ---- cluster setup (from clusters/@@CLUSTER@@.yaml) ----------------------
@@SETUP@@

export COMPAS_ROOT_DIR="@@COMPAS_ROOT@@"

DATA_DIR="@@DATA_DIR@@"
MAIN_RUN="${DATA_DIR}/MainRun"
cd "${MAIN_RUN}"

# ---- work out which slice of the run this task owns ----------------------
# Tasks are equal-sized except the last, which picks up the remainder.
TASK=${SLURM_ARRAY_TASK_ID}
BATCH_SIZE=@@BATCH_SIZE@@
N_TOTAL=@@N_TOTAL@@

START=$(( TASK * BATCH_SIZE ))
REMAINING=$(( N_TOTAL - START ))
if [ "${REMAINING}" -le 0 ]; then
    echo "Nothing to do for task ${TASK} (start ${START} >= total ${N_TOTAL})"
    exit 0
fi
if [ "${REMAINING}" -lt "${BATCH_SIZE}" ]; then
    N_THIS=${REMAINING}
else
    N_THIS=${BATCH_SIZE}
fi

echo "this task evolves ${N_THIS} systems, starting at offset ${START}"

# ---- where to do the actual I/O ------------------------------------------
# When the cluster profile sets scratch_root, COMPAS writes to disk that is
# LOCAL to this node, and the finished batch is copied back to the shared run
# directory afterwards. Shared/NFS filesystems have high latency and cope badly
# with many array tasks writing at once, so this is usually much faster -- and
# on some clusters (e.g. coma) the shared volume is not writable from compute
# nodes at all.
SCRATCH_ROOT="@@SCRATCH_ROOT@@"
OUT_NAME="batch_${TASK}"

if [ -n "${SCRATCH_ROOT}" ]; then
    WORK_DIR="${SCRATCH_ROOT}/${USER:-$(whoami)}/compas_${SLURM_ARRAY_JOB_ID}_${TASK}"
    mkdir -p "${WORK_DIR}"

    # Copy results back and clean up on ANY exit -- including failure or
    # timeout -- so partial output survives and scratch never fills up.
    stage_out() {
        if [ -d "${WORK_DIR}/${OUT_NAME}" ]; then
            echo ">>> copying ${OUT_NAME} back to ${MAIN_RUN}"
            mkdir -p "${MAIN_RUN}/${OUT_NAME}"
            cp -a "${WORK_DIR}/${OUT_NAME}/." "${MAIN_RUN}/${OUT_NAME}/" \
                || echo "WARNING: copy-back from ${WORK_DIR} failed"
        else
            echo "WARNING: nothing to copy back from ${WORK_DIR}"
        fi
        rm -rf "${WORK_DIR}"
    }
    trap stage_out EXIT

    echo "working on node-local scratch : ${WORK_DIR}"
else
    WORK_DIR="${MAIN_RUN}"
    echo "working directly in : ${WORK_DIR}"
fi

# ---- run COMPAS ----------------------------------------------------------
# Per-batch bookkeeping is passed on the COMPAS command line; the physics all
# comes from compasConfig.yaml. Nothing edits the yaml per batch.
@@PYTHON@@ runSubmit.py compasConfig.yaml \
    @@SLICE_ARGS@@ \
    --output-path "${WORK_DIR}" \
    --output-container "${OUT_NAME}" \
    --random-seed $(( @@SEED_BASE@@ + START ))

echo "finished : $(date)"
