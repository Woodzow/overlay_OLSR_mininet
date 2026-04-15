#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

from mininet.log import info, setLogLevel
from mn_wifi.link import adhoc, wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

try:
    from mn_wifi.cli import CLI
except ImportError:
    from mn_wifi.cli import CLI_wifi as CLI


TOPOLOGY_FILE = Path(__file__).resolve().parents[1] / "configs" / "mininet_wifi_complex_12sta" / "topology.json"
VIDEO_DATA_PORT = 6200
BENCH_DATA_PORT = 6300
BENCH_RESULTS_DIR = Path(__file__).resolve().parents[1] / "logs" / "overlay_bench_results"


def load_topology() -> dict:
    return json.loads(TOPOLOGY_FILE.read_text(encoding="utf-8"))


def parse_args(topology: dict) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the 12-station complex Mininet-WiFi topology for the OLSR overlay.")
    parser.add_argument("--cli", action="store_true", help="Drop into the Mininet-WiFi CLI after the automated steps.")
    parser.add_argument("--neighbor-wait-sec", type=float, default=4.0, help="Time to wait before querying neighbor and route state.")
    parser.add_argument("--video-file", default="data.mp4", help="Video file path relative to repo root, or absolute path if needed.")
    parser.add_argument("--video-dest-ip", default=topology["video_dest_ip"], help="Destination node IP for the automated file transfer.")
    parser.add_argument("--video-data-port", type=int, default=VIDEO_DATA_PORT, help="UDP data port used by video_forwarder.py.")
    parser.add_argument("--video-chunk-size", type=int, default=900, help="Raw bytes per file chunk before base64 in video_forwarder.py.")
    parser.add_argument("--skip-file-transfer", action="store_true", help="Only build topology and OLSR processes, do not run the file transfer demo.")
    parser.add_argument("--link-loss", type=float, default=0.0, help="Apply tc netem packet loss percentage on each sta wlan interface, e.g. 5 for 5%%.")
    parser.add_argument("--run-bench", action="store_true", help="Run automated route-convergence and throughput/loss benchmarks.")
    parser.add_argument("--bench-src", default=topology["video_source"], help="Source station name used by the benchmark sender.")
    parser.add_argument("--bench-dest-ip", default=topology["video_dest_ip"], help="Destination IP used by the benchmark sender.")
    parser.add_argument("--bench-data-port", type=int, default=BENCH_DATA_PORT, help="UDP data port used by overlay_bench.py.")
    parser.add_argument("--bench-count", type=int, default=1000, help="Number of packets to send in throughput benchmark.")
    parser.add_argument("--bench-payload-size", type=int, default=1000, help="Payload size in bytes per benchmark packet.")
    parser.add_argument("--bench-interval-ms", type=float, default=0.0, help="Inter-packet delay in milliseconds for throughput benchmark.")
    parser.add_argument("--bench-report-timeout-sec", type=float, default=30.0, help="Timeout waiting for the destination throughput result file.")
    return parser.parse_args()


def run_cmd(node, command: str) -> str:
    return node.cmd(command).strip()


def send_control(node, command: str, timeout_sec: float = 3.0, port: int = 5100) -> str:
    script = (
        "import socket; "
        "sock=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
        f"sock.settimeout({timeout_sec}); "
        f"sock.sendto({command!r}.encode('utf-8'), ('127.0.0.1', {port})); "
        "data,_=sock.recvfrom(8192); "
        "print(data.decode('utf-8', 'ignore'))"
    )
    return run_cmd(node, f"python3 -c {shlex.quote(script)}")


def parse_key_value_lines(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def start_olsr(node, repo_root: Path) -> None:
    repo_text = shlex.quote(str(repo_root))
    src_text = shlex.quote(str(repo_root / "src"))
    log_dir = repo_root / "logs" / "mininet_wifi"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_text = shlex.quote(str(log_dir / f"{node.name}-olsr.log"))
    cmd = (
        f"cd {repo_text} && "
        f"PYTHONPATH={src_text} "
        f"nohup python3 -u src/olsr_main.py {node.IP()} > {log_text} 2>&1 &"
    )
    node.cmd(cmd)


def stop_olsr(node) -> None:
    node.cmd("pkill -f 'src/olsr_main.py' >/dev/null 2>&1 || true")


def start_video_forwarder(
    node,
    node_ip: str,
    repo_root: Path,
    output_dir: Path | None = None,
    data_port: int = VIDEO_DATA_PORT,
) -> None:
    repo_text = shlex.quote(str(repo_root))
    src_text = shlex.quote(str(repo_root / "src"))
    log_dir = repo_root / "logs" / "mininet_wifi"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_text = shlex.quote(str(log_dir / f"{node.name}-video_forwarder.log"))
    cmd_parts = [
        f"cd {repo_text}",
        f"PYTHONPATH={src_text} nohup python3 src/video_forwarder.py --node-ip {node_ip} --data-port {int(data_port)}",
    ]
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        cmd_parts[-1] += f" --output-dir {shlex.quote(str(output_dir))}"
    cmd_parts[-1] += f" --log-file {log_text} > /dev/null 2>&1 &"
    node.cmd(" && ".join(cmd_parts))


def stop_video_forwarder(node) -> None:
    node.cmd("pkill -f 'src/video_forwarder.py --node-ip' >/dev/null 2>&1 || true")


def start_overlay_bench_daemon(node, node_ip: str, repo_root: Path, data_port: int = BENCH_DATA_PORT) -> None:
    repo_text = shlex.quote(str(repo_root))
    src_text = shlex.quote(str(repo_root / "src"))
    log_dir = repo_root / "logs" / "mininet_wifi"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_text = shlex.quote(str(log_dir / f"{node.name}-overlay_bench.log"))
    cmd = (
        f"cd {repo_text} && "
        f"PYTHONPATH={src_text} "
        f"nohup python3 src/overlay_bench.py daemon --node-ip {node_ip} --data-port {int(data_port)} "
        f"--quiet --log-file {log_text} > /dev/null 2>&1 &"
    )
    node.cmd(cmd)


def stop_overlay_bench_daemon(node) -> None:
    node.cmd("pkill -f 'src/overlay_bench.py daemon' >/dev/null 2>&1 || true")


def cleanup_overlay_bench_results(repo_root: Path) -> None:
    results_dir = repo_root / "logs" / "overlay_bench_results"
    if not results_dir.exists():
        return
    for path in results_dir.glob("*.json"):
        path.unlink()
    for path in results_dir.glob("*.json.tmp"):
        path.unlink()


def source_ip_of(topology: dict, station_name: str) -> str:
    for item in topology["stations"]:
        if item["name"] == station_name:
            return str(item["ip"]).split("/", 1)[0]
    raise KeyError(f"unknown station in topology: {station_name}")


def station_name_of_ip(topology: dict, target_ip: str) -> str:
    for item in topology["stations"]:
        station_ip = str(item["ip"]).split("/", 1)[0]
        if station_ip == target_ip:
            return item["name"]
    raise KeyError(f"unknown destination ip in topology: {target_ip}")


def resolve_overlay_path(stations_by_name: dict[str, object], topology: dict, source_name: str, dest_ip: str) -> list[str]:
    source_ip = source_ip_of(topology, source_name)
    path = [source_ip]
    current_name = source_name
    current_ip = source_ip
    visited = {source_ip}

    while current_ip != dest_ip:
        detail = send_control(stations_by_name[current_name], f"SHOW_ROUTE_DETAIL:{dest_ip}")
        fields = parse_key_value_lines(detail)
        next_hop_ip = fields.get("next_hop_ip", "")
        valid = fields.get("valid", "").lower() == "true"
        state = fields.get("state", "")
        if (not next_hop_ip) or (not valid) or state != "VALID":
            raise RuntimeError(f"path resolution failed at {current_name}: {detail}")
        if next_hop_ip in visited:
            raise RuntimeError(f"path resolution loop detected at next_hop={next_hop_ip}")
        path.append(next_hop_ip)
        visited.add(next_hop_ip)
        current_ip = next_hop_ip
        if current_ip == dest_ip:
            break
        current_name = station_name_of_ip(topology, current_ip)

    return path


def topology_edges(topology: dict) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for node_name, neighbors in topology["edges"].items():
        for neighbor_name in neighbors:
            pairs.add(tuple(sorted((node_name, neighbor_name))))
    return sorted(pairs)


def print_underlay_checks(stations_by_name: dict[str, object], topology: dict) -> None:
    for source_name, target_name in topology_edges(topology):
        source = stations_by_name[source_name]
        target_ip = source_ip_of(topology, target_name)
        info(f"\n=== underlay check: {source_name} -> {target_ip} ===\n")
        info(run_cmd(source, f"ping -c 1 -W 1 {target_ip}") + "\n")


def print_node_state(node) -> None:
    info(f"\n=== {node.name} neighbors ===\n")
    info(send_control(node, "SHOW_NEIGHBORS") + "\n")
    info(f"=== {node.name} routes ===\n")
    info(send_control(node, "SHOW_ROUTE") + "\n")


def apply_link_loss(stations, loss_percent: float) -> None:
    if loss_percent <= 0:
        return
    info(f"*** Applying tc netem loss={loss_percent:.2f}% on sta wlan interfaces\n")
    for sta in stations:
        intf = f"{sta.name}-wlan0"
        sta.cmd(f"tc qdisc replace dev {intf} root netem loss {loss_percent:.2f}%")
        qdisc_state = run_cmd(sta, f"tc qdisc show dev {intf}")
        info(f"[{sta.name}] {qdisc_state}\n")


def build_topology(topology: dict):
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)
    radio_range = float(topology["radio_range"])

    stations = []
    for item in topology["stations"]:
        station = net.addStation(
            item["name"],
            ip=item["ip"],
            position=f"{item['x']},{item['y']},0",
            range=radio_range,
        )
        stations.append(station)

    net.setPropagationModel(
        model=topology.get("propagation_model", "logDistance"),
        exp=float(topology.get("propagation_exp", 4.0)),
    )
    net.configureWifiNodes()

    for sta in stations:
        net.addLink(
            sta,
            cls=adhoc,
            intf=f"{sta.name}-wlan0",
            ssid=topology.get("ssid", "olsr-adhoc"),
            mode=topology.get("mode", "g"),
            channel=int(topology.get("channel", 1)),
        )
    return net, tuple(stations)


def sha256sum(node, path: Path) -> str:
    return run_cmd(node, f"sha256sum {shlex.quote(str(path))}").split()[0]


def automated_file_transfer(
    repo_root: Path,
    stations,
    topology: dict,
    video_file: Path,
    dest_ip: str,
    data_port: int,
    chunk_size: int,
) -> None:
    source_name = topology["video_source"]
    dest_name = station_name_of_ip(topology, dest_ip)
    stations_by_name = {node.name: node for node in stations}
    source_node = stations_by_name[source_name]
    received_dir = repo_root / "logs" / "received_videos"
    received_dir.mkdir(parents=True, exist_ok=True)
    expected_output = received_dir / video_file.name

    for cleanup_path in (expected_output, expected_output.with_suffix(expected_output.suffix + ".part")):
        if cleanup_path.exists():
            cleanup_path.unlink()

    info("*** Starting video_forwarder relay/receiver processes\n")
    for node in stations:
        if node.name == source_name:
            continue
        node_ip = source_ip_of(topology, node.name)
        if node.name == dest_name:
            start_video_forwarder(node, node_ip, repo_root, output_dir=received_dir, data_port=data_port)
        else:
            start_video_forwarder(node, node_ip, repo_root, data_port=data_port)
    time.sleep(1.0)

    info(f"*** Sending video file from {source_name} to {dest_name}\n")
    send_cmd = (
        f"cd {shlex.quote(str(repo_root))} && "
        f"PYTHONPATH={shlex.quote(str(repo_root / 'src'))} "
        f"python3 src/video_forwarder.py --node-ip {source_ip_of(topology, source_name)} "
        f"--data-port {int(data_port)} --send-file {shlex.quote(str(video_file))} "
        f"--dest-ip {dest_ip} --chunk-size {int(chunk_size)} "
        f"--log-file {shlex.quote(str(repo_root / 'logs' / 'mininet_wifi' / f'{source_name}-video_forwarder.log'))} "
        "--exit-after-send"
    )
    sender_output = run_cmd(source_node, send_cmd)
    info(sender_output + "\n")

    for sta in stations:
        info(f"\n=== {sta.name} routes after file transfer ===\n")
        info(send_control(sta, "SHOW_ROUTE") + "\n")

    if not expected_output.exists():
        raise RuntimeError(f"received file not found: {expected_output}")

    src_hash = sha256sum(source_node, video_file)
    dst_hash = sha256sum(stations_by_name[dest_name], expected_output)
    info(
        f"\n=== file verification ===\nsource={video_file}\ndest={expected_output}\n"
        f"sha256_src={src_hash}\nsha256_dst={dst_hash}\n"
    )
    if src_hash != dst_hash:
        raise RuntimeError("sha256 mismatch after file transfer")

    info("*** One-click file transfer succeeded\n")


def run_overlay_bench(
    repo_root: Path,
    stations,
    topology: dict,
    source_name: str,
    dest_ip: str,
    data_port: int,
    count: int,
    payload_size: int,
    interval_ms: float,
    report_timeout_sec: float,
) -> tuple[dict, dict, list[str]]:
    stations_by_name = {node.name: node for node in stations}
    source_node = stations_by_name[source_name]

    info("*** Cleaning previous overlay_bench daemons and result files\n")
    for node in stations:
        stop_overlay_bench_daemon(node)
    cleanup_overlay_bench_results(repo_root)

    info("*** Starting overlay_bench daemons on relay/destination nodes\n")
    for node in stations:
        if node.name == source_name:
            continue
        start_overlay_bench_daemon(node, source_ip_of(topology, node.name), repo_root, data_port=data_port)
    time.sleep(1.0)

    route_cmd = (
        f"cd {shlex.quote(str(repo_root))} && "
        f"PYTHONPATH={shlex.quote(str(repo_root / 'src'))} "
        f"python3 src/overlay_bench.py route --node-ip {source_ip_of(topology, source_name)} "
        f"--dest-ip {dest_ip} --data-port {int(data_port)} --quiet --json"
    )
    route_result = json.loads(run_cmd(source_node, route_cmd))

    resolved_path = resolve_overlay_path(stations_by_name, topology, source_name, dest_ip)

    throughput_cmd = (
        f"cd {shlex.quote(str(repo_root))} && "
        f"PYTHONPATH={shlex.quote(str(repo_root / 'src'))} "
        f"python3 src/overlay_bench.py throughput --node-ip {source_ip_of(topology, source_name)} "
        f"--dest-ip {dest_ip} --data-port {int(data_port)} --count {int(count)} "
        f"--payload-size {int(payload_size)} --interval-ms {float(interval_ms)} "
        f"--report-timeout-sec {float(report_timeout_sec)} "
        f"--path {shlex.quote(','.join(resolved_path))} "
        f"--quiet --json"
    )
    throughput_result = json.loads(run_cmd(source_node, throughput_cmd))
    return route_result, throughput_result, resolved_path


def automated_bench(
    repo_root: Path,
    stations,
    topology: dict,
    source_name: str,
    dest_ip: str,
    data_port: int,
    count: int,
    payload_size: int,
    interval_ms: float,
    report_timeout_sec: float,
) -> None:
    route_result, throughput_result, resolved_path = run_overlay_bench(
        repo_root=repo_root,
        stations=stations,
        topology=topology,
        source_name=source_name,
        dest_ip=dest_ip,
        data_port=data_port,
        count=count,
        payload_size=payload_size,
        interval_ms=interval_ms,
        report_timeout_sec=report_timeout_sec,
    )
    info("\n=== route convergence benchmark ===\n")
    info(json.dumps(route_result, ensure_ascii=False, indent=2) + "\n")
    info("\n=== resolved overlay path ===\n")
    info(" -> ".join(resolved_path) + "\n")
    info("\n=== throughput/loss benchmark ===\n")
    info(json.dumps(throughput_result, ensure_ascii=False, indent=2) + "\n")
    result_path = throughput_result.get("result_path")
    if result_path:
        info(f"throughput_result_file={result_path}\n")


def main() -> int:
    topology = load_topology()
    args = parse_args(topology)

    if os.geteuid() != 0:
        print("This script must be run with sudo/root.", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parents[1]
    video_file = Path(args.video_file)
    if not video_file.is_absolute():
        video_file = repo_root / video_file
    video_file = video_file.resolve()

    if (not args.skip_file_transfer) and (not video_file.is_file()):
        print(f"Video file not found: {video_file}", file=sys.stderr)
        return 1

    net, stations = build_topology(topology)

    try:
        info("*** Building Mininet-WiFi network\n")
        net.build()

        apply_link_loss(stations, args.link_loss)

        info("*** Starting OLSR node processes inside station namespaces\n")
        for sta in stations:
            start_olsr(sta, repo_root)

        time.sleep(1.0)

        if args.run_bench:
            automated_bench(
                repo_root=repo_root,
                stations=stations,
                topology=topology,
                source_name=args.bench_src,
                dest_ip=args.bench_dest_ip,
                data_port=args.bench_data_port,
                count=args.bench_count,
                payload_size=args.bench_payload_size,
                interval_ms=args.bench_interval_ms,
                report_timeout_sec=args.bench_report_timeout_sec,
            )

        info(f"*** Waiting {args.neighbor_wait_sec:.1f}s before querying OLSR state\n")
        time.sleep(args.neighbor_wait_sec)

        stations_by_name = {node.name: node for node in stations}
        print_underlay_checks(stations_by_name, topology)

        for sta in stations:
            print_node_state(sta)

        if args.skip_file_transfer:
            info("*** File transfer step skipped by --skip-file-transfer\n")
        else:
            automated_file_transfer(
                repo_root=repo_root,
                stations=stations,
                topology=topology,
                video_file=video_file,
                dest_ip=args.video_dest_ip,
                data_port=args.video_data_port,
                chunk_size=args.video_chunk_size,
            )

        if args.cli:
            info("*** Starting Mininet-WiFi CLI\n")
            CLI(net)
        return 0
    finally:
        info("*** Stopping overlay_bench daemons\n")
        for sta in stations:
            stop_overlay_bench_daemon(sta)
        info("*** Stopping video_forwarder processes\n")
        for sta in stations:
            stop_video_forwarder(sta)
        info("*** Stopping OLSR node processes\n")
        for sta in stations:
            stop_olsr(sta)
        net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    raise SystemExit(main())
