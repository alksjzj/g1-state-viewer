import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace as NS
import unittest


spec = importlib.util.spec_from_file_location("viewer_lidar", Path(__file__).parents[1] / "lidar.py")
lidar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lidar)


def cloud(points, endian="<", fmt="fff", width=None, padding=0):
    width = len(points) if width is None else width
    height = len(points) // width if width else 0
    sizes = {"f": 4, "d": 8}
    offsets = [0, sizes[fmt[0]], sizes[fmt[0]] + sizes[fmt[1]]]
    point_step = struct.calcsize(endian + fmt)
    fields = [NS(name=name, offset=offset, datatype=7 if code == "f" else 8, count=1)
              for name, offset, code in zip("xyz", offsets, fmt)]
    data = b"".join(b"".join(struct.pack(endian + fmt, *point)
                            for point in points[start:start + width]) + b"\xff" * padding
                    for start in range(0, len(points), width or 1))
    return NS(width=width, height=height, point_step=point_step,
              row_step=width * point_step + padding, data=data, fields=fields,
              is_bigendian=endian == ">", is_dense=True,
              header=NS(frame_id="mid360_link", stamp=NS(sec=12, nanosec=345)))


class DecodeCloudTests(unittest.TestCase):
    def test_complete_frame_survives_tiny_same_stamp_followup(self):
        full = cloud([(1., 2., 3.)] * 10)
        tiny = cloud([(0., 0., 0.)])
        selected, token = lidar.select_cloud([full, tiny])
        self.assertIs(selected, full)
        self.assertIsNone(lidar.select_cloud([tiny], token)[0])
        tiny.header.stamp.sec += 1
        self.assertIs(lidar.select_cloud([tiny], token)[0], tiny)
        full.header.stamp.sec = 0  # Robot timestamp reset must still recover.
        self.assertIs(lidar.select_cloud([full], token)[0], full)

    def test_little_endian_and_metadata(self):
        result = lidar.decode_cloud(cloud([(1., 2., 3.)]))
        self.assertEqual(result, {"frame_id": "mid360_link", "stamp": {"sec": 12, "nanosec": 345},
                                  "points": [[1., 2., 3.]], "input_points": 1, "shown_points": 1})

    def test_big_endian_mixed_float_types_and_row_padding(self):
        points = [(1.25, 2.5, 3.75), (4., 5., 6.), (7., 8., 9.), (10., 11., 12.)]
        self.assertEqual(lidar.decode_cloud(cloud(points, ">", "dfd", width=2, padding=13))["points"],
                         [list(point) for point in points])

    def test_reordered_fields_and_byte_list(self):
        msg = cloud([(1., 2., 3.)])
        msg.fields.reverse()
        msg.data = list(msg.data)
        self.assertEqual(lidar.decode_cloud(msg)["points"], [[1., 2., 3.]])

    def test_nan_inf_and_range_filtered_even_if_dense(self):
        msg = cloud([(float("nan"), 0., 0.), (0., float("inf"), 0.), (3., 4., 0.), (5., 1., 0.)])
        self.assertEqual(lidar.decode_cloud(msg, max_range=5.)["points"], [[3., 4., 0.]])

    def test_sample_limit(self):
        result = lidar.decode_cloud(cloud([(float(x), 0., 0.) for x in range(20)]), 3, 100.)
        self.assertEqual(result["input_points"], 20)
        self.assertEqual(result["points"], [[7., 0., 0.], [14., 0., 0.]])

    def test_zero_range_returns_are_not_visible(self):
        result = lidar.decode_cloud(cloud([(0., 0., 0.), (-0., 0., 0.), (0., 0., 0.1)]))
        self.assertEqual(result['input_points'], 3)
        self.assertEqual(result['shown_points'], 1)
        self.assertAlmostEqual(result['points'][0][2], 0.1)

    def test_empty_cloud(self):
        self.assertEqual(lidar.decode_cloud(cloud([]))["shown_points"], 0)

    def test_truncated_data(self):
        msg = cloud([(1., 2., 3.)])
        msg.data = msg.data[:-1]
        with self.assertRaises(ValueError):
            lidar.decode_cloud(msg)

    def test_integer_is_not_a_payload(self):
        msg = cloud([(1., 2., 3.)])
        msg.data = 12
        with self.assertRaises(ValueError):
            lidar.decode_cloud(msg)

    def test_invalid_row_and_point_stride(self):
        for field, value in [("row_step", 1), ("point_step", 0), ("height", -1), ("width", 2 ** 32)]:
            with self.subTest(field=field):
                msg = cloud([(1., 2., 3.)])
                setattr(msg, field, value)
                with self.assertRaises(ValueError):
                    lidar.decode_cloud(msg)

    def test_invalid_fields(self):
        for field, value in [("offset", 100), ("datatype", 6), ("count", 2)]:
            with self.subTest(field=field):
                msg = cloud([(1., 2., 3.)])
                setattr(msg.fields[0], field, value)
                with self.assertRaises(ValueError):
                    lidar.decode_cloud(msg)
        for fields in [[], [NS(name="x", offset=0, datatype=7, count=1)] * 3]:
            msg = cloud([(1., 2., 3.)])
            msg.fields = fields
            with self.assertRaises(ValueError):
                lidar.decode_cloud(msg)

    def test_invalid_limits_and_stamp(self):
        msg = cloud([(1., 2., 3.)])
        for limits in [{"max_points": 0}, {"max_points": 1.5}, {"max_range": float("inf")}, {"max_range": -1}]:
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                lidar.decode_cloud(msg, **limits)
        msg.header.stamp.nanosec = 1000000000
        with self.assertRaises(ValueError):
            lidar.decode_cloud(msg)

    def test_noncontiguous_xyz_offsets(self):
        msg = cloud([(1., 2., 3.)])
        msg.point_step = msg.row_step = 20
        msg.fields[0].offset, msg.fields[1].offset, msg.fields[2].offset = 4, 12, 16
        msg.data = struct.pack("<IfIff", 999, 1., 888, 2., 3.)
        self.assertEqual(lidar.decode_cloud(msg)["points"], [[1., 2., 3.]])


if __name__ == "__main__":
    unittest.main()
