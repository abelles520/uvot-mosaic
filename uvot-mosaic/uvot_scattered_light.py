import numpy as np
import matplotlib.pyplot as plt
import glob
import os
import subprocess
import copy

from astropy.io import fits
from astropy.table import Table
from astropy.convolution import Gaussian2DKernel
from astropy.convolution import convolve
from astropy.stats import biweight_location, sigma_clip, biweight_midvariance
import aplpy

import pdb

def fix_sl(input_folders,
                  output_prefix,
                  filter_list=['w2','m2','w1','uu','bb','vv'],
                  fix_redo=False):
    """
    Manually apply scattered light (SL) corrections.  The SL images generated by uvot_deep are automatically found using the input_folder and filter list.

    The parameterization is currently fairly arbitrary (though hopefully will be better quantified soon!).  Parameters will be saved in a file in the image folder.

    Parameters
    ----------
    input_folders : list of strings
        each item of the string is the 11-digit name of the folder downloaded from HEASARC

    output_prefix : string
        the prefix for output files (be sure to include an underscore or similar for readability)

    filter_list : list of strings
        some or all of ['w2','m2','w1','uu','bb','vv'] (default is all of them)

    fix_redo : boolean (default=False)
        choose whether to redo any of the ones that have already been done, otherwise just skip to new ones


    Returns
    -------
    nothing

    """


    # ------------------------
    # identify the filters in each snapshot
    # ------------------------

    # dictionary to hold filters that exist for each folder
    filter_exist = {key:[] for key in input_folders}
    
    for i in input_folders:

        # list all of the sky images
        sk_list = glob.glob(i + '/uvot/image/*_sk.img')
       
        # check that images exist
        if len(sk_list) == 0:
            print('No images found for input folder: ' + i)

        # grab the filter from the filename of each sky image
        for sk in sk_list:
            filter_name = sk[-9:-7]
            if filter_name in filter_list:
                filter_exist[i].append(filter_name)


    # ------------------------
    # go through each filter and build the images
    # ------------------------

    for filt in filter_list:

        # get the images that have observations in that filter
        obs_list = [im for im in filter_exist.keys() if filt in filter_exist[im]]

        # check that images exist
        if len(obs_list) == 0:
            print('No images found for filter: ' + filt)
            continue


        for obs in obs_list:

            print('')
            print('*************************************************************')
            print('  observation ', obs, ', filter = ', filt)
            print('*************************************************************')
            print('')

            
            # the SL image file for this obs/filter
            sl_image = obs+'/uvot/image/sw'+obs+'u'+filt+'.sl'

            # check that it exists
            if not os.path.isfile(sl_image):
                print('No scattered light image for '+filt+' in '+obs)
                continue

            # the sky (counts) image, corrected for LSS
            sk_image = obs+'/uvot/image/sw'+obs+'u'+filt+'_sk_corr.img'

            # file where SL fits will be saved
            sl_file = obs+'/uvot/image/sw'+obs+'u'+filt+'_sl.info'

            # do the manual adjusting
            sl_manual(sk_image, sl_image, sl_file, fix_redo=fix_redo)

            # apply the parameters to the images
            sl_apply(sk_image, sl_image, sl_file)
            
            #pdb.set_trace()



def sl_apply(sk_image, sl_image, sl_file):
    """
    Apply the manual correction to create/save the new counts images

    Parameters
    ----------
    sk_image : string
        path+file name for the sky (counts) image

    sl_image : string
        path+file name for the scattered light image

    sl_file : string
        path+file name to save parameters for best-fit SL image


    Returns
    -------
    nothing
    
    """

    print('applying SL corrections to sky image')

    # read SL corrections
    sl_data = Table.read(sl_file, format='ascii')
    
    with fits.open(sk_image) as hdu_sk, fits.open(sl_image) as hdu_sl:

        # HDU to hold the new images
        hdu_new = fits.HDUList()
        # first empty extension
        hdu_new.append(fits.ImageHDU(data=hdu_sk[0].data, header=hdu_sk[0].header))

        for i in range(1,len(hdu_sk)):

            # calculate scaled SL image
            new_image = calc_counts_image(hdu_sk[i].data, hdu_sl[i].data,
                                              sl_data['exp_param'][i-1],
                                              sl_data['flat_param'][i-1])

            # append to HDU list
            hdu_new.append(fits.ImageHDU(data=new_image, header=hdu_sk[i].header))

        # write out the file
        hdu_new.writeto(sk_image.replace('_corr.img','_corr_sl.img'), overwrite=True)



def sl_manual(sk_image, sl_image, sl_file, fix_redo=False):
    """
    Wrapper for the part where there is manual adjusting

    Parameters
    ----------
    sk_image : string
        path+file name for the sky (counts) image

    sl_image : string
        path+file name for the scattered light image

    sl_file : string
        path+file name to save parameters for best-fit SL image

    fix_redo : boolean (default=False)
        choose whether to redo any of the ones that have already been done, otherwise just skip to new ones

    Returns
    -------
    nothing

    """

    # if the SL file exists, open it
    if os.path.isfile(sl_file):
        sl_data = Table.read(sl_file, format='ascii')
    # otherwise, make a new table
    else:
        sl_data = Table(names=('tstart','exp_param','flat_param'))

    
    with fits.open(sk_image) as hdu_sk, fits.open(sl_image) as hdu_sl:

        for i in range(1,len(hdu_sk)):

            # get start time (it's the most unique identifier for a given snapshot)
            tstart = hdu_sk[i].header['tstart']

            # that time is in the table, and fix_redo=True,
            # OR
            # that time isn't in the table
            # -> do the calculations
            if tstart in sl_data['tstart'] and fix_redo == True:
                print('starting manual corrections for extension '+str(i))
                ind = np.where(tstart == sl_data['tstart'])[0][0]
                exp_param, flat_param = run_manual(hdu_sk[i], hdu_sl[i], sl_data['exp_param'][ind],
                                                       sl_data['flat_param'][ind])
                sl_data['exp_param'][ind] = exp_param
                sl_data['flat_param'][ind] = flat_param                
                sl_data.write(sl_file, format='ascii', overwrite=True)
            elif tstart not in sl_data['tstart']:
                print('starting manual corrections for extension '+str(i))
                #exp_param, flat_param = run_manual(hdu_sk[i], hdu_sl[i], 1.5, 0.35)
                exp_param, flat_param = run_manual(hdu_sk[i], hdu_sl[i], 1.2, 0.4)
                sl_data.add_row([tstart, exp_param, flat_param])
                sl_data.write(sl_file, format='ascii', overwrite=True)
            # otherwise, skip it
            else:
                print('skipping manual corrections for extension '+str(i))
            



def run_manual(hdu_sk, hdu_sl, exp_param, flat_param):
    """
    The nuts and bolts of getting/updating SL stretch

    Parameters
    ----------
    hdu_sk : HDU
        hdu for the sky (counts) image

    hdu_sl : HDU
        hdu for the scattered light image

    exp_param : float
        value for the exp_param to apply to the SL image

    fix_redo : boolean (default=False)
        choose whether to redo any of the ones that have already been done, otherwise just skip to new ones

    Returns
    -------
    nothing

    """

    # smooth the counts image for easier viewing
    kernel = Gaussian2DKernel(8)
    hdu_sk_smooth = copy.copy(hdu_sk)
    hdu_sk_smooth.data = convolve(hdu_sk.data, kernel)

    # make a copy to hold new smoothed images
    hdu_sk_smooth_new = copy.copy(hdu_sk)

    
    while True:

        # set up a figure
        fig = plt.figure(figsize=(10,5),
                             num='exp_param='+str(exp_param)+', flat_param='+str(flat_param))
        
        # plot original
        f = aplpy.FITSFigure(hdu_sk_smooth, figure=fig,
                                subplot=[0, 0, 0.45, 1] )
        #vmin = np.percentile(hdu_sk_smooth.data[hdu_sk_smooth.data > 0], 2)
        #vmin = biweight_location(hdu_sk_smooth.data[hdu_sk_smooth.data > 0])/1.5
        #vmin = biweight_location(hdu_sk_smooth.data[hdu_sk_smooth.data > 0]) \
        #       - biweight_midvariance(hdu_sk_smooth.data[hdu_sk_smooth.data > 0])
        filt = sigma_clip(hdu_sk_smooth.data[hdu_sk_smooth.data > 0], sigma=2, iters=3)
        vmin = np.mean(filt.data[~filt.mask]) - 3*np.std(filt.data[~filt.mask])
        vmax = np.percentile(hdu_sk_smooth.data[hdu_sk_smooth.data > 0], 99)

        vmax = np.percentile(hdu_sk_smooth.data[hdu_sk_smooth.data > 0], 99)
        f.show_colorscale(cmap='magma', stretch='log', vmin=vmin, vmax=vmax)
        f.hide_xaxis_label()
        f.hide_xtick_labels()
        f.hide_yaxis_label()
        f.hide_ytick_labels()
        f.ticks.hide()
        f.frame.set_linewidth(0)

        
        # calculate scaled SL image
        new_image = calc_counts_image(hdu_sk.data, hdu_sl.data,
                                          exp_param, flat_param)

        # smooth new image for displaying
        hdu_sk_smooth_new.data = convolve(new_image, kernel)

        
        # plot the new image
        f = aplpy.FITSFigure(hdu_sk_smooth_new, figure=fig,
                                subplot=[0.5, 0, 0.45, 1] )
        #vmin = np.percentile(hdu_sk_smooth_new.data[hdu_sk_smooth.data > 0], 2)
        #vmin = biweight_location(hdu_sk_smooth_new.data[hdu_sk_smooth_new.data > 0])
        f.show_colorscale(cmap='magma', stretch='log', vmin=vmin, vmax=vmax)
        f.hide_xaxis_label()
        f.hide_xtick_labels()
        f.hide_yaxis_label()
        f.hide_ytick_labels()
        f.ticks.hide()
        f.frame.set_linewidth(0)


        # ask for input
        print('current exp_param and flat_param: '+str(exp_param)+', '+str(flat_param))
        input_info = input('new exp/flat, or one value if done: ')

        # parse input
        input_parse = [i for i in input_info.replace(',',' ').split(' ') if i != '']
        if len(input_parse) == 1:
            break
        if len(input_parse) == 2:
            exp_param = float(input_parse[0])
            flat_param = float(input_parse[1])


    # return the results
    return exp_param, flat_param
    

def calc_counts_image(sk_array, sl_array, exp_param, flat_param):
    """
    The math calculation:
    using the counts and SL images, output the new corrected counts image
    """
    
    # - ignore the giant 0 border
    fov = np.where(sl_array > 0)
        
    # - subtract the minimum
    sl_array[fov] -= np.min(sl_array[fov])
    # - make the circle more prominent relative to bg
    sl_array[fov] = exp_param**sl_array[fov]
    # - make the mean = 1
    sl_array = sl_array / np.mean(sl_array[fov])
    # - flatten it
    m = np.mean(sl_array[fov])
    sl_array -= m
    sl_array *= flat_param
    sl_array += m
        
    # make a new image: counts / scattered light
    new_image = sk_array / sl_array

    return new_image
