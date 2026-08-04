#!/bin/bash
# =====================================================================
#  Post-processing: merge the per-batch COMPAS files into one h5.
# =====================================================================
#  GENERATED FILE. Written from masterfolder/slurms/COMPAS_PP.sbatch.
#
#  A run leaves you with MainRun/batch_0/, batch_1/, ... each holding its own
#  h5. h5copy walks that tree (-r 2 = two directory levels deep) and
#  concatenates every matching dataset into a single COMPAS_Output.h5.
#
#  For stroopwafel runs a second step attaches the mixture weights, giving
#  COMPAS_Output_wWeights.h5. That is the file you analyse.
# =====================================================================
#SBATCH --job-name=@@JOB_NAME@@
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --output=@@LOG_DIR@@/postProcessing_%j.out
#SBATCH --error=@@LOG_DIR@@/postProcessing_%j.err
#SBATCH --time=@@WALLTIME@@
#SBATCH --mem=@@MEMORY@@
#SBATCH --partition=@@PARTITION@@
#SBATCH --mail-user=@@MAIL_USER@@
#SBATCH --mail-type=@@MAIL_TYPE@@
@@EXTRA_SBATCH@@

set -euo pipefail

echo "=============================================="
echo " job id  : ${SLURM_JOB_ID}"
echo " started : $(date)"
echo "=============================================="

# ---- cluster setup (from clusters/@@CLUSTER@@.yaml) ----------------------
@@SETUP@@

export COMPAS_ROOT_DIR="@@COMPAS_ROOT@@"

DATA_DIR="@@DATA_DIR@@"
cd "${DATA_DIR}/postProcessing"

# ---- 1. merge the per-batch h5 files -------------------------------------
# IMPORTANT: h5copy scans MainRun/ two levels deep, and the merged file is
# written INTO MainRun/. If a merged file from a previous run is still sitting
# there it gets picked up as an *input* too, and every system ends up in the
# output twice -- silently, with no error. That is easy to hit, because
# resubmitting a few failed array tasks and re-running post-processing is a
# completely normal thing to do. So always start from a clean slate.
echo ">>> removing any previous merged output"
rm -f "${DATA_DIR}/MainRun/COMPAS_Output.h5" \
      "${DATA_DIR}/MainRun/COMPAS_Output_wWeights.h5"

# -c is the HDF5 chunk size. It matters more than you would think: h5copy
# allocates a full chunk for EVERY dataset, and COMPAS output has ~840 of them
# across its groups. At the h5copy default of 100000 that is
# 840 x 100000 x 8 bytes ~ 670 MB of mostly-empty chunks, regardless of how few
# systems you actually evolved. The driver scales this to the run size.
echo ">>> merging batches into COMPAS_Output.h5 (chunk size @@H5_CHUNK@@)"
@@PYTHON@@ h5copy.py "${DATA_DIR}/MainRun/" -r 2 -c @@H5_CHUNK@@ \
    -o "${DATA_DIR}/MainRun/COMPAS_Output.h5"

# ---- 2. attach stroopwafel weights (AIS runs only) -----------------------
@@APPEND_WEIGHTS@@

echo "finished : $(date)"
