import numpy as np
import math

import tensorflow as tf
import tensorflow_probability as tfp
import pdb

# for data saving stuff
import sys
import pickle
import json
import os

# import from files
from transformation.fhn import fhn_transformation, tf_fhn_transformation
from transformation.linear import linear_transformation, tf_linear_transformation
from transformation.lorenz import lorenz_transformation, tf_lorenz_transformation
from transformation.MLP import MLP_transformation

from distribution.dirac_delta import dirac_delta
from distribution.mvn import mvn, tf_mvn
from distribution.poisson import poisson, tf_poisson

from rslts_saving.rslts_saving import *
from rslts_saving.fhn_rslts_saving import *
from rslts_saving.lorenz_rslts_saving import *
from trainer import trainer

from encoder import encoder_cell
from sampler import create_dataset
from SMC import SMC

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2" # to avoid lots of log about the device

print("the code is written at:")
print("\ttensorflow version: 1.12.0")
print("\ttensorflow_probability version: 0.5.0")

print("the system uses:")
print("\ttensorflow version:", tf.__version__)
print("\ttensorflow_probability version:", tfp.__version__)

if __name__ == "__main__":

	# ============================================ parameter part ============================================ #
	# training hyperparameters
	Dx = 2
	Dy = 2
	n_particles = 1000
	time = 150

	batch_size = 16
	lr = 1e-4
	epoch = 50
	seed = 0
	tf.set_random_seed(seed)
	np.random.seed(seed)

	n_train = 50	* batch_size
	n_test  = 1 	* batch_size
	MSE_steps = 1

	# printing and data saving params

	print_freq = 10

	store_res = True
	save_freq = 10
	saving_num = min(n_train, 2*batch_size)
	rslt_dir_name = "fhn_2D_obs"
	
	# ============================================== model part ============================================== #
	# for data generation
	sigma, rho, beta, dt = 10.0, 28.0, 8.0/3.0, 0.01
	f_params = (sigma, rho, beta, dt)

	a, b, c, I, dt = 1.0, 0.95, 0.05, 1.0, 0.15
	f_params = (a, b, c, I, dt)

	f_sample_cov = 0.15*np.eye(Dx)

	g_params = np.diag([2.0, 3.0]) # np.random.randn(Dy, Dx)
	g_sample_cov = 0.15 * np.eye(Dy)

	# transformation can be: fhn_transformation, linear_transformation, lorenz_transformation
	# distribution can be: dirac_delta, mvn, poisson
	f_sample_tran = fhn_transformation(f_params)
	f_sample_dist = dirac_delta(f_sample_tran)

	g_sample_tran = linear_transformation(g_params)
	g_sample_dist = mvn(g_sample_tran, g_sample_cov)

	true_model_dict = {"f_params":f_params,
					   "f_cov":f_sample_cov,
					   "g_params":g_params,
					   "g_cov":g_sample_cov}
	# for training
	x_0 = tf.placeholder(tf.float32, shape=(batch_size, Dx), name = "x_0")

	my_encoder_cell = None
	q_train_tran = MLP_transformation([50], Dx, name="q_train_tran")
	f_train_tran = MLP_transformation([50], Dx, name="f_train_tran")
	g_train_tran = MLP_transformation([50], Dy, name="g_train_tran")

	# my_encoder_cell = encoder_cell(Dx, Dy, batch_size, time, name = "encoder_cell")
	# q_train_tran = my_encoder_cell.q_transformation
	# f_train_tran = my_encoder_cell.f_transformation

	q_sigma_init, q_sigma_min = 25, 4
	f_sigma_init, f_sigma_min = 25, 4
	g_sigma_init, g_sigma_min = 25, 4

	q_train_dist = tf_mvn(q_train_tran, x_0, sigma_init=q_sigma_init, sigma_min=q_sigma_min, name="q_train_dist")
	f_train_dist = tf_mvn(f_train_tran, x_0, sigma_init=f_sigma_init, sigma_min=f_sigma_min, name="f_train_dist")
	g_train_dist = tf_mvn(g_train_tran, None, sigma_init=g_sigma_init, sigma_min=g_sigma_min, name="g_train_dist")

	init_dict = {"q_sigma_init": q_sigma_init,
				 "q_sigma_min":  q_sigma_min,
				 "f_sigma_init": f_sigma_init,
				 "f_sigma_min":  f_sigma_min,
				 "g_sigma_init": g_sigma_init,
				 "g_sigma_min":  g_sigma_min}

	# for evaluating log_ZSMC_true
	q_A = tf.eye(Dx)
	q_cov = 100*tf.eye(Dx)

	f_cov = 100*tf.eye(Dx)

	g_A = tf.constant(g_params, dtype = tf.float32)
	g_cov = tf.constant(g_sample_cov, dtype = tf.float32)

	q_true_tran = tf_linear_transformation(q_A)
	q_true_dist = tf_mvn(q_true_tran, x_0, sigma=q_cov, name="q_true_dist")
	f_true_tran = tf_fhn_transformation(f_params)
	f_true_dist = tf_mvn(f_true_tran, x_0, sigma=f_cov, name="f_true_dist")
	g_true_tran = tf_linear_transformation(g_A)
	g_true_dist = tf_mvn(g_true_tran, name="g_true_dist")

	# =========================================== data saving part =========================================== #
	if store_res == True:
		Experiment_params = {"n_particles":n_particles, "time":time, "batch_size":batch_size,
							 "lr":lr, "epoch":epoch, "seed":seed, "n_train":n_train,
							 "rslt_dir_name":rslt_dir_name}
		print("Experiment_params")
		for key, val in Experiment_params.items():
			print("\t{}:{}".format(key, val))

		RLT_DIR = create_RLT_DIR(Experiment_params)
		print("RLT_DIR:", RLT_DIR)

	# ============================================= dataset part ============================================= #
	# Create train and test dataset
	hidden_train, obs_train, hidden_test, obs_test = \
		create_dataset(n_train, n_test, time, Dx, Dy, f_sample_dist, g_sample_dist, lb=-3, ub=3)
	print("finish creating dataset")

	# ========================================== another model part ========================================== #

	# placeholders
	obs = tf.placeholder(tf.float32, shape=(batch_size, time, Dy), name = "obs")
	hidden = tf.placeholder(tf.float32, shape=(batch_size, time, Dx), name = "hidden")

	SMC_true  = SMC(q_true_dist,  f_true_dist,  g_true_dist,  n_particles, batch_size, name = "log_ZSMC_true")
	SMC_train = SMC(q_train_dist, f_train_dist, g_train_dist, n_particles, batch_size, 
					encoder_cell = my_encoder_cell, name = "log_ZSMC_train")

	# ============================================= training part ============================================ #
	mytrainer = trainer(Dx, Dy,
						n_particles, time,
						batch_size, lr, epoch,
						MSE_steps,
						store_res)
	mytrainer.set_SMC(SMC_true, SMC_train)
	mytrainer.set_placeholders(x_0, obs, hidden)
	if store_res:
		mytrainer.set_rslt_saving(RLT_DIR, save_freq, saving_num)
		if isinstance(f_sample_tran, fhn_transformation) and my_encoder_cell is None:
			lattice = tf.placeholder(tf.float32, shape=(50, 50, Dx), name = "lattice")
			nextX = SMC_train.get_nextX(lattice)
			mytrainer.set_quiver_arg(nextX, lattice)

	losses, tensors = mytrainer.train(hidden_train, obs_train, hidden_test, obs_test, print_freq)


	# ======================================= another data saving part ======================================= #
	if store_res == True:
		log_ZSMC_true_val, log_ZSMC_trains, log_ZSMC_tests, MSE_true_val, MSE_trains, MSE_tests = losses
		log_true, log_train, ys_hat = tensors

		Xs = log_train[0]
		Xs_val = mytrainer.evaluate(Xs, {obs:obs_train[0:saving_num], x_0:hidden_train[0:saving_num, 0]})
		ys_hat_val = mytrainer.evaluate(ys_hat, {obs:obs_train[0:saving_num], hidden:hidden_train[0:saving_num]})

		print("finish evaluating training results")

		plot_training_data(RLT_DIR, hidden_train, obs_train, saving_num = saving_num)
		plot_learning_results(RLT_DIR, Xs_val, hidden_train, saving_num = saving_num)
		plot_y_hat(RLT_DIR, ys_hat_val, obs_train)

		if isinstance(f_sample_tran, fhn_transformation):
			plot_fhn_results(RLT_DIR, Xs_val)

		if isinstance(f_sample_tran, lorenz_transformation):
			plot_lorenz_results(RLT_DIR, Xs_val)

		params_dict = {"time":time,
					   "n_particles":n_particles,
					   "batch_size":batch_size,
					   "lr":lr,
					   "epoch":epoch,
					   "n_train":n_train,
					   "seed":seed}
		loss_dict = {"log_ZSMC_true":log_ZSMC_true_val,
					 "log_ZSMC_trains":log_ZSMC_trains,
					 "log_ZSMC_tests":log_ZSMC_tests,
					 "MSE_true":MSE_true_val,
					 "MSE_trains":MSE_trains,
					 "MSE_tests":MSE_tests}
		data_dict = {"params":params_dict,
					 "true_model_dict":true_model_dict,
					 "init_dict":init_dict,
					 "loss":loss_dict}

		with open(RLT_DIR + "data.json", "w") as f:
			json.dump(data_dict, f, indent = 4, cls = NumpyEncoder)
		train_data_dict = {"hidden_train":hidden_train[0:saving_num],
						   "obs_train":obs_train[0:saving_num]}
		learned_model_dict = {"Xs_val":Xs_val,
							  "ys_hat_val":ys_hat_val}
		data_dict["train_data_dict"] = train_data_dict
		data_dict["learned_model_dict"] = learned_model_dict

		with open(RLT_DIR + "data.p", "wb") as f:
			pickle.dump(data_dict, f)

		plot_log_ZSMC(RLT_DIR, log_ZSMC_true_val, log_ZSMC_trains, log_ZSMC_tests)
		plot_MSEs(RLT_DIR, MSE_true_val, MSE_trains, MSE_tests)
 