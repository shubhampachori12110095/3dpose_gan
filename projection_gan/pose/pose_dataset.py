#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2017 Yasunori Kudo

import chainer
import copy
import glob
import numpy as np
import os
import pickle

# Joints in H3.6M -- data has 32 joints, but only 17 that move; these are the indices.
H36M_NAMES = ['']*32
H36M_NAMES[0]  = 'Hip'
H36M_NAMES[1]  = 'RHip'
H36M_NAMES[2]  = 'RKnee'
H36M_NAMES[3]  = 'RFoot'
H36M_NAMES[6]  = 'LHip'
H36M_NAMES[7]  = 'LKnee'
H36M_NAMES[8]  = 'LFoot'
H36M_NAMES[12] = 'Spine'
H36M_NAMES[13] = 'Thorax'
H36M_NAMES[14] = 'Neck/Nose'
H36M_NAMES[15] = 'Head'
H36M_NAMES[17] = 'LShoulder'
H36M_NAMES[18] = 'LElbow'
H36M_NAMES[19] = 'LWrist'
H36M_NAMES[25] = 'RShoulder'
H36M_NAMES[26] = 'RElbow'
H36M_NAMES[27] = 'RWrist'


def project_point_radial(P, R, T, f, c, k, p):
    """
    Project points from 3d to 2d using camera parameters
    including radial and tangential distortion
    Args
    P: Nx3 points in world coordinates
    R: 3x3 Camera rotation matrix
    T: 3x1 Camera translation parameters
    f: (scalar) Camera focal length
    c: 2x1 Camera center
    k: 3x1 Camera radial distortion coefficients
    p: 2x1 Camera tangential distortion coefficients
    Returns
    Proj: Nx2 points in pixel space
    D: 1xN depth of each point in camera space
    radial: 1xN radial distortion per point
    tan: 1xN tangential distortion per point
    r2: 1xN squared radius of the projected points before distortion
    """

    # P is a matrix of 3-dimensional points
    assert len(P.shape) == 2
    assert P.shape[1] == 3

    N = P.shape[0]
    X = R.dot(P.T - T) # rotate and translate
    XX = X[:2, :] / X[2, :]
    r2 = XX[0, :]**2 + XX[1, :]**2

    radial = 1 + np.einsum('ij,ij->j', np.tile(k, (1, N)), np.array([r2, r2**2, r2**3]))
    tan = p[0] * XX[1, :] + p[1] * XX[0, :]

    XXX = XX * np.tile(radial + tan, (2, 1)) + np.outer(np.array([p[1], p[0]]).reshape(-1), r2)

    Proj = (f * XXX) + c
    Proj = Proj.T

    D = X[2,]

    return Proj, D, radial, tan, r2


class PoseDataset(chainer.dataset.DatasetMixin):

    def __init__(self, action='all', length=1, train=True, use_sh_detection=False):
        if train:
            subjects = ['S1', 'S5', 'S6', 'S7', 'S8']
        else:
            subjects = ['S9', 'S11']

        # 3Dポイント
        with open('data/points_3d.pickle', 'rb') as f:
            p3d = pickle.load(f)
        # カメラパラメータ
        with open('data/cameras.pickle', 'rb') as f:
            cams = pickle.load(f)
        # StackedHourglassによる検出結果
        with open('data/sh_detect.pickle', 'rb') as f:
            p2d_sh = pickle.load(f)

        with open('data/actions.txt') as f:
            actions_all = f.read().split('\n')[:-1]
        if action == 'all':
            actions = actions_all
        elif action in actions_all:
            actions = [action]
        else:
            raise Exception('Invalid action.')

        # 使用する関節位置のインデックス(17点)
        dim_to_use_x = np.where(np.array([x != '' for x in H36M_NAMES]))[0] * 3
        dim_to_use_y = dim_to_use_x + 1
        dim_to_use_z = dim_to_use_x + 2
        dim_to_use = np.array([dim_to_use_x, dim_to_use_y, dim_to_use_z]).T.flatten()
        self.N = len(dim_to_use_x)

        p3d = copy.deepcopy(p3d)
        self.data_list = []
        for s in subjects:
            for action_name in actions:
                def search(a):
                    fs = list(filter(
                        lambda x: x.split()[0] == a, p3d[s].keys()))
                    return fs
                files = []
                files += search(action_name)
                # 'Photo' is 'TakingPhoto' in S1
                if action_name == 'Photo':
                    files += search('TakingPhoto')
                # 'WalkDog' is 'WalkingDog' in S1
                if action_name == 'WalkDog':
                    files += search('WalkingDog')
                for file_name in files:
                    p3d[s][file_name] = p3d[s][file_name][:, dim_to_use]
                    L = p3d[s][file_name].shape[0]
                    for cam_name in cams[s].keys():
                        if not (cam_name == '54138969' and s == 'S11' \
                                and action_name == 'Directions'):
                            for start_pos in range(0, L - length + 1, 5):  # 50Hz -> 10Hz
                                info = {'subject': s, 'action_name': action_name,
                                        'start_pos': start_pos, 'length': length,
                                        'cam_name': cam_name, 'file_name': file_name}
                                self.data_list.append(info)
        self.p3d = p3d
        self.cams = cams
        self.p2d_sh = p2d_sh
        self.train = train
        self.use_sh_detection = use_sh_detection

    def __len__(self):
        return len(self.data_list)

    def get_example(self, i):
        info = self.data_list[i]
        subject = info['subject']
        action_name = info['action_name']
        start_pos = info['start_pos']
        length = info['length']
        cam_name = info['cam_name']
        file_name = info['file_name']

        poses_xyz = self.p3d[subject][file_name][start_pos:start_pos+length]
        params = self.cams[subject][cam_name]
        if self.use_sh_detection:
            if 'TakingPhoto' in file_name:
                file_name = file_name.replace('TakingPhoto', 'Photo')
            if 'WalkingDog' in file_name:
                file_name = file_name.replace('WalkingDog', 'WalkDog')
            sh_detect_xy = self.p2d_sh[subject][file_name][cam_name][start_pos:start_pos+length]

        # カメラ位置からの平行投影
        P = poses_xyz.reshape(-1, 3)
        X = params['R'].dot(P.T).T
        X = X.reshape(-1, self.N * 3)  # shape=(length, 3*n_joints)

        # 3Dモデルの正規化
        # hip(0)と各関節点の距離の平均値が1になるようにスケール
        xs = X.T[0::3] - X.T[0]
        ys = X.T[1::3] - X.T[1]
        ls = np.sqrt(xs[1:]**2 + ys[1:]**2)  # 原点からの距離 shape=(N-1,length)
        scale = ls.mean(axis=0)
        X = X.T / scale
        # hip(0)が原点になるようにシフト
        X[0::3] -= X[0]
        X[1::3] -= X[1]
        X[2::3] -= X[2]
        X = X.T.astype(np.float32)[None]

        if not self.use_sh_detection:
            # カメラパラメータを用いた画像上への投影
            proj = project_point_radial(P, **params)[0]
            proj = proj.reshape(-1, self.N * 2)  # shape=(length, 2*n_joints)

            # Ground Truth 2DPoseの正規化
            # hip(0)と各関節点の距離の平均値が1になるようにスケール
            xs = proj.T[0::2] - proj.T[0]
            ys = proj.T[1::2] - proj.T[1]
            proj = proj.T / np.sqrt(xs[1:]**2 + ys[1:]**2).mean(axis=0)
            # hip(0)が原点になるようにシフト
            proj[0::2] -= proj[0]
            proj[1::2] -= proj[1]
            proj = proj.T.astype(np.float32)[None]

            return proj, X, scale.astype(np.float32)

        else:
            # StackedHourglassにより検出されたPoseの正規化
            # hip(0)と各関節点の距離の平均値が1になるようにスケール
            xs = sh_detect_xy.T[0::2] - sh_detect_xy.T[0]
            ys = sh_detect_xy.T[1::2] - sh_detect_xy.T[1]
            sh_detect_xy = sh_detect_xy.T / np.sqrt(xs[1:]**2 + ys[1:]**2).mean(axis=0)
            # hip(0)が原点になるようにシフト
            sh_detect_xy[0::2] -= sh_detect_xy[0]
            sh_detect_xy[1::2] -= sh_detect_xy[1]
            sh_detect_xy = sh_detect_xy.T.astype(np.float32)[None]

            return sh_detect_xy, X, scale.astype(np.float32)
