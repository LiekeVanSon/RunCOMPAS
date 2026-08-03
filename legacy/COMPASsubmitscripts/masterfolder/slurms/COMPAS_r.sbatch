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
data_dir='/mnt/home/lvanson/ceph/CompasOutput/v03.21.00/N5e6_MassiveWDWD_NSNS_fid'
cd $data_dir/MainRun/

#Load modules
# module load gsl hdf5 gcc python boost
module purge
# Load the module collection / stack for this specific COMPAS version 
#  v03.21.00 (gsl v2.7.1, boost v1.84.0, HDF5 v1.12.3)
module load modules/2.3-20240529

# Now load exactly what COMPAS needs
module load gcc/11.4.0
module load boost/1.84.0
module load gsl/2.7.1
module load hdf5/1.12.3
module load python/3.10.13


# Run your job
python stroopwafel_interface_Lieke.py > SW.log 2>&1


