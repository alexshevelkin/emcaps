#!/usr/bin/env python3


# TODO

# https://github.com/lucidrains/vit-pytorch#cct
# --> https://arxiv.org/abs/2104.05704

# 

########


"""
Demo of a 2D semantic segmentation on TUM ML data v2, distinguishing QtEnc from MxEnc particles on patch basis.

Uses a patch dataset that can be created by inference/patchifyseg.py

"""

import argparse
import datetime
from math import inf
import os
import random
from typing import Literal
from elektronn3.data.transforms.transforms import RandomCrop

import torch
from torch import nn
from torch import optim
import numpy as np
import pandas as pd


# Don't move this stuff, it needs to be run this early to work
import elektronn3
from torch.nn.modules.loss import MSELoss
from torch.utils import data

elektronn3.select_mpl_backend('Agg')

from elektronn3.training import Trainer, Backup
from elektronn3.training import metrics
from elektronn3.data import transforms
from elektronn3.models.unet import UNet

import cv2; cv2.setNumThreads(0); cv2.ocl.setUseOpenCL(False)
import albumentations

from training.tifdirdata import Patches

from models.effnetv2 import effnetv2_s, effnetv2_m
from models.cct import CCT


parser = argparse.ArgumentParser(description='Train a network.')
parser.add_argument('--disable-cuda', action='store_true', help='Disable CUDA')
parser.add_argument('-n', '--exp-name', default=None, help='Manually set experiment name')
parser.add_argument(
    '-m', '--max-steps', type=int, default=40_000,
    help='Maximum number of training steps to perform.'
)
parser.add_argument(
    '-r', '--resume', metavar='PATH',
    help='Path to pretrained model state dict from which to resume training.'
)
parser.add_argument('--seed', type=int, default=0, help='Base seed for all RNGs.')
parser.add_argument(
    '--deterministic', action='store_true',
    help='Run in fully deterministic mode (at the cost of execution speed).'
)
args = parser.parse_args()

# Set up all RNG seeds, set level of determinism
random_seed = args.seed
torch.manual_seed(random_seed)
np.random.seed(random_seed)
random.seed(random_seed)
deterministic = args.deterministic
if deterministic:
    torch.backends.cudnn.deterministic = True
else:
    torch.backends.cudnn.benchmark = True  # Improves overall performance in *most* cases

if not args.disable_cuda and torch.cuda.is_available():
    device = torch.device('cuda')
else:
    device = torch.device('cpu')

print(f'Running on device: {device}')

ERASE_DISK_MASK_RADIUS = 0
# ERASE_DISK_MASK_RADIUS = 12 # Mask that should cover any encapsulin foreground pixels, without leaking encapsulin shape info

ERASE_MASK_BG = True
# ERASE_MASK_BG = False

# NEGATIVE_SAMPLING = True
NEGATIVE_SAMPLING = False

if NEGATIVE_SAMPLING:
    assert not ERASE_MASK_BG



data_root = '~/tumdata2/'
if NEGATIVE_SAMPLING:
    descr_sheet = (os.path.expanduser('~/tum/patches_v2neg/patchmeta_traintest.xlsx'), 'Sheet1')
else:
    # descr_sheet = (os.path.expanduser('~/tum/patches_v2/patchmeta_traintest.xlsx'), 'Sheet1')
    descr_sheet = (os.path.expanduser('~/tum/patches_v2_hek/patchmeta_traintest.xlsx'), 'Sheet1')


out_channels = 2

model = effnetv2_s(in_c=1, num_classes=out_channels).to(device)
# model = effnetv2_m(in_c=1, num_classes=out_channels).to(device)
# model = CCT(
#     img_size=28,
#     n_input_channels=1,
#     kernel_size=3,
#     embedding_dim=96,
# )





# USER PATHS
save_root = os.path.expanduser('~/tum/trainings3')
save_root = os.path.expanduser('~/tum/trainings3_hek')

max_steps = args.max_steps
lr = 1e-3
lr_stepsize = 1000
lr_dec = 0.9
batch_size = 64


if args.resume is not None:  # Load pretrained network params
    model.load_state_dict(torch.load(os.path.expanduser(args.resume)))

dataset_mean = (128.0,)
dataset_std = (128.0,)


# Transformations to be applied to samples before feeding them to the network
common_transforms = [
    transforms.Normalize(mean=dataset_mean, std=dataset_std, inplace=False),
    transforms.RandomFlip(ndim_spatial=2),
]

train_transform = common_transforms + [
    transforms.AlbuSeg2d(albumentations.ShiftScaleRotate(
        p=0.99, rotate_limit=180, shift_limit=0.0, scale_limit=0.02, interpolation=2
    )),  # interpolation=2 means cubic interpolation (-> cv2.CUBIC constant).
    # transforms.ElasticTransform(prob=0.5, sigma=2, alpha=5),
    # transforms.AdditiveGaussianNoise(prob=0.9, sigma=0.1),
    # transforms.RandomGammaCorrection(prob=0.9, gamma_std=0.2),
    # transforms.RandomBrightnessContrast(prob=0.9, brightness_std=0.125, contrast_std=0.125),
]

valid_transform = common_transforms + []


train_transform = transforms.Compose(train_transform)
valid_transform = transforms.Compose(valid_transform)

# Specify data set


train_dataset = Patches(
    descr_sheet=descr_sheet,
    train=True,
    transform=train_transform,
    epoch_multiplier=5 if NEGATIVE_SAMPLING else 200,
    erase_mask_bg=ERASE_MASK_BG,
    erase_disk_mask_radius=ERASE_DISK_MASK_RADIUS,
)

valid_dataset = Patches(
    descr_sheet=descr_sheet,
    train=False,
    transform=valid_transform,
    epoch_multiplier=4 if NEGATIVE_SAMPLING else 10,
    erase_mask_bg=ERASE_MASK_BG,
    erase_disk_mask_radius=ERASE_DISK_MASK_RADIUS,
)

# Set up optimization
optimizer = optim.Adam(
    model.parameters(),
    weight_decay=5e-5,
    lr=lr,
    amsgrad=True
)
lr_sched = optim.lr_scheduler.StepLR(optimizer, lr_stepsize, lr_dec)

# Validation metrics

# class _ClassError:
#     def __init__(self, kind: Literal['mx', 'qt']):
#         self.kind = kind

#     def __call__(self, target: torch.Tensor, out: torch.Tensor):
#         pred = torch.argmax(out, 1)
#         true_mx_but_pred_qt = torch.mean(target == 0 & pred == 1) * 100.
#         true_qt_but_pred_mx = torch.mean(target == 1 & pred == 0) * 100.
#         if self.kind == 'mx':
#             return true_mx_but_pred_qt
#         elif self.kind == 'qt':
#             return true_qt_but_pred_mx


valid_metrics = {}

class_names = {0: 'MxEnc', 1: 'QtEnc'}
for evaluator in [metrics.Accuracy, metrics.Precision, metrics.Recall]:
    for c in range(out_channels):
        valid_metrics[f'val_{evaluator.name}_{class_names[c]}'] = evaluator(c)

valid_metrics[f'val_accuracy_mean'] = metrics.Accuracy()  # Mean metric

criterion = nn.CrossEntropyLoss().to(device)

inference_kwargs = {
    'apply_softmax': True,
    'transform': valid_transform,
}

exp_name = args.exp_name
if exp_name is None:
    exp_name = ''
timestamp = datetime.datetime.now().strftime('%y-%m-%d_%H-%M-%S')
exp_name = f'{exp_name}__{model.__class__.__name__ + "__" + timestamp}'
if ERASE_MASK_BG:
    exp_name = f'erasemaskbg_{exp_name}'
if NEGATIVE_SAMPLING:
    exp_name = f'negsample_{exp_name}'
if ERASE_DISK_MASK_RADIUS > 0:
    exp_name = f'erasemaskbg_disk_r{ERASE_DISK_MASK_RADIUS}_{exp_name}'

# exp_name = f'maskonly_{exp_name}'

# Create trainer
trainer = Trainer(
    model=model,
    criterion=criterion,
    optimizer=optimizer,
    device=device,
    train_dataset=train_dataset,
    valid_dataset=valid_dataset,
    batch_size=batch_size,
    num_workers=1,  # TODO
    save_root=save_root,
    exp_name=exp_name,
    inference_kwargs=inference_kwargs,
    save_jit=None,
    schedulers={"lr": lr_sched},
    valid_metrics=valid_metrics,
    out_channels=out_channels,
    mixed_precision=True,
    extra_save_steps=list(range(2000, 30_000 + 1, 2000)),
)

# Archiving training script, src folder, env info
bk = Backup(script_path=__file__,
            save_path=trainer.save_path).archive_backup()

# Start training
trainer.run(max_steps)
