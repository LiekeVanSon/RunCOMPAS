##############################################
# created 20-02-2023
#
# Grid_Call_Stroopwafel.py
#
# Python script meant to run COMPAS using the runSubmit.py
# runsubmit will use the instructions listed in compasConfig.yaml
# 
# This file should be run in a folder that contains 'masterfolder' 
#
##############################################
import numpy as np
import os
import time
from subprocess import Popen, PIPE, call
import subprocess
import sys
import pickle
import math
import shutil
import fileinput
import itertools


#################################################################
## 
##    Step 0: set variables
##    Should be Changed by user ##
##
#################################################################
root_out_dir         = "/n/holystore01/LABS/hernquist_lab/Users/lvanson/CompasOutput/v02.35.02/test/"
file_name            = 'COMPAS_Output_wWeights.h5'
user_email           = "aac.van.son@gmail.com"

# What scripts do you want to run?
RunStroopwafel       = True
RunPostProcessing    = True
RunCosmicIntegration = False
# Do you want to run the cosmic integration locally or on an HPC? 
Run_cluster          = False

# details for your run
with open('./masterfolder/BSE_grid.txt', 'r') as f:
    # Read the file into a list of lines
    lines = f.readlines()
num_lines = len(lines)

N_binaries           = num_lines  # how many binaries to run in total
N_chunks             = 10         # how many batches to run this in (N_binaries/N_chunks is not an int, you will run the remainder in an extra last batch)



##################################################################
# This is the slurm script youre using
#SBATCH --partition=%s  # Partition to submit to
SlurmJobString="""#!/bin/bash
#SBATCH --job-name=%s               #job name
#SBATCH --nodes=%s                  # Number of nodes
#SBATCH --ntasks=%s                 # Number of cores
#SBATCH --output=%s                 # output storage file
#SBATCH --error=%s                  # error storage file
#SBATCH --time=%s                   # Runtime in minutes
#SBATCH --mem=%s                    # Memory per cpu in MB (see also --mem-per-cpu)
#SBATCH -p %s
#SBATCH --mail-user=%s              # Send email to user
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
cd %s
#
# Run your job
%s
"""
###############################################
###
###############################################
def MakeSlurmBatch(run_dir = None, sub_dir = 'MainRun/', python_name = "runSubmit", job_name = "runCOMPAS",\
                   number_of_nodes = 1, number_of_cores = 1, partition='gen,cca', flags=" ",\
                   walltime = '05:00:00' ,memory = '16000', email = None):

    outfile = run_dir + job_name+ '.out'
    errfile = run_dir + job_name+ '.err'

    job_line = "python "+python_name+".py "+flags+" > "+job_name+".log"

    # Make slurm script string
    interface_job_string = SlurmJobString % (job_name, number_of_nodes, number_of_cores, \
        outfile, errfile, walltime, memory, partition, user_email,\
        run_dir, job_line)

    sbatchFile = open('./masterfolder/'+sub_dir +job_name+'.sbatch','w')
    print('writing ',  './masterfolder/'+sub_dir +job_name+'.sbatch')
    sbatchFile.write(interface_job_string)
    sbatchFile.close()

    return interface_job_string


###############################################
###
###############################################
def RunSlurmBatch(run_dir = None, job_name = "runCOMPAS", dependency = False, dependent_ID = None):

    if not dependency:
        sbatchArrayCommand = 'sbatch ' + os.path.join(run_dir+job_name+'.sbatch') 
    else:
        sbatchArrayCommand = 'sbatch --dependency=afterok:' + str(int(dependent_ID)) + ' ' + os.path.join(run_dir+job_name+'.sbatch') 

    # Open a pipe to the sbatch command.
    proc = Popen(sbatchArrayCommand, shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE, close_fds=True)

    # Send job_string to sbatch
    if (sys.version_info > (3, 0)):
        proc.stdin.write(sbatchArrayCommand.encode('utf-8'))
    else:
        proc.stdin.write(sbatchArrayCommand)

    print('sbatchArrayCommand:', sbatchArrayCommand)
    out, err = proc.communicate()
    print("out = ", out)
    job_id = out.split()[-1]
    print("job_id", job_id)
    return job_id

###############################################
###
###############################################
def replaceFileLine(file_dir, line_num, replacestr):
    """
    file_dir   = The file of which you would like to change a line
    line_num   = The line number that you want to change
    replacestr = The string that you want to replace this line wtth
    """
    #Open file of interest
    with open (file_dir, "r") as myfile:
        data = myfile.readlines()
    #Replace line
    data[line_num-1] = replacestr
    # Write everything back
    with open (file_dir, "w") as wfile:
        wfile.writelines(data)
    
def divide_with_remainder(numerator, denominator):
    batch_size = numerator // denominator
    n_jobs     = numerator/batch_size
    remainder  = numerator % denominator
    return batch_size, int(n_jobs), remainder


###############################################
# Make the output directory if it doesn't exist
if not os.path.exists(root_out_dir):
    print('making ', root_out_dir)
    os.mkdir(root_out_dir) 
    # copy this python script to the ROOT out dir
    shutil.copyfile('Grid_Call.ipynb', root_out_dir+'Grid_Call.ipynb')  
    shutil.copyfile('masterfolder/BSE_grid.txt', root_out_dir+'BSE_grid.txt')  
else:
    ValueError("The output folder already exists. Either remove it, or choose a new output folder name")


###############################################
# Determine how many batches to run
batch_size, n_jobs, remainder = divide_with_remainder(N_binaries, N_chunks)
last_batch_size, extra_job    =  batch_size, 0
if remainder != 0.:
    extra_job = 1
    print(r'N_binaries = %s can not be divided properly into N_chunks=%s'%(N_binaries, N_chunks))
    print('You will run 1 extra job with %s binaries'%(remainder))

    
#################################################################
## 
##    ## Step 1 adjust compasConfig.yaml, make and submit slurm jobs
##
##
#################################################################
    
check_job_completionID = []


for Njob in range(n_jobs + extra_job):
    # directory where you will copy the files to and run compas from
    run_dir = root_out_dir+'/MainRun/batch'+'_%s'%(Njob) +'/'
    ############################################
    # Change the yaml file for this batch job
    # run from grid line
    replaceFileLine("./masterfolder/MainRun/compasConfig.yaml",55, "    --grid-start-line: %s"%(Njob*batch_size) +"\n")

    # if you are on the last job and the remainder is nonzero, run the remainder
    if np.logical_and(remainder !=0, Njob == n_jobs):
        print('you are on the extra job')
        batch_size = remainder
        
    # number of gridlines you will run
    replaceFileLine("./masterfolder/MainRun/compasConfig.yaml",54,"    --grid-lines-to-process: %s"%(batch_size) +"\n")
        
    # change the root output dir in the yaml file
    replaceFileLine("./masterfolder/MainRun/compasConfig.yaml",245, '    --output-path:  \"%s\"'%(run_dir) + "\n")
    
    ############################################
    # Make and safe a slurm command  
    MakeSlurmBatch(run_dir = run_dir, job_name = "COMPAS_r")
    
    ############################################
    # copy everything over to the root dir
    shutil.copytree('masterfolder/MainRun', run_dir)  
    
    ############################################
    # Submit the job to sbatch! 
    job_id = RunSlurmBatch(run_dir = run_dir , job_name = "COMPAS_r", dependency = False, dependent_ID = None)

    # make a list of job IDs run
    check_job_completionID.append(job_id.decode("utf-8"))

# save the IDs in the main run dir    
np.savetxt(root_out_dir+'/MainRun/job_IDs.txt', np.c_[check_job_completionID],header = "# job IDs ", delimiter=',', fmt="%s")
  

    
#################################################################
## 
##     Step 2 Check if all jobs completed succesfully
##
##
#################################################################


###############################################
# Make and safe a slurm command
location_flag = ' -l '+root_out_dir+'/MainRun/job_IDs.txt'

PP_job_string = MakeSlurmBatch(run_dir = root_out_dir + 'postProcessing/', sub_dir = 'postProcessing/', python_name = "CheckJobCompletion",\
 job_name = "checkCOMPL", number_of_nodes = 1, number_of_cores = 1, partition='gen,cca',\
 walltime = "1:00:00" ,memory = "500", email = user_email, flags= location_flag)

############################################
# # check if postprocessing folder exists, and copy over local verion
if os.path.exists(root_out_dir+'/postProcessing'):
    shutil.rmtree(root_out_dir+'/postProcessing')
# copy everything over to the root dir
shutil.copytree('masterfolder/postProcessing', root_out_dir+'/postProcessing')

############################################
# Submit the job to sbatch
# dependency on last submitted COMPAS job, so only start checking after that one completes
combinejob_id = RunSlurmBatch(run_dir = root_out_dir+'/postProcessing/', job_name = "checkCOMPL",\
 dependency = True, dependent_ID = job_id)


#################################################################
## 
##     Step 3 Combine the hdf5 files in post processing
##
##
#################################################################

###############################################
# Make Post Processing batch and submit it
# This job depends on the CheckJobCompletion job to be done
###############################################
print(10* "*" + ' You are Going to Run PostProcessing.py')

###############################################
# Make and safe a slurm command
h5Flags = ' '+root_out_dir+'/MainRun/ -r 2 -o ../MainRun/COMPAS_Output.h5'
PP_job_string = MakeSlurmBatch(run_dir = root_out_dir + 'postProcessing/', sub_dir = 'postProcessing/', python_name = "h5copy",\
 job_name = "COMPAS_PP", number_of_nodes = 1, number_of_cores = 1, partition='gen,cca',\
 walltime = "1:00:00" ,memory = "5000", email = user_email, flags= h5Flags)

############################################
# # check if postprocessing folder exists, and copy over local verion
if os.path.exists(root_out_dir+'/postProcessing'):
    shutil.rmtree(root_out_dir+'/postProcessing')
# copy everything over to the root dir
shutil.copytree('masterfolder/postProcessing', root_out_dir+'/postProcessing')

############################################
# Submit the job to sbatch! 
PPjob_id = RunSlurmBatch(run_dir = root_out_dir+'/postProcessing/', job_name = "COMPAS_PP",\
 dependency = True, dependent_ID = combinejob_id)
    
    
