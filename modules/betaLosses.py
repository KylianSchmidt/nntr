
from __future__ import print_function
import tensorflow as tf
import keras
import keras.backend as K
from index_dicts import create_index_dict, split_feat_pred, create_feature_dict


#factorise a bit


#
#
#
#  all inputs/outputs of dimension B x V x F (with F being 1 in some cases)
#
#

def create_loss_dict(truth, pred):
    return create_index_dict(truth, pred)

def killNan(a):
    return a

def construct_ragged_matrices_indexing_tensors(row_splits):
    print('row_splits',row_splits)
    sub = row_splits[1:] - row_splits[:-1]
    a = sub**2
    b = tf.cumsum(a)
    b = tf.concat(([0], b), axis=0)
    ai = tf.ragged.row_splits_to_segment_ids(b)[..., tf.newaxis]
    b = tf.gather_nd(b, ai)
    c = tf.range(0, tf.reduce_sum(a)) - b
    vector_num_elements = tf.gather_nd(sub, ai)
    vector_range_within_batch_elements = c
    A = tf.cast(vector_range_within_batch_elements/vector_num_elements, tf.int64)[..., tf.newaxis]
    B = tf.math.floormod(vector_range_within_batch_elements,vector_num_elements)[..., tf.newaxis]

    batch_offset = tf.gather_nd(row_splits, ai)
    
    A+=batch_offset[..., tf.newaxis]
    B+=batch_offset[..., tf.newaxis]
    
    C = tf.concat(([0], tf.cumsum(tf.gather_nd(sub, tf.ragged.row_splits_to_segment_ids(row_splits)[..., tf.newaxis]))), axis=0)

    '''
    A is like this:
    [0 0 0 0 0 1 1 1 1 1 2 2 2 2 2 3 3 3 3 3 4 4 4 4 4 5!.. 0 0 0 1 1 1 1 2 2 2 2
     3 3 3 3 0 0 0 0 1 1 1 1 2 2 2 2 3 3 3 3 0 0 0 0 0 0 1 1 1 1 1 1 2 2 2 2 2
     2 3 3 3 3 3 3 4 4 4 4 4 4 5 5 5 5 5 5]
     
    B is like this:
    [0 1 2 3 4 0 1 2 3 4 0 1 2 3 4 0 1 2 3 4 0 1 2 3 4 5! 6! .. 1 2 3 0 1 2 3 0 1 2 3
     0 1 2 3 0 1 2 3 0 1 2 3 0 1 2 3 0 1 2 3 0 1 2 3 4 5 0 1 2 3 4 5 0 1 2 3 4
     5 0 1 2 3 4 5 0 1 2 3 4 5 0 1 2 3 4 5]
     
    C is like this:
    [ 0  5 10 15 20 25 29 33 37 41 45 49 53 57 63 69 75 81 87 93]
    
    C is used for constructing tensors with two ragged dimensions
     
    '''

    return A,B, C


def printAsRagged(msg, S, C , row_splits):
    print(msg, tf.RaggedTensor.from_row_splits(values=tf.RaggedTensor.from_row_splits(values =   S, row_splits=C), 
                                                     row_splits=row_splits))
    


def get_one_over_sigma(beta, beta_min=1e-2):
    return (( 1. / (1. - beta + K.epsilon()) - 1.) + beta_min)
    



def get_neighbour_loss(nneighbours, ccoords, row_splits, beta, is_noise, cluster_asso, beta_min=1e-3):
    
    row_splits = tf.reshape(row_splits, (-1,))
    batch_size_plus_1 = tf.cast(row_splits[-1], tf.int32)
    row_splits32 = tf.slice(row_splits, [0], batch_size_plus_1[..., tf.newaxis])
    row_splits32 = tf.cast(row_splits32, tf.int32)
    row_splits = row_splits[:batch_size_plus_1,...]
    
    
    from rknn_op import rknn_ragged, rknn_op
    
    #stoachastic + nearest neighbours = random close-by neighbours
    random_smeared_ccoords = ccoords + tf.random.normal(tf.shape(ccoords))
    
    ragged_split_added_indices, _ = rknn_op.RaggedKnn(num_neighbors=int(nneighbours), row_splits=row_splits32, data=random_smeared_ccoords, add_splits=True) # [SV, N+1]
    ragged_split_added_indices = ragged_split_added_indices[:,0:][..., tf.newaxis]  # [SV, N], also use self

    d_square = tf.reduce_sum((ccoords[:, tf.newaxis, :] - tf.gather_nd(ccoords, ragged_split_added_indices))**2, axis=-1)  # [SV, N]
    distance = tf.sqrt(d_square+K.epsilon())
    
    
    cluster_asso = tf.expand_dims(cluster_asso, axis=1)
    S = tf.reduce_sum((cluster_asso[:, tf.newaxis, :] - tf.gather_nd(cluster_asso, ragged_split_added_indices))**2, axis=-1) # SV x N
    
    S = tf.where(S < 0.1, tf.zeros_like(S)+1., tf.zeros_like(S))
    Snot = tf.where( S > 0.5, tf.zeros_like(S), tf.zeros_like(S)+1.)
    
   
    
    noise_matrix = tf.gather_nd(is_noise, ragged_split_added_indices)
    S *= 1. - noise_matrix
    #Snot *= 1. - noise_matrix #let's keep this as noise
    
    nS    = tf.reduce_sum(S, axis=1) + K.epsilon()
    nSnot = tf.reduce_sum(Snot, axis=1) + K.epsilon()
    
    beta_matrix = tf.gather_nd(beta, ragged_split_added_indices)
    one_over_sigma_matrix = get_one_over_sigma(beta_matrix, 1e-2)
    one_over_sigma = get_one_over_sigma(beta, 1e-2)
    

    
    
    # for now all these things work in SV x N
    attraction = tf.reduce_sum(S* distance * one_over_sigma_matrix , axis = 1) / nS

    

    repulsion = Snot * (1. - distance) * one_over_sigma_matrix
    repulsion = tf.where(repulsion<0., tf.zeros_like(repulsion), repulsion)
    repulsion = tf.reduce_sum(repulsion , axis=1) / nSnot
    
    #split by ragged not needed if noise is not weighted away
    
    
    #minbeta = S * (1. - beta_matrix) + (1. - S)*100. #beta is between 0 and 1
    #minbeta = tf.reduce_min(minbeta, axis=1)
    #minbeta = tf.where(minbeta > 1., tf.zeros_like(minbeta),minbeta) #just noise contributions
    
    #simplified:

    print(beta)

    print(is_noise)

    cluster_asso = tf.squeeze(cluster_asso, axis=1)
    print(cluster_asso)

    # 0.1 trick as suggested by Jan
    # +1 so noise gets converted to 0 - easier to deal with
    cluster_asso_integer = tf.cast(cluster_asso+1.1, tf.int64) 
    # Convert it into row splits
    cluster_asso_integer = tf.RaggedTensor.from_row_splits(cluster_asso_integer, row_splits)

    # Also convert betas into row splits
    beta = tf.RaggedTensor.from_row_splits(beta, row_splits)


    # Multiply row ids by a huge number and then add it so we do argmax within each batch element
    sorting_tensor = cluster_asso_integer.values + cluster_asso_integer.value_rowids() * 40000000
    sorting_tensor = tf.argsort(sorting_tensor)[..., tf.newaxis]

    # Now the second dimension is sorted by shower index
    ragged_tensor_beta_values = tf.RaggedTensor.from_row_splits(
        tf.gather_nd(beta.values, indices=sorting_tensor), row_splits=row_splits)
    ragged_tensor_shower_indices = tf.RaggedTensor.from_row_splits(
        tf.gather_nd(cluster_asso_integer.values, indices=sorting_tensor), row_splits=row_splits)

    # make row splits according to number of showers in each of the batch element
    row_splits_secondary = tf.cumsum(tf.concat(([0], 1 + tf.reduce_max(ragged_tensor_shower_indices, axis=1)), axis=0))

    additive = (row_splits_secondary[0:-1])[..., tf.newaxis]
    ragged_tensor_shower_indices_across_all_batch_elements = ragged_tensor_shower_indices + additive

    # ragged_tensor_shower_indices_across_all_batch_elements = tf.cast(ragged_tensor_shower_indices_across_all_batch_elements, tf.int32)


    ragged_tensor_shower_indices_across_all_batch_elements = ragged_tensor_shower_indices_across_all_batch_elements.values

    # showers_ragged_indices_only = tf.RaggedTensor.from_value_rowids(values=ragged_tensor_shower_indices.values,
    #                                                                 value_rowids=ragged_tensor_shower_indices_across_all_batch_elements)
    # ragged_tensor_shower_indices = tf.RaggedTensor.from_row_splits(values=showers_ragged_indices_only,
    #                                                                row_splits=row_splits_secondary)

    # TODO: Remove this
    # ragged_tensor_shower_indices_across_all_batch_elements = tf.Print(ragged_tensor_shower_indices_across_all_batch_elements,[ragged_tensor_shower_indices_across_all_batch_elements],'shower indices', summarize=500)

    showers_ragged_beta_values = tf.RaggedTensor.from_value_rowids(values=ragged_tensor_beta_values.values,
                                                                   value_rowids=ragged_tensor_shower_indices_across_all_batch_elements)
    ragged_tensor_beta_values = tf.RaggedTensor.from_row_splits(values=showers_ragged_beta_values,
                                                                row_splits=row_splits_secondary)


    # print(ragged_tensor_beta_values.shape)
    # 0/0
    #
    # attraction = tf.Print(
    #     attraction,
    #     [ragged_tensor_beta_values[0].values], 'X', summarize=500)
    # attraction = tf.Print(
    #     attraction,
    #     [ragged_tensor_beta_values[0].value_rowids()], 'Y', summarize=500)

    ragged_tensor_beta_values = ragged_tensor_beta_values[:, 1:, :]

    #attraction = tf.Print(
    #    attraction,
    #    [ragged_tensor_beta_values[0].values], 'X', summarize=500)
    #attraction = tf.Print(
    #    attraction,
    #    [ragged_tensor_beta_values[0].value_rowids()], 'Y', summarize=500)
    # attraction = tf.Print(
    #     attraction,
    #     [tf.reduce_max(ragged_tensor_beta_values[0], axis=-1).value_rowids()], 'reduced max i', summarize=500)

    # beta = tf.reduce_mean(ragged_tensor_beta_values)
    beta = tf.reduce_mean(1-tf.reduce_max(ragged_tensor_beta_values, axis=-1))
    # minbeta =  (1. - beta) * (1. - is_noise)
    
    return tf.reduce_mean(attraction), tf.reduce_mean(repulsion), beta
    #
    #
    #
    #attraction = tf.RaggedTensor.from_row_splits(values=attraction, row_splits=row_splits)
    #repulsion = tf.RaggedTensor.from_row_splits(values=repulsion, row_splits=row_splits)
    #
    #row_splits = tf.Print(row_splits,[row_splits, attraction],'row_splits ', summarize=200)
    #return tf.reduce_mean(attraction)+tf.reduce_mean(row_splits), 0, 0
    #
    ##now these are B x V
    #
    #N_minus_N_noise = tf.RaggedTensor.from_row_splits(values=(1.-is_noise), row_splits=row_splits)
    #N_minus_N_noise = tf.reduce_sum(N_minus_N_noise, axis=1)+K.epsilon() 
    #
    #attraction_loss = tf.reduce_mean(tf.reduce_sum(attraction, axis=1)  / N_minus_N_noise)
    #repulsion_loss = tf.reduce_mean(tf.reduce_sum(repulsion, axis=1) / N_minus_N_noise)
    #
    ##now min beta loss
    ##attraction = tf.Print(attraction,[attraction],'attraction ')
    #
    ##beta_matrix, S, Snot
    ##would also work: minbeta *= (1.-is_noise)
    #
    ##now normalise per row split
    #minbeta = tf.RaggedTensor.from_row_splits(values=minbeta, row_splits=row_splits)
    #minbeta_loss = tf.reduce_mean(tf.reduce_sum(minbeta, axis=1) / N_minus_N_noise)
    #
    #return 0, 0,minbeta_loss #att, repulsion_loss, minbeta_loss



def get_arb_loss(ccoords, row_splits, beta, is_noise, cluster_asso, beta_min=1e-3,
                 rep_cutoff=100):
    
    #### get dimensions right
    #padded row splits
    row_splits = tf.reshape(row_splits, (-1,))#should not be necessary
    batch_size_plus_1 = tf.cast(row_splits[-1], tf.int32)#int32 needed?
    row_splits = tf.slice(row_splits, [0], batch_size_plus_1[..., tf.newaxis])
     
    cluster_asso = tf.expand_dims(cluster_asso, axis=1)
    
    ####
    
    A,B,C = construct_ragged_matrices_indexing_tensors(row_splits)

    print("A,B,C",A[:,0],B[:,0],C)

    # Jan's losses
    # S is given (I am just setting it to all ones)
    d_square = tf.reduce_sum((tf.gather_nd(ccoords, A) - tf.gather_nd(ccoords, B))**2, axis=-1)

    printAsRagged("d_square", d_square, C, row_splits)
    print("These values should ")
    # reate S and notS
    
    is_notnoise_matrix = (1-tf.gather_nd(is_noise, A))* (1-tf.gather_nd(is_noise, B))
    S = tf.reduce_sum((tf.gather_nd(cluster_asso, A) - tf.gather_nd(cluster_asso, B))**2, axis=-1)
    
    S    = tf.where( S < 0.1, tf.zeros_like(S)+1., tf.zeros_like(S))
    Snot = tf.where( S > 0.5, tf.zeros_like(S), tf.zeros_like(S)+1.)
    S    *= is_notnoise_matrix
    Snot *= is_notnoise_matrix
    #Snot can include noise
    
    printAsRagged("\nS\n",S,C,row_splits)
    printAsRagged("\nSnot\n",Snot,C,row_splits)
    printAsRagged("\nis_notnoise_matrix\n",is_notnoise_matrix,C,row_splits)
    

    
  

    #now this is S and Snot as defined in the paper draft

    N_minus_N_noise = tf.RaggedTensor.from_row_splits(values=(1-is_noise), row_splits=row_splits)
    
    print('preN_minus_N_noise',N_minus_N_noise)
    printAsRagged("is_notnoise_matrix", is_notnoise_matrix, C, row_splits)
    
    N_minus_N_noise = tf.reduce_sum(N_minus_N_noise, axis=1) # seems wrong? reduce sum, also axis?
    N = tf.RaggedTensor.from_row_splits(values=(tf.zeros_like(is_noise)+1.), row_splits=row_splits)
    N = tf.reduce_sum(N, axis=1)+K.epsilon() # seems wrong? reduce sum, also axis?
    

    N_minus_N_noise = tf.Print(N_minus_N_noise, [N], 'N ',summarize=200)


    # This is given sorry it was easy to make a dummy version over here
    #N_minus_N_noise = tf.Print(N_minus_N_noise,[tf.reduce_mean(N_minus_N_noise)],'mean N_minus_N_noise ')

    one_over_collected_sigma_i = tf.gather_nd(get_one_over_sigma(beta, beta_min), A)
    one_over_collected_sigma_j = tf.gather_nd(get_one_over_sigma(beta, beta_min), B)
    beta_j = tf.gather_nd(beta, B)

    print("N_minus_N_noise",N_minus_N_noise)
    

    attractive_loss = (S* tf.sqrt( d_square+K.epsilon()))*(one_over_collected_sigma_i*one_over_collected_sigma_j)
    attractive_loss = tf.RaggedTensor.from_row_splits(values=attractive_loss, row_splits=C)
    attractive_loss = tf.RaggedTensor.from_row_splits(values=attractive_loss, row_splits=row_splits)
    attractive_loss = tf.reduce_sum(attractive_loss, axis=[1,2])
    # Normalize
    
    attractive_loss = attractive_loss / (N_minus_N_noise**2+K.epsilon())
    attractive_loss = killNan(attractive_loss)
    # Mean over the batch dimension
    attractive_loss = tf.reduce_mean(attractive_loss)



    #rep_loss = (Snot)*(one_over_collected_sigma_i*one_over_collected_sigma_j)/(d_square + 1/rep_cutoff + K.epsilon())
    rep_loss = (Snot)*(one_over_collected_sigma_i*one_over_collected_sigma_j) * (1. - tf.sqrt(d_square+K.epsilon()))
    rep_loss = tf.where(rep_loss < 0., tf.zeros_like(rep_loss), rep_loss)
    
    rep_loss = tf.RaggedTensor.from_row_splits(values=rep_loss, row_splits=C)
    rep_loss = tf.RaggedTensor.from_row_splits(values=rep_loss, row_splits=row_splits)
    rep_loss = tf.reduce_sum(rep_loss, axis=[1,2])
    # Normalize
    rep_loss = rep_loss / (N_minus_N_noise**2+K.epsilon())
    rep_loss = killNan(rep_loss)
    # Mean over the batch dimension
    rep_loss = tf.reduce_mean(rep_loss)



    # It's a ragged tensor with two ragged axes
    
    ## this one is NAN directly!
    
    S_r = tf.RaggedTensor.from_row_splits(values=(tf.RaggedTensor.from_row_splits(values = S , row_splits=C)), row_splits=row_splits)
    is_not_same = tf.cast(tf.equal(tf.reduce_sum(S_r, axis=-1),0), tf.float32)
    
    
    #make it a reduce max

    min_beta_loss = (1.-S)*100.*(1.-beta_j) + (S*(1.-beta_j))

    
    min_beta_loss = (Snot)*100. + (S*(1./(one_over_collected_sigma_j + K.epsilon())))
   
   
    #min_beta_loss = S * (1. - beta)
    
    min_beta_loss = tf.RaggedTensor.from_row_splits(values = min_beta_loss , row_splits=C)
    
    min_beta_loss = tf.RaggedTensor.from_row_splits(values = min_beta_loss, row_splits=row_splits)
    
    min_beta_loss = tf.reduce_min(min_beta_loss, axis=2) 
    
    print("after reducemin\n",min_beta_loss)
    print("is same\n", 1 - is_not_same)
    min_beta_loss *= (1-is_not_same)


    n_withsame = tf.reduce_sum((1-is_not_same), axis=-1)
    
    print("n_withsame",n_withsame)
    
    #n_withsame = tf.Print(n_withsame,[n_withsame.values],'n_withsame ')

   
   # min_beta_loss *= tf.RaggedTensor.from_row_splits(values = (1 - is_noise), row_splits=row_splits)
    
     ##THIS IS WEIRD OUTPUT: SHOULD BE 0.5 everywhere (see line 109)
    
    min_beta_losssum = tf.reduce_sum(min_beta_loss, axis=1)
    
    #rep_loss = tf.Print(rep_loss,[min_beta_losssum, N_minus_N_noise, min_beta_loss.values, min_beta_loss.row_splits],'min_beta_loss, N_minus_N_noise, min_beta_loss ', summarize=2000)

    rep_loss = tf.Print(rep_loss,[N],'N ', summarize=30)

    # Normalize
    min_beta_loss = min_beta_losssum / (n_withsame+K.epsilon())
    
    
    # this kicks in immediately for no reason! there is not gradient?!?
    #min_beta_loss = killNan(min_beta_loss)
    # Mean over the batch dimension
    
    #DEBUG: the output should be 0.5
    min_beta_loss = tf.reduce_mean(min_beta_loss)


    return attractive_loss, rep_loss, min_beta_loss


###### keras trick


def pre_training_loss(truth, pred):
    feat,pred = split_feat_pred(pred)
    d = create_loss_dict(truth, pred)
    feat = create_feature_dict(feat)
    
    etadiff = (d['predCCoords'][:,0:1]+feat['recHitEta']  -   d['truthHitAssignedEtas'])**2
    phidiff = (d['predCCoords'][:,1:2]+feat['recHitRelPhi'] - d['truthHitAssignedPhis'])**2
    
    return tf.reduce_mean(etadiff+phidiff)
    
    
def batch_beta_weighted_truth_mean(b_l_in,b_istruth,b_beta_scaling):
    
    
    t_l_in = tf.reduce_sum(b_beta_scaling*b_istruth*b_l_in)#  1
    t_den =  tf.reduce_sum(b_istruth*b_beta_scaling) + 1e-9# 1
    t_den = tf.where(t_den==0, 1e-6, t_den)
    return t_l_in/t_den


#this needs to be per ragged batch! same for spectators   
#but we're in eager so whatever 
def beta_weighted_truth_mean(l_in, d, row_splits, beta_scaling, is_not_spectator=None):#l_in  V x 1
    
    batch_size = row_splits.shape[0] - 1
    out = tf.constant(0., tf.float32)
    istruth = d['truthNoNoise']
    if is_not_spectator is not None:
        istruth *= is_not_spectator
    
    for b in tf.range(batch_size):
        b_beta_scaling = beta_scaling[row_splits[b]:row_splits[b + 1]]
        b_istruth = istruth[row_splits[b]:row_splits[b + 1]]
        b_l_in = l_in[row_splits[b]:row_splits[b + 1]]
        if tf.reduce_max(b_istruth) == 0:
            continue
        out += batch_beta_weighted_truth_mean(b_l_in,b_istruth,b_beta_scaling)
    
    out /= float(batch_size)+1e-5
    return out

def batch_spectator_penalty(isspect,beta):
    out = tf.reduce_sum(isspect * beta )
    out /= tf.reduce_sum(isspect)+1e-3
    return out

def spectator_penalty(d,row_splits):
    
    batch_size = row_splits.shape[0] - 1
    out = tf.constant(0., tf.float32)
    isspect = d['truthIsSpectator']
    beta = d['predBeta']
    
    for b in tf.range(batch_size):
        out += batch_spectator_penalty(isspect[row_splits[b]:row_splits[b + 1]],
                                       beta[row_splits[b]:row_splits[b + 1]])
    
    out /= float(batch_size)+1e-3
    return out
    
def null_loss(truth, pred):
    return 0*tf.reduce_mean(pred)+0*tf.reduce_mean(truth)

from LayersRagged import RaggedConstructTensor
ragged_constructor=RaggedConstructTensor()


def full_obj_cond_loss(truth, pred, rowsplits):
    
    if truth.shape[0] is None: 
        return tf.constant(0., tf.float32)
    
    from object_condensation import indiv_object_condensation_loss_2

    rowsplits = tf.cast(rowsplits, tf.int64)#just for first loss evaluation from stupid keras
    
    feat,pred = split_feat_pred(pred)
    d = create_loss_dict(truth, pred)
    feat = create_feature_dict(feat)
    #print('feat',feat.shape)

    truthIsSpectator = d['truthIsSpectator'][:, 0]
    
    classes, row_splits = d['truthHitAssignementIdx'][...,0], rowsplits[ : rowsplits[-1,0],0]
    
    energyweights = d['truthHitAssignedEnergies']
    energyweights = tf.math.log(0.1 * energyweights + 1.)*0. + 1.
    
    attractive_loss, rep_loss, noise_loss, min_beta_loss  = indiv_object_condensation_loss_2(d['predCCoords'], #
                                                                                             d['predBeta'][...,0],  #remove last 1 dim
                                                                                             classes, 
                                                                                             row_splits,
                                                                                             truthIsSpectator,
                                                                                             Q_MIN=.75, 
                                                                                             S_B=1.,
                                                                                             energyweights=energyweights[...,0])
    
    beta_scaling = tf.math.atanh(tf.clip_by_value( d['predBeta'], 1e-3, 1. - 1e-3))**2 #avoid nans
    
    #energy loss. For particles other than muons, the highest energy hits contribute with about 1% of the total energy
    energy_diff = (d['predEnergy'] - d['truthHitAssignedEnergies'])
    energy_loss = beta_weighted_truth_mean(energyweights *  
                                           energy_diff**2/(d['truthHitAssignedEnergies']**2+5), d,row_splits, beta_scaling, (1.-d['truthIsSpectator']) )
    

    etadiff = d['predEta']+feat['recHitEta']  -   d['truthHitAssignedEtas']
    phidiff = d['predPhi']+feat['recHitRelPhi'] - d['truthHitAssignedPhis']
    pos_offs = tf.concat( [etadiff,  phidiff],axis=-1)
    pos_offs =  tf.reduce_sum(pos_offs**2, axis=-1, keepdims=True) # B x V x 1
    position_loss = 100.* beta_weighted_truth_mean(energyweights * pos_offs, d,row_splits, beta_scaling, (1.-d['truthIsSpectator']))
    
    
    spectator_beta_penalty =  0.1 * spectator_penalty(d,row_splits)
    
    min_beta_loss*=1.
    
    attractive_loss = tf.where(tf.math.is_nan(attractive_loss),0,attractive_loss)
    rep_loss = tf.where(tf.math.is_nan(rep_loss),0,rep_loss)
    min_beta_loss = tf.where(tf.math.is_nan(min_beta_loss),0,min_beta_loss)
    noise_loss = tf.where(tf.math.is_nan(noise_loss),0,noise_loss)
    energy_loss = tf.where(tf.math.is_nan(energy_loss),0,energy_loss)
    position_loss = tf.where(tf.math.is_nan(position_loss),0,position_loss)
    spectator_beta_penalty = tf.where(tf.math.is_nan(spectator_beta_penalty),0,spectator_beta_penalty)
    
    energy_loss *= 0.0000001
    
    # neglect energy loss almost fully
    loss = attractive_loss + rep_loss +  min_beta_loss +  noise_loss  + energy_loss +  position_loss + spectator_beta_penalty
    
    print('loss',loss.numpy(), 
          'attractive_loss',attractive_loss.numpy(), 
          'rep_loss', rep_loss.numpy(), 
          'min_beta_loss', min_beta_loss.numpy(), 
          'noise_loss' , noise_loss.numpy(),
          '(energy_loss)', energy_loss.numpy(), 
          'sqrt(energy_loss)', tf.sqrt(energy_loss).numpy(), 
          '(position_loss)' , position_loss.numpy(),
          'spectator_beta_penalty', spectator_beta_penalty.numpy())
    
    return loss
    
    
subloss_passed_tensor=None
def obj_cond_loss_rowsplits(truth, pred):
    global subloss_passed_tensor
    #print('>>>>>>>>>>> nbatch',truth.shape[0])
    if subloss_passed_tensor is not None: #passed_tensor is actual truth
        temptensor=subloss_passed_tensor
        subloss_passed_tensor=None
        #print('calling min_beta_loss_rowsplits', temptensor)
        return full_obj_cond_loss(temptensor, pred, truth)
        
    subloss_passed_tensor = truth #=rs
    return  0.*tf.reduce_mean(pred)


def obj_cond_loss_truth(truth, pred):
    global subloss_passed_tensor
    if subloss_passed_tensor is not None: #passed_tensor is rs from other function
        temptensor=subloss_passed_tensor
        subloss_passed_tensor=None
        #print('calling min_beta_loss_truth', temptensor)
        return full_obj_cond_loss(truth, pred, temptensor)

    subloss_passed_tensor = truth #=rs
    return  0.*tf.reduce_mean(pred)


def pretrain_obj_cond_loss_rowsplits(truth, pred):
    global subloss_passed_tensor
    #print('>>>>>>>>>>> nbatch',truth.shape[0])
    if subloss_passed_tensor is not None: #passed_tensor is actual truth
        temptensor=subloss_passed_tensor
        subloss_passed_tensor=None
        #print('calling min_beta_loss_rowsplits', temptensor)
        return pre_training_loss(temptensor, pred)
        
    subloss_passed_tensor = truth #=rs
    return  0.*tf.reduce_mean(pred)


def pretrain_obj_cond_loss_truth(truth, pred):
    global subloss_passed_tensor
    if subloss_passed_tensor is not None: #passed_tensor is rs from other function
        temptensor=subloss_passed_tensor
        subloss_passed_tensor=None
        #print('calling min_beta_loss_truth', temptensor)
        return pre_training_loss(truth, pred)

    subloss_passed_tensor = truth #=rs
    return  0.*tf.reduce_mean(pred)




    
