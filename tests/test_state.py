import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from state import Cache, JOINT_NAMES, HAND_NAMES, convert_state, convert_hand


def message():
    return NS(tick=42, mode_pr=0, mode_machine=5,
              imu_state=NS(quaternion=[2, 0, 0, 0], rpy=[0, 0, 0],
                           gyroscope=[0, 0, 0], accelerometer=[0, 0, 9.81]),
              motor_state=[NS(q=i / 100, dq=0, tau_est=0, temperature=[30, 31],
                              vol=48, motorstate=0) for i in range(35)])


class StateTests(unittest.TestCase):
    def test_dex3_side_specific_mapping(self):
        msg = NS(motor_state=message().motor_state[:7], power_v=24, power_a=1, error=[0, 0])
        left, right = convert_hand(msg, 'left'), convert_hand(msg, 'right')
        self.assertEqual(left['motors'][3]['name'], 'left_hand_middle_0_joint')
        self.assertEqual(right['motors'][3]['name'], 'right_hand_middle_0_joint')
        self.assertEqual(right['motors'][5]['name'], 'right_hand_index_0_joint')
        self.assertEqual(right['motors'][3]['q'], 0.03)
        alternate = convert_hand(msg, 'right', 'index-first')
        self.assertEqual(alternate['motors'][3]['name'], 'right_hand_index_0_joint')
        self.assertEqual(alternate['motors'][5]['name'], 'right_hand_middle_0_joint')
        msg.motor_state.pop()
        with self.assertRaises(ValueError):
            convert_hand(msg, 'left')

    def test_body_mapping_and_normalization(self):
        result = convert_state(message())
        self.assertEqual(len(result['motors']), 29)
        self.assertEqual(result['motors'][12]['name'], 'waist_yaw_joint')
        self.assertEqual(result['motors'][28]['name'], 'right_wrist_yaw_joint')
        self.assertEqual(result['quaternion_wxyz'], [1, 0, 0, 0])

    def test_invalid_pose_rejected(self):
        for quat in ([0, 0, 0, 0], [math.nan, 0, 0, 0]):
            msg = message()
            msg.imu_state.quaternion = quat
            with self.assertRaises(ValueError):
                convert_state(msg)
        msg = message()
        msg.motor_state[3].q = math.inf
        with self.assertRaises(ValueError):
            convert_state(msg)

    def test_waiting_live_stale_error_recovery(self):
        with patch('state.time.monotonic', return_value=100) as clock:
            cache = Cache('live', 'lo', 197, 'test')
            self.assertEqual(cache.snapshot()['channels']['state']['status'], 'waiting')
            cache.put('state', convert_state(message()))
            self.assertEqual(cache.snapshot()['channels']['state']['status'], 'live')
            clock.return_value = 103
            self.assertEqual(cache.snapshot()['channels']['state']['status'], 'stale')
            self.assertEqual(cache.snapshot()['channels']['lidar']['status'], 'waiting')
            cache.error('state', 'bad sample')
            self.assertEqual(cache.snapshot()['channels']['state']['status'], 'error')
            cache.put('state', convert_state(message()))
            self.assertEqual(cache.snapshot()['channels']['state']['status'], 'live')


if __name__ == '__main__':
    unittest.main()
