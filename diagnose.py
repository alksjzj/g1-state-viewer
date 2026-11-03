"""Bounded DDS publication discovery, subscriptions only, no service calls."""
import argparse
import json
import time
from transport import domain_xml


def main():
    parser = argparse.ArgumentParser(description='只读发现当前网卡上的 DDS 发布话题')
    parser.add_argument('--interface', default='auto')
    parser.add_argument('--robot-ip', default='192.168.123.161')
    parser.add_argument('--domain', type=int, default=0)
    parser.add_argument('--seconds', type=float, default=8)
    parser.add_argument('--peer', action='append', default=[])
    args = parser.parse_args()
    if not 0 <= args.domain <= 232 or not 0 < args.seconds <= 60:
        parser.error('domain 需为 0..232，seconds 需为 (0,60]')
    if args.interface == 'auto':
        from network import NetworkError, resolve_interface
        try:
            args.interface = resolve_interface(args.interface, args.robot_ip)
        except NetworkError as exc:
            parser.error(str(exc))
    from cyclonedds.domain import Domain, DomainParticipant
    from cyclonedds.builtin import BuiltinDataReader, BuiltinTopicDcpsPublication
    from cyclonedds.internal import InvalidSample
    domain = Domain(args.domain, domain_xml(args.interface, args.peer))
    participant = DomainParticipant(args.domain)
    reader = BuiltinDataReader(participant, BuiltinTopicDcpsPublication)
    seen = set()
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        for sample in reader.take(100):
            if not isinstance(sample, InvalidSample):
                key = (sample.topic_name, sample.type_name)
                if key not in seen:
                    seen.add(key)
                    print(json.dumps(dict(topic=key[0], type=key[1]), ensure_ascii=False), flush=True)
        time.sleep(0.1)
    print(json.dumps(dict(discovered_topics=len(seen), interface=args.interface, domain=args.domain)))


if __name__ == '__main__':
    main()
