"""Pure data conversion and bounded, thread-safe receive cache. No robot writes."""
import math
import threading
import time
from collections import deque

JOINT_NAMES = [
    side + '_' + joint + '_joint'
    for side in ('left', 'right')
    for joint in ('hip_pitch', 'hip_roll', 'hip_yaw', 'knee', 'ankle_pitch', 'ankle_roll')
] + ['waist_yaw_joint', 'waist_roll_joint', 'waist_pitch_joint'] + [
    side + '_' + joint + '_joint'
    for side in ('left', 'right')
    for joint in ('shoulder_pitch', 'shoulder_roll', 'shoulder_yaw', 'elbow',
                  'wrist_roll', 'wrist_pitch', 'wrist_yaw')
]

# Current attached robot: user visually verified middle-first on the right.
# Official SDK variants also use index-first on the right; expose an override.
HAND_NAMES = {
    side: [side + '_hand_' + joint + '_joint' for joint in joints]
    for side, joints in {
        'left': ('thumb_0', 'thumb_1', 'thumb_2', 'middle_0', 'middle_1', 'index_0', 'index_1'),
        'right': ('thumb_0', 'thumb_1', 'thumb_2', 'middle_0', 'middle_1', 'index_0', 'index_1'),
    }.items()
}


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('状态包含 NaN/Infinity')
    return value


def convert_state(msg):
    if len(msg.motor_state) < 29:
        raise ValueError('需要 G1 29 个身体关节状态')
    imu = msg.imu_state
    quat = [finite(v) for v in imu.quaternion]
    norm = math.sqrt(sum(v * v for v in quat))
    if len(quat) != 4 or norm < 1e-6:
        raise ValueError('IMU 四元数无效')
    return {
        'tick': int(msg.tick), 'mode_pr': int(msg.mode_pr),
        'mode_machine': int(msg.mode_machine),
        'quaternion_wxyz': [v / norm for v in quat],
        'rpy': [finite(v) for v in imu.rpy],
        'gyroscope': [finite(v) for v in imu.gyroscope],
        'accelerometer': [finite(v) for v in imu.accelerometer],
        'imu_temperature_raw': int(imu.temperature) if hasattr(imu, 'temperature') else None,
        'motors': [dict(index=i, name=name, q=finite(m.q), dq=finite(m.dq),
                        tau_est=finite(m.tau_est), temperature=[int(t) for t in m.temperature],
                        vol=finite(m.vol), motorstate=int(m.motorstate))
                   for i, (name, m) in enumerate(zip(JOINT_NAMES, msg.motor_state))],
    }


def convert_hand(msg, side, right_hand_order='middle-first'):
    if len(msg.motor_state) != 7:
        raise ValueError('Dex3 需要恰好 7 个手指关节，实际为 %d' % len(msg.motor_state))
    names = HAND_NAMES[side]
    if side == 'right' and right_hand_order == 'index-first':
        names = [names[i] for i in (0, 1, 2, 5, 6, 3, 4)]
    return dict(side=side, power_v=finite(msg.power_v), power_a=finite(msg.power_a),
                error=[int(v) for v in msg.error],
                motors=[dict(index=i, name=name, q=finite(m.q), dq=finite(m.dq),
                             tau_est=finite(m.tau_est), temperature=[int(t) for t in m.temperature],
                             vol=finite(m.vol), motorstate=int(m.motorstate))
                        for i, (name, m) in enumerate(zip(names, msg.motor_state))])


def convert_battery(msg):
    soc = int(msg.soc)
    if not 0 <= soc <= 100:
        raise ValueError('电池 SOC 超出 0..100，拒绝将无效值显示为电量')
    return dict(soc_pct=soc, soh_raw=int(msg.soh), cycle=int(msg.cycle),
                voltage_raw=[int(v) for v in msg.bmsvoltage], current_raw=int(msg.current),
                temperature_raw=[int(v) for v in msg.temperature],
                cell_voltage_raw=[int(v) for v in msg.cell_vol],
                state_raw=[int(v) for v in msg.bmsstate])


def convert_mainboard(msg):
    return dict(temperature_raw=[int(v) for v in msg.temperature],
                fan_state_raw=[int(v) for v in msg.fan_state],
                value_raw=[finite(v) for v in msg.value],
                state_raw=[int(v) for v in msg.state])


def convert_motion(msg):
    # Labels follow official g1/loco/g1_loco_client.hpp; unknown IDs stay explicit.
    # Start(500) is not proof that the robot is physically walking.
    labels = {0: '零力矩', 1: '阻尼', 2: '下蹲', 3: '坐下', 4: '站起',
              500: '运动控制（Start）', 702: '躺卧起身', 706: '蹲站转换'}
    fsm_id = int(msg.fsm_id)
    return dict(fsm_id=fsm_id, fsm_mode=int(msg.fsm_mode), task_id=int(msg.task_id),
                task_time=finite(msg.task_time), mode_label=labels.get(fsm_id, '未映射模式'))


class Cache:
    def __init__(self, mode, interface, domain, lidar_topic, stale_after=2.0):
        self.lock = threading.Lock()
        self.meta = dict(mode=mode, interface=interface, domain=domain,
                         started_at=time.time(), joint_names=JOINT_NAMES)
        self.stale_after = stale_after
        self.video_image = None
        self.channels = {name: dict(count=0, last=None, times=deque(maxlen=1000),
                                    error=None, rejected=0, matched=0, topic=topic)
                         for name, topic in [('state', 'rt/lowstate'), ('lidar', lidar_topic),
                                             ('hand_left', 'rt/dex3/left/state'),
                                             ('hand_right', 'rt/dex3/right/state'),
                                             ('battery', 'rt/lf/bmsstate'),
                                             ('mainboard', 'rt/lf/mainboardstate'),
                                             ('motion', 'rt/sportmodestate'),
                                             ('video', 'rt/frontvideostream')]}
        self.data = dict.fromkeys(self.channels)

    def put(self, name, value):
        now = time.monotonic()
        with self.lock:
            c = self.channels[name]
            c['count'] += 1
            c['last'] = now
            c['times'].append(now)
            c['error'] = None
            if name in ('lidar', 'video'):
                value['seq'] = c['count']
            self.data[name] = value

    def put_video(self, value, image, mime):
        # Store binary data outside the JSON response and replace it atomically.
        with self.lock:
            c = self.channels['video']
            now = time.monotonic()
            c['count'] += 1
            c['last'] = now
            c['times'].append(now)
            c['error'] = None
            value = dict(value, seq=c['count'], image_available=image is not None)
            self.data['video'] = value
            self.video_image = (image, mime) if image is not None else None

    def get_video_image(self):
        with self.lock:
            c = self.channels['video']
            if c['last'] is None or c['error'] or time.monotonic() - c['last'] > self.stale_after:
                return None
            return self.video_image

    def error(self, name, error):
        with self.lock:
            self.channels[name]['error'] = str(error)
            self.channels[name]['rejected'] += 1

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            channels = {}
            for name, c in self.channels.items():
                age = None if c['last'] is None else max(0, now - c['last'])
                times = [t for t in c['times'] if now - t < 2]
                hz = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 else 0
                status = ('error' if c['error'] else 'waiting' if age is None else
                          'stale' if age > self.stale_after else 'live')
                channels[name] = dict(status=status, age_s=age, hz=round(hz, 1),
                                      **{k: c[k] for k in ('count', 'error', 'rejected', 'matched', 'topic')})
            return dict(self.meta, channels=channels, **self.data)
