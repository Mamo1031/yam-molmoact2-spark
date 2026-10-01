import numpy as np
import pytest

from dryrun.kinematics import ArmKinematics
from dryrun.yam_stats import DEFAULT_START_POSE


@pytest.fixture(scope="module")
def kin() -> ArmKinematics:
    return ArmKinematics()


def test_grasp_position_is_finite_and_moves(kin: ArmKinematics) -> None:
    folded = kin.grasp_position(np.zeros(6))
    raised = kin.grasp_position(DEFAULT_START_POSE[:6])
    assert np.all(np.isfinite(folded)) and np.all(np.isfinite(raised))
    assert np.linalg.norm(folded - raised) > 0.1


def test_height_changes_with_shoulder(kin: ArmKinematics) -> None:
    q = DEFAULT_START_POSE[:6].copy()
    h0 = kin.grasp_height(q)
    q[1] += 0.3
    h1 = kin.grasp_height(q)
    assert h0 != pytest.approx(h1, abs=1e-3)
