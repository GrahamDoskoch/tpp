""".
TPP Job Launcher: Thorny Flat Edition


Author:    Sarah Burke-Spolaor but also maybe mostly Joe Glaser
Init Date: 22 May 2023

This code will be called by the Globus file transfer script (intended
to be written by Joe Glaser). The purpose of this code is to:

 - Be runnable on Thorny Flat (and potentially easily repurposed for
   Dolly Sods in the future).

 - Access the TPP database manager (TPP-DB).

 - Identify the location of requested data from TPP-DB.

 - Set up the job command and call Slurm.

 - Also initiate a Globus transfer of H5 files to JBOD at the end of
   the script? Or will this be done in the processing script? - Joe G
   to comment.


THERE ARE SEVERAL REASONS LAUNCHER SHOULD FORCE-FAIL:

 - It can't reach the TPP database.
 - It can't find the file.
 - There's not enough space on thorny flat.

""" 

# -----------------------------------------------
# General Module Imports
# -----------------------------------------------
import subprocess # To call sbatch/slurm.
import yaml       # For reading private authentication data.
import globus_sdk as globus
from globus_sdk.scopes import TransferScopes
import argparse
import database as db
from datetime import datetime
import getpass
import traceback
import file_manager as fm


# -----------------------------------------------
# BEGIN MAIN LOOP
# -----------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Launches the TPP pipeline.")
    parser.add_argument('--dataID', '-d', dest='dataID', type=int, default=None,
                        help="The Unique Data Identifier for the file to be processed by the pipeline.")
    args = parser.parse_args()

    # -----------------------------------------------
    # Data Identifier & Configuration File
    # -----------------------------------------------
    # Data ID requested by user (unique identifier in DM of file to be processed).
    dataID = args.dataID
    if dataID == None:
        print("Please enter the Unique Data Identifier to be processed. Otherwise this code cannot run.")
        exit()


    # -----------------------------------------------
    # GLOBUS Configuration
    # -----------------------------------------------

    # Requires Globus Authentication with West Virginia University as the IdP
    CLIENT_ID = db.dbconfig.globus_client_id
    auth_client = globus.NativeAppAuthClient(CLIENT_ID)
    auth_client.oauth2_start_flow(refresh_tokens=True, requested_scopes=TransferScopes.all)

    
    # Begin authorization via URL & User Input Code to Retrieve Token
    ## TODO: Use the Refresh_Tokens to Enable SSO Authentication for 24-Hours (work-around to avoid constant duofactor authentication)
    authorize_url = auth_client.oauth2_get_authorize_url()
    print(f"Please go to this URL and login:\n\n{authorize_url}\n")
    auth_code = input("Please enter the code here: ").strip()
    tokens = auth_client.oauth2_exchange_code_for_tokens(auth_code)
    transfer_tokens = tokens.by_resource_server["transfer.api.globus.org"]

    # Construct the AccessTokenAuthorizer to Enable the TransferClient (tc)
    tc = globus.TransferClient(authorizer=globus.AccessTokenAuthorizer(transfer_tokens["access_token"]))

    # Set Up the Storage Collection and Compute Collection IDs
    storage = db.dbconfig.globus_stor_id
    compute = db.dbconfig.globus_comp_id



    # -----------------------------------------------
    # Set up TPP-DB connections
    # -----------------------------------------------

    time_start = datetime.utcnow()
    
    try:
        # Check that the current pipeline is being run (and test of TPPDB connection).
        current_pipelineID = db.current_pipelineID()

        # Test that the requested dataID exists.
        db_response = db.get("data",dataID)
        file_dir = db_response['location_on_filesystem']
        file_base = db_response['regex_filename']
        file_location = file_base + file_dir

        #Initiate submissions doc, after we are sure that the job is likely to be launched successfully.
        submissionID = db.init_document("job_submissions",dataID,pipelineID=current_pipelineID)
        print("Created submissionID "+str(submissionID))
        username = getpass.getuser()
        db.patch("job_submissions",submissionID,data={"started_globus":time_start.isoformat(),"username":username})
    except:
        # Hopefully db will print all appropriate errors.
        # Here we want to exit if there are fundamental issues with the DB.
        # Send error to submissionID STATUS. This will only work if there isn't a tppdb comms error.
        db.patch("job_submissions",submissionID,data={"status":{"date_of_completion":time_start.isoformat(),"error":traceback.format_exc()}})
        exit()
                


    # -----------------------------------------------
    # Transfer Necessary Files to Compute FS
    # -----------------------------------------------        
    stor_location = file_base + file_dir
    print("Will transfer file from " + stor_location)

    # Construct the Location on Compute FS
    ## TODO: Finalize FS Structure on Compute
    comp_location = db.dbconfig.globus_comp_dir

    # Transfer the Required files from Storage to Compute
    try:
        time_UTC = datetime.utcnow().isoformat()
        db.patch("job_submissions",submissionID,data={"started_transfer_data":time_UTC,"target_directory":comp_location})
    except:
        # Send error to submissionID STATUS. This will only work if there isn't a tppdb comms error.
        time_UTC = datetime.utcnow().isoformat()
        db.patch("job_submissions",submissionID,data={"status":{"date_of_completion":time_UTC,"error":traceback.format_exc()}})
        exit()


    # Here is a main operation: Transfer data from storaget to compute location.
    try:
        fm.manage_single_transfer(tc, storage, compute, stor_location, comp_location)
    except:
        # Send error to submissionID STATUS. This will only work if there isn't a tppdb comms error.
        time_UTC = datetime.utcnow().isoformat()
        db.patch("job_submissions",submissionID,data={"status":{"date_of_completion":time_UTC,"error":traceback.format_exc()}})
        exit()

    
    # -----------------------------------------------
    # Launch the TPP Pipeline via the SLURM Command
    # -----------------------------------------------

    # Set up logging directory and file. !H!H!H need to get slurm to write to this log!
    log_name = f"{time_start.year:04d}{time_start.month:02d}{time_start.day:02d}_{time_start.hour:02d}{time_start.minute:02d}{time_start.second:02d}_{submissionID}.log"
    log_dir = comp_location
    
    # Initiate outcome doc before job submission.
    # Also Initiate RESULTS document? -- I don't think so, it can be written at
    # end of pipeline. Outcomes will track progress. But add it in here if
    # there's a need to have the diagnostics that an incomplete results DB could
    # provide (over, for instance, the job log alone).
    try:
        outcomeID = db.init_document("processing_outcomes",dataID,submissionID=submissionID)
        #!H!H!H  ADD RESULTS DOC HERE?
        time_UTC = datetime.utcnow().isoformat()
        db.patch("job_submissions",submissionID,data={"started_slurm":time_UTC,"log_name":log_name,"log_dir":log_dir})
    except:
        # Send error to submissionID STATUS. This will only work if there isn't a tppdb comms error.
        time_UTC = datetime.utcnow().isoformat()
        db.patch("job_submissions",submissionID,data={"status":{"date_of_completion":time_UTC,"error":traceback.format_exc()}})
        exit()
    
    # Need to figure out how to pass to tpp_pipeline at least the outcomeID relevant to this job.
    #!H!H!H
    # Use command line to call slurm.

    #SBATCH --nodes=1  # number of nodes
    #SBATCH --ntasks-per-node=10
    #SBATCH --partition=comm_gpu_inter 
    #SBATCH --gres=gpu:1
    #SBATCH --gpu_cmode=shared
    #SBATCH --job-name=TPP_pipeline
    #SBATCH --mail-user=rat0022@mix.wvu.edu
    #SBATCH --mail-type BEGIN,END,FAIL

    tpp_pipe = #!!! THE LOCATION OF tpp_pipeline.py

    max_jobtime = 5760 # Set jobs to force fail after 4 full days of processing.

    slurm_settings = f"--time={max_jobtime} --nodes=1 --ntasks-per-node=10 --job-name=\"TPP-{submissionID}\" --partition=comm_gpu_week --gres=gpu:1 --mail-user={username}@mix.wvu.edu --mail-type BEGIN,END,FAIL --wrap=\"singularity exec /shared/containers/radio_transients/radio_transients.sif {tpp_pipe}
-f {} filename  !!! NEED TO ADD these arguments to tpp_pipeline and make sure we have the right values here.
-s {} submission id
-wd {} working dir
"

    # !!! NOTE THE -W below forces the sbatch sub-process to not finish until the batch job actually completes (with failure or success). 
    subprocess.run(["sbatch","-W --time=5-23:45:00 --nodes=1 --ntasks-per-node=10 --job-name=\"tpp-\" --partition=thepartitiontouse --wrap=\" ; COMMAND TO RUN"]) ###### NEED TO FIX THIS

    # Communicate to TPP-Database that the SLURM Job has been Launched

    # Wait for Slurm Job to Complete, Alerting any Errors

    # Communicate to TPP-Database the Final Status of SLURM Job

    # !!!! NEED TO ADD A WAIT HERE AND HAVE IT LOOK FOR THE COMPLETED SLURM JOB

    
    
    # -----------------------------------------------
    # Transfer Products from Compute to Storage
    # -----------------------------------------------
    try:
        time_UTC = datetime.utcnow().isoformat()
        db.patch("job_submissions",submissionID,data={"status":"final transfer"})
    except:
        # Send error to submissionID STATUS. This will only work if there isn't a tppdb comms error.
        time_UTC = datetime.utcnow().isoformat()
        db.patch("job_submissions",submissionID,data={"status":{"date_of_completion":time_UTC,"error":traceback.format_exc()}})
        exit()

    #Construct the Location on Compute FS of Products
    #!!! THIS BLOCK should find the hdf5 file and related plots that were produced and then transfer them. MAKE SURE IT KNOWS WHAT THE FULL FILE NAME/DIR TO LOOK FOR.
    comp_location_hdf5 = db.dbconfig.globus_comp_dir+"BLAHBLAHBLAH"+".hdf5"

    #Construct the Location on Storage FS of Products
    #!!!! Add algorithm here to determine final output directory which will ultimately be on tingle (but temporarily can be wherever for testing)
    stor_location_hdf5 = db.dbconfig.globus_res_dir+"/BLAHBLAHBLAH"+".hdf5"

    #Transfer the Final Products from Compute to Storage
    try:
        fm.manage_single_transfer(tc, compute, storage, comp_location_hdf5, stor_location_hdf5)
    except:
        # Send error to submissionID STATUS. This will only work if there isn't a tppdb comms error.
        time_UTC = datetime.utcnow().isoformat()
        db.patch("job_submissions",submissionID,data={"status":{"date_of_completion":time_UTC,"error":traceback.format_exc()}})
        exit()
        
    time_end = utc.datetime()

    delta_time = time_end - time_start
    duration_minutes = int(delta_time.seconds()/60)
    
    try:
        time_UTC = datetime.utcnow().isoformat()
        db.patch("job_submissions",submissionID,data={"status":{"competed":True,"date_of_completion":time_UTC},"duration":duration_minutes})
    except:
        # Send error to submissionID STATUS. This will only work if there isn't a tppdb comms error.
        time_UTC = datetime.utcnow().isoformat()
        db.patch("job_submissions",submissionID,data={"status":{"date_of_completion":time_UTC,"error":traceback.format_exc()}})
        exit()



    
# CUSTOM CONFIG FILE FUNCTIONALITY not yet allowed or implemented. It remains here as a historical relic.
#config    parser.add_argument('--config', '-c', dest='config_file', type=str, default=None,
#config                        help="The user-specific Configuration File required for the pipeline (USUALLY YOU SHOUDL NOT SPECIFY THIS. By default user's config.yml will be read from the TPP pipeline install directory.", required=False)
#config    # Configuration YAML provided by the user (contains tokens, networking settings, etc)
#config    config_file = args.config_file
#config    if config_file == None:
#config        config_file = input("Please enter the absolute path of your TPP Configuration File: ").strip()
#config
#config    # Read config file for authentication info
#config    with open(config_file, 'r') as file:
#config        config = yaml.safe_load(file)
#config
#config    # Set Required Variables
#config    tppdb_ip = config['tpp-db']['url']
#config    tppdb_port = config['tpp-db']['port']
#config    user_token = config['tpp-db']['token']
#config    # -----------------------------------------------
#config    # TPP-Database Communication Configuration
#config    # -----------------------------------------------
#config    tppdb_base = "http://" + tppdb_ip + ":" + tppdb_port
#config    tppdb_data = tppdb_base + "/data"
#config    headers = {"Authorization": f"Bearer{user_token}"}
#config    CLIENT_ID = config['globus']['client_id']
#config    storage = config['globus']['storage_collection_id']
#config    compute = config['globus']['compute_collection_id']
#config    comp_location = config['globus']['compute_scratch_dir']+"BLAHBLAHBLAH"
