import os
import copy
import torch
import torch.nn as nn
import torch.autograd as autograd
import numpy as np
import pandas as pd
import torch.optim as optim
import matplotlib.pyplot as plt
from tqdm import tqdm
from torch.optim.lr_scheduler import CosineAnnealingLR, StepLR
from utils import set_lr, get_lr, generate_noise, plot_multiple_images, plot_multiple_spectrograms, save_fig, save, get_sample_images_list

class Trainer_WGAN_GP():
	def __init__(self, netD, netG, device, train_dl, lr_D = 0.0002, lr_G = 0.0002, n_critic = 5, lambd = 10, loss_interval = 50, image_interval = 50, snapshot_interval = None, save_img_dir = 'saved_images/', save_snapshot_dir = 'saved_snapshots', resample = None):
		self.netD = netD
		self.netG = netG
		self.train_dl = train_dl
		self.lr_D = lr_D
		self.lr_G = lr_G
		self.n_critic = n_critic
		self.lambd = lambd
		self.train_iteration_per_epoch = len(self.train_dl)
		self.device = device
		self.resample = resample
		self.special = None

		self.optimizerD = optim.Adam(self.netD.parameters(), lr = self.lr_D, betas = (0, 0.9))
		self.optimizerG = optim.Adam(self.netG.parameters(), lr = self.lr_G, betas = (0, 0.9))

		self.real_label = 1
		self.fake_label = 0
		self.nz = self.netG.nz

		self.fixed_noise = generate_noise(16, self.nz, self.device)
		self.loss_interval = loss_interval
		self.image_interval = image_interval
		self.snapshot_interval = snapshot_interval

		self.errD_records = []
		self.errG_records = []
		self.w_dist_records = []

		self.save_cnt = 0
		self.save_img_dir = save_img_dir
		self.save_snapshot_dir = save_snapshot_dir
		if(not os.path.exists(self.save_img_dir)):
			os.makedirs(self.save_img_dir)
		if(not os.path.exists(self.save_snapshot_dir)):
			os.makedirs(self.save_snapshot_dir)

		assert (self.resample is not None), "Resample parameter is unnecessary for wgan_gp because it already resamples by default."

	def train(self, num_epoch):
		for epoch in range(num_epoch):
			for i, data in enumerate(tqdm(self.train_dl)):
				# (1) : minimize -mean(D(x)) + mean(D(G(z))) + lambd * GP(D(x), D(G(z)))
				self.netD.zero_grad()
				real_images = data[0].to(self.device)
				bs = real_images.size(0)
				# real labels (bs)
				real_label = torch.full((bs, ), self.real_label, device = self.device)
				# fake labels (bs)
				fake_label = torch.full((bs, ), self.fake_label, device = self.device)
				# noise (bs, nz, 1, 1), fake images (bs, cn, 64, 64)
				noise = generate_noise(bs, self.nz, self.device)
				fake_images = self.netG(noise)
				# calculate the discriminator results for both real & fake
				c_xr = self.netD(real_images)				# (bs, 1, 1, 1)
				c_xr = c_xr.view(-1)						# (bs)
				c_xf = self.netD(fake_images.detach())		# (bs, 1, 1, 1)
				c_xf = c_xf.view(-1)						# (bs)
				# calculate the Discriminator loss
				errD_real = -torch.mean(c_xr)
				errD_fake = torch.mean(c_xf)

				errD = errD_real + errD_fake + self.lambd * self.gradient_penalty(real_images, fake_images)
				errD.backward()
				# update D using the gradients calculated previously
				self.optimizerD.step()

				# (2) : minimize -mean(D(G(z)))
				#		only do this when i % n_critic == 0
				if(i % self.n_critic == 0):
					self.netG.zero_grad()
					# we updated the discriminator once, therefore recalculate c_xr, c_xf
					noise = generate_noise(bs, self.nz, self.device)
					fake_images = self.netG(noise)
					c_xf = self.netD(fake_images)		# (bs, 1, 1, 1)
					c_xf = c_xf.view(-1)						# (bs)
					# calculate the Generator loss
					errG = -torch.mean(c_xf)
					errG.backward()
					#update G using the gradients calculated previously
					self.optimizerG.step()

				w_dist = -float(errD_real) - float(errD_fake)
				self.errD_records.append(float(errD))
				self.errG_records.append(float(errG))
				self.w_dist_records.append(w_dist)

				if(i % self.loss_interval == 0):
					print('[%d/%d] [%d/%d] errD : %.4f, errG : %.4f, Wasserstein Distance : %.4f'
						  %(epoch+1, num_epoch, i+1, self.train_iteration_per_epoch, errD, errG, w_dist))

				if(i % self.image_interval == 0):
					if(self.special == None):
						sample_images_list = get_sample_images_list('Unsupervised', (self.fixed_noise, self.netG))
						plot_fig = plot_multiple_images(sample_images_list, 4, 4)
						cur_file_name = os.path.join(self.save_img_dir, str(self.save_cnt)+' : '+str(epoch)+'-'+str(i)+'.jpg')
						self.save_cnt += 1
						save_fig(cur_file_name, plot_fig)
						plot_fig.clf()

					elif(self.special == 'Wave'):
						sample_audios_list = get_sample_images_list('Unsupervised_Audio', (self.fixed_noise, self.netG))
						plot_fig = plot_multiple_spectrograms(sample_audios_list, 4, 4, freq = 16000)
						cur_file_name = os.path.join(self.save_img_dir, str(self.save_cnt)+' : '+str(epoch)+'-'+str(i)+'.jpg')
						self.save_cnt += 1
						save_fig(cur_file_name, plot_fig)
						plot_fig.clf()

				if(self.snapshot_interval is not None):
					if(i % self.snapshot_interval == 0):
						save(os.path.join(self.save_snapshot_dir, 'Epoch' + str(epoch) + '_' + str(i) + '.state'), self.netD, self.netG, self.optimizerD, self.optimizerG)
						

	def gradient_penalty(self, real_image, fake_image):
		bs = real_image.size(0)
		alpha = torch.FloatTensor(bs, 1, 1, 1).uniform_(0, 1).expand(real_image.size()).to(self.device)
		interpolation = alpha * real_image + (1 - alpha) * fake_image

		c_xi = self.netD(interpolation)
		gradients = autograd.grad(c_xi, interpolation, torch.ones(c_xi.size()).to(self.device),
								  create_graph = True, retain_graph = True, only_inputs = True)[0]
		gradients = gradients.view(bs, -1)
		penalty = torch.mean((gradients.norm(2, dim=1) - 1) ** 2)
		return penalty
		



