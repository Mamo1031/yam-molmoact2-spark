"""No-actuation dry-run tools for MolmoAct2 on bimanual YAM arms.

Nothing in this package sends motor commands. It captures observations,
queries a policy server, validates the returned action chunk and logs it.
"""

STATE_DIM = 14
"""Joint-position state: left arm (6 joints + gripper) then right arm (6 joints + gripper)."""

STATE_NAMES = [
    *[f"left_joint_{i}" for i in range(6)],
    "left_gripper",
    *[f"right_joint_{i}" for i in range(6)],
    "right_gripper",
]
