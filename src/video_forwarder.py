from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import queue
import select
import socket
import threading
import time
import uuid
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_NAME = "video_forwarder"
APP_VERSION = 1
DEFAULT_DATA_PORT = 6200
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "logs" / "received_videos"


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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class RouteInfo:
    dest: str
    next_hop_ip: str
    hop_count: int
    valid: bool
    state: str


@dataclass
class ReceiveState:
    transfer_id: str
    src_ip: str
    file_name: str
    file_size: int
    total_chunks: int
    file_sha256: str
    part_path: Path
    final_path: Path
    next_chunk_id: int = 0
    bytes_written: int = 0


class VideoForwarder:
    def __init__(
        self,
        node_ip: str,
        data_port: int,
        control_client: ControlClient,
        output_dir: Path,
        route_timeout_sec: float,
        route_poll_interval_sec: float,
    ):
        self.node_ip = node_ip
        self.data_port = int(data_port)
        self.control_client = control_client
        self.output_dir = output_dir
        self.route_timeout_sec = float(route_timeout_sec)
        self.route_poll_interval_sec = float(route_poll_interval_sec)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.data_port))
        self.sock.setblocking(False)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event = threading.Event()
        self._receive_states: dict[str, ReceiveState] = {}
        self._ack_waiters: dict[tuple[str, str, int], queue.Queue[dict[str, Any]]] = {}
        self._ack_lock = threading.Lock()

    def close(self) -> None:
        self._stop_event.set()
        self.sock.close()

    def log(self, text: str) -> None:
        print(f"[{self.node_ip}] {text}", flush=True)

    def send_packet(self, packet: dict[str, Any]) -> None:
        dest_ip = str(packet["dest_ip"])
        next_hop_ip = self.wait_for_route(dest_ip)
        payload = json.dumps(packet, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self.sock.sendto(payload, (next_hop_ip, self.data_port))
        self.log(
            f"send kind={packet.get('kind')} transfer_id={packet.get('transfer_id')} "
            f"next_hop={next_hop_ip} final_dest={dest_ip}"
        )

    def wait_for_route(self, dest_ip: str) -> str:
        stop_at = time.time() + self.route_timeout_sec
        first_attempt = True
        while time.time() < stop_at:
            route = self.query_route(dest_ip)
            if route is not None:
                return route.next_hop_ip
            if first_attempt:
                self.log(self.control_client.discover_route(dest_ip))
                first_attempt = False
            else:
                self.control_client.discover_route(dest_ip)
            time.sleep(self.route_poll_interval_sec)
        raise TimeoutError(f"route lookup timeout for {dest_ip}")

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
        if not fields:
            return None
        valid = fields.get("valid", "").lower() == "true"
        state = fields.get("state", "")
        next_hop_ip = fields.get("next_hop_ip", "")
        if (not valid) or state != "VALID" or not next_hop_ip:
            return None
        return RouteInfo(
            dest=fields.get("dest", dest_ip),
            next_hop_ip=next_hop_ip,
            hop_count=int(fields.get("hop_count", "0")),
            valid=valid,
            state=state,
        )

    def build_ack_key(self, packet: dict[str, Any]) -> tuple[str, str, int]:
        return (
            str(packet.get("transfer_id", "")),
            str(packet.get("ack_for", "")),
            int(packet.get("chunk_id", -1)),
        )

    def register_ack_waiter(self, transfer_id: str, ack_for: str, chunk_id: int = -1) -> queue.Queue[dict[str, Any]]:
        key = (transfer_id, ack_for, chunk_id)
        ack_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._ack_lock:
            self._ack_waiters[key] = ack_queue
        return ack_queue

    def pop_ack_waiter(self, transfer_id: str, ack_for: str, chunk_id: int = -1) -> None:
        key = (transfer_id, ack_for, chunk_id)
        with self._ack_lock:
            self._ack_waiters.pop(key, None)

    def deliver_ack(self, packet: dict[str, Any]) -> bool:
        key = self.build_ack_key(packet)
        with self._ack_lock:
            ack_queue = self._ack_waiters.get(key)
        if ack_queue is None:
            return False
        try:
            ack_queue.put_nowait(packet)
        except queue.Full:
            pass
        return True

    def send_ack(self, dest_ip: str, transfer_id: str, ack_for: str, status: str, chunk_id: int = -1, **fields: Any) -> None:
        packet = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "kind": "ack",
            "src_ip": self.node_ip,
            "dest_ip": dest_ip,
            "transfer_id": transfer_id,
            "ack_for": ack_for,
            "status": status,
            "chunk_id": chunk_id,
        }
        packet.update(fields)
        self.send_packet(packet)

    def build_final_path(self, file_name: str) -> Path:
        base_path = self.output_dir / file_name
        if not base_path.exists():
            return base_path
        stem = base_path.stem
        suffix = base_path.suffix
        for index in range(1, 1000):
            candidate = base_path.with_name(f"{stem}_{index}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"could not allocate output path for {file_name}")

    def handle_meta(self, packet: dict[str, Any]) -> None:
        transfer_id = str(packet["transfer_id"])
        src_ip = str(packet["src_ip"])
        if transfer_id in self._receive_states:
            self.send_ack(src_ip, transfer_id, "meta", "ok")
            return
        file_name = str(packet["file_name"])
        final_path = self.build_final_path(file_name)
        part_path = final_path.with_suffix(final_path.suffix + ".part")
        with part_path.open("wb"):
            pass
        state = ReceiveState(
            transfer_id=transfer_id,
            src_ip=src_ip,
            file_name=file_name,
            file_size=int(packet["file_size"]),
            total_chunks=int(packet["total_chunks"]),
            file_sha256=str(packet["file_sha256"]),
            part_path=part_path,
            final_path=final_path,
        )
        self._receive_states[transfer_id] = state
        self.log(f"recv meta transfer_id={transfer_id} file={file_name} total_chunks={state.total_chunks}")
        self.send_ack(src_ip, transfer_id, "meta", "ok")

    def handle_chunk(self, packet: dict[str, Any]) -> None:
        transfer_id = str(packet["transfer_id"])
        src_ip = str(packet["src_ip"])
        state = self._receive_states.get(transfer_id)
        if state is None:
            self.send_ack(src_ip, transfer_id, "chunk", "error", chunk_id=int(packet.get("chunk_id", -1)), detail="missing meta")
            return
        chunk_id = int(packet["chunk_id"])
        if chunk_id < state.next_chunk_id:
            self.send_ack(src_ip, transfer_id, "chunk", "ok", chunk_id=chunk_id, duplicate=True)
            return
        if chunk_id > state.next_chunk_id:
            self.send_ack(src_ip, transfer_id, "chunk", "error", chunk_id=chunk_id, detail=f"expected chunk {state.next_chunk_id}")
            return
        raw_chunk = base64.b64decode(str(packet["payload_b64"]).encode("ascii"))
        actual_sha = hashlib.sha256(raw_chunk).hexdigest()
        expected_sha = str(packet["chunk_sha256"])
        if actual_sha != expected_sha:
            self.send_ack(src_ip, transfer_id, "chunk", "error", chunk_id=chunk_id, detail="chunk sha mismatch")
            return
        with state.part_path.open("ab") as handle:
            handle.write(raw_chunk)
        state.next_chunk_id += 1
        state.bytes_written += len(raw_chunk)
        self.log(f"recv chunk transfer_id={transfer_id} chunk={chunk_id + 1}/{state.total_chunks}")
        self.send_ack(src_ip, transfer_id, "chunk", "ok", chunk_id=chunk_id)

    def handle_eof(self, packet: dict[str, Any]) -> None:
        transfer_id = str(packet["transfer_id"])
        src_ip = str(packet["src_ip"])
        state = self._receive_states.get(transfer_id)
        if state is None:
            self.send_ack(src_ip, transfer_id, "eof", "error", detail="missing meta")
            return
        if state.next_chunk_id != state.total_chunks:
            self.send_ack(src_ip, transfer_id, "eof", "error", detail=f"incomplete chunks {state.next_chunk_id}/{state.total_chunks}")
            return
        if state.bytes_written != state.file_size:
            self.send_ack(src_ip, transfer_id, "eof", "error", detail=f"size mismatch {state.bytes_written}/{state.file_size}")
            return
        actual_file_sha = file_sha256(state.part_path)
        if actual_file_sha != state.file_sha256:
            self.send_ack(src_ip, transfer_id, "eof", "error", detail="file sha mismatch")
            return
        state.part_path.replace(state.final_path)
        self.log(f"transfer complete transfer_id={transfer_id} saved={state.final_path}")
        self.send_ack(src_ip, transfer_id, "eof", "complete", output_path=str(state.final_path))
        self._receive_states.pop(transfer_id, None)

    def handle_local_packet(self, packet: dict[str, Any]) -> None:
        kind = str(packet.get("kind", ""))
        if kind == "ack":
            delivered = self.deliver_ack(packet)
            if not delivered:
                self.log(f"unhandled ack transfer_id={packet.get('transfer_id')} ack_for={packet.get('ack_for')}")
            return
        if kind == "meta":
            self.handle_meta(packet)
        elif kind == "chunk":
            self.handle_chunk(packet)
        elif kind == "eof":
            self.handle_eof(packet)

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
                self.send_packet(packet)
            except Exception as exc:
                self.log(f"forward failed transfer_id={packet.get('transfer_id')} dest={dest_ip}: {exc}")
            return
        self.handle_local_packet(packet)

    def run(self) -> None:
        self.log(f"listening on udp/{self.data_port}")
        while not self._stop_event.is_set():
            try:
                readable, _, _ = select.select([self.sock], [], [], 0.5)
            except (OSError, ValueError):
                break
            for sock in readable:
                raw, _remote_addr = sock.recvfrom(65535)
                self.handle_packet(raw)


class FileSender:
    def __init__(
        self,
        forwarder: VideoForwarder,
        source_path: Path,
        dest_ip: str,
        chunk_size: int,
        ack_timeout_sec: float,
        max_retries: int,
    ):
        self.forwarder = forwarder
        self.source_path = source_path
        self.dest_ip = dest_ip
        self.chunk_size = int(chunk_size)
        self.ack_timeout_sec = float(ack_timeout_sec)
        self.max_retries = int(max_retries)
        self.transfer_id = uuid.uuid4().hex

    def send_with_ack(self, packet: dict[str, Any], ack_for: str, chunk_id: int = -1) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                ack_queue = self.forwarder.register_ack_waiter(self.transfer_id, ack_for, chunk_id)
                try:
                    self.forwarder.send_packet(packet)
                    ack = ack_queue.get(timeout=self.ack_timeout_sec)
                finally:
                    self.forwarder.pop_ack_waiter(self.transfer_id, ack_for, chunk_id)
                status = str(ack.get("status", "ok"))
                self.forwarder.log(f"ack kind={ack_for} chunk_id={chunk_id} attempt={attempt} status={status}")
                if status not in {"ok", "complete"}:
                    raise RuntimeError(f"application ack failure: {ack}")
                return ack
            except Exception as exc:
                last_error = exc
                self.forwarder.log(f"retry kind={ack_for} chunk_id={chunk_id} attempt={attempt} error={exc}")
        raise RuntimeError(f"failed after retries kind={ack_for} chunk_id={chunk_id}: {last_error}")

    def run(self) -> None:
        file_size = self.source_path.stat().st_size
        total_chunks = max(1, math.ceil(file_size / self.chunk_size))
        file_sha = file_sha256(self.source_path)
        self.forwarder.log(
            f"start send transfer_id={self.transfer_id} file={self.source_path.name} size={file_size} total_chunks={total_chunks}"
        )
        meta_packet = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "kind": "meta",
            "transfer_id": self.transfer_id,
            "src_ip": self.forwarder.node_ip,
            "dest_ip": self.dest_ip,
            "file_name": self.source_path.name,
            "file_size": file_size,
            "total_chunks": total_chunks,
            "file_sha256": file_sha,
        }
        self.send_with_ack(meta_packet, "meta")

        with self.source_path.open("rb") as handle:
            for chunk_id in range(total_chunks):
                raw_chunk = handle.read(self.chunk_size)
                chunk_packet = {
                    "app": APP_NAME,
                    "version": APP_VERSION,
                    "kind": "chunk",
                    "transfer_id": self.transfer_id,
                    "src_ip": self.forwarder.node_ip,
                    "dest_ip": self.dest_ip,
                    "file_name": self.source_path.name,
                    "chunk_id": chunk_id,
                    "total_chunks": total_chunks,
                    "payload_b64": base64.b64encode(raw_chunk).decode("ascii"),
                    "chunk_sha256": hashlib.sha256(raw_chunk).hexdigest(),
                }
                self.send_with_ack(chunk_packet, "chunk", chunk_id=chunk_id)
                self.forwarder.log(f"chunk sent {chunk_id + 1}/{total_chunks}")

        eof_packet = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "kind": "eof",
            "transfer_id": self.transfer_id,
            "src_ip": self.forwarder.node_ip,
            "dest_ip": self.dest_ip,
            "file_name": self.source_path.name,
            "file_size": file_size,
            "total_chunks": total_chunks,
            "file_sha256": file_sha,
        }
        ack = self.send_with_ack(eof_packet, "eof")
        self.forwarder.log(f"send complete transfer_id={self.transfer_id} ack={ack}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Independent UDP video/file forwarder using OLSR only for route lookup.")
    parser.add_argument("--node-ip", required=True, help="Local node IP known by the OLSR overlay.")
    parser.add_argument("--data-port", type=int, default=DEFAULT_DATA_PORT, help="UDP port used by the file forwarder.")
    parser.add_argument("--control-ip", default="127.0.0.1", help="Local OLSR control endpoint IP.")
    parser.add_argument("--control-port", type=int, default=5100, help="Local OLSR control endpoint port.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory used to store received files.")
    parser.add_argument("--log-file", help="Optional file path used to store forwarder stdout/stderr logs.")
    parser.add_argument("--route-timeout-sec", type=float, default=12.0, help="Max time to wait for a route lookup.")
    parser.add_argument("--route-poll-interval-sec", type=float, default=0.5, help="Polling interval while waiting for routes.")
    parser.add_argument("--send-file", help="Optional local file path to send after the forwarder starts.")
    parser.add_argument("--dest-ip", help="Destination IP for --send-file.")
    parser.add_argument("--chunk-size", type=int, default=900, help="Raw bytes per chunk before base64.")
    parser.add_argument("--ack-timeout-sec", type=float, default=5.0, help="Ack timeout for each transmitted packet.")
    parser.add_argument("--max-retries", type=int, default=8, help="Max retries per packet when sending a file.")
    parser.add_argument("--exit-after-send", action="store_true", help="Exit after --send-file finishes.")
    return parser.parse_args()


def configure_log_file(log_file: str | None) -> None:
    if not log_file:
        return
    log_path = Path(log_file).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = Tee(sys.stdout, handle)
    sys.stderr = Tee(sys.stderr, handle)


def main() -> int:
    args = parse_args()
    if args.send_file and not args.dest_ip:
        raise ValueError("--dest-ip is required with --send-file")
    if args.chunk_size <= 0:
        raise ValueError("--chunk-size must be positive")

    configure_log_file(args.log_file)

    control_client = ControlClient(control_ip=args.control_ip, control_port=args.control_port)
    forwarder = VideoForwarder(
        node_ip=args.node_ip,
        data_port=args.data_port,
        control_client=control_client,
        output_dir=Path(args.output_dir).expanduser().resolve(),
        route_timeout_sec=args.route_timeout_sec,
        route_poll_interval_sec=args.route_poll_interval_sec,
    )

    sender_thread: threading.Thread | None = None
    sender_error: list[BaseException] = []

    if args.send_file:
        source_path = Path(args.send_file).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"file not found: {source_path}")
        sender = FileSender(
            forwarder=forwarder,
            source_path=source_path,
            dest_ip=str(args.dest_ip),
            chunk_size=args.chunk_size,
            ack_timeout_sec=args.ack_timeout_sec,
            max_retries=args.max_retries,
        )

        def _run_sender() -> None:
            try:
                sender.run()
            except BaseException as exc:
                sender_error.append(exc)
            finally:
                if args.exit_after_send:
                    forwarder._stop_event.set()

        sender_thread = threading.Thread(target=_run_sender, daemon=True)
        sender_thread.start()

    try:
        forwarder.run()
    finally:
        forwarder.close()

    if sender_thread is not None:
        sender_thread.join(timeout=1.0)
    if sender_error:
        raise sender_error[0]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
