#!/usr/bin/env python3
"""Read-only Linux interface discovery. No network or system configuration changes."""
import argparse
import ipaddress
import json
from pathlib import Path
import shutil
import subprocess
import sys

DEFAULT_ROBOT_IP = '192.168.123.161'


class NetworkError(ValueError):
    pass


def _ip_json(*args):
    try:
        result = subprocess.run(['ip', '-j', *args], check=True, capture_output=True, text=True, timeout=5)
        return json.loads(result.stdout)
    except FileNotFoundError as exc:
        raise NetworkError('未找到 ip 命令；Ubuntu 需要 iproute2。请手动安装后重试。') from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        detail = getattr(exc, 'stderr', None) or str(exc)
        raise NetworkError('无法读取 ip -j %s: %s' % (' '.join(args), detail.strip())) from exc


def collect_snapshot(sys_root='/sys/class/net'):
    """Capture iproute2 JSON plus sysfs facts; callers can inject this whole dict."""
    links = _ip_json('link', 'show')
    addresses = _ip_json('address', 'show')
    routes = _ip_json('route', 'show', 'table', 'all')
    devices = {}
    for link in links:
        name = link['ifname']
        base = Path(sys_root) / name
        try:
            carrier = (base / 'carrier').read_text().strip() == '1'
        except OSError:
            carrier = 'LOWER_UP' in link.get('flags', [])
        devices[name] = {'physical': (base / 'device').exists(), 'carrier': carrier,
                         'wireless': (base / 'wireless').exists()}
    return {'links': links, 'addresses': addresses, 'routes': routes, 'devices': devices}


def interfaces(snapshot):
    addresses = {item['ifname']: item.get('addr_info', []) for item in snapshot['addresses']}
    result = []
    for link in snapshot['links']:
        name = link['ifname']
        ipv4 = []
        for addr in addresses.get(name, []):
            if addr.get('family') == 'inet':
                try:
                    ipv4.append(str(ipaddress.IPv4Interface('%s/%s' % (addr['local'], addr['prefixlen']))))
                except (KeyError, ValueError):
                    continue
        device = snapshot.get('devices', {}).get(name, {})
        result.append({'name': name, 'up': 'UP' in link.get('flags', []),
                       'carrier': bool(device.get('carrier', 'LOWER_UP' in link.get('flags', []))),
                       'physical': bool(device.get('physical', False)),
                       'wireless': bool(device.get('wireless', False)), 'ipv4': ipv4})
    return result


def _target(robot_ip):
    try:
        return ipaddress.IPv4Address(robot_ip)
    except ipaddress.AddressValueError as exc:
        raise NetworkError('--robot-ip 必须是 IPv4 地址: %s' % robot_ip) from exc


def candidates(snapshot, robot_ip=DEFAULT_ROBOT_IP):
    target = _target(robot_ip)
    return [item for item in interfaces(snapshot)
            if item['up'] and item['carrier'] and item['physical'] and
            any(target in ipaddress.IPv4Interface(addr).network for addr in item['ipv4'])]


def describe(item):
    return '%s | %s | carrier=%s | %s | IPv4=%s' % (
        item['name'], 'UP' if item['up'] else 'DOWN', 'yes' if item['carrier'] else 'no',
        'physical' if item['physical'] else 'virtual', ','.join(item['ipv4']) or '(none)')


def choose_interface(snapshot, interface='auto', robot_ip=DEFAULT_ROBOT_IP, interactive=False,
                     input_func=None):
    """Choose conservatively. A supplied callback receives the candidate rows, returns a name."""
    _target(robot_ip)
    rows = interfaces(snapshot)
    if interface != 'auto':
        selected = next((item for item in rows if item['name'] == interface), None)
        if selected is None:
            raise NetworkError('网卡 %s 不存在。请运行 ./run.sh --interfaces。' % interface)
        if not selected['up'] or not selected['ipv4']:
            raise NetworkError('指定网卡必须为 UP 且有 IPv4: %s。请自行检查网络配置。' % describe(selected))
        if selected['physical'] and not selected['carrier']:
            raise NetworkError('指定物理网卡没有链路 carrier: %s。请检查网线。' % interface)
        return interface
    choices = candidates(snapshot, robot_ip)
    if len(choices) == 1:
        return choices[0]['name']
    if not choices:
        detail = '\n'.join('  ' + describe(item) for item in rows)
        raise NetworkError('没有找到 UP、有链路、有 IPv4 且与目标 %s 同网段的物理网卡。\n%s\n'
                           '请核对机器人实际 IPv4 与主机网络配置；可用 --robot-ip IP 辅助自动选择，'
                           '或自行确认后使用 --interface NAME。程序不会修改 IP/路由。' % (robot_ip, detail))
    if interactive and input_func is not None:
        name = input_func(choices)
        if name in {item['name'] for item in choices}:
            return name
        raise NetworkError('选择无效；请使用 --interface NAME 明确指定。')
    detail = '\n'.join('  %s\n    ./run.sh --interface %s' % (describe(item), item['name']) for item in choices)
    raise NetworkError('多个物理网卡与目标 %s 同网段，无法安全自动选择：\n%s\n'
                       '请明确指定 --interface NAME；后台或非交互环境不会自动选第一个。' % (robot_ip, detail))


def prompt_choice(rows):
    print('多个候选网卡，请选择编号：', file=sys.stderr)
    for index, item in enumerate(rows, 1):
        print('  %d. %s' % (index, describe(item)), file=sys.stderr)
    print('编号: ', end='', file=sys.stderr, flush=True)
    answer = sys.stdin.readline().strip()
    try:
        index = int(answer) - 1
        return rows[index]['name'] if 0 <= index < len(rows) else ''
    except ValueError:
        return ''


def resolve_interface(interface='auto', robot_ip=DEFAULT_ROBOT_IP, interactive=False):
    """Convenience API for direct app/DDS entrypoints; interactive selection is opt-in."""
    return choose_interface(collect_snapshot(), interface, robot_ip,
                            interactive=interactive, input_func=prompt_choice)


def doctor(snapshot, interface, robot_ip, image, network_error=None):
    print('只读诊断：不安装软件、不修改 IP/路由、不启动容器。')
    print('Python: %s' % sys.version.split()[0])
    print('目标 IPv4: %s（仅用于网卡子网判断，并非保证的机器人地址）' % robot_ip)
    for item in interfaces(snapshot) if snapshot is not None else []:
        print('  ' + describe(item))
    healthy = True
    try:
        if snapshot is None:
            raise NetworkError(network_error or '网络快照不可用')
        print('选择网卡: ' + choose_interface(snapshot, interface, robot_ip))
    except NetworkError as exc:
        print('网卡检查: ' + str(exc))
        healthy = False
    if shutil.which('docker') is None:
        print('Docker: 未找到 docker 命令；真机模式需要 Linux Docker Engine。')
        return False
    result = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], capture_output=True, text=True)
    if result.returncode:
        print('Docker: 无法访问 daemon，请检查服务和当前用户权限。')
        healthy = False
    else:
        print('Docker daemon: ' + result.stdout.strip())
        image_result = subprocess.run(['docker', 'image', 'inspect', image], capture_output=True, text=True)
        print('镜像 %s: %s' % (image, '已存在' if image_result.returncode == 0 else '未构建；启动时自动构建'))
    print('路由（只作诊断，不按默认路由选择网卡）:')
    for route in snapshot['routes'] if snapshot is not None else []:
        print('  ' + json.dumps(route, ensure_ascii=False, sort_keys=True))
    print('链路/子网检查不证明 DDS、机器人在线或传感器数据已就绪。')
    return healthy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interfaces', action='store_true')
    parser.add_argument('--doctor', action='store_true')
    parser.add_argument('--json', action='store_true', help='以 JSON 输出网卡列表')
    parser.add_argument('--interface', default='auto')
    parser.add_argument('--robot-ip', default=DEFAULT_ROBOT_IP)
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--image', default='g1-state-viewer:local')
    args = parser.parse_args()
    try:
        if args.doctor:
            try:
                snapshot = collect_snapshot()
                network_error = None
            except NetworkError as exc:
                snapshot, network_error = None, str(exc)
            return 0 if doctor(snapshot, args.interface, args.robot_ip, args.image, network_error) else 1
        snapshot = collect_snapshot()
        if args.interfaces:
            rows = interfaces(snapshot)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for item in rows:
                    print(describe(item))
            return 0
        selected = choose_interface(snapshot, args.interface, args.robot_ip,
                                    interactive=sys.stdin.isatty() and not args.non_interactive,
                                    input_func=prompt_choice)
        print(selected)
        return 0
    except NetworkError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
