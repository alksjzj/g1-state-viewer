"""Bounded, passive camera-frame inspection without a video service client.

JPEG and PNG frames can be served unchanged to the browser. Compressed video
packets are reported honestly: a packet is not a browser-displayable still.
No DDS entities, network requests, filesystem writes or frame retention here.
"""
import struct

MAX_FRAME_BYTES = 8 * 1024 * 1024
_FIELDS = ('data', 'video720p', 'video360p', 'video180p')
_VIDEO_TYPE = None


def get_video_type():
    """Current G1 frontvideostream schema, verified from its DDS TypeObject."""
    global _VIDEO_TYPE
    if _VIDEO_TYPE is None:
        from dataclasses import dataclass
        from cyclonedds.idl import IdlStruct
        from cyclonedds.idl.annotations import final, autoid
        from cyclonedds.idl.types import uint64, uint16, uint8, sequence

        @dataclass
        @final
        @autoid('sequential')
        class FrontVideoData(IdlStruct, typename='unitree_go::msg::dds_::Go2FrontVideoData_'):
            time_frame: uint64
            resolution: uint16
            data: sequence[uint8]
        _VIDEO_TYPE = FrontVideoData
    return _VIDEO_TYPE
_SOF = {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}


def _jpeg_size(payload):
    """Read dimensions from bounded JPEG markers; require an end marker."""
    if not payload.startswith(b'\xff\xd8') or not payload.endswith(b'\xff\xd9'):
        raise ValueError('JPEG 帧不完整')
    position = 2
    for _ in range(1024):
        if position >= len(payload) or payload[position] != 0xff:
            break
        while position < len(payload) and payload[position] == 0xff:
            position += 1
        if position >= len(payload):
            break
        marker = payload[position]
        position += 1
        if marker in (0xd8, 0xd9, 0x01) or 0xd0 <= marker <= 0xd7:
            continue
        if position + 2 > len(payload):
            break
        length = struct.unpack_from('>H', payload, position)[0]
        if length < 2 or position + length > len(payload):
            break
        if marker in _SOF:
            if length < 8:
                break
            height, width = struct.unpack_from('>HH', payload, position + 3)
            if not 0 < width <= 8192 or not 0 < height <= 8192:
                raise ValueError('JPEG 尺寸超出范围')
            return width, height
        if marker == 0xda:
            break
        position += length
    raise ValueError('JPEG 缺少有效尺寸信息')


def _image(payload):
    if payload.startswith(b'\xff\xd8'):
        width, height = _jpeg_size(payload)
        return 'JPEG', 'image/jpeg', width, height
    if payload.startswith(b'\x89PNG\r\n\x1a\n'):
        if len(payload) < 45 or payload[12:16] != b'IHDR' or payload[-12:] != b'\x00\x00\x00\x00IEND\xaeB`\x82':
            raise ValueError('PNG 帧不完整')
        width, height = struct.unpack_from('>II', payload, 16)
        if not 0 < width <= 8192 or not 0 < height <= 8192:
            raise ValueError('PNG 尺寸超出范围')
        return 'PNG', 'image/png', width, height
    return None


def decode_video(msg):
    """Return (JSON metadata, still-image bytes or None, MIME type or None).

    Prefer the highest nonempty resolution. If it contains unsupported video
    packets, try any smaller field carrying a directly usable image instead.
    The caller owns freshness, sequence numbers and the image cache.
    """
    timestamp = getattr(msg, 'time_frame', None)
    metadata = {'time_frame': timestamp, 'format': 'empty', 'bytes': 0,
                'image_available': False, 'note': '视频消息没有图像载荷'}
    first = None
    for field in _FIELDS:
        data = getattr(msg, field, ())
        size = len(data)
        if not size:
            continue
        if size > MAX_FRAME_BYTES:
            raise ValueError('视频帧超过 8 MiB 限制')
        try:
            payload = bytes(data)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError('视频载荷不是字节序列') from exc
        info = {'time_frame': timestamp, 'resolution': getattr(msg, 'resolution', None), 'source': field, 'bytes': size,
                'image_available': False, 'format': 'unknown',
                'note': '已收到视频载荷，编码暂不支持直接显示'}
        if payload.startswith((b'\x00\x00\x00\x01', b'\x00\x00\x01')):
            info.update(format='Annex B', note='已收到压缩视频包，尚未获得可显示图像')
        try:
            image = _image(payload)
        except ValueError as exc:
            info.update(format='invalid image', note=str(exc))
            image = None
        if image:
            format_name, mime, width, height = image
            info.update(format=format_name, width=width, height=height,
                        image_available=True, note='前置相机实时图像')
            return info, payload, mime
        if first is None:
            first = info
    return first or metadata, None, None


class VideoDecoder:
    """Persistent bounded H.264 decoder using the container's existing FFmpeg ABI.

    Feed only one resolution into a codec context. decode() returns None for
    other resolutions, else the same tuple as decode_video(). No camera pixels
    are written to files. close() releases all native allocations.
    """
    def __init__(self, resolution=360, jpeg_quality=80):
        self.resolution = resolution
        self.jpeg_quality = jpeg_quality
        self._context = self._packet = self._frame = self._scale = None
        self._codec = self._util = self._swscale = None
        self._output_seq = 0
        self._started = False

    def _initialize(self):
        import ctypes as c
        import ctypes.util
        import cv2
        import numpy
        self._cv2, self._numpy, self._c = cv2, numpy, c
        class Packet(c.Structure):
            _fields_ = [('buf', c.c_void_p), ('pts', c.c_int64), ('dts', c.c_int64),
                        ('data', c.POINTER(c.c_uint8)), ('size', c.c_int)]
        class Frame(c.Structure):
            _fields_ = [('data', c.POINTER(c.c_uint8) * 8), ('linesize', c.c_int * 8),
                        ('extended_data', c.c_void_p), ('width', c.c_int), ('height', c.c_int),
                        ('nb_samples', c.c_int), ('format', c.c_int)]
        self._Packet, self._Frame = Packet, Frame
        self._codec = codec = c.CDLL(ctypes.util.find_library('avcodec'))
        self._util = util = c.CDLL(ctypes.util.find_library('avutil'))
        self._swscale = scale = c.CDLL(ctypes.util.find_library('swscale'))
        def bind(lib, name, result, args):
            function = getattr(lib, name)
            function.restype, function.argtypes = result, args
            return function
        bind(codec, 'avcodec_version', c.c_uint, [])
        bind(util, 'avutil_version', c.c_uint, [])
        bind(scale, 'swscale_version', c.c_uint, [])
        versions = (codec.avcodec_version() >> 16, util.avutil_version() >> 16,
                    scale.swscale_version() >> 16)
        if versions != (58, 56, 5):
            raise RuntimeError('当前视频解码器需要 FFmpeg ABI 58/56/5，实际 %s' % (versions,))
        bind(codec, 'avcodec_find_decoder', c.c_void_p, [c.c_int])
        bind(codec, 'avcodec_alloc_context3', c.c_void_p, [c.c_void_p])
        bind(codec, 'avcodec_open2', c.c_int, [c.c_void_p,c.c_void_p,c.c_void_p])
        bind(codec, 'avcodec_free_context', None, [c.POINTER(c.c_void_p)])
        bind(codec, 'av_packet_alloc', c.c_void_p, [])
        bind(codec, 'av_new_packet', c.c_int, [c.c_void_p,c.c_int])
        bind(codec, 'av_packet_unref', None, [c.c_void_p])
        bind(codec, 'av_packet_free', None, [c.POINTER(c.c_void_p)])
        bind(codec, 'avcodec_send_packet', c.c_int, [c.c_void_p,c.c_void_p])
        bind(codec, 'avcodec_receive_frame', c.c_int, [c.c_void_p,c.c_void_p])
        bind(util, 'av_frame_alloc', c.c_void_p, [])
        bind(util, 'av_frame_unref', None, [c.c_void_p])
        bind(util, 'av_frame_free', None, [c.POINTER(c.c_void_p)])
        bind(scale, 'sws_getCachedContext', c.c_void_p,
             [c.c_void_p,c.c_int,c.c_int,c.c_int,c.c_int,c.c_int,c.c_int,c.c_int,c.c_void_p,c.c_void_p,c.c_void_p])
        bind(scale, 'sws_scale', c.c_int,
             [c.c_void_p,c.POINTER(c.POINTER(c.c_uint8)),c.POINTER(c.c_int),c.c_int,c.c_int,
              c.POINTER(c.POINTER(c.c_uint8)),c.POINTER(c.c_int)])
        bind(scale, 'sws_freeContext', None, [c.c_void_p])
        decoder = codec.avcodec_find_decoder(27)  # AV_CODEC_ID_H264
        if not decoder:
            raise RuntimeError('FFmpeg 没有 H.264 解码器')
        self._context = codec.avcodec_alloc_context3(decoder)
        if not self._context or codec.avcodec_open2(self._context, decoder, None) < 0:
            self.close()
            raise RuntimeError('H.264 解码器初始化失败')
        self._packet, self._frame = codec.av_packet_alloc(), util.av_frame_alloc()
        if not self._packet or not self._frame:
            self.close()
            raise RuntimeError('视频解码内存分配失败')

    def decode(self, msg):
        if hasattr(msg, 'resolution') and msg.resolution != self.resolution:
            return None
        metadata, image, mime = decode_video(msg)
        if image is not None or metadata['format'] != 'Annex B':
            return metadata, image, mime
        payload = bytes(getattr(msg, metadata['source']))
        metadata.update(format='H.264', note='已收到视频，等待关键帧', seq=self._output_seq)
        if not self._started:
            import re
            # Joining midstream is normal. Skip slices until stream headers
            # arrive rather than feeding undecodable references into FFmpeg.
            for marker in re.finditer(b'\x00\x00\x01', payload):
                if marker.end() < len(payload) and payload[marker.end()] & 31 == 7:
                    self._started = True
                    break
            if not self._started:
                return metadata, None, None
        if self._context is None:
            self._initialize()
        c, codec = self._c, self._codec
        if codec.av_new_packet(self._packet, len(payload)) < 0:
            raise RuntimeError('视频包内存分配失败')
        packet = c.cast(self._packet, c.POINTER(self._Packet)).contents
        c.memmove(packet.data, payload, len(payload))
        try:
            status = codec.avcodec_send_packet(self._context, self._packet)
        finally:
            codec.av_packet_unref(self._packet)
        metadata.update(format='H.264', note='已收到视频，等待关键帧', seq=self._output_seq)
        if status < 0:
            return metadata, None, None
        output = None
        # One packet normally yields one frame; cap draining malformed streams.
        for _ in range(8):
            if codec.avcodec_receive_frame(self._context, self._frame) < 0:
                break
            try:
                frame = c.cast(self._frame, c.POINTER(self._Frame)).contents
                width, height = frame.width, frame.height
                if not 0 < width <= 4096 or not 0 < height <= 2160:
                    raise ValueError('解码视频尺寸超出范围')
                self._scale = self._swscale.sws_getCachedContext(self._scale,
                    width, height, frame.format, width, height, 3, 2, None, None, None)
                if not self._scale:
                    raise RuntimeError('视频颜色转换初始化失败')
                pixels = (c.c_uint8 * (width * height * 3))()
                planes = (c.POINTER(c.c_uint8) * 4)(c.cast(pixels,c.POINTER(c.c_uint8)))
                strides = (c.c_int * 4)(width * 3,0,0,0)
                if self._swscale.sws_scale(self._scale, frame.data, frame.linesize,
                                          0, height, planes, strides) != height:
                    raise RuntimeError('视频颜色转换失败')
                array = self._numpy.ctypeslib.as_array(pixels).reshape(height,width,3)
                ok, encoded = self._cv2.imencode('.jpg', array,
                    [self._cv2.IMWRITE_JPEG_QUALITY,self.jpeg_quality])
                if not ok:
                    raise RuntimeError('视频图像编码失败')
                output = encoded.tobytes()
                self._output_seq += 1
                metadata.update(width=width,height=height,image_available=True,
                    seq=self._output_seq,note='前置相机实时图像')
            finally:
                self._util.av_frame_unref(self._frame)
        return metadata, output, 'image/jpeg' if output else None

    def close(self):
        if self._codec is None:
            return
        c = self._c
        if self._scale:
            self._swscale.sws_freeContext(self._scale)
            self._scale = None
        for name, library, function in (('_context',self._codec,'avcodec_free_context'),
                                       ('_packet',self._codec,'av_packet_free'),
                                       ('_frame',self._util,'av_frame_free')):
            pointer = getattr(self,name)
            if pointer:
                getattr(library,function)(c.byref(c.c_void_p(pointer)))
                setattr(self,name,None)
