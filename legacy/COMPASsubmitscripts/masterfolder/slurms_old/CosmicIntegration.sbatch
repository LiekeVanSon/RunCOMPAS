#!/bin/bash
#SBATCH --job-name=CosmicIntegration
#SBATCH --output=CosmicIntegration%j.out
#SBATCH --mail-type=ALL
#SBATCH --mail-user=aac.van.son@gmail.com
#SBATCH --partition=gen
#SBATCH --time=0-01:00:00                   # Runtime format: D-HH:MM:SS use 0-12:00:00  for N1e7 sim


#Set data directories
root_script_dir=/mnt/home/lvanson/WDWD_NSNS_rates/code/COMPASsubmitscripts/masterfolder/
data_dir='/mnt/home/lvanson/ceph/CompasOutput/v03.21.00/N5e6_MassiveWDWD_NSNS_fid'

# check if CosmicIntegration folder exists, and copy scripts to output directory
mkdir -p $data_dir/CosmicIntegration/
cp $root_script_dir/CosmicIntegration/* $data_dir/CosmicIntegration/

#CD to the root directory
cd $data_dir/CosmicIntegration/

# Activate the python virtual environment
module load python

# Call the Cosmic Integration script with the input parameters and flags (dco_type = ["all", "BBH", "BHNS", "BNS", "WDWD"])
python -u FastCosmicIntegration.py --path $data_dir/MainRun/COMPAS_Output_wWeights.h5  --weight mixture_weight --keepRLOF_postCE --zstep 0.01 --sens O3 --m1min 1.0 --dco_type all --maxzdet 8 --mu0 0.025 --muz -0.049 --sigma0 1.129 --sigmaz 0.048 --alpha -1.79 --aSF 0.017 --bSF 1.487 --cSF 4.442 --dSF 5.886 &> CosmicIntegration.log 
