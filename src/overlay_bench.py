from __future__ import annotations

import argparse
import json
import errno
import queue
import select
import socket
import statistics
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "overlay_bench"
APP_VERSION = 1
DEFAULT_DATA_PORT = 6300
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOCKET_BUFFER_BYTES = 1_048_576
DEFAULT_END_RETRY_INTERVAL_SEC = 1.0
DEFAULT_RESULTS_DIR = REPO_ROOT / "logs" / "overlay_bench_results"


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


@dataclass
class RouteInfo:
    dest: str
    next_hop_ip: str
    hop_count: int
    valid: bool
    state: str
    first_seen_at: float | None = None
    protocol_started_at: float | None = None


@dataclass
class ThroughputSession:
    session_id: str
    src_ip: str
    received_packets: int = 0
    duplicate_packets: int = 0
    received_bytes: int = 0
    first_rx_ns: int | None = None
    last_rx_ns: int | None = None
    seen_seqs: set[int] = field(default_factory=set)


def session_result_path(dest_ip: str, session_id: str) -> Path:
    safe_ip = str(dest_ip).replace(".", "_")
    return DEFAULT_RESULTS_DIR / f"{safe_ip}__{session_id}.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    tmp_path.replace(path)


class ControlClient:
    def __init__(self, control_ip: str = "127.0.0.1", control_port: int = 5100, timeout_sec: float = 3.0):
        self.control_ip = control_ip
        self.control_port = int(control_port)
        self.timeout_sec = float(timeout_sec)

    def send_command(self, command: str) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(self.timeout_sec)
            sock.sendto(command.encode("utf-8"), (self.control_ip, self.control_port))
            data, _ = sock.recvfrom(65535)
            return data.decode("utf-8", errors="ignore")
        finally:
            sock.close()

    def discover_route(self, dest_ip: str) -> str:
        return self.send_command(f"DISCOVER_ROUTE:{dest_ip}")

    def show_route_detail(self, dest_ip: str) -> str:
        return self.send_command(f"SHOW_ROUTE_DETAIL:{dest_ip}")


class OverlayBenchNode:
    def __init__(
        self,
        node_ip: str,
        data_port: int,
        control_client: ControlClient,
        route_timeout_sec: float,
        route_poll_interval_sec: float,
        send_retries: int,
        send_retry_sleep_ms: float,
        socket_sndbuf_bytes: int,
        socket_rcvbuf_bytes: int,
        quiet: bool,
    ):
        self.node_ip = node_ip
        self.data_port = int(data_port)
        self.control_client = control_client
        self.route_timeout_sec = float(route_timeout_sec)
        self.route_poll_interval_sec = float(route_poll_interval_sec)
        self.send_retries = int(send_retries)
        self.send_retry_sleep_ms = float(send_retry_sleep_ms)
        self.quiet = quiet
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, int(socket_sndbuf_bytes))
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(socket_rcvbuf_bytes))
        self.sock.bind(("0.0.0.0", self.data_port))
        self.sock.setblocking(False)
        self._stop_event = threading.Event()
        self._waiters: dict[tuple[str, str], queue.Queue[dict[str, Any]]] = {}
        self._waiters_lock = threading.Lock()
        self._pending_packets: dict[tuple[str, str], dict[str, Any]] = {}
        self._route_cache: dict[str, RouteInfo] = {}
        self._route_lock = threading.Lock()
        self._throughput_sessions: dict[str, ThroughputSession] = {}
        self.send_retry_count = 0
        self.send_retry_events = 0
        self.send_failures = 0

    def close(self) -> None:
        self._stop_event.set()
        try:
            self.sock.close()
        except OSError:
            pass

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread

    def log(self, text: str) -> None:
        if self.quiet:
            return
        print(f"[{self.node_ip}] {text}", flush=True)

    def register_waiter(self, wait_kind: str, wait_id: str) -> queue.Queue[dict[str, Any]]:
        key = (wait_kind, wait_id)
        wait_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._waiters_lock:
            self._waiters[key] = wait_queue
            pending = self._pending_packets.pop(key, None)
        if pending is not None:
            try:
                wait_queue.put_nowait(pending)
            except queue.Full:
                pass
        return wait_queue

    def pop_waiter(self, wait_kind: str, wait_id: str) -> None:
        key = (wait_kind, wait_id)
        with self._waiters_lock:
            self._waiters.pop(key, None)

    def deliver_waiter(self, wait_kind: str, wait_id: str, packet: dict[str, Any]) -> bool:
        key = (wait_kind, wait_id)
        with self._waiters_lock:
            wait_queue = self._waiters.get(key)
        if wait_queue is None:
            with self._waiters_lock:
                self._pending_packets[key] = packet
            return False
        try:
            wait_queue.put_nowait(packet)
        except queue.Full:
            pass
        return True

    def wait_for_packet(self, wait_kind: str, wait_id: str, timeout_sec: float) -> dict[str, Any]:
        wait_queue = self.register_waiter(wait_kind, wait_id)
        try:
            return wait_queue.get(timeout=timeout_sec)
        finally:
            self.pop_waiter(wait_kind, wait_id)

    def query_route(self, dest_ip: str) -> RouteInfo | None:
        text = self.control_client.show_route_detail(dest_ip)
        if text.startswith("未找到路由") or text.startswith("非法地址"):
            return None
        fields: dict[str, str] = {}
        for line in text.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
        required = {"dest", "next_hop_ip", "hop_count", "valid", "state"}
        if not required.issubset(fields):
            return None
        valid = fields.get("valid", "").lower() == "true"
        state = fields.get("state", "")
        next_hop_ip = fields.get("next_hop_ip", "")
        if (not valid) or state != "VALID" or not next_hop_ip:
            return None
        return RouteInfo(
            dest=fields["dest"],
            next_hop_ip=next_hop_ip,
            hop_count=int(fields["hop_count"]),
            valid=valid,
            state=state,
            first_seen_at=float(fields["first_seen_at"]) if "first_seen_at" in fields else None,
            protocol_started_at=float(fields["protocol_started_at"]) if "protocol_started_at" in fields else None,
        )

    def establish_route(self, dest_ip: str) -> tuple[RouteInfo, float]:
        start_ns = time.perf_counter_ns()
        self.log(self.control_client.discover_route(dest_ip))
        deadline = time.monotonic() + self.route_timeout_sec
        while time.monotonic() < deadline:
            route = self.query_route(dest_ip)
            if route is not None:
                elapsed_sec = (time.perf_counter_ns() - start_ns) / 1_000_000_000.0
                with self._route_lock:
                    self._route_cache[dest_ip] = route
                return route, elapsed_sec
            time.sleep(self.route_poll_interval_sec)
        raise TimeoutError(f"route convergence timeout for {dest_ip}")

    def resolve_next_hop(self, dest_ip: str) -> RouteInfo:
        with self._route_lock:
            cached = self._route_cache.get(dest_ip)
        if cached is not None:
            return cached
        route, _ = self.establish_route(dest_ip)
        return route

    def send_overlay(self, packet: dict[str, Any]) -> None:
        dest_ip = str(packet["dest_ip"])
        route = self.resolve_next_hop(dest_ip)
        raw = json.dumps(packet, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        retry_used = False
        for attempt in range(self.send_retries + 1):
            try:
                self.sock.sendto(raw, (route.next_hop_ip, self.data_port))
                if retry_used:
                    self.send_retry_events += 1
                self.log(
                    f"send kind={packet.get('kind')} dest={dest_ip} next_hop={route.next_hop_ip} "
                    f"retry_used={retry_used} attempt={attempt + 1}"
                )
                return
            except BlockingIOError:
                retry_used = True
                self.send_retry_count += 1
                if attempt >= self.send_retries:
                    self.send_failures += 1
                    raise
                if self.send_retry_sleep_ms > 0:
                    time.sleep(self.send_retry_sleep_ms / 1000.0)
                else:
                    select.select([], [self.sock], [], 0.001)
            except OSError:
                self.send_failures += 1
                raise

    def handle_ping(self, packet: dict[str, Any]) -> None:
        reply = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "kind": "ping_reply",
            "src_ip": self.node_ip,
            "dest_ip": str(packet["src_ip"]),
            "ping_id": str(packet["ping_id"]),
            "send_ts_ns": int(packet["send_ts_ns"]),
            "payload_size": int(packet.get("payload_size", 0)),
        }
        self.send_overlay(reply)

    def handle_ping_reply(self, packet: dict[str, Any]) -> None:
        ping_id = str(packet.get("ping_id", ""))
        if not self.deliver_waiter("ping_reply", ping_id, packet):
            self.log(f"unhandled ping reply ping_id={ping_id}")

    def handle_throughput_data(self, packet: dict[str, Any]) -> None:
        session_id = str(packet["session_id"])
        session = self._throughput_sessions.get(session_id)
        if session is None:
            session = ThroughputSession(session_id=session_id, src_ip=str(packet["src_ip"]))
            self._throughput_sessions[session_id] = session
        seq = int(packet["seq"])
        now_ns = time.perf_counter_ns()
        if seq in session.seen_seqs:
            session.duplicate_packets += 1
            return
        session.seen_seqs.add(seq)
        session.received_packets += 1
        session.received_bytes += int(packet.get("payload_size", 0))
        if session.first_rx_ns is None:
            session.first_rx_ns = now_ns
        session.last_rx_ns = now_ns

    def handle_throughput_end(self, packet: dict[str, Any]) -> None:
        session_id = str(packet["session_id"])
        dest_ip = str(packet["dest_ip"])
        src_ip = str(packet["src_ip"])
        session = self._throughput_sessions.get(session_id)
        if session is None:
            session = ThroughputSession(session_id=session_id, src_ip=src_ip)
        first_rx_ns = session.first_rx_ns or 0
        last_rx_ns = session.last_rx_ns or first_rx_ns
        duration_ns = max(0, last_rx_ns - first_rx_ns)
        result = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "metric": "throughput",
            "session_id": session_id,
            "src_ip": src_ip,
            "dest_ip": dest_ip,
            "receiver_ip": self.node_ip,
            "expected_packets": int(packet["expected_packets"]),
            "expected_bytes": int(packet["expected_bytes"]),
            "received_packets": session.received_packets,
            "received_bytes": session.received_bytes,
            "duplicate_packets": session.duplicate_packets,
            "first_rx_ns": first_rx_ns,
            "last_rx_ns": last_rx_ns,
            "duration_ns": duration_ns,
            "result_written_at_sec": time.time(),
        }
        write_json_atomic(session_result_path(dest_ip, session_id), result)
        self._throughput_sessions.pop(session_id, None)

    def handle_throughput_report(self, packet: dict[str, Any]) -> None:
        session_id = str(packet.get("session_id", ""))
        if not self.deliver_waiter("throughput_report", session_id, packet):
            self.log(f"unhandled throughput report session_id={session_id}")

    def handle_throughput_report_ack(self, packet: dict[str, Any]) -> None:
        return

    def handle_local(self, packet: dict[str, Any]) -> None:
        kind = str(packet.get("kind", ""))
        if kind == "ping":
            self.handle_ping(packet)
        elif kind == "ping_reply":
            self.handle_ping_reply(packet)
        elif kind == "throughput_data":
            self.handle_throughput_data(packet)
        elif kind == "throughput_end":
            self.handle_throughput_end(packet)
        elif kind == "throughput_report":
            self.handle_throughput_report(packet)
        elif kind == "throughput_report_ack":
            self.handle_throughput_report_ack(packet)

    def handle_packet(self, raw: bytes) -> None:
        try:
            packet = json.loads(raw.decode("utf-8"))
        except Exception:
            return
        if not isinstance(packet, dict):
            return
        if packet.get("app") != APP_NAME:
            return
        dest_ip = str(packet.get("dest_ip", ""))
        if not dest_ip:
            return
        if dest_ip != self.node_ip:
            try:
                self.send_overlay(packet)
            except Exception as exc:
                self.log(f"forward failed kind={packet.get('kind')} dest={dest_ip}: {exc}")
            return
        self.handle_local(packet)

    def run(self) -> None:
        self.log(f"benchmark daemon listening on udp/{self.data_port}")
        while not self._stop_event.is_set():
            try:
                readable, _, _ = select.select([self.sock], [], [], 0.5)
            except (OSError, ValueError):
                break
            for sock in readable:
                try:
                    raw, _remote = sock.recvfrom(65535)
                    self.handle_packet(raw)
                except Exception as exc:
                    if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.EBADF:
                        if self._stop_event.is_set():
                            return
                    self.log(f"packet handling error: {exc}")


def configure_log_file(log_file: str | None) -> None:
    if not log_file:
        return
    log_path = Path(log_file).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = Tee(sys.stdout, handle)
    sys.stderr = Tee(sys.stderr, handle)


def print_result(result: dict[str, Any], json_only: bool) -> None:
    if json_only:
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")), flush=True)
        return
    for key, value in result.items():
        print(f"{key}={value}", flush=True)
    print("json=" + json.dumps(result, ensure_ascii=True, separators=(",", ":")), flush=True)


def add_common_node_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--node-ip", required=True, help="Local node IP known by the OLSR overlay.")
    parser.add_argument("--data-port", type=int, default=DEFAULT_DATA_PORT, help="UDP port used by overlay_bench.")
    parser.add_argument("--control-ip", default="127.0.0.1", help="Local OLSR control endpoint IP.")
    parser.add_argument("--control-port", type=int, default=5100, help="Local OLSR control endpoint port.")
    parser.add_argument("--route-timeout-sec", type=float, default=12.0, help="Max time to wait for route establishment.")
    parser.add_argument("--route-poll-interval-sec", type=float, default=0.2, help="Polling interval while waiting for routes.")
    parser.add_argument("--send-retries", type=int, default=200, help="Max retries when UDP send buffer is temporarily full.")
    parser.add_argument("--send-retry-sleep-ms", type=float, default=0.5, help="Sleep per retry after BlockingIOError.")
    parser.add_argument("--socket-sndbuf-bytes", type=int, default=DEFAULT_SOCKET_BUFFER_BYTES, help="UDP send buffer size.")
    parser.add_argument("--socket-rcvbuf-bytes", type=int, default=DEFAULT_SOCKET_BUFFER_BYTES, help="UDP receive buffer size.")
    parser.add_argument("--quiet", action="store_true", help="Reduce benchmark daemon and sender log output.")
    parser.add_argument("--log-file", help="Optional log file path.")
    parser.add_argument("--json", action="store_true", help="Print only one JSON line result for sender commands.")
    parser.add_argument(
        "--end-retry-interval-sec",
        type=float,
        default=DEFAULT_END_RETRY_INTERVAL_SEC,
        help="Retry interval while waiting for the destination result file after sending throughput_end.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Overlay benchmark tool for OLSR route convergence, latency, throughput, and delivery ratio.")
    sub = parser.add_subparsers(dest="command", required=True)

    daemon_parser = sub.add_parser("daemon", help="Run a benchmark relay/receiver daemon on one node.")
    add_common_node_args(daemon_parser)

    route_parser = sub.add_parser("route", help="Measure route convergence time to a destination.")
    add_common_node_args(route_parser)
    route_parser.add_argument("--dest-ip", required=True, help="Destination IP to discover.")

    latency_parser = sub.add_parser("latency", help="Measure RTT/loss with ping-reply probes through the overlay.")
    add_common_node_args(latency_parser)
    latency_parser.add_argument("--dest-ip", required=True, help="Destination IP for probes.")
    latency_parser.add_argument("--count", type=int, default=20, help="Number of probes to send.")
    latency_parser.add_argument("--payload-size", type=int, default=64, help="Probe payload size in bytes.")
    latency_parser.add_argument("--interval-ms", type=float, default=100.0, help="Interval between probes in milliseconds.")
    latency_parser.add_argument("--reply-timeout-sec", type=float, default=3.0, help="Per-probe reply timeout.")

    throughput_parser = sub.add_parser("throughput", help="Measure throughput, PDR, and loss over the overlay.")
    add_common_node_args(throughput_parser)
    throughput_parser.add_argument("--dest-ip", required=True, help="Destination IP for the test stream.")
    throughput_parser.add_argument("--count", type=int, default=1000, help="Number of data packets to send.")
    throughput_parser.add_argument("--payload-size", type=int, default=1000, help="Payload size in bytes per data packet.")
    throughput_parser.add_argument("--interval-ms", type=float, default=0.0, help="Delay between packets in milliseconds. 0 means send as fast as possible.")
    throughput_parser.add_argument("--report-timeout-sec", type=float, default=10.0, help="Timeout waiting for the receiver report.")
    return parser


def build_node(args: argparse.Namespace) -> OverlayBenchNode:
    control = ControlClient(control_ip=args.control_ip, control_port=args.control_port)
    return OverlayBenchNode(
        node_ip=args.node_ip,
        data_port=args.data_port,
        control_client=control,
        route_timeout_sec=args.route_timeout_sec,
        route_poll_interval_sec=args.route_poll_interval_sec,
        send_retries=args.send_retries,
        send_retry_sleep_ms=args.send_retry_sleep_ms,
        socket_sndbuf_bytes=args.socket_sndbuf_bytes,
        socket_rcvbuf_bytes=args.socket_rcvbuf_bytes,
        quiet=args.quiet,
    )


def run_route_command(args: argparse.Namespace) -> int:
    node = build_node(args)
    try:
        route, route_setup_sec = node.establish_route(args.dest_ip)
        startup_route_convergence_sec = None
        if route.first_seen_at is not None and route.protocol_started_at is not None:
            startup_route_convergence_sec = max(0.0, route.first_seen_at - route.protocol_started_at)
        result = {
            "metric": "route_convergence",
            "src_ip": args.node_ip,
            "dest_ip": args.dest_ip,
            "route_setup_sec": round(route_setup_sec, 6),
            "startup_route_convergence_sec": round(startup_route_convergence_sec, 6) if startup_route_convergence_sec is not None else None,
            "next_hop_ip": route.next_hop_ip,
            "hop_count": route.hop_count,
            "send_retry_events": node.send_retry_events,
            "send_retry_count": node.send_retry_count,
            "send_failures": node.send_failures,
        }
        print_result(result, args.json)
        return 0
    finally:
        node.close()


def run_latency_command(args: argparse.Namespace) -> int:
    node = build_node(args)
    try:
        listener = node.start_background()
        route, route_setup_sec = node.establish_route(args.dest_ip)
        payload_text = "x" * int(args.payload_size)
        rtts_ms: list[float] = []
        lost = 0
        for _index in range(int(args.count)):
            ping_id = uuid.uuid4().hex
            reply_queue = node.register_waiter("ping_reply", ping_id)
            packet = {
                "app": APP_NAME,
                "version": APP_VERSION,
                "kind": "ping",
                "src_ip": args.node_ip,
                "dest_ip": args.dest_ip,
                "ping_id": ping_id,
                "send_ts_ns": time.perf_counter_ns(),
                "payload_size": int(args.payload_size),
                "payload": payload_text,
            }
            start_ns = time.perf_counter_ns()
            try:
                node.send_overlay(packet)
                reply_queue.get(timeout=float(args.reply_timeout_sec))
                rtt_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
                rtts_ms.append(rtt_ms)
            except queue.Empty:
                lost += 1
            finally:
                node.pop_waiter("ping_reply", ping_id)
            if args.interval_ms > 0:
                time.sleep(float(args.interval_ms) / 1000.0)
        node._stop_event.set()
        listener.join(timeout=1.0)
        sent = int(args.count)
        received = len(rtts_ms)
        loss_rate = (lost / sent) if sent else 0.0
        result = {
            "metric": "latency",
            "src_ip": args.node_ip,
            "dest_ip": args.dest_ip,
            "route_setup_sec": round(route_setup_sec, 6),
            "next_hop_ip": route.next_hop_ip,
            "hop_count": route.hop_count,
            "sent": sent,
            "received": received,
            "lost": lost,
            "loss_rate": round(loss_rate, 6),
            "pdr": round(1.0 - loss_rate, 6),
            "rtt_min_ms": round(min(rtts_ms), 3) if rtts_ms else None,
            "rtt_avg_ms": round(statistics.mean(rtts_ms), 3) if rtts_ms else None,
            "rtt_p95_ms": round(sorted(rtts_ms)[max(0, int(len(rtts_ms) * 0.95) - 1)], 3) if rtts_ms else None,
            "rtt_max_ms": round(max(rtts_ms), 3) if rtts_ms else None,
            "one_way_estimated_ms": round(statistics.mean(rtts_ms) / 2.0, 3) if rtts_ms else None,
            "send_retry_events": node.send_retry_events,
            "send_retry_count": node.send_retry_count,
            "send_failures": node.send_failures,
        }
        print_result(result, args.json)
        return 0
    finally:
        node.close()


def run_throughput_command(args: argparse.Namespace) -> int:
    node = build_node(args)
    try:
        listener = node.start_background()
        route, route_setup_sec = node.establish_route(args.dest_ip)
        payload_text = "x" * int(args.payload_size)
        session_id = uuid.uuid4().hex
        result_path = session_result_path(args.dest_ip, session_id)
        for cleanup_path in (result_path, result_path.with_suffix(result_path.suffix + ".tmp")):
            if cleanup_path.exists():
                cleanup_path.unlink()
        send_start_ns = time.perf_counter_ns()
        for seq in range(int(args.count)):
            packet = {
                "app": APP_NAME,
                "version": APP_VERSION,
                "kind": "throughput_data",
                "src_ip": args.node_ip,
                "dest_ip": args.dest_ip,
                "session_id": session_id,
                "seq": seq,
                "payload_size": int(args.payload_size),
                "payload": payload_text,
            }
            node.send_overlay(packet)
            if args.interval_ms > 0:
                time.sleep(float(args.interval_ms) / 1000.0)
        send_end_ns = time.perf_counter_ns()
        end_packet = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "kind": "throughput_end",
            "src_ip": args.node_ip,
            "dest_ip": args.dest_ip,
            "session_id": session_id,
            "expected_packets": int(args.count),
            "expected_bytes": int(args.count) * int(args.payload_size),
        }
        deadline = time.monotonic() + float(args.report_timeout_sec)
        result = None
        while time.monotonic() < deadline:
            node.send_overlay(end_packet)
            wait_timeout = min(
                float(args.end_retry_interval_sec),
                max(0.0, deadline - time.monotonic()),
            )
            time.sleep(wait_timeout)
            if not result_path.exists():
                continue
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                break
            except Exception:
                continue
        if result is None:
            raise TimeoutError(
                f"throughput result timeout for session={session_id} "
                f"dest={args.dest_ip} after {float(args.report_timeout_sec):.3f}s"
            )
        node._stop_event.set()
        listener.join(timeout=1.0)

        sent_packets = int(args.count)
        sent_bytes = sent_packets * int(args.payload_size)
        sender_duration_sec = max(1e-9, (send_end_ns - send_start_ns) / 1_000_000_000.0)
        received_packets = int(result["received_packets"])
        received_bytes = int(result["received_bytes"])
        receiver_duration_ns = int(result["duration_ns"])
        receiver_duration_sec = max(1e-9, receiver_duration_ns / 1_000_000_000.0) if received_packets > 1 else sender_duration_sec
        offered_load_mbps = (sent_bytes * 8.0) / sender_duration_sec / 1_000_000.0
        goodput_mbps = (received_bytes * 8.0) / receiver_duration_sec / 1_000_000.0
        loss_packets = sent_packets - received_packets
        loss_rate = (loss_packets / sent_packets) if sent_packets else 0.0
        result = {
            "metric": "throughput",
            "src_ip": args.node_ip,
            "dest_ip": args.dest_ip,
            "route_setup_sec": round(route_setup_sec, 6),
            "next_hop_ip": route.next_hop_ip,
            "hop_count": route.hop_count,
            "sent_packets": sent_packets,
            "received_packets": received_packets,
            "lost_packets": loss_packets,
            "duplicate_packets": int(result["duplicate_packets"]),
            "pdr": round((received_packets / sent_packets) if sent_packets else 0.0, 6),
            "loss_rate": round(loss_rate, 6),
            "sent_bytes": sent_bytes,
            "received_bytes": received_bytes,
            "sender_duration_sec": round(sender_duration_sec, 6),
            "receiver_duration_sec": round(receiver_duration_sec, 6),
            "offered_load_mbps": round(offered_load_mbps, 6),
            "goodput_mbps": round(goodput_mbps, 6),
            "receiver_ip": result.get("receiver_ip"),
            "result_path": str(result_path),
            "send_retry_events": node.send_retry_events,
            "send_retry_count": node.send_retry_count,
            "send_failures": node.send_failures,
        }
        print_result(result, args.json)
        return 0
    finally:
        node.close()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    configure_log_file(getattr(args, "log_file", None))

    if args.command == "daemon":
        node = build_node(args)
        try:
            node.run()
            return 0
        finally:
            node.close()
    if args.command == "route":
        return run_route_command(args)
    if args.command == "latency":
        return run_latency_command(args)
    if args.command == "throughput":
        return run_throughput_command(args)
    raise ValueError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
