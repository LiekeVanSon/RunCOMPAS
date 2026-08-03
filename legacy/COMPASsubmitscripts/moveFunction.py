## Created on 2025-07-21 by lvanson
## This script moves files into the MainRun/ subdirectory for each simulation variation
## Was tested only in another notebook, not sure it's self-consistent as a standalone script, but should be close
import os
import glob
from datetime import datetime
import shutil
import json

# get the home dir 
home = os.path.expanduser("~")

# open the json file with the flag variations
with open("./masterfolder/simulation_variations.json", "r") as f:
    flag_variants = json.load(f)


# I made a mistake when running the initial script, I now have to loop over all variations and mv
# batch_*/ , distributions.csv,  samples.csv, log.txt, logs/ 
# into new_dir/ MainRun/ 

base_dir = f"{home}/ceph/CompasOutput/v03.21.00/N5e6_MassiveWDWD_NSNS_"


for variant in flag_variants:
    simname = variant["simname"]
    new_dir = os.path.join(base_dir + simname)
    mainrun_dir = os.path.join(new_dir, "MainRun") 
    os.makedirs(mainrun_dir, exist_ok=True)

    # Log the terminal output from all this moving
    log_path = os.path.join(new_dir, f"move_log_{simname}.txt")
    with open(log_path, "w") as log:
        def log_msg(msg):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            full_msg = f"[{timestamp}] {msg}"
            print(full_msg)
            log.write(full_msg + "\n")

        log_msg(f"--- Fixing files for: {simname} ---")

        # 1. Move batch_*/ folders
        for batch_path in glob.glob(os.path.join(new_dir, "batch_*")):
            dest = os.path.join(mainrun_dir, os.path.basename(batch_path))
            if os.path.exists(dest):
                log_msg(f"SKIPPED: {dest} already exists.")
                continue
            try:
                shutil.move(batch_path, dest)
                log_msg(f"MOVED: {batch_path} → {dest}")
            except Exception as e:
                log_msg(f"ERROR moving {batch_path}: {e}")

        # 2. Move sw info files
        for fname in ["distributions.csv", "samples.csv", "log.txt"]:
            src = os.path.join(new_dir, fname)
            dest = os.path.join(mainrun_dir, fname)
            if os.path.exists(src):
                if os.path.exists(dest):
                    log_msg(f"SKIPPED: {dest} already exists.")
                    continue
                try:
                    shutil.move(src, dest)
                    log_msg(f"MOVED: {src} → {dest}")
                except Exception as e:
                    log_msg(f"ERROR moving {src}: {e}")

        # 3. Move batch logs/ folder
        logs_src = os.path.join(new_dir, "logs")
        logs_dest = os.path.join(mainrun_dir, "logs")
        if os.path.exists(logs_src):
            if os.path.exists(logs_dest):
                log_msg(f"SKIPPED: {logs_dest} already exists.")
            else:
                try:
                    shutil.move(logs_src, logs_dest)
                    log_msg(f"MOVED: {logs_src} → {logs_dest}")
                except Exception as e:
                    log_msg(f"ERROR moving {logs_src}: {e}")

