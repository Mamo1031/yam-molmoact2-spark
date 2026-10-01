"""Forward kinematics of one YAM arm from the i2rt MuJoCo model (grasp-site height check)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

DEFAULT_XML = Path(__file__).resolve().parents[1] / "assets" / "yam" / "yam_4310_linear.xml"
GRASP_SITE = "grasp_site"


class ArmKinematics:
    """Position of the grasp site (between the gripper fingers) in the arm base frame."""

    def __init__(self, xml_path: str | Path = DEFAULT_XML) -> None:
        import mujoco

        self._mujoco = mujoco
        self.model = mujoco.MjModel.from_xml_path(str(xml_path))
        self.data = mujoco.MjData(self.model)
        self._site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, GRASP_SITE)
        if self._site < 0:
            raise ValueError(f"site {GRASP_SITE!r} not found in {xml_path}")
        if self.model.nq < 6:
            raise ValueError(f"model has {self.model.nq} DoF, expected at least 6")

    def grasp_position(self, q6: np.ndarray) -> np.ndarray:
        """(x, y, z) of the grasp site for the six arm joint angles, in the base frame [m]."""
        q = np.zeros(self.model.nq)
        q[:6] = np.asarray(q6, dtype=np.float64)[:6]
        self.data.qpos[:] = q
        self._mujoco.mj_kinematics(self.model, self.data)
        return np.array(self.data.site_xpos[self._site])

    def grasp_height(self, q6: np.ndarray) -> float:
        return float(self.grasp_position(q6)[2])

    def lift_to(self, q6: np.ndarray, min_z: float, iterations: int = 6) -> np.ndarray | None:
        """Smallest joint change that raises the grasp site to ``min_z``; None if there is none.

        Least-squares step along the height gradient, so the motion parallel to the table that
        the pose already contains is kept. A pose at or above ``min_z`` is returned unchanged.
        """
        q = np.asarray(q6, dtype=np.float64)[:6].copy()
        eps = 1e-4
        for _ in range(iterations):
            z = self.grasp_height(q)
            if z >= min_z:
                return q
            grad = np.array([(self.grasp_height(q + eps * e) - z) / eps for e in np.eye(6)])
            norm2 = float(grad @ grad)
            if norm2 < 1e-8:
                return None
            # 0.1 mm above the plane so the iteration ends on the allowed side.
            q = q + grad * (min_z - z + 1e-4) / norm2
        return q if self.grasp_height(q) >= min_z else None
