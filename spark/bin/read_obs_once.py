"""Read one observation from a YAM arm (reference.md section 6.3). Starts gravity compensation briefly."""
import sys, json, time
import numpy as np
from i2rt.robots.get_robot import get_yam_robot
from i2rt.robots.utils import GripperType

channel = sys.argv[1]
robot = get_yam_robot(channel, gripper_type=GripperType.LINEAR_4310)
try:
    time.sleep(1.0)
    obs = robot.get_observations()
    out = {k: np.asarray(v).round(4).tolist() for k, v in obs.items()}
    print("OBS_JSON " + json.dumps(out))
    print("joint_pos len:", len(obs["joint_pos"]), "gripper_pos len:", len(obs["gripper_pos"]))
finally:
    robot.close()
