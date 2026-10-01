"""Fixed numbers about the YAM arm and the MolmoAct2-BimanualYAM training distribution.

Sources (read on the DGX Spark, 2026-09-30):
- Joint ranges: i2rt ``robot_models/yam/yam_4310_linear.xml`` (commit f3dbf01).
- PD gains: i2rt ``robots/get_robot.py`` (``get_yam_robot``).
- Training statistics: ``norm_stats.json`` of checkpoint allenai/MolmoAct2-BimanualYAM
  (snapshot 8dcbed66), tag ``yam_dual_molmoact2``, ``action_stats``.
State/action order everywhere: left arm (6 joints + gripper) then right arm.
"""

from __future__ import annotations

import numpy as np

XML_JOINT_RANGE = np.array(
    [
        [-2.61799, 3.13],
        [0.0, 3.65],
        [0.0, 3.13],
        [-1.5708, 1.5708],
        [-1.5708, 1.5708],
        [-2.0944, 2.0944],
    ]
)
"""Per-joint [lower, upper] in radians for one arm (joint1..joint6)."""

GRIPPER_RANGE = (0.0, 1.0)
"""Normalized gripper command range: 0 = closed, 1 = open."""

KP = np.array([80.0, 80.0, 80.0, 40.0, 10.0, 10.0])
KD = np.array([5.0, 5.0, 5.0, 1.5, 1.5, 1.5])
"""Follower-server PD gains per joint (the gripper has its own gains)."""

GRAVITY_COMP_FACTOR = 1.3

TRAIN_ACTION_MIN = np.array(
    [
        -1.988,
        -0.007,
        -0.003,
        -1.696,
        -1.573,
        -2.184,
        0.0,
        -1.677,
        -0.007,
        -0.003,
        -1.706,
        -1.629,
        -2.143,
        0.0,
    ]
)
TRAIN_ACTION_MAX = np.array(
    [1.808, 3.199, 3.151, 1.593, 1.589, 2.208, 1.0, 2.441, 3.108, 3.153, 1.665, 1.595, 2.164, 1.0]
)
TRAIN_ACTION_Q01 = np.array(
    [
        -0.66,
        0.004,
        0.014,
        -1.374,
        -0.359,
        -0.93,
        0.051,
        -0.494,
        0.005,
        0.017,
        -1.424,
        -0.974,
        -0.472,
        0.033,
    ]
)
TRAIN_ACTION_Q99 = np.array(
    [0.47, 2.244, 2.008, 0.134, 0.883, 0.334, 0.987, 0.738, 2.285, 2.061, 0.24, 0.53, 0.962, 0.995]
)
TRAIN_ACTION_Q50 = np.array(
    [
        -0.073,
        1.449,
        1.283,
        -0.802,
        0.113,
        -0.222,
        0.733,
        0.082,
        1.542,
        1.252,
        -0.682,
        -0.129,
        0.192,
        0.697,
    ]
)
"""Training action distribution (radians, gripper 0..1); 76M samples."""

DEFAULT_START_POSE = TRAIN_ACTION_Q50.copy()
"""Median training pose: a sensible start pose (arms raised over the table, grippers ~0.7)."""
