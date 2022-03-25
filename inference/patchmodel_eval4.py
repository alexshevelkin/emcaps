#!/usr/bin/env python3


"""
Evaluates a patch classifier model trained by training/patchtrain.py

"""

# TODO: Update for v4 data format

import argparse
import datetime
from locale import normalize
from math import inf
import os
import random
from typing import Literal
from unicodedata import category
from elektronn3.data.transforms.transforms import RandomCrop

import matplotlib.pyplot as plt
import yaml
import torch
from torch import nn
from torch import optim
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, ConfusionMatrixDisplay


# Don't move this stuff, it needs to be run this early to work
import elektronn3
from torch.nn.modules.loss import MSELoss
from torch.utils import data
elektronn3.select_mpl_backend('Agg')

from elektronn3.training import metrics
from elektronn3.data import transforms
from elektronn3.inference import Predictor

from training.tifdirdata import UPatches

from models.effnetv2 import effnetv2_s, effnetv2_m
from analysis.cf_matrix import plot_confusion_matrix

parser = argparse.ArgumentParser(description='Train a network.')
parser.add_argument(
    '-m', '--model-path', metavar='PATH',
    help='Path to pretrained model which to use.',
    default='/wholebrain/scratch/mdraw/tum/patch_trainings_v4a_uni/erasemaskbg___EffNetV2__22-03-19_02-42-10/model_final.pt',
)
parser.add_argument('--disable-cuda', action='store_true', help='Disable CUDA')
args = parser.parse_args()


# Set up all RNG seeds, set level of determinism
random_seed = 0
torch.manual_seed(random_seed)
np.random.seed(random_seed)
random.seed(random_seed)

if not args.disable_cuda and torch.cuda.is_available():
    device = torch.device('cuda')
else:
    device = torch.device('cpu')

print(f'Running on device: {device}')


out_channels = 8

DILATE_MASKS_BY = 5

valid_split_path = './class_info.yaml'
with open(valid_split_path) as f:
    CLASS_INFO = yaml.load(f, Loader=yaml.FullLoader)

CLASS_IDS = CLASS_INFO['class_ids']
CLASS_NAMES = list(CLASS_IDS.keys())

SHORT_CLASS_NAMES = [name[4:15] for name in CLASS_NAMES]

# USER PATHS

patches_root = os.path.expanduser('/wholebrain/scratch/mdraw/tum/patches_v4a_uni/')

dataset_mean = (128.0,)
dataset_std = (128.0,)

# Transformations to be applied to samples before feeding them to the network
common_transforms = [
    transforms.Normalize(mean=dataset_mean, std=dataset_std, inplace=False),
]
valid_transform = common_transforms + []
valid_transform = transforms.Compose(valid_transform)


valid_dataset = UPatches(
    descr_sheet=(f'{patches_root}/patchmeta_traintest.xlsx', 'Sheet1'),
    train=False,
    # transform=valid_transform,  # Don't transform twice (already transformed by predictor below)
    dilate_masks_by=DILATE_MASKS_BY,
    erase_mask_bg=True,
)


predictor = Predictor(
    model=os.path.expanduser(args.model_path),
    device=device,
    # float16=True,
    transform=valid_transform,  
    apply_softmax=True,
    # apply_argmax=True,
)

n_correct = 0
n_total = 0

# Load gt sheet for restoring original patch_id indexing
gt_sheet = pd.read_excel(f'{patches_root}/samples_gt.xlsx')

preds = []
targets = []
pred_labels = []
target_labels = []

img_preds = {}

predictions = {}
meta = valid_dataset.meta
for i in range(len(valid_dataset)):

    # We have different patch_id indices in the gt_sheet for human eval because
    # of an index reset at the bottom of patchifyseg.py,
    # so we have to remap to the gt_sheet entry by finding the corresponding
    # patch_fname (which hasn't changed).
    patch_fname = valid_dataset.meta.patch_fname.iloc[i]

    # TODO: FIX
    ####
    # gt_match = gts_id = gt_sheet[gt_sheet.patch_fname == patch_fname]
    # if gt_match.empty:
    #     import IPython ; IPython.embed(); raise SystemExit
    #     continue  # Not found in gt_sheet, so skip this patch
    # gts_id = gt_match.patch_id.item()

    sample = valid_dataset[i]
    inp = sample['inp'][None]
    out = predictor.predict(inp)
    pred = out[0].argmax(0).item()

    confidence = out[0].numpy().ptp()  # peak-to-peak as confidence proxy

    target = sample['target'].item()

    pred_label = SHORT_CLASS_NAMES[pred]
    target_label = SHORT_CLASS_NAMES[target]

    preds.append(pred)
    targets.append(target)
    pred_labels.append(pred_label)
    target_labels.append(target_label)

    n_total += 1
    if pred == target:
        n_correct += 1

    img_num = int(valid_dataset.meta.img_num.iloc[i])
    if img_num not in img_preds:
        img_preds[img_num] = []
    img_preds[img_num].append(pred)

    # TODO: Fix ##
    ###
    # predictions[gts_id] = (pred_label, confidence)

print(f'{n_correct} correct out of {n_total}')
print(f' -> accuracy: {100 * n_correct / n_total:.2f}%')


preds = np.array(preds)
targets = np.array(targets)


# TODO
majority_preds = {}
majority_pred_names = {}
for k, v in img_preds.items():
    majority_preds[k] = np.argmax(np.bincount(v))
    majority_pred_names[k] = SHORT_CLASS_NAMES[majority_preds[k]]



if False:  # Sanity check: Calculate confusion matrix entries myself
    for a in range(2, 8):
        for b in range(2, 8):
            v = np.sum((targets == a) & (preds == b))
            print(f'T: {SHORT_CLASS_NAMES[a]}, P: {SHORT_CLASS_NAMES[b]} -> {v}')

    # T: 1xMT3-MxEnc, P: 1xMT3-MxEnc -> 3
    # T: 1xMT3-MxEnc, P: 1xMT3-QtEnc -> 0
    # T: 1xMT3-MxEnc, P: 2xMT3-MxEnc -> 23
    # T: 1xMT3-MxEnc, P: 2xMT3-QtEnc -> 0
    # T: 1xMT3-MxEnc, P: 3xMT3-QtEnc -> 0
    # T: 1xMT3-MxEnc, P: 1xTmEnc-BC2 -> 4
    # T: 1xMT3-QtEnc, P: 1xMT3-MxEnc -> 0
    # T: 1xMT3-QtEnc, P: 1xMT3-QtEnc -> 27
    # T: 1xMT3-QtEnc, P: 2xMT3-MxEnc -> 0
    # T: 1xMT3-QtEnc, P: 2xMT3-QtEnc -> 2
    # T: 1xMT3-QtEnc, P: 3xMT3-QtEnc -> 1
    # T: 1xMT3-QtEnc, P: 1xTmEnc-BC2 -> 0
    # T: 2xMT3-MxEnc, P: 1xMT3-MxEnc -> 0
    # T: 2xMT3-MxEnc, P: 1xMT3-QtEnc -> 0
    # T: 2xMT3-MxEnc, P: 2xMT3-MxEnc -> 26
    # T: 2xMT3-MxEnc, P: 2xMT3-QtEnc -> 0
    # T: 2xMT3-MxEnc, P: 3xMT3-QtEnc -> 4
    # T: 2xMT3-MxEnc, P: 1xTmEnc-BC2 -> 0
    # T: 2xMT3-QtEnc, P: 1xMT3-MxEnc -> 2
    # T: 2xMT3-QtEnc, P: 1xMT3-QtEnc -> 0
    # T: 2xMT3-QtEnc, P: 2xMT3-MxEnc -> 17
    # T: 2xMT3-QtEnc, P: 2xMT3-QtEnc -> 6
    # T: 2xMT3-QtEnc, P: 3xMT3-QtEnc -> 5
    # T: 2xMT3-QtEnc, P: 1xTmEnc-BC2 -> 0
    # T: 3xMT3-QtEnc, P: 1xMT3-MxEnc -> 0
    # T: 3xMT3-QtEnc, P: 1xMT3-QtEnc -> 0
    # T: 3xMT3-QtEnc, P: 2xMT3-MxEnc -> 9
    # T: 3xMT3-QtEnc, P: 2xMT3-QtEnc -> 0
    # T: 3xMT3-QtEnc, P: 3xMT3-QtEnc -> 21
    # T: 3xMT3-QtEnc, P: 1xTmEnc-BC2 -> 0
    # T: 1xTmEnc-BC2, P: 1xMT3-MxEnc -> 0
    # T: 1xTmEnc-BC2, P: 1xMT3-QtEnc -> 1
    # T: 1xTmEnc-BC2, P: 2xMT3-MxEnc -> 16
    # T: 1xTmEnc-BC2, P: 2xMT3-QtEnc -> 0
    # T: 1xTmEnc-BC2, P: 3xMT3-QtEnc -> 2
    # T: 1xTmEnc-BC2, P: 1xTmEnc-BC2 -> 11


cm = confusion_matrix(targets, preds)

fig, ax = plt.subplots(tight_layout=True, figsize=(7, 5.5))

cma = plot_confusion_matrix(cm, categories=SHORT_CLASS_NAMES[2:], normalize='pred', cmap='viridis', sum_stats=False, ax=ax)
ax.set_title('Patch classification confusion matrix v4a (top: count, bottom: percentages normalized over true labels)\n')
plt.savefig(f'{patches_root}/patch_confusion_matrix.pdf')

# cma = ConfusionMatrixDisplay.from_predictions(target_labels, pred_labels, labels=SHORT_CLASS_NAMES[2:], normalize='pred', xticks_rotation='vertical', ax=ax)
# cma.figure_.savefig(f'{patches_root}/patch_confusion_matrix.pdf')

predictions = pd.DataFrame.from_dict(predictions, orient='index', columns=['class', 'confidence'])

predictions = predictions.sort_index().convert_dtypes()
predictions.to_excel(f'{patches_root}/samples_nnpredictions.xlsx', index_label='patch_id', float_format='%.2f')

# TODO: Save predictions

import IPython ; IPython.embed(); raise SystemExit

# label_names = [
#     '1xMT3-MxEnc',
#     '1xMT3-QtEnc',
#     '2xMT3-MxEnc',
#     '2xMT3-QtEnc',
#     '3xMT3-QtEnc',
#     '1xTmEnc-BC2',
# ]