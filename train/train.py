#!/usr/bin/pytho
# -*- coding: UTF-8 -*-

import numpy as np
import os
import sys
# import dependencies
import time
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
import sklearn
import gc

#Pytorch requirements
import unicodedata
import string
import re
import random
import argparse

import torch
import torch.nn as nn
from torch.nn import init
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F

#Fix import issues
project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.append(project_dir)
os.chdir(project_dir)

import experiment
import config
import models.siamese as siamese
import models.base_model as base_model
import layers.suffix as suffix
from data_generator import Generator, classification_dataloader, IMDB_MAX_NUM_NODES, selfsupervised_dataloader
from Logger import Logger
parser = argparse.ArgumentParser()

###############################################################################
#                             General Settings                                #
###############################################################################

parser.add_argument('--num_examples_train', nargs='?', const=1, type=int,
                    default=int(20000))
parser.add_argument('--num_examples_test', nargs='?', const=1, type=int,
                    default=int(1000))
parser.add_argument('--num_examples_val', nargs='?', const=1, type=int,
                    default=int(1000))
parser.add_argument('--edge_density', nargs='?', const=1, type=float,
                    default=0.2)
parser.add_argument('--n_vertices', nargs='?', const=1, type=int, default=50)
parser.add_argument('--random_noise', action='store_true')
parser.add_argument('--noise', nargs='?', const=1, type=float, default=0.03)
parser.add_argument('--noise_model', nargs='?', const=1, type=int, default=2)
parser.add_argument('--generative_model', nargs='?', const=1, type=str,
                    default='ErdosRenyi')
parser.add_argument('--epoch', nargs='?', const=1, type=int,
                    default=5)
parser.add_argument('--step_epoch', nargs='?', const=1, type=int,
                    default=5)
parser.add_argument('--batch_size', nargs='?', const=1, type=int, default=32)
parser.add_argument('--lr', nargs='?', const=1, type=float, default=1e-3)
parser.add_argument('--gamma', nargs='?', const=1, type=float, default=1)
parser.add_argument('--mode', nargs='?', const=1, type=str, default='train')
parser.add_argument('--path_dataset', nargs='?', const=1, type=str, default='dataset')
parser.add_argument('--path_logger', nargs='?', const=1, type=str, default='logger')
parser.add_argument('--path_exp', nargs='?', const=1, type=str, default='experiments/logs.json')
parser.add_argument('--pickle', nargs='?', const=1, type=str)
parser.add_argument('--print_freq', nargs='?', const=1, type=int, default=100)
parser.add_argument('--test_freq', nargs='?', const=1, type=int, default=500)
parser.add_argument('--save_freq', nargs='?', const=1, type=int, default=2000)
parser.add_argument('--clip_grad_norm', nargs='?', const=1, type=float,
                    default=40.0)

###############################################################################
#                                 GNN Settings                                #
###############################################################################

parser.add_argument('--num_features', nargs='?', const=1, type=int,
                    default=20)
parser.add_argument('--classification', nargs= '?', const=1, type=bool,
                    default=False)
parser.add_argument('--pretrained_classification', nargs= '?', const=1, type=bool,
                    default=False)
parser.add_argument('--real_world_dataset', nargs= '?', const=1, type=bool,
                    default=False)
parser.add_argument('--semi_supervised', nargs= '?', const=1, type=bool,
                    default=False)
parser.add_argument('--overfit', nargs= '?', const=1, type=bool,
                    default=False)
parser.add_argument('--validation', nargs= '?', const=1, type=bool,
                    default=False)
parser.add_argument('--num_layers', nargs='?', const=1, type=int,
                    default=20)
parser.add_argument('--num_blocks', nargs='?', const=1, type=int,
                    default=3)

args = parser.parse_args()
print(args)

if torch.cuda.is_available():
    dtype = torch.cuda.FloatTensor
    dtype_l = torch.cuda.LongTensor
    torch.cuda.manual_seed(0)
else:
    dtype = torch.FloatTensor
    dtype_l = torch.LongTensor
    torch.manual_seed(0)

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

def make_config(args):
    arch = {
            'depth_of_mlp' : args.num_layers,
            'block_features' : [args.num_features] * args.num_blocks,
            }
    conf = {
            'node_labels' : 1,
            'classification': args.classification,
            'pretrained_classification': args.pretrained_classification,
            }
    return config.Configuration(conf, arch)


batch_size = args.batch_size
criterion = nn.CrossEntropyLoss()
template1 = '{:<10} {:<10} {:<10} {:<15} {:<15} {:<15} {:<10} {:<10} {:<10}'
template2 = '{:<10} {:<10} {:<10.5f} {:<15.5f} {:<15.5f} {:<15} {:<10} {:<10} {:<10.3f} \n'


def compute_loss(pred, labels):
    pred = pred.reshape(-1, pred.size()[-1])
    labels = torch.flatten(labels)
    labels = labels.view(-1)
    return criterion(pred, labels)

def train(siamese_gnn, logger, gen, lr, gamma, step_epoch, dataloader = None):
    siamese_gnn.train()
    optimizer = torch.optim.Adam(siamese_gnn.parameters(), lr=lr)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_epoch, gamma=gamma)
    if not dataloader:
        dataloader = gen.train_loader(args.batch_size)
    start = time.time()
    for epoch in range(args.epoch):
        for it, sample in enumerate(dataloader):
            sample = sample.to(device)
            pred, mask = siamese_gnn(sample)
            labels = torch.arange(0, pred.size(1)).unsqueeze(0).expand(batch_size, pred.size(1)).to(device)
            loss = compute_loss(pred, labels[: len(pred)])
            siamese_gnn.zero_grad()
            loss.backward()
            #nn.utils.clip_grad_norm(siamese_gnn.parameters(), args.clip_grad_norm)
            optimizer.step()
            logger.add_train_loss(loss)
            logger.add_train_accuracy(pred, labels[: len(pred)], mask)
            elapsed = time.time() - start
            if it % logger.args['print_freq'] == 0:
                loss = loss.data.cpu().numpy()#[0]
                info = ['epoch', 'iteration', 'loss', 'LAP accuracy', 'plain accuracy', 'edge_density',
                    'noise', 'model', 'elapsed']
                out = [epoch, it, loss.item(), logger.accuracy_train_lap[-1].item(), logger.accuracy_train_plain[-1].item(), args.edge_density,
                   args.noise, args.generative_model, elapsed]
                print(template1.format(*info))
                print(template2.format(*out))
            if it % logger.args['save_freq'] == 0:
                logger.save_model(siamese_gnn)
                logger.save_results()
        scheduler.step()
    print('Optimization finished.')
    logger.save_model(siamese_gnn)

def test(siamese_gnn, logger, gen, dataloader=None):
    siamese_gnn.eval()
    if not dataloader:
        dataloader = gen.test_loader(args.batch_size)
    for it, sample in enumerate(dataloader):
        sample = sample.to(device)
        pred, mask = siamese_gnn(sample)
        labels = torch.arange(0, pred.size(1)).unsqueeze(0).expand(batch_size, pred.size(1)).to(device)
        logger.add_test_accuracy(pred, labels[: len(pred)], mask)
    accuracy_lap = sum(logger.accuracy_test_lap)/len(logger.accuracy_test_lap)
    accuracy_plain = sum(logger.accuracy_test_plain)/len(logger.accuracy_test_plain)
    print('LAP Accuracy: ', accuracy_lap)
    print('Plain Accuracy: ', accuracy_plain)
    return accuracy_lap

def setup():
    logger = Logger(args.path_logger)
    logger.write_settings(args)
    config = make_config(args)
    model = base_model.BaseModel(config)
    siamese_gnn = siamese.Siamese(model).to(device)
    gen = Generator()
    gen.set_args(vars(args))
    if not args.real_world_dataset:
        gen.load_dataset()
    return siamese_gnn, logger, gen

def make_qap():
    siamese_gnn, logger, gen = setup()
    if args.real_world_dataset:
        train_dl, val_dl, test_dl = selfsupervised_dataloader(args, gen)
        train(siamese_gnn, logger, gen, args.lr, args.gamma, args.step_epoch, dataloader = train_dl)
        test(siamese_gnn, logger, gen, dataloader=val_dl)
    elif args.mode == 'train':
        train(siamese_gnn, logger, gen, args.lr, args.gamma, args.step_epoch)
    elif args.mode == 'test':
        siamese_gnn = logger.load_model()
        test(siamese_gnn, logger, gen)
    elif args.mode == 'experiment':
        start = time.time()
        train(siamese_gnn, logger, gen, args.lr, args.gamma, args.step_epoch)
        siamese_gnn = logger.load_model()
        acc = test(siamese_gnn, logger, gen)
        end = time.time()
        delta = end - start
        experiment.save_experiment(args, acc, delta)
    elif args.mode == 'validation':
        train(siamese_gnn, logger, gen, args.lr, args.gamma, args.step_epoch)
        siamese_gnn = logger.load_model()
        acc = test(siamese_gnn, logger, gen)
        with open(args.pickle, 'wb') as f:
            torch.save(args, f)
            torch.save(acc, f)

###############################################################################
#                                Classification                               #
###############################################################################

def classification_setup():
    logger = Logger(args.path_logger)
    logger.write_settings(args)
    config = make_config(args)
    model = base_model.BaseModel(config).to(device)
    dataloaders = classification_dataloader(args)
    return model, logger, dataloaders

def classification_train(model, logger, dataloader, lr, gamma):
    model.train()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    start = time.time()
    scheduler = optim.lr_scheduler.StepLR(optimizer, 20, gamma = gamma)
    for epoch in range(args.epoch):
        for it, (sample, label) in enumerate(dataloader):
            model.zero_grad()
            sample = sample.to(device)
            label = label.to(device)
            pred = model(sample)
            loss = criterion(pred, label)
            loss.backward()
            optimizer.step()
            logger.add_train_loss(loss)
            accuracy = ((label == pred.max(-1)[1])).float().mean()
            elapsed = time.time() - start
            if (it % logger.args['print_freq'] == 0) and not args.validation:
                loss = loss.data.cpu().numpy()#[0]
                info = ['epoch', 'iteration', 'loss', 'accuracy', 'elapsed']
                out = [epoch, it, loss.item(), accuracy.item(), elapsed]
                print(("{:<10} "*5).format(*info))
                print(("{:<10} "*2 + "{:<10.5f} "*2 + "{:<10.3f}").format(*out))
        scheduler.step()
    print('Optimization finished.')

def classification_test(model, logger, dataloader):
    model.eval()
    acc = 0
    for it, (sample, label) in enumerate(dataloader):
        sample = sample.to(device)
        label = label.to(device)
        pred = model(sample)
        accuracy = ((label == pred.max(-1)[1])).float().mean().item()
        acc += accuracy
    return acc/(it+1)

def make_classification():
    print(args)
    start = time.time()
    model, logger, dataloaders = classification_setup()
    classification_train(model, logger, dataloaders[0], args.lr, args.gamma)
    if args.overfit:
        acc = classification_test(model, logger, dataloaders[0])
    elif args.validation:
        acc = classification_test(model, logger, dataloaders[1])
        print('accuracy: ', acc)
        with open(args.pickle, 'wb') as f:
            torch.save(args, f)
            torch.save(acc, f)
    else:
        acc = classification_test(model, logger, dataloaders[2])
        end = time.time()
        print('accuracy: ', acc)
        delta = end - start
        experiment.save_experiment(args, acc, delta)

###############################################################################
#                              Self supervised                                #
###############################################################################
def apply_model(model, dataloader, X, Y):
    for i, (x, y) in enumerate(dataloader):
        x = x.to(device)
        y = y.to(device)
        out = model(x)
        X[i*dataloader.batch_size : i*dataloader.batch_size+out.size(0)] = out.data.cpu().numpy()
        Y[i*dataloader.batch_size : i*dataloader.batch_size+y.size(0)] = y.data.cpu().numpy()

def make_pretrained_classification():
    model, logger, dataloaders = classification_setup()
    logger.load_model()
    model.classification = True
    model.pretrained_classification = True
    model.suffix = suffix.MaxSuffixClassification()
    X_train = np.empty((args.num_examples_train, 2*args.num_features))
    Y_train = np.empty(args.num_examples_train)
    X_test  = np.empty((args.num_examples_val, 2*args.num_features))
    Y_test  = np.empty(args.num_examples_val)
    apply_model(model, dataloaders[0], X_train, Y_train)
    apply_model(model, dataloaders[1], X_test,  Y_test)
    print(Y_train, Y_test)
    clf = sklearn.svm.SVC(gamma='scale', cache_size=10000) #default mode
    clf.fit(X_train, Y_train)
    acc = clf.score(X_test, Y_test)
    print('Accuracy: {:.5f} with {}/{} split'.format(acc, len(Y_train), len(Y_test)))
    pred = clf.predict(X_test[0:10])
    print(pred, Y_test[0:10])

###############################################################################
#                                   Main                                      #
###############################################################################

if __name__ == '__main__':
    if args.pretrained_classification:
        make_pretrained_classification()
    else:
        make_qap()
