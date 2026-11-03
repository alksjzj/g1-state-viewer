"""Read-only PointCloud2 types and bounded, layout-aware XYZ decoding.

The message layout follows the official ROS 2 sensor_msgs definitions:
https://github.com/ros2/common_interfaces/blob/rolling/sensor_msgs/msg/PointCloud2.msg
https://github.com/ros2/common_interfaces/blob/rolling/sensor_msgs/msg/PointField.msg

CycloneDDS is imported only by get_pointcloud_type(), so the decoder can also be
tested or used with recorded messages on a host without a DDS installation.
"""

import math
import operator
import struct


_POINTCLOUD_TYPE = None


def select_cloud(samples, previous=None):
    """Prefer the complete frame when a driver follows it with a tiny same-stamp frame.

    The attached G1 emits both with identical timestamps. Keep arrival order
    across different timestamps (including clock resets), not numerical max time.
    Returns (sample or None, selection token) without inventing new freshness.
    """
    samples = [s for s in samples if hasattr(s, 'header') and hasattr(s, 'width')]
    if not samples:
        return None, previous
    def stamp(s):
        return s.header.frame_id, s.header.stamp.sec, s.header.stamp.nanosec
    latest = stamp(samples[-1])
    sample = max((s for s in samples if stamp(s) == latest), key=lambda s: s.width * s.height)
    size = sample.width * sample.height
    if previous and previous[0] == latest and previous[1] >= size:
        return None, previous
    return sample, (latest, size)


def get_pointcloud_type():
    """Return a CycloneDDS type matching ROS 2's DDS topic wire schema."""
    global _POINTCLOUD_TYPE
    if _POINTCLOUD_TYPE is not None:
        return _POINTCLOUD_TYPE
    from dataclasses import dataclass, field
    from cyclonedds.idl import IdlStruct
    from cyclonedds.idl.annotations import final
    from cyclonedds.idl.types import int32, sequence, uint8, uint32

    @dataclass
    @final
    class Time(IdlStruct, typename="builtin_interfaces::msg::dds_::Time_"):
        sec: int32 = 0
        nanosec: uint32 = 0

    @dataclass
    @final
    class Header(IdlStruct, typename="std_msgs::msg::dds_::Header_"):
        stamp: Time = field(default_factory=Time)
        frame_id: str = ""

    @dataclass
    @final
    class PointField(IdlStruct, typename="sensor_msgs::msg::dds_::PointField_"):
        name: str = ""
        offset: uint32 = 0
        datatype: uint8 = 0
        count: uint32 = 0

    @dataclass
    @final
    class PointCloud2(IdlStruct, typename="sensor_msgs::msg::dds_::PointCloud2_"):
        header: Header = field(default_factory=Header)
        height: uint32 = 0
        width: uint32 = 0
        fields: sequence[PointField] = field(default_factory=list)
        is_bigendian: bool = False
        point_step: uint32 = 0
        row_step: uint32 = 0
        data: sequence[uint8] = field(default_factory=list)
        is_dense: bool = False

    _POINTCLOUD_TYPE = PointCloud2
    return PointCloud2


def _integer(value, name, minimum=0, maximum=0xffffffff):
    try:
        result = operator.index(value)
    except TypeError:
        raise ValueError("%s must be an integer" % name)
    if isinstance(value, bool) or not minimum <= result <= maximum:
        raise ValueError("%s is outside its valid range" % name)
    return result


def decode_cloud(msg, max_points=12000, max_range=50.0):
    """Return bounded XYZ samples in the message's original sensor frame.

    Supports organized clouds, padded rows, noncontiguous fields, both byte
    orders and mixed float32/float64 XYZ. Samples evenly before decoding so
    callback work stays bounded; invalid or out-of-range samples are omitted.
    A malformed layout raises ValueError instead of producing misleading data.
    No frame transformation, motion compensation or map accumulation occurs.
    """
    limit = _integer(max_points, "max_points", 1, 1000000)
    try:
        distance = float(max_range)
    except (ValueError, TypeError):
        raise ValueError("max_range must be finite and positive")
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("max_range must be finite and positive")

    width = _integer(msg.width, "width")
    height = _integer(msg.height, "height")
    point_step = _integer(msg.point_step, "point_step")
    row_step = _integer(msg.row_step, "row_step")
    count = width * height
    # Reject nonsensical layouts before inspecting or copying their payload.
    if count > 100000000 or row_step * height > 256 * 1024 * 1024:
        raise ValueError("cloud exceeds decoder bounds")
    if count and point_step == 0:
        raise ValueError("point_step must be positive for a nonempty cloud")
    if row_step < width * point_step:
        raise ValueError("row_step is smaller than the packed row")
    try:
        data_length = len(msg.data)
    except TypeError:
        raise ValueError("data must contain bytes")
    if data_length > 256 * 1024 * 1024:
        raise ValueError("cloud data exceeds decoder bounds")
    try:
        payload = memoryview(msg.data).cast("B")
    except (TypeError, ValueError):
        try:
            payload = memoryview(bytes(msg.data))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("data must contain bytes")
    if len(payload) < row_step * height:
        raise ValueError("data is shorter than row_step * height")
    sec = _integer(msg.header.stamp.sec, "stamp.sec", -(2 ** 31), 2 ** 31 - 1)
    nanosec = _integer(msg.header.stamp.nanosec, "stamp.nanosec", 0, 999999999)
    if not isinstance(msg.header.frame_id, str):
        raise ValueError("frame_id must be a string")
    result = {
        "frame_id": msg.header.frame_id,
        "stamp": {"sec": sec, "nanosec": nanosec},
        "points": [],
        "input_points": count,
        "shown_points": 0,
    }
    if count == 0:
        return result

    readers = {}
    endian = ">" if msg.is_bigendian else "<"
    for field in msg.fields:
        if field.name not in ("x", "y", "z"):
            continue
        if field.name in readers:
            raise ValueError("duplicate XYZ field: %s" % field.name)
        offset = _integer(field.offset, "field.offset")
        datatype = _integer(field.datatype, "field.datatype", 0, 255)
        if _integer(field.count, "field.count") != 1 or datatype not in (7, 8):
            raise ValueError("XYZ fields must be scalar FLOAT32 or FLOAT64")
        decoder = struct.Struct(endian + ("f" if datatype == 7 else "d"))
        if offset + decoder.size > point_step:
            raise ValueError("XYZ field extends beyond point_step")
        readers[field.name] = (offset, decoder)
    if len(readers) != 3:
        raise ValueError("cloud must contain x, y and z fields")

    points = result["points"]
    step = max(1, (count + limit - 1) // limit)
    xyz_readers = [readers[axis] for axis in ("x", "y", "z")]
    for index in range(0, count, step):
        row, column = divmod(index, width)
        base = row * row_step + column * point_step
        xyz = [decoder.unpack_from(payload, base + offset)[0]
               for offset, decoder in xyz_readers]
        # A zero-range sample cannot describe a visible surface; real MID-360
        # frames contain many such entries. Exclude them from visible points.
        if all(math.isfinite(value) for value in xyz) and 0 < math.hypot(*xyz) <= distance:
            points.append(xyz)
    result["shown_points"] = len(points)
    return result
