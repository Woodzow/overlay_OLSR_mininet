import argparse
import os
import random
import socket
import struct
import threading
import time

from constants import *
from flooding_mpp import DuplicateSet
from hello_msg_body import create_hello_body, parse_hello_body
from link_sensing import LinkSet
from neigh_manager import NeighborManager
from olsr_control import process_control_command
from pkt_msg_fmt import create_message_header, create_packet_header, decode_mantissa
from routing_manager import RoutingManager
from tc_msg_body import create_tc_body, parse_tc_body
from topology_manager import TopologyManager


class OLSRNode:
    def __init__(self, my_ip, port=5005, control_port=5100):
        self.my_ip = my_ip
        self.port = int(port)
        self.control_port = int(control_port)
        self.running = True
        self.started_at = time.time()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("0.0.0.0", self.port))

        self.control_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.control_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.control_sock.bind(("127.0.0.1", self.control_port))

        self.link_set = LinkSet()
        self.link_set.my_ip = my_ip
        self.neighbor_manager = NeighborManager(my_ip)
        self.topology_manager = TopologyManager(my_ip)
        self.routing_manager = RoutingManager(
            my_ip,
            self.neighbor_manager,
            self.topology_manager,
        )
        self.duplicate_set = DuplicateSet()
        self.lock = threading.Lock()

        self.pkt_seq_num = 0
        self.msg_seq_num = 0
        self.ansn = 0

    def start(self):
        print(
            f"[*] OLSR Node {self.my_ip} started on udp/{self.port} "
            f"control=127.0.0.1:{self.control_port}"
        )
        threading.Thread(target=self.loop_hello, daemon=True).start()
        threading.Thread(target=self.loop_tc, daemon=True).start()
        threading.Thread(target=self.loop_cleanup, daemon=True).start()
        threading.Thread(target=self.loop_control, daemon=True).start()
        self.receive_loop()

    def stop(self):
        self.running = False
        for sock in (self.sock, self.control_sock):
            try:
                sock.close()
            except OSError:
                pass

    def receive_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
            except OSError:
                break
            except Exception as exc:
                print(f"[Error] Receive: {exc}")
                continue

            sender_ip = addr[0]
            if sender_ip == self.my_ip:
                continue
            self.process_packet(data, sender_ip)

    def process_packet(self, data, sender_ip):
        if len(data) < 4:
            return

        pkt_len, _pkt_seq = struct.unpack("!HH", data[:4])
        cursor = 4
        if pkt_len != len(data):
            return

        with self.lock:
            while cursor < len(data):
                if len(data) - cursor < 12:
                    break

                msg_head = data[cursor : cursor + 12]
                msg_type, vtime, msg_size, orig_bytes, ttl, hop, msg_seq = struct.unpack(
                    "!BBH4sBBH", msg_head
                )
                if msg_size < 12:
                    break

                orig_ip = socket.inet_ntoa(orig_bytes)
                validity_time = decode_mantissa(vtime)

                body_start = cursor + 12
                body_end = cursor + msg_size
                if body_end > len(data):
                    break
                msg_body_bytes = data[body_start:body_end]

                if not self.duplicate_set.is_duplicate(orig_ip, msg_seq):
                    self.duplicate_set.record_message(orig_ip, msg_seq, time.time())

                    if msg_type == HELLO_MESSAGE:
                        hello_info = parse_hello_body(msg_body_bytes)
                        if hello_info:
                            self.process_hello(sender_ip, hello_info, validity_time)
                    elif msg_type == TC_MESSAGE:
                        tc_info = parse_tc_body(msg_body_bytes)
                        if tc_info:
                            self.process_tc(orig_ip, tc_info, validity_time)

                if self.check_forwarding_condition(sender_ip, orig_ip, msg_seq, ttl):
                    full_msg_data = data[cursor:body_end]
                    self.forward_message(full_msg_data, ttl, hop)

                cursor += msg_size

    def process_hello(self, sender_ip, hello_info, validity_time):
        current_time = time.time()
        self.link_set.process_hello(sender_ip, hello_info, validity_time)

        link = self.link_set.links.get(sender_ip)
        is_sym = link.is_symmetric() if link else False

        self.neighbor_manager.update_neighbor_status(
            sender_ip,
            hello_info["willingness"],
            is_sym,
        )

        if not is_sym:
            return

        self.neighbor_manager.process_2hop_neighbors(
            sender_ip,
            hello_info,
            validity_time,
            current_time,
        )
        self.neighbor_manager.process_mpr_selector(
            sender_ip,
            hello_info,
            validity_time,
            current_time,
        )
        self.neighbor_manager.recalculate_mpr()
        self.routing_manager.recalculate_routing_table()

    def process_tc(self, originator_ip, tc_info, validity_time):
        self.topology_manager.process_tc_message(
            originator_ip,
            tc_info,
            validity_time,
            time.time(),
        )
        self.routing_manager.recalculate_routing_table()

    def generate_and_send_hello(self):
        mpr_set = self.neighbor_manager.current_mpr_set
        groups = self.link_set.get_hello_groups(mpr_set)
        hello_info = {
            "htime_seconds": HELLO_INTERVAL,
            "willingness": WILL_DEFAULT,
            "neighbor_groups": groups,
        }
        hello_body = create_hello_body(hello_info)
        header = create_message_header(
            HELLO_MESSAGE,
            NEIGHB_HOLD_TIME,
            len(hello_body),
            self.my_ip,
            1,
            0,
            self.get_next_msg_seq(),
        )
        self.send_packet(header + hello_body)
        print(f"[Send] HELLO ({len(groups)} groups)")

    def generate_and_send_tc(self):
        selectors = list(self.neighbor_manager.mpr_selectors.keys())
        if not selectors:
            return

        self.ansn = (self.ansn + 1) % 65535
        tc_body = create_tc_body(self.ansn, selectors)
        header = create_message_header(
            TC_MESSAGE,
            TOP_HOLD_TIME,
            len(tc_body),
            self.my_ip,
            255,
            0,
            self.get_next_msg_seq(),
        )
        self.send_packet(header + tc_body)
        print(f"[Send] TC (Selectors: {selectors})")

    def check_forwarding_condition(self, sender_ip, orig_ip, seq, ttl):
        if ttl <= 1:
            return False
        if orig_ip == self.my_ip:
            return False
        if self.duplicate_set.is_duplicate(orig_ip, seq):
            entry = self.duplicate_set.entries.get((orig_ip, seq))
            if entry and entry.retransmitted:
                return False
        return sender_ip in self.neighbor_manager.mpr_selectors

    def forward_message(self, msg_data, _old_ttl, _old_hop):
        fmt = "!BBH4sBBH"
        fields = list(struct.unpack(fmt, msg_data[:12]))
        fields[4] -= 1
        fields[5] += 1
        new_head = struct.pack(fmt, *fields)

        orig_ip = socket.inet_ntoa(fields[3])
        seq = fields[6]
        self.duplicate_set.mark_retransmitted(orig_ip, seq)

        print(f"[Forward] forwarding message from {orig_ip}")
        self.send_packet(new_head + msg_data[12:])

    def send_packet(self, msg_bytes):
        pkt_head = create_packet_header(len(msg_bytes), self.get_next_pkt_seq())
        data = pkt_head + msg_bytes
        interfaces = self.get_interfaces()
        if not interfaces:
            try:
                self.sock.sendto(data, ("255.255.255.255", self.port))
            except OSError:
                pass
            return

        for intf in interfaces:
            try:
                send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                send_sock.setsockopt(socket.SOL_SOCKET, 25, intf.encode("utf-8"))
                send_sock.sendto(data, ("255.255.255.255", self.port))
                send_sock.close()
            except Exception as exc:
                print(f"[Send Error] on {intf}: {exc}")

    def loop_control(self):
        while self.running:
            try:
                data, addr = self.control_sock.recvfrom(4096)
            except OSError:
                break
            except Exception as exc:
                print(f"[Error] Control Receive: {exc}")
                continue

            try:
                command_text = data.decode("utf-8", errors="ignore").strip()
                with self.lock:
                    response = process_control_command(self, command_text)
            except Exception as exc:
                response = f"control_error={exc}"

            try:
                self.control_sock.sendto(response.encode("utf-8", errors="ignore"), addr)
            except OSError:
                pass

    def get_next_msg_seq(self):
        self.msg_seq_num = (self.msg_seq_num + 1) % 65535
        return self.msg_seq_num

    def get_next_pkt_seq(self):
        self.pkt_seq_num = (self.pkt_seq_num + 1) % 65535
        return self.pkt_seq_num

    def loop_hello(self):
        while self.running:
            try:
                with self.lock:
                    self.generate_and_send_hello()
                time.sleep(HELLO_INTERVAL - 0.5 + random.random())
            except Exception as exc:
                print(f"[Error] Hello Loop: {exc}")

    def loop_tc(self):
        while self.running:
            try:
                with self.lock:
                    self.generate_and_send_tc()
                time.sleep(TC_INTERVAL - 0.5 + random.random())
            except Exception as exc:
                print(f"[Error] TC Loop: {exc}")

    def loop_cleanup(self):
        while self.running:
            time.sleep(2.0)
            with self.lock:
                self.link_set.cleanup()
                self.neighbor_manager.cleanup()
                self.topology_manager.cleanup()
                self.duplicate_set.cleanup()
                self.routing_manager.recalculate_routing_table()

    def get_interfaces(self):
        interfaces = []
        try:
            for intf in os.listdir("/sys/class/net/"):
                if intf == "lo":
                    continue
                if intf.startswith("h") and "eth" in intf:
                    interfaces.append(intf)
                    continue
                if intf.startswith("sta") and "wlan" in intf:
                    interfaces.append(intf)
                    continue
                if intf.startswith(("eth", "wlan")):
                    interfaces.append(intf)
        except Exception:
            pass
        return sorted(set(interfaces))


def parse_args():
    parser = argparse.ArgumentParser(description="Run one OLSR overlay node.")
    parser.add_argument("ip", nargs="?", default="192.168.3.5", help="Local node IPv4 address.")
    parser.add_argument("--port", type=int, default=5005, help="UDP broadcast port used by OLSR.")
    parser.add_argument(
        "--control-port",
        type=int,
        default=5100,
        help="Local UDP control port bound on 127.0.0.1.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    node = OLSRNode(args.ip, port=args.port, control_port=args.control_port)
    try:
        node.start()
    finally:
        node.stop()
