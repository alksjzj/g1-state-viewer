"""Host ROS 2 -> SDK container interoperability. Loopback/domain 198 only."""
import json
import os
from pathlib import Path
import struct
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
os.environ['ROS_DOMAIN_ID'] = '198'
os.environ['ROS_LOCALHOST_ONLY'] = '1'
os.environ['RMW_IMPLEMENTATION'] = 'rmw_cyclonedds_cpp'
os.environ['CYCLONEDDS_URI'] = ('<CycloneDDS><Domain Id="any"><General><Interfaces>'
                             '<NetworkInterface name="lo"/></Interfaces></General></Domain></CycloneDDS>')


def main():
    import rclpy
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from sensor_msgs.msg import PointCloud2, PointField
    rclpy.init()
    node = rclpy.create_node('g1_viewer_local_test')
    name = 'g1-viewer-ros2-test-%d' % os.getpid()
    app = subprocess.Popen([
        'docker', 'run', '--rm', '--name', name, '--network', 'host', '--read-only',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--user', '%d:%d' % (os.getuid(), os.getgid()),
        '--mount', 'type=bind,src=%s,dst=/app,readonly' % ROOT,
        '-e', 'PYTHONDONTWRITEBYTECODE=1', '--entrypoint', 'python3',
        os.environ.get('G1_VIEWER_IMAGE', 'g1-state-viewer:local'),
        '-B', '/app/app.py', '--interface', 'lo', '--domain', '198', '--port', '18766',
    ])
    try:
        for reliability in (ReliabilityPolicy.BEST_EFFORT, ReliabilityPolicy.RELIABLE):
            qos = QoSProfile(depth=1, reliability=reliability, durability=DurabilityPolicy.VOLATILE)
            pub = node.create_publisher(PointCloud2, '/utlidar/cloud_livox_mid360', qos)
            msg = PointCloud2()
            msg.header.frame_id = 'ros2_' + reliability.name
            msg.header.stamp.sec = 456
            msg.height, msg.width = 1, 2
            msg.fields = [PointField(name=a, offset=i * 4, datatype=7, count=1)
                          for i, a in enumerate('xyz')]
            msg.point_step, msg.row_step = 12, 24
            msg.data = struct.pack('<ffffff', 1.5, -2.5, 3.5, 4.5, 5.5, 6.5)
            result = None
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                pub.publish(msg)
                rclpy.spin_once(node, timeout_sec=0.1)
                try:
                    with urllib.request.urlopen('http://127.0.0.1:18766/api/state', timeout=2) as response:
                        result = json.load(response)
                except OSError:
                    continue
                if result['lidar'] and result['lidar']['frame_id'] == msg.header.frame_id:
                    break
            assert result and result['lidar'] and result['lidar']['frame_id'] == msg.header.frame_id, result
            assert result['lidar']['points'] == [[1.5, -2.5, 3.5], [4.5, 5.5, 6.5]], result['lidar']
            node.destroy_publisher(pub)
        print(json.dumps(dict(result='PASS', interface='lo', domain=198,
                              robot_contacted=False, checks=['ROS 2 Jazzy PointCloud2 -> CycloneDDS 0.10.2',
                              'best-effort writer', 'reliable writer']), indent=2))
    finally:
        node.destroy_node()
        rclpy.shutdown()
        subprocess.run(['docker', 'stop', '-t', '3', name], stdout=subprocess.DEVNULL, check=False)
        app.wait(timeout=8)


if __name__ == '__main__':
    main()
