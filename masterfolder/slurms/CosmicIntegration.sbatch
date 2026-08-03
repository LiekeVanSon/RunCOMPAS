#!/bin/bash
# =====================================================================
#  Cosmic integration: convolve the population with a star formation
#  history to get intrinsic and detectable merger rates.
# =====================================================================
#  GENERATED FILE. Written from masterfolder/slurms/CosmicIntegration.sbatch.
#
#  This turns "here are N simulated binaries" into "here is a merger rate per
#  Gpc^3 per year as a function of redshift", and applies detector selection
#  effects. Results are written back into the input h5 as new groups.
#
#  The metallicity-specific SFR parameters (mu0, muz, sigma0, sigmaz, alpha)
#  and the SFR normalisation (aSF..dSF) default to the van Son et al. (2022)
#  best fit. Change them in the driver script, not here.
# =====================================================================
#SBATCH --job-name=@@JOB_NAME@@
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --output=@@LOG_DIR@@/cosmicIntegration_%j.out
#SBATCH --error=@@LOG_DIR@@/cosmicIntegration_%j.err
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
cd "${DATA_DIR}/CosmicIntegration"

@@PYTHON@@ -u FastCosmicIntegration.py \
    --path "${DATA_DIR}/MainRun/@@INPUT_H5@@" \
    @@CI_ARGS@@

echo "finished : $(date)"
