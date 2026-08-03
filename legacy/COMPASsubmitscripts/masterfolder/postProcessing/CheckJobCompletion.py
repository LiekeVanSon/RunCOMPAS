"""
This script checks the outcome of all your jobs
"""
import numpy as np
from subprocess import Popen, PIPE, call
import subprocess
import time

# root_out_dir+'/MainRun/job_IDs.txt'

import optparse

def main():
    parser = optparse.OptionParser()
    parser.add_option("-l", "--loc", dest="id_dir", help="Location of text file with job IDs")
    (options, args) = parser.parse_args()
    if options.id_dir is None:
        print("Please enter a location using the -l or --loc flag")
        
    else:
        check_job_completionID = np.loadtxt(options.id_dir, delimiter=',')
        print('check_job_completionID', check_job_completionID)

        No_jobs_failed = True
        ###########################
        # Now wait for your (last) job to be done
        for job_id in check_job_completionID:
            job_id=int(job_id)
            print('job_id', job_id)
            command = "sacct  -j %s --format State "%(job_id)
            print(command)
            done = False
            while not done:
                # First check the status of your job with sacct
                p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
                nline = 0
                while True:
                    line = p.stdout.readline()
                    nline +=1
                    #print(line)
                    if not line:
                        break
                    if nline == 3:
                        break


                result = str(line)
                print('result = ', result)
                if b"COMPLETE" in line:
                    print('YAY your job finished! ID = %s'%(job_id) )
                    done = True
                elif b"FAILED" in line:
                    print('Job failed :(  ID=%s'%(job_id) )
                    done = True
                    No_jobs_failed = False
                elif b"CANCELLED" in line:
                    print('Job was CANCELLED  ID=%s'%(job_id) )
                    done = True
                    No_jobs_failed = False
                elif b"TIMEOUT" in line:
                    print('Job timed out! :(  ID=%s'%(job_id) )
                    done = True
                    No_jobs_failed = False
                elif np.logical_or(b"RUNNING" in line, b"PENDING" in line):
                    print('darn, still running, check back in 2 min')
                    time.sleep(120) # Sleep 2 min and then check back

        print(10* "*" + " You done with all your jobs! " + 10* "*")

        if No_jobs_failed:
            exit(0)  # exit with zero code if condition is true
        else:
            exit(1)  # exit with non-zero code if condition is false

     
        
##############################
# Run Main
if __name__ == '__main__':
    main()

