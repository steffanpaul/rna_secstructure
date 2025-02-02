from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os, sys, h5py
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sb
import tensorflow as tf
import scipy

import sys
sys.path.append('../../..')
import mutagenesisfunctions as mf
import helper
from deepomics import neuralnetwork as nn
from deepomics import utils, fit, visualize, saliency

from Bio import AlignIO
import time as time
import pandas as pd
np.random.seed(274)


def make_variable_length(X_train_fixed, addon=5):
    N = len(X_train_fixed)
    start_buffer = np.random.randint(1, addon, size=N)
    end_buffer = np.random.randint(1, addon, size=N)

    X_train_all = []
    starts = []
    for n in range(N):
        start_size = start_buffer[n]
        start_index = np.random.randint(0,4, size=start_size)
        start_one_hot = np.zeros((start_size, 4))
        for i in range(start_size):
            start_one_hot[i, start_index[i]] = 1
        starts.append(len(start_index))
        
        end_size = end_buffer[n]
        end_index = np.random.randint(0,4, size=end_size)
        end_one_hot = np.zeros((end_size, 4))
        for i in range(end_size):
            end_one_hot[i, end_index[i]] = 1
        X_train_all.append(np.vstack([start_one_hot, X_train_fixed[n], end_one_hot]))
    return X_train_all, starts
#---------------------------------------------------------------------------------------------------------------------------------
'''DEFINE ACTIONS'''
TEST = False
WRITE = False
FOM = False
SOMCALC = False
SOMVIS = False

if '--test' in sys.argv:
  TEST = True
if '--write' in sys.argv:
  WRITE = True
if '--fom' in sys.argv:
  FOM = True
if '--somcalc' in sys.argv:
  SOMCALC = True
if '--somvis' in sys.argv:
  SOMVIS = True

#---------------------------------------------------------------------------------------------------------------------------------
'''DEFINE LOOP'''
trials = ['small']#['small', 'med', 'large']
varlengths = [10]#[10, 20, 30]
exp = 'toyhp'  #for both the data folder and the params folder
exp_data = 'data_%s'%(exp)

img_folder = 'Images'

for t in trials:
  for v in varlengths:

    #---------------------------------------------------------------------------------------------------------------------------------

    '''OPEN DATA'''

    starttime = time.time()

    #Open data from h5py
    filename = '%s_50k_%s.hdf5'%(exp, t)
    data_path = os.path.join('../..', exp_data, filename)
    with h5py.File(data_path, 'r') as dataset:
        X_data = np.array(dataset['X_data'])
        Y_data = np.array(dataset['Y_data'])
        
    numdata, seqlen, dims = X_data.shape
    

    #Make variable!
    addon = v
    X_data, starts = make_variable_length(X_data, addon=addon)
    #pad
    X_data, _ = helper.pad_inputs(X_data, MAX=helper.get_maxlength(X_data))

    X_data = np.expand_dims(X_data, axis=2)
    # get validation and test set from training set
    test_frac = 0.3
    valid_frac = 0.1
    N = numdata
    split_1 = int(N*(1-valid_frac-test_frac))
    split_2 = int(N*(1-test_frac))
    shuffle = np.random.permutation(N)

    #set up dictionaries
    train = {'inputs': X_data[shuffle[:split_1]], 
             'targets': Y_data[shuffle[:split_1]]}
    valid = {'inputs': X_data[shuffle[split_1:split_2]], 
             'targets': Y_data[shuffle[split_1:split_2]]}
    test = {'inputs': X_data[shuffle[split_2:]], 
             'targets': Y_data[shuffle[split_2:]]}
    test_starts = np.asarray(starts)[shuffle[split_2:]]    
        
    print ('Data extraction and dict construction completed in: ' + mf.sectotime(time.time() - starttime))


    #---------------------------------------------------------------------------------------------------------------------------------


    '''SAVE PATHS AND PARAMETERS'''
    params_results = '../../results'

    modelarch = 'mlp'
    trial = 'var' + str(v) + t
    modelsavename = '%s_%s'%(modelarch, trial)



    '''BUILD NEURAL NETWORK'''

    def cnn_model(input_shape, output_shape):

      # create model
      layer1 = {'layer': 'input', #41
              'input_shape': input_shape
              }

      layer2 = {'layer': 'dense',        # input, conv1d, dense, conv1d_residual, dense_residual, conv1d_transpose,
                                          # concat, embedding, variational_normal, variational_softmax, + more
                'num_units': 44,
                'norm': 'batch',          # if removed, automatically adds bias instead
                'activation': 'relu',     # or leaky_relu, prelu, sigmoid, tanh, etc
                'dropout': 0.5,           # if removed, default is no dropout
               }

      layer3 = {'layer': 'dense',
              'num_units': output_shape[1],
              'activation': 'sigmoid'
              }

      model_layers = [layer1, layer2, layer3]

      # optimization parameters
      optimization = {"objective": "binary",
                    "optimizer": "adam",
                    "learning_rate": 0.0003,
                    "l2": 1e-5,
                    #"label_smoothing": 0.05,
                    #"l1": 1e-6,
                    }
      return model_layers, optimization

    tf.reset_default_graph()

    # get shapes of inputs and targets
    input_shape = list(train['inputs'].shape)
    input_shape[0] = None
    output_shape = train['targets'].shape

    # load model parameters
    model_layers, optimization = cnn_model(input_shape, output_shape)

    # build neural network class
    nnmodel = nn.NeuralNet(seed=247)
    nnmodel.build_layers(model_layers, optimization)

    # compile neural trainer
    save_path = os.path.join(params_results, exp)
    param_path = os.path.join(save_path, modelsavename)
    nntrainer = nn.NeuralTrainer(nnmodel, save='best', file_path=param_path)

    sess = utils.initialize_session()
    '''TEST'''
    if TEST:
      
      # set best parameters
      nntrainer.set_best_parameters(sess)

      # test model
      loss, mean_vals, std_vals = nntrainer.test_model(sess, test, name='test')
      if WRITE:
        metricsline = '%s,%s,%s,%s,%s,%s,%s'%(exp, modelarch, trial, loss, mean_vals[0], mean_vals[1], mean_vals[2])
        fd = open('test_metrics.csv', 'a')
        fd.write(metricsline+'\n')
        fd.close()
    '''SORT ACTIVATIONS'''
    nntrainer.set_best_parameters(sess)
    predictionsoutput = nntrainer.get_activations(sess, test, layer='output')
    plot_index = np.argsort(predictionsoutput[:,0])[::-1]

    #---------------------------------------------------------------------------------------------------------------------------------
    '''FIRST ORDER MUTAGENESIS'''
    if FOM:
      num_plots = range(1)
      for ii in num_plots: 

          X = np.expand_dims(test['inputs'][plot_index[10000+ii]], axis=0)
          
          mf.fom_saliency(X, layer='dense_1_bias', alphabet='rna', nntrainer=nntrainer, sess=sess, figsize=(15,1.5))
          fom_file = modelsavename + 'FoM' + '.png'
          fom_file = os.path.join(img_folder, fom_file)
          plt.savefig(fom_file)

      plt.close()
    #---------------------------------------------------------------------------------------------------------------------------------
    '''SECOND ORDER MUTAGENESIS'''

    '''Som calc'''
    if SOMCALC:
      num_summary = 2000

      arrayspath = 'Arrays/%s_%s%s_so%.0fk.npy'%(exp, modelarch, trial, num_summary/1000)
      X = test['inputs'][plot_index[:num_summary]]

      ugidx_list = [range(test_starts[plot_index[s]], seqlen+test_starts[plot_index[s]]) for s in range(num_summary)]
      mean_mut2 = helper.som_average_ungapped_logodds(X, ugidx_list, arrayspath, nntrainer, sess, progress='short', 
                                                 save=True, layer='dense_1_bias')

    if SOMVIS:  
      #Load the saved data
      num_summary = 2000
      arrayspath = 'Arrays/%s_%s%s_so%.0fk.npy'%(exp, modelarch, trial, num_summary/1000)
      mean_mut2 = np.load(arrayspath)

      #Reshape into a holistic tensor organizing the mutations into 4*4
      meanhol_mut2 = mean_mut2.reshape(seqlen,seqlen,4,4)

      #Normalize
      normalize = True
      if normalize:
          norm_meanhol_mut2 = mf.normalize_mut_hol(meanhol_mut2, nntrainer, sess, normfactor=1)

      #Let's try something weird
      bpfilter = np.ones((4,4))*0.
      for i,j in zip(range(4), range(4)):
          bpfilter[i, -(j+1)] = +1.

      nofilter = np.ones((4,4))

      C = (norm_meanhol_mut2*bpfilter)
      C = np.sum((C).reshape(seqlen,seqlen,dims*dims), axis=2)
      #C = C - np.mean(C)
      #C = C/np.max(C)

      plt.figure(figsize=(15,6))
      plt.subplot(1,2,1)
      sb.heatmap(C,vmin=None, cmap='Blues', linewidth=0.0)
      plt.title('Base Pair scores: %s %s %s'%(exp, modelarch, trial))

      som_file = modelsavename + 'SoM_bpfilter' + '.png'
      som_file = os.path.join(img_folder, som_file)
      plt.savefig(som_file)
      plt.close()


      blocklen = np.sqrt(np.product(meanhol_mut2.shape)).astype(int)
      S = np.zeros((blocklen, blocklen))
      i,j,k,l = meanhol_mut2.shape

      for ii in range(i):
          for jj in range(j):
              for kk in range(k):
                  for ll in range(l):
                      S[(4*ii)+kk, (4*jj)+ll] = meanhol_mut2[ii,jj,kk,ll]

      plt.figure(figsize=(15,15))
      plt.imshow(S,  cmap='Reds', vmin=None)
      plt.colorbar()
      plt.title('Blockvis of all mutations: %s %s %s'%(exp, modelarch, trial))

      som_file = modelsavename + 'SoM_blockvis' + '.png'
      som_file = os.path.join(img_folder, som_file)
      plt.savefig(som_file)
      plt.close()