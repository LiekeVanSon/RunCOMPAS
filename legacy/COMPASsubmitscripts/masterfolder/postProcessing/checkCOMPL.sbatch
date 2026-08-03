#!/bin/bash
#SBATCH --job-name=checkCOMPL               #job name
#SBATCH --nodes=1                  # Number of nodes
#SBATCH --ntasks=1                 # Number of cores
#SBATCH --output=/n/holystore01/LABS/hernquist_lab/Users/lvanson/CompasOutput/v02.35.02/StrongCoolWindN1e6/postProcessing/checkCOMPL.out                 # output storage file
#SBATCH --error=/n/holystore01/LABS/hernquist_lab/Users/lvanson/CompasOutput/v02.35.02/StrongCoolWindN1e6/postProcessing/checkCOMPL.err                  # error storage file
#SBATCH --time=1:00:00                   # Runtime in minutes
#SBATCH --mem=500                    # Memory per cpu in MB (see also --mem-per-cpu)
#SBATCH -p conroy,shared
#SBATCH --mail-user=aac.van.son@gmail.com              # Send email to user
#SBATCH --mail-type=FAIL            #
#
#Print some stuff on screen
echo $SLURM_JOB_ID
echo $SLURM_JOB_NAME
echo $SLURM_ARRAY_TASK_ID
#
#Load modules
module load Anaconda3/2020.11
# 
#Set variables
export QT_QPA_PLATFORM=offscreen # To avoid the X Display error
#
#CD to output directory
cd /n/holystore01/LABS/hernquist_lab/Users/lvanson/CompasOutput/v02.35.02/StrongCoolWindN1e6/postProcessing/
#
# Run your job
python CheckJobCompletion.py  -l /n/holystore01/LABS/hernquist_lab/Users/lvanson/CompasOutput/v02.35.02/StrongCoolWindN1e6//MainRun/job_IDs.txt > checkCOMPL.log
