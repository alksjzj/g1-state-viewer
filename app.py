#!/usr/bin/env python3
"""Local web application for subscribed G1 telemetry; no command API."""
import argparse
import json
import math
import signal
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from state import Cache, JOINT_NAMES

ROOT = Path(__file__).resolve().parent


def demo(cache, stop):
    """Explicitly artificial data, no DDS imports and no network to the robot."""
    t0 = time.monotonic()
    while not stop.is_set():
        t = time.monotonic() - t0
        roll = 0.08 * math.sin(t)
        motors = [dict(index=i, name=n, q=0.0, dq=0.0, tau_est=0.0,
                       temperature=[30, 31], vol=48.0, motorstate=0)
                  for i, n in enumerate(JOINT_NAMES)]
        for i in (3, 9):
            motors[i]['q'] = 0.15
        for i in (18, 25):
            motors[i]['q'] = 0.4 + 0.2 * math.sin(t)
            motors[i]['dq'] = 0.2 * math.cos(t)
        cache.put('state', dict(tick=int(t * 1000), mode_pr=0, mode_machine=0,
                  quaternion_wxyz=[math.cos(roll / 2), math.sin(roll / 2), 0, 0],
                  rpy=[roll, 0, 0], gyroscope=[0.08 * math.cos(t), 0, 0],
                  accelerometer=[0, 0, 9.81], imu_temperature_raw=35, motors=motors))
        cache.put('battery', dict(soc_pct=76, soh_raw=98, cycle=12,
                  voltage_raw=[49000, 49000, 0], current_raw=-2000,
                  temperature_raw=[30, 31, 29, 30] + [0] * 8,
                  cell_voltage_raw=[3769] * 13 + [0] * 27, state_raw=[0] * 5))
        cache.put('motion', dict(fsm_id=1, fsm_mode=0, task_id=0, task_time=0,
                                mode_label='阻尼'))
        cache.put('mainboard', dict(temperature_raw=[40] + [0] * 5,
                  fan_state_raw=[0] * 6, value_raw=[49, 49, 2, 0, 0, 0], state_raw=[0] * 6))
        points = []
        for i in range(1600):
            a = i * 2.39996323
            r = 1.0 + (i % 80) / 16
            points.append([round(r * math.cos(a), 4), round(r * math.sin(a), 4),
                           round(-0.6 if i % 4 else (i % 23) / 10, 4)])
        cache.put('lidar', dict(frame_id='DEMO_sensor', stamp=dict(sec=int(t), nanosec=0),
                               points=points, input_points=len(points), shown_points=len(points)))
        stop.wait(0.1)


def handler_for(cache):
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path == '/api/video/frame':
                frame = cache.get_video_image()
                self.send_response(200 if frame else 204)
                if frame:
                    self.send_header('Content-Type', frame[1])
                    self.send_header('Content-Length', str(len(frame[0])))
                self.end_headers()
                if frame:
                    try:
                        self.wfile.write(frame[0])
                    except (BrokenPipeError, ConnectionResetError):
                        pass
            elif path == '/api/state':
                body = json.dumps(cache.snapshot(), ensure_ascii=False, allow_nan=False,
                                  separators=(',', ':')).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                super().do_GET()

        def translate_path(self, path):
            path = unquote(urlsplit(path).path)
            base = ROOT / ('assets' if path.startswith('/assets/') else 'static')
            rel = (path[len('/assets/'):] if path.startswith('/assets/') else
                   path[len('/static/'):] if path.startswith('/static/') else path.lstrip('/'))
            target = (base / (rel or 'index.html')).resolve()
            if base not in target.parents or target.is_dir():
                return str(ROOT / '__not_found__')
            return str(target)

        def end_headers(self):
            self.send_header('X-Content-Type-Options', 'nosniff')
            # Local app assets change in-place during updates. Revalidate rather
            # than leaving an old UI attached to the new telemetry schema.
            self.send_header('Cache-Control', 'no-store')
            super().end_headers()

        def log_message(self, fmt, *args):
            if args and str(args[1]) not in ('200', '304'):
                super().log_message(fmt, *args)
    return Handler


def main():
    parser = argparse.ArgumentParser(description='G1 只读状态与雷达可视化')
    parser.add_argument('--demo', action='store_true', help='显式离线演示，不连接机器人')
    parser.add_argument('--interface', default='auto', help='机器人网卡名称，auto 按目标网段识别')
    parser.add_argument('--robot-ip', default='192.168.123.161', help='仅用于自动选择网卡的目标 IPv4')
    parser.add_argument('--domain', type=int, default=0)
    parser.add_argument('--peer', action='append', default=[], help='可选 DDS 对端 IP，可多次传入')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--lidar-topic', default='rt/utlidar/cloud_livox_mid360')
    parser.add_argument('--lidar-reliable', action='store_true', help='只匹配 reliable 点云发布端')
    parser.add_argument('--right-hand-order', choices=['middle-first', 'index-first'], default='middle-first',
                        help='右手 DDS 指序，当前实机现场校正为 middle-first')
    parser.add_argument('--max-points', type=int, default=50000)
    parser.add_argument('--max-range', type=float, default=50.0)
    args = parser.parse_args()
    if not 0 <= args.domain <= 232 or not 1 <= args.max_points <= 100000 or not 0 < args.max_range <= 1000:
        parser.error('domain 必须在 0..232，max-points 在 1..100000，max-range 在 (0,1000]')
    if not args.demo and args.interface == 'auto':
        from network import NetworkError, resolve_interface
        try:
            args.interface = resolve_interface(args.interface, args.robot_ip)
        except NetworkError as exc:
            parser.error(str(exc))
    cache = Cache('demo' if args.demo else 'live', args.interface, args.domain, args.lidar_topic)
    stop = threading.Event()
    receiver = None
    if args.demo:
        threading.Thread(target=demo, args=(cache, stop), daemon=True).start()
    else:
        try:
            from transport import Receiver
            receiver = Receiver(cache, args.interface, args.domain, args.peer, args.lidar_topic,
                                args.max_points, args.max_range, args.lidar_reliable, args.right_hand_order)
        except Exception as exc:
            message = 'DDS 初始化失败: %s: %s' % (type(exc).__name__, exc)
            for channel in cache.channels:
                cache.error(channel, message)
            print(message, flush=True)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(cache))
    server.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    print('G1 查看器 http://%s:%d | %s | Ctrl+C 退出' %
          (args.host, args.port, '离线演示（人工数据）' if args.demo else '只读订阅'), flush=True)
    server.timeout = 0.3
    try:
        while not stop.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if receiver:
            receiver.close()


if __name__ == '__main__':
    main()
