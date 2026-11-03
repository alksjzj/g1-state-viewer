import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from state import Cache, convert_battery, convert_mainboard, convert_motion


def battery(soc=47):
    return NS(soc=soc, soh=96, cycle=9, bmsvoltage=[48305, 48308, 0],
              current=-2396, temperature=[36, 32, 29, 36] + [0] * 8,
              cell_vol=[3718] * 13 + [0] * 27, bmsstate=[8, 8, 262144, 7, 0])


class HealthTests(unittest.TestCase):
    def test_soc_range_and_unscaled_raw_fields(self):
        for soc in (0, 47, 100):
            value = convert_battery(battery(soc))
            self.assertEqual(value['soc_pct'], soc)
            self.assertEqual(value['voltage_raw'], [48305, 48308, 0])
            self.assertEqual(value['current_raw'], -2396)
            self.assertEqual(len(value['temperature_raw']), 12)
        for invalid in (-1, 101, 255):
            with self.assertRaises(ValueError):
                convert_battery(battery(invalid))

    def test_motion_unknown_id_and_invalid_sample(self):
        value = convert_motion(NS(fsm_id=999, fsm_mode=7, task_id=3, task_time=2.5))
        self.assertEqual(value['mode_label'], '未映射模式')
        self.assertEqual(value['fsm_id'], 999)
        with self.assertRaises(ValueError):
            convert_motion(NS(fsm_id=1, fsm_mode=0, task_id=0, task_time=float('nan')))

    def test_mainboard_preserves_unidentified_slots(self):
        value = convert_mainboard(NS(temperature=[52, 0, 0, 0, 0, 0],
                     fan_state=[0] * 6, value=[48.1, 48.2, 1.95, 0, 0, 0], state=[32] + [0] * 5))
        self.assertEqual(value['temperature_raw'], [52, 0, 0, 0, 0, 0])
        self.assertEqual(value['state_raw'][0], 32)

    def test_independent_freshness_and_no_stale_video_image(self):
        with patch('state.time.monotonic', return_value=100) as clock:
            cache = Cache('live', 'lo', 197, 'test')
            cache.put('battery', convert_battery(battery()))
            cache.put_video({'format': 'jpeg'}, b'test-only', 'image/jpeg')
            self.assertEqual(cache.get_video_image(), (b'test-only', 'image/jpeg'))
            self.assertEqual(cache.snapshot()['channels']['motion']['status'], 'waiting')
            json.dumps(cache.snapshot(), allow_nan=False)  # bytes must never leak into JSON
            clock.return_value = 103
            cache.put('motion', convert_motion(NS(fsm_id=1, fsm_mode=0, task_id=0, task_time=0)))
            state = cache.snapshot()
            self.assertEqual(state['channels']['battery']['status'], 'stale')
            self.assertEqual(state['channels']['motion']['status'], 'live')
            self.assertIsNone(cache.get_video_image())
            cache.put_video({'format': 'unsupported'}, None, None)
            self.assertFalse(cache.snapshot()['video']['image_available'])
            self.assertIsNone(cache.get_video_image())


if __name__ == '__main__':
    unittest.main()
