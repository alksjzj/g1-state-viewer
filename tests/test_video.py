import base64
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video import decode_video, VideoDecoder, MAX_FRAME_BYTES

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS1sAAAAASUVORK5CYII=')


class VideoTests(unittest.TestCase):
    def test_png_current_schema(self):
        meta, image, mime = decode_video(NS(time_frame=1, resolution=360, data=list(PNG)))
        self.assertEqual(image, PNG)
        self.assertEqual(mime, 'image/png')
        self.assertEqual((meta['width'], meta['height']), (1, 1))

    def test_invalid_and_unsupported_payloads_are_not_images(self):
        for data in (b'unknown', PNG[:-4], b'\xff\xd8broken\xff\xd9', b''):
            meta, image, mime = decode_video(NS(time_frame=2, resolution=360, data=data))
            self.assertFalse(meta['image_available'])
            self.assertIsNone(image)
            self.assertIsNone(mime)

    def test_packet_size_limit(self):
        with self.assertRaises(ValueError):
            decode_video(NS(data=b'x' * (MAX_FRAME_BYTES + 1)))

    def test_mixed_resolution_and_midstream_join(self):
        decoder = VideoDecoder()
        self.assertIsNone(decoder.decode(NS(time_frame=1, resolution=720, data=PNG)))
        meta, image, mime = decoder.decode(NS(time_frame=2, resolution=360,
                                             data=b'\x00\x00\x00\x01\x41not-an-idr'))
        self.assertEqual(meta['format'], 'H.264')
        self.assertIsNone(image)
        self.assertIsNone(decoder._context)  # no native decoder until SPS arrives
        decoder.close()
        decoder.close()


if __name__ == '__main__':
    unittest.main()
