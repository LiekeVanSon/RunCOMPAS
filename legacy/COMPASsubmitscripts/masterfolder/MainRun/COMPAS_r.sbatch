#!/bin/bash
#SBATCH --job-name=COMPAS_r                 #job name
#SBATCH --nodes=1                           # Number of nodes
#SBATCH --ntasks=1                          # Number of cores
#SBATCH --output=SW.out                     # output storage file
#SBATCH --error=SW.err                      # error storage file
#SBATCH --time=0-10:00:00                   # Runtime format: D-HH:MM:SS
#SBATCH --mem=2G                            # use 2 GB of Memory per cpu 
#SBATCH -p cca,gen,genx                     # Partitions
#SBATCH --mail-user=aac.van.son@gmail.com   # Send email to user
#SBATCH --mail-type=FAIL                    # Only if failed

#Print some stuff on screen
echo $SLURM_JOB_ID
echo $SLURM_JOB_NAME
echo $SLURM_ARRAY_TASK_ID

#Set variables
export QT_QPA_PLATFORM=offscreen # To avoid the X Display error

#Set data directories
root_script_dir=/mnt/home/lvanson/NSNS_masses/code/GridSubmitCOMPAS/masterfolder/
data_dir=/mnt/home/lvanson/ceph/CompasOutput/v02.41.06/Test/

#Load modules
module load gsl boost hdf5 gcc python

#CD to the root directory
cd $root_script_dir/MainRun/

# Run your job
python stroopwafel_interface_Lieke.py   > SW.log

# copy the scripts you used for your run to the output directory
cp $root_script_dir/MainRun/* $data_dir/MainRun/
