##################################################
# Created 18 November 2020
# Lieke van son
# This folder contains the scripts that combine
# the raw COMPAS & stroopwagel output in a single hdf5
#################################################

(Hopefully this step will be redundant soon!)


#################################################
##			Step 2 PostProcessing			   ##
#################################################

This folder contains:


# # # # # # # # # # # # # # # 
2.1) postProcessing.py:
Looks into dataRootDir (= '../../output/' ) and all it's subdirectories for csv files that look like COMPAS output, it combines them into big csv files which are then appended to an hdf5 file 

WARNING this getts very slow, very fast if you have many grid directories and subdirectories.. It also takes up a lot of space

Set which groups you would like in the hdf5 file in:
    filesToCombine = [\
        'SystemParameters',\
    #    'CommonEnvelopes',\
        'DoubleCompactObjects',\
    #    'Supernovae',\
    #    'RLOF',\
    #    'errors',\            
    #    'output'\
    ]

 !!!!!!! Make sure h5Name='COMPAS_Output.h5', and dataRootDir='../../output/' are correct

# # # # # # # # # # # # # # # 
2.2) append_weights.py:
Takes the postprocessing output (by default COMPAS_Output.h5 ) and appends weights from the stroopwafel samples.csv file 


!!!!!!! Make sure SW_name, matches stroopwafel_interface.py output  and Raw_name matches postProcessing.py output 
SW_name  = 'samples.csv'
Raw_name = 'COMPAS_Output.h5' 

Also set what you want your new file to be called:
New_name = 'COMPAS_Output_wWeights.h5'