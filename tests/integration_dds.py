"""Real DDS -> HTTP test. Publishers are confined to domain 197 and loopback.

Only this test creates synthetic state/point-cloud publishers. No control data.
Run inside the existing SDK image; do not change its hard-coded interface/domain.
"""
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transport import domain_xml


def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:18765/api/state', timeout=3) as response:
        return json.load(response)


def main():
    from cyclonedds.domain import Domain, DomainParticipant
    from cyclonedds.topic import Topic
    from cyclonedds.pub import DataWriter
    from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_, BmsState_, MainBoardState_
    from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowState_, unitree_hg_msg_dds__BmsState_, unitree_hg_msg_dds__MainBoardState_
    from telemetry import get_motion_type
    from unitree_sdk2py.idl.sensor_msgs.msg.dds_ import PointCloud2_, PointField_
    from unitree_sdk2py.idl.std_msgs.msg.dds_ import Header_
    from unitree_sdk2py.idl.builtin_interfaces.msg.dds_ import Time_
    from lidar import get_pointcloud_type
    domain = Domain(197, domain_xml('lo'))
    participant = DomainParticipant(197)
    state_topic = Topic(participant, 'rt/lowstate', LowState_)
    cloud_topic = Topic(participant, 'rt/utlidar/cloud_livox_mid360', PointCloud2_)
    state_writer, cloud_writer = DataWriter(participant, state_topic), DataWriter(participant, cloud_topic)
    battery_topic = Topic(participant, 'rt/lf/bmsstate', BmsState_)
    mainboard_topic = Topic(participant, 'rt/lf/mainboardstate', MainBoardState_)
    motion_topic = Topic(participant, 'rt/sportmodestate', get_motion_type())
    battery_writer, mainboard_writer, motion_writer = (DataWriter(participant, topic)
                    for topic in (battery_topic, mainboard_topic, motion_topic))
    battery = unitree_hg_msg_dds__BmsState_()
    battery.soc, battery.soh, battery.cycle, battery.current = 47, 96, 9, -2396
    battery.bmsvoltage = [48305, 48308, 0]
    mainboard = unitree_hg_msg_dds__MainBoardState_()
    mainboard.temperature = [52, 0, 0, 0, 0, 0]
    motion = get_motion_type()(1, 0, 4, 0.0)
    msg = unitree_hg_msg_dds__LowState_()
    msg.imu_state.quaternion = [1, 0, 0, 0]
    msg.motor_state[3].q = 0.375
    cloud = PointCloud2_(Header_(Time_(123, 456), 'sdk_test_sensor'), 1, 2,
                        [PointField_(axis, i * 4, 7, 1) for i, axis in enumerate('xyz')],
                        False, 12, 24, list(struct.pack('<ffffff', 1, 2, 3, 4, 5, 6)), True)
    # Check CDR compatibility against the independently generated SDK type.
    decoded = get_pointcloud_type().deserialize(cloud.serialize())
    assert decoded.header.frame_id == 'sdk_test_sensor'
    app = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'app.py'),
                            '--interface', 'lo', '--domain', '197', '--port', '18765'])
    try:
        deadline = time.monotonic() + 15
        result = None
        while time.monotonic() < deadline:
            state_writer.write(msg)
            cloud_writer.write(cloud)
            battery_writer.write(battery)
            mainboard_writer.write(mainboard)
            motion_writer.write(motion)
            time.sleep(0.1)
            try:
                result = snapshot()
            except OSError:
                continue
            if all(result[k] for k in ('state', 'lidar', 'battery', 'mainboard', 'motion')):
                break
        assert result and result['state'] and result['lidar'], result
        assert result['state']['motors'][3]['q'] == 0.375, result['state']
        assert result['lidar']['points'] == [[1, 2, 3], [4, 5, 6]], result['lidar']
        assert result['channels']['state']['status'] == 'live'
        assert result['channels']['lidar']['status'] == 'live'
        assert result['battery']['soc_pct'] == 47
        assert result['battery']['current_raw'] == -2396
        assert result['mainboard']['temperature_raw'][0] == 52
        assert result['motion']['mode_label'] == '阻尼'
        with urllib.request.urlopen('http://127.0.0.1:18765/api/video/frame') as frame:
            assert frame.status == 204  # absent video must not supply fake image bytes
        with urllib.request.urlopen('http://127.0.0.1:18765/') as page:
            assert b'<html' in page.read().lower()
        time.sleep(2.3)
        stale = snapshot()
        assert all(stale['channels'][k]['status'] == 'stale' for k in ('state', 'lidar', 'battery', 'mainboard', 'motion')), stale['channels']
        msg.imu_state.quaternion = [0, 0, 0, 0]
        for _ in range(5):
            state_writer.write(msg)
            time.sleep(0.1)
        assert snapshot()['channels']['state']['status'] == 'error'
        msg.imu_state.quaternion = [1, 0, 0, 0]
        msg.mode_pr = 1
        for _ in range(5):
            state_writer.write(msg)
            time.sleep(0.1)
        recovered = snapshot()
        assert recovered['channels']['state']['status'] == 'live'
        assert recovered['state']['mode_pr'] == 1
        print(json.dumps({'result': 'PASS', 'domain': 197, 'interface': 'lo',
                          'robot_contacted': False, 'checks': ['SDK CDR interoperability',
                          'DDS LowState -> HTTP', 'DDS PointCloud2 -> HTTP', 'stale timeout',
                          'DDS BMS/MainBoard/Motion -> HTTP', 'missing video returns HTTP 204',
                          'invalid sample detection', 'recovery and AB mode passthrough']}, indent=2))
    finally:
        app.terminate()
        try:
            app.wait(timeout=5)
        except subprocess.TimeoutExpired:
            app.kill()
            app.wait()


if __name__ == '__main__':
    main()
