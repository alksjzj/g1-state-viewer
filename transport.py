"""Read-only CycloneDDS receiver. This module never constructs a DataWriter."""
import ipaddress
import socket
import threading
import time
from xml.sax.saxutils import quoteattr

from state import convert_state, convert_hand, convert_battery, convert_mainboard, convert_motion


def domain_xml(interface, peers=()):
    if interface not in {name for _, name in socket.if_nameindex()}:
        raise ValueError('网卡不存在: ' + interface)
    addresses = [str(ipaddress.ip_address(peer)) for peer in peers]
    peer_xml = ''.join('<Peer Address=%s/>' % quoteattr(p) for p in addresses)
    return ('<CycloneDDS><Domain Id="any"><General><Interfaces>'
            '<NetworkInterface name=%s multicast="default"/>' % quoteattr(interface)
            + '</Interfaces></General><Discovery><Peers>' + peer_xml
            + '</Peers></Discovery></Domain></CycloneDDS>')


class Receiver:
    def __init__(self, cache, interface, domain, peers, lidar_topic, max_points, max_range,
                 lidar_reliable=False, right_hand_order='middle-first'):
        from cyclonedds.domain import Domain, DomainParticipant
        from cyclonedds.topic import Topic
        from cyclonedds.sub import DataReader
        from cyclonedds.qos import Qos, Policy
        from cyclonedds.util import duration
        from cyclonedds.core import Listener
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_, HandState_, BmsState_, MainBoardState_
        from telemetry import get_motion_type
        from video import get_video_type, VideoDecoder
        from lidar import get_pointcloud_type
        self.cache = cache
        self.stop = threading.Event()
        self.domain = Domain(domain, domain_xml(interface, peers))
        self.participant = DomainParticipant(domain)
        self.readers = {}
        self.topics = []
        self.threads = []
        self.max_points, self.max_range = max_points, max_range
        self.right_hand_order = right_hand_order
        self.video_decoder = VideoDecoder()
        for name, topic_name, dtype in [('state', 'rt/lowstate', LowState_),
                                       ('lidar', lidar_topic, get_pointcloud_type()),
                                       ('hand_left', 'rt/dex3/left/state', HandState_),
                                       ('hand_right', 'rt/dex3/right/state', HandState_),
                                       ('battery', 'rt/lf/bmsstate', BmsState_),
                                       ('mainboard', 'rt/lf/mainboardstate', MainBoardState_),
                                       ('motion', 'rt/sportmodestate', get_motion_type()),
                                       ('video', 'rt/frontvideostream', get_video_type())]:
            reliable = Policy.Reliability.Reliable(max_blocking_time=duration(milliseconds=100))
            qos = Qos(reliable if (name == 'lidar' and lidar_reliable) or name == 'video' else Policy.Reliability.BestEffort,
                      Policy.Durability.Volatile, Policy.History.KeepLast(32 if name == 'video' else 8 if name == 'lidar' else 1))
            topic = Topic(self.participant, topic_name, dtype)
            self.topics.append(topic)
            def matched(reader, status, channel=name):
                with self.cache.lock:
                    self.cache.channels[channel]['matched'] = status.current_count
            self.readers[name] = DataReader(self.participant, topic, qos=qos,
                                            listener=Listener(on_subscription_matched=matched))
        for name in self.readers:
            t = threading.Thread(target=self._run, args=(name,), daemon=True)
            t.start()
            self.threads.append(t)

    def _run(self, name):
        from cyclonedds.internal import InvalidSample
        from lidar import decode_cloud, select_cloud
        reader = self.readers[name]
        previous_cloud = None
        while not self.stop.is_set():
            try:
                samples = reader.take(32 if name == 'video' else 8 if name == 'lidar' else 1)
                if name == 'video':
                    # H.264 depends on preceding packets. Do not discard all but
                    # the newest packet as we do for independent joint samples.
                    for sample in samples:
                        if not isinstance(sample, InvalidSample):
                            result = self.video_decoder.decode(sample)
                            if result is not None:
                                self.cache.put_video(*result)
                    self.stop.wait(0.01)
                    continue
                if name == 'lidar':
                    selected, token = select_cloud(samples, previous_cloud)
                    samples = [] if selected is None else [selected]
                if samples and not isinstance(samples[-1], InvalidSample):
                    if name == 'state':
                        value = convert_state(samples[-1])
                    elif name.startswith('hand_'):
                        value = convert_hand(samples[-1], name[5:], self.right_hand_order)
                    elif name == 'battery':
                        value = convert_battery(samples[-1])
                    elif name == 'mainboard':
                        value = convert_mainboard(samples[-1])
                    elif name == 'motion':
                        value = convert_motion(samples[-1])
                    else:
                        value = decode_cloud(samples[-1], self.max_points, self.max_range)
                        previous_cloud = token
                    self.cache.put(name, value)
            except Exception as exc:
                self.cache.error(name, '%s: %s' % (type(exc).__name__, exc))
                self.stop.wait(0.2)
            self.stop.wait(0.02 if name == 'lidar' else 0.003)

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=2)
        if not any(t.is_alive() for t in self.threads):
            self.video_decoder.close()
