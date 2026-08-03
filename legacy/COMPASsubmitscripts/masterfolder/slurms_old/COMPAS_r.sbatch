#!/bin/bash
#SBATCH --job-name=COMPAS_r                 #job name
#SBATCH --ntasks=42                         # Number of cores (this should match what stroopwafel_interface_Lieke.py wants to use +1)
#SBATCH --output=SW%j.out                     # output storage file
#SBATCH --error=SW%j.err                      # error storage file
#SBATCH --time=1-12:00:00                   # Runtime format: D-HH:MM:SS use 1-00:00:00  for N1e7 sim
#SBATCH -p cca                              # Partitions
#SBATCH --mail-user=aac.van.son@gmail.com   # Send email 
#SBATCH --mail-type=FAIL                    # Only if failed

#Print some stuff on screen
echo $SLURM_JOB_ID
echo $SLURM_JOB_NAME
echo $SLURM_ARRAY_TASK_ID

#Set variables
export QT_QPA_PLATFORM=offscreen # To avoid the X Display error

#Set data directories
root_script_dir=/mnt/home/lvanson/WDWD_NSNS_rates/code/COMPASsubmitscripts/masterfolder/
data_dir='/mnt/home/lvanson/ceph/CompasOutput/v03.21.00/N5e6_MassiveWDWD_NSNS_fid'

# Create data_dir if it does not exist
mkdir -p $data_dir

# copy the scripts you used for your run to the output directory
cp -r $root_script_dir/MainRun $data_dir
cp $root_script_dir/slurms/COMPAS_r.sbatch $data_dir/MainRun/

#CD to the run directory
cd $data_dir/MainRun/

#Load modules
module load gsl hdf5 gcc python boost

# Run your job
python stroopwafel_interface_Lieke.py > SW.log 2>&1


