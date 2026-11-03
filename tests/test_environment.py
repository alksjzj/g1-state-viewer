"""Opt-in checks of the container's real native dependencies (no robot required)."""
import ctypes
import ctypes.util
import importlib.metadata
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@unittest.skipUnless(os.environ.get('G1_CHECK_ENVIRONMENT') == '1',
                     'set G1_CHECK_ENVIRONMENT=1 inside the built Docker image')
class EnvironmentTests(unittest.TestCase):
    def test_sdk_and_dds_imports(self):
        from cyclonedds.domain import DomainParticipant
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import (
            LowState_, HandState_, BmsState_, MainBoardState_)
        from telemetry import get_motion_type
        from video import get_video_type
        from lidar import get_pointcloud_type
        self.assertEqual(importlib.metadata.version('cyclonedds'), '0.10.2')
        self.assertEqual(importlib.metadata.version('unitree_sdk2py'), '1.0.1')
        self.assertTrue(all((DomainParticipant, ChannelFactoryInitialize, LowState_,
                             HandState_, BmsState_, MainBoardState_,
                             get_motion_type(), get_video_type(), get_pointcloud_type())))

    def test_image_roundtrip_and_decoder_initialization(self):
        import cv2
        import numpy as np
        from video import VideoDecoder
        self.assertEqual(np.__version__, '1.26.4')
        self.assertEqual(importlib.metadata.version('opencv-python-headless'), '4.10.0.84')
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        ok, encoded = cv2.imencode('.jpg', image)
        self.assertTrue(ok)
        self.assertEqual(cv2.imdecode(encoded, cv2.IMREAD_COLOR).shape, image.shape)
        for name, expected in [('avcodec', 58), ('avutil', 56), ('swscale', 5)]:
            library = ctypes.CDLL(ctypes.util.find_library(name))
            version = getattr(library, name + '_version')
            version.restype = ctypes.c_uint
            version.argtypes = []
            self.assertEqual(version() >> 16, expected)
        decoder = VideoDecoder()
        try:
            decoder._initialize()
            self.assertTrue(decoder._context)
        finally:
            decoder.close()


if __name__ == '__main__':
    unittest.main()
