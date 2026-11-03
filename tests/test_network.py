"""Conservative host-interface selection and launcher contracts; no Docker/robot needed."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('viewer_network', ROOT / 'network.py')
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


def snapshot(*rows, routes=None):
    """Rows: name, addresses, physical, up, carrier."""
    return {
        'links': [{'ifname': name, 'flags': ['UP'] if up else []} for name, _, _, up, _ in rows],
        'addresses': [{'ifname': name, 'addr_info': [
            {'family': 'inet', 'local': addr.split('/')[0], 'prefixlen': int(addr.split('/')[1])}
            for addr in addresses]} for name, addresses, _, _, _ in rows],
        'devices': {name: {'physical': physical, 'carrier': carrier}
                    for name, _, physical, _, carrier in rows},
        'routes': routes or []}


def nic(name='enp4s0', addresses=None, physical=True, up=True, carrier=True):
    return name, ['192.168.123.10/24'] if addresses is None else addresses, physical, up, carrier


class NetworkSelectionTest(unittest.TestCase):
    def test_unique_physical_subnet_not_default_wifi(self):
        state = snapshot(nic(), nic('wlan0', ['10.0.0.10/24']),
                         routes=[{'dst': 'default', 'dev': 'wlan0'}])
        self.assertEqual(network.choose_interface(state), 'enp4s0')

    def test_virtual_vpn_docker_and_loopback_excluded(self):
        state = snapshot(nic('vpn0', physical=False), nic('docker0', physical=False),
                         nic('lo', ['127.0.0.1/8'], physical=False), nic())
        self.assertEqual(network.choose_interface(state), 'enp4s0')
        with self.assertRaises(network.NetworkError):
            network.choose_interface(snapshot(nic('vpn0', physical=False)))

    def test_no_ipv4_fails_without_modification(self):
        with self.assertRaisesRegex(network.NetworkError, 'IPv4=\\(none\\)'):
            network.choose_interface(snapshot(nic(addresses=[])))

    def test_link_and_admin_state_required(self):
        for row in (nic(up=False), nic(carrier=False)):
            with self.subTest(row=row), self.assertRaises(network.NetworkError):
                network.choose_interface(snapshot(row))

    def test_wrong_subnet_fails_even_with_default_route(self):
        with self.assertRaisesRegex(network.NetworkError, '不会修改 IP/路由'):
            network.choose_interface(snapshot(nic(addresses=['10.0.0.2/24']),
                                     routes=[{'dst': 'default', 'dev': 'enp4s0'}]))

    def test_multiple_ipv4_subnets_on_one_interface(self):
        state = snapshot(nic(addresses=['10.0.0.2/24', '192.168.123.10/24']))
        self.assertEqual(network.choose_interface(state), 'enp4s0')

    def test_overlapping_addresses_do_not_duplicate_same_interface(self):
        state = snapshot(nic(addresses=['192.168.123.10/24', '192.168.0.10/16']))
        self.assertEqual(network.choose_interface(state), 'enp4s0')

    def test_overlapping_networks_across_interfaces_are_ambiguous(self):
        state = snapshot(nic('eno1'), nic('enxusb', ['192.168.0.2/16']))
        with self.assertRaisesRegex(network.NetworkError, './run.sh --interface eno1') as error:
            network.choose_interface(state)
        self.assertIn('./run.sh --interface enxusb', str(error.exception))

    def test_multiple_candidates_interactively_chosen(self):
        state = snapshot(nic('eno1'), nic('enxusb'))
        self.assertEqual(network.choose_interface(state, interactive=True,
                                                  input_func=lambda rows: rows[1]['name']), 'enxusb')
        with self.assertRaisesRegex(network.NetworkError, '选择无效'):
            network.choose_interface(state, interactive=True, input_func=lambda _: 'wlan0')

    def test_background_or_non_tty_does_not_prompt(self):
        state = snapshot(nic('eno1'), nic('enxusb'))
        def forbidden_prompt(_):
            self.fail('non-interactive selection must never prompt')
        with self.assertRaisesRegex(network.NetworkError, '后台或非交互'):
            network.choose_interface(state, interactive=False, input_func=forbidden_prompt)

    def test_explicit_allows_virtual_loopback_and_other_subnet(self):
        state = snapshot(nic('lo', ['127.0.0.1/8'], physical=False),
                         nic('wlan0', ['10.0.0.2/24']))
        self.assertEqual(network.choose_interface(state, 'lo'), 'lo')
        self.assertEqual(network.choose_interface(state, 'wlan0'), 'wlan0')

    def test_explicit_missing_down_or_no_address_rejected(self):
        for state, name in ((snapshot(nic()), 'missing'),
                            (snapshot(nic(up=False)), 'enp4s0'),
                            (snapshot(nic(addresses=[])), 'enp4s0'),
                            (snapshot(nic(carrier=False)), 'enp4s0')):
            with self.subTest(name=name, state=state), self.assertRaises(network.NetworkError):
                network.choose_interface(state, name)

    def test_custom_target_and_invalid_target(self):
        state = snapshot(nic(addresses=['10.20.30.2/24']))
        self.assertEqual(network.choose_interface(state, robot_ip='10.20.30.9'), 'enp4s0')
        for address in ('::1', 'bad', '192.168.1.999'):
            with self.subTest(address=address), self.assertRaises(network.NetworkError):
                network.choose_interface(state, robot_ip=address)

    def test_collect_reads_only_ip_json_and_sysfs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'eno1' / 'device').mkdir(parents=True)
            (root / 'eno1' / 'carrier').write_text('1\n')
            outputs = [[{'ifname': 'eno1', 'flags': ['UP']}], [], []]
            with patch.object(network, '_ip_json', side_effect=outputs) as command:
                result = network.collect_snapshot(root)
            self.assertTrue(result['devices']['eno1']['physical'])
            self.assertTrue(result['devices']['eno1']['carrier'])
            self.assertEqual(command.call_args_list[0].args, ('link', 'show'))
            self.assertEqual(command.call_args_list[1].args, ('address', 'show'))
            self.assertEqual(command.call_args_list[2].args, ('route', 'show', 'table', 'all'))

    def test_missing_ip_is_actionable(self):
        with patch.object(network.subprocess, 'run', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(network.NetworkError, 'iproute2'):
                network.collect_snapshot()

    def test_resolver_direct_entrypoint_api(self):
        with patch.object(network, 'collect_snapshot', return_value=snapshot(nic())):
            self.assertEqual(network.resolve_interface(), 'enp4s0')

    def test_doctor_keeps_checking_dependencies_when_network_read_fails(self):
        with patch.object(network, 'collect_snapshot', side_effect=network.NetworkError('netlink denied')), \
                patch.object(network.shutil, 'which', return_value=None), \
                patch.object(network.sys, 'argv', ['network.py', '--doctor']), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(network.main(), 1)
            self.assertIn('netlink denied', output.getvalue())
            self.assertIn('未找到 docker 命令', output.getvalue())

    def test_cli_non_tty_and_json(self):
        state = snapshot(nic('eno1'), nic('enxusb'))
        with patch.object(network, 'collect_snapshot', return_value=state), \
                patch.object(network.sys, 'argv', ['network.py']), \
                patch.object(network.sys.stdin, 'isatty', return_value=False), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(network.main(), 1)
            self.assertIn('--interface enxusb', errors.getvalue())
        with patch.object(network, 'collect_snapshot', return_value=state), \
                patch.object(network.sys, 'argv', ['network.py', '--interfaces', '--json']), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(network.main(), 0)
            self.assertEqual(len(json.loads(output.getvalue())), 2)


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.log = self.root / 'commands'
        self.environment = dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'],
                                VIEWER_TEST_LOG=str(self.log))
        self.fake('python3', '''#!/bin/sh
printf 'python3' >> "$VIEWER_TEST_LOG"
printf ' <%s>' "$@" >> "$VIEWER_TEST_LOG"
printf '\\n' >> "$VIEWER_TEST_LOG"
case "$*" in
  *network.py*) printf '%s\\n' enxtest ;;
esac
''')
        self.fake('docker', '''#!/bin/sh
printf 'docker' >> "$VIEWER_TEST_LOG"
printf ' <%s>' "$@" >> "$VIEWER_TEST_LOG"
printf '\\n' >> "$VIEWER_TEST_LOG"
case "$1" in
  info) exit 0 ;;
  image) exit "${VIEWER_TEST_IMAGE_MISSING:-0}" ;;
  inspect)
    case "$*" in
      *--format*) printf '%s\\n' "${VIEWER_TEST_OWNER:-}"; exit 0 ;;
      *) exit 1 ;;
    esac ;;
  *) exit 0 ;;
esac
''')

    def tearDown(self):
        self.temporary.cleanup()

    def fake(self, name, text):
        path = self.root / name
        path.write_text(text)
        path.chmod(0o755)

    def launch(self, *args):
        return subprocess.run(['bash', str(ROOT / 'run.sh'), *args], env=self.environment,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def commands(self):
        return self.log.read_text() if self.log.exists() else ''

    def test_help_requires_no_python_or_docker(self):
        self.assertEqual(self.launch('--help').returncode, 0)
        self.assertEqual(self.commands(), '')

    def test_demo_uses_host_python_only(self):
        self.assertEqual(self.launch('--demo', '--port', '8766').returncode, 0)
        self.assertIn('<--demo> <--port> <8766>', self.commands())
        self.assertNotIn('docker', self.commands())

    def test_interfaces_and_doctor_do_not_launch_or_build(self):
        for option in ('--interfaces', '--doctor'):
            with self.subTest(option=option):
                self.assertEqual(self.launch(option).returncode, 0)
        self.assertNotIn('docker', self.commands())

    def test_live_passes_selected_host_interface_and_reliable_default(self):
        result = self.launch('--interface', 'enxtest', '--port', '8766')
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        self.assertIn('<--interface> <enxtest> <--lidar-reliable> <--port> <8766>', commands)
        self.assertIn('<--name> <g1-state-viewer-release>', commands)
        self.assertIn('<--entrypoint> <python3>', commands)
        self.assertIn('</app/app.py>', commands)
        self.assertNotIn('type=bind', commands)

    def test_background_is_non_interactive(self):
        self.assertEqual(self.launch('--background').returncode, 0)
        self.assertIn('<--non-interactive>', self.commands())
        self.assertIn('<--detach>', self.commands())

    def test_best_effort_removes_reliable_option(self):
        self.assertEqual(self.launch('--lidar-best-effort').returncode, 0)
        self.assertNotIn('<--lidar-reliable>', self.commands())

    def test_missing_image_builds_before_run(self):
        self.environment['VIEWER_TEST_IMAGE_MISSING'] = '1'
        self.assertEqual(self.launch().returncode, 0)
        commands = self.commands()
        self.assertLess(commands.index('docker <build>'), commands.index('docker <run>'))

    def test_discovery_runs_diagnose_without_lidar_arguments(self):
        self.assertEqual(self.launch('--discover', '--seconds', '2').returncode, 0)
        commands = self.commands()
        self.assertIn('</app/diagnose.py>', commands)
        self.assertIn('<--seconds> <2>', commands)
        self.assertNotIn('<--lidar-reliable>', commands)

    def test_stop_refuses_container_owned_by_another_directory(self):
        self.environment['VIEWER_TEST_OWNER'] = '/other/project'
        self.assertNotEqual(self.launch('--stop').returncode, 0)
        self.assertNotIn('docker <stop>', self.commands())

    def test_stop_owned_container_only(self):
        self.environment['VIEWER_TEST_OWNER'] = str(ROOT)
        self.assertEqual(self.launch('--stop').returncode, 0)
        self.assertIn('docker <stop> <--time> <5> <g1-state-viewer-release>', self.commands())

    def test_incompatible_modes_and_missing_interface_value(self):
        for args in (('--demo', '--background'), ('--doctor', '--build'),
                     ('--demo', '--discover'), ('--interface',)):
            with self.subTest(args=args):
                self.assertEqual(self.launch(*args).returncode, 2)
        self.assertEqual(self.commands(), '')


if __name__ == '__main__':
    unittest.main()
