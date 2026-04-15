#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

from mininet.log import info, setLogLevel

from mininet_wifi_complex_12sta import (
    BENCH_DATA_PORT,
    apply_link_loss,
    build_topology,
    load_topology,
    run_overlay_bench,
    source_ip_of,
    start_olsr,
    stop_olsr,
    stop_overlay_bench_daemon,
    stop_video_forwarder,
)


def parse_args(topology: dict) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sweep tc netem loss on the 12-station OLSR Mininet-WiFi topology."
    )
    parser.add_argument("--bench-src", default=topology["video_source"], help="Benchmark source station name.")
    parser.add_argument("--bench-dest-ip", default=topology["video_dest_ip"], help="Benchmark destination IP.")
    parser.add_argument("--bench-data-port", type=int, default=BENCH_DATA_PORT, help="UDP port used by overlay_bench.py.")
    parser.add_argument("--bench-count", type=int, default=300, help="Number of packets sent in each throughput run.")
    parser.add_argument("--bench-payload-size", type=int, default=512, help="Payload size in bytes per packet.")
    parser.add_argument("--bench-interval-ms", type=float, default=2.0, help="Inter-packet delay in milliseconds.")
    parser.add_argument(
        "--bench-report-timeout-sec",
        type=float,
        default=30.0,
        help="Timeout waiting for the destination throughput result file.",
    )
    parser.add_argument(
        "--olsr-start-wait-sec",
        type=float,
        default=1.0,
        help="Wait after starting all OLSR processes before running the benchmark.",
    )
    parser.add_argument("--loss-start", type=int, default=1, help="Start loss percent for the sweep.")
    parser.add_argument("--loss-end", type=int, default=10, help="End loss percent for the sweep.")
    parser.add_argument("--loss-step", type=int, default=1, help="Loss percent increment for the sweep.")
    parser.add_argument(
        "--output-dir",
        default="logs/loss_sweep_12sta",
        help="Directory used to store the aggregated JSON/CSV results.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Record the failed loss point and continue the sweep instead of stopping immediately.",
    )
    return parser.parse_args()


def build_loss_points(start: int, end: int, step: int) -> list[int]:
    if step <= 0:
        raise ValueError("--loss-step must be greater than 0")
    if end < start:
        raise ValueError("--loss-end must be greater than or equal to --loss-start")
    return list(range(int(start), int(end) + 1, int(step)))


def cleanup_runtime(stations, net) -> None:
    if stations is not None:
        info("*** Stopping overlay_bench daemons\n")
        for sta in stations:
            stop_overlay_bench_daemon(sta)
        info("*** Stopping video_forwarder processes\n")
        for sta in stations:
            stop_video_forwarder(sta)
        info("*** Stopping OLSR node processes\n")
        for sta in stations:
            stop_olsr(sta)
    if net is not None:
        net.stop()


def write_per_loss_result(output_dir: Path, loss_percent: int, payload: dict) -> None:
    target = output_dir / f"loss_{loss_percent:02d}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_summary_files(output_dir: Path, summary: dict) -> None:
    json_path = output_dir / "summary.json"
    csv_path = output_dir / "summary.csv"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "loss_percent",
        "status",
        "startup_route_convergence_sec",
        "route_setup_sec",
        "hop_count",
        "path",
        "sent_packets",
        "received_packets",
        "lost_packets",
        "loss_rate",
        "pdr",
        "goodput_mbps",
        "offered_load_mbps",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in summary["results"]:
            writer.writerow(
                {
                    "loss_percent": item.get("loss_percent"),
                    "status": item.get("status"),
                    "startup_route_convergence_sec": item.get("startup_route_convergence_sec"),
                    "route_setup_sec": item.get("route_setup_sec"),
                    "hop_count": item.get("hop_count"),
                    "path": " -> ".join(item.get("path", [])),
                    "sent_packets": item.get("sent_packets"),
                    "received_packets": item.get("received_packets"),
                    "lost_packets": item.get("lost_packets"),
                    "loss_rate": item.get("loss_rate"),
                    "pdr": item.get("pdr"),
                    "goodput_mbps": item.get("goodput_mbps"),
                    "offered_load_mbps": item.get("offered_load_mbps"),
                    "error": item.get("error", ""),
                }
            )


def run_single_loss_case(
    repo_root: Path,
    topology: dict,
    args: argparse.Namespace,
    loss_percent: int,
) -> dict:
    net = None
    stations = None
    try:
        net, stations = build_topology(topology)
        info(f"*** Building Mininet-WiFi network for loss={loss_percent}%\n")
        net.build()

        apply_link_loss(stations, float(loss_percent))

        info("*** Starting OLSR node processes inside station namespaces\n")
        for sta in stations:
            start_olsr(sta, repo_root)

        time.sleep(float(args.olsr_start_wait_sec))

        route_result, throughput_result, resolved_path = run_overlay_bench(
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

        return {
            "loss_percent": loss_percent,
            "status": "ok",
            "src_ip": source_ip_of(topology, args.bench_src),
            "dest_ip": args.bench_dest_ip,
            "path": resolved_path,
            "startup_route_convergence_sec": route_result.get("startup_route_convergence_sec"),
            "route_setup_sec": route_result.get("route_setup_sec"),
            "hop_count": throughput_result.get("hop_count"),
            "sent_packets": throughput_result.get("sent_packets"),
            "received_packets": throughput_result.get("received_packets"),
            "lost_packets": throughput_result.get("lost_packets"),
            "loss_rate": throughput_result.get("loss_rate"),
            "pdr": throughput_result.get("pdr"),
            "offered_load_mbps": throughput_result.get("offered_load_mbps"),
            "goodput_mbps": throughput_result.get("goodput_mbps"),
            "route_result": route_result,
            "throughput_result": throughput_result,
        }
    finally:
        cleanup_runtime(stations, net)


def main() -> int:
    topology = load_topology()
    args = parse_args(topology)

    if os.geteuid() != 0:
        print("This script must be run with sudo/root.", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parents[1]
    output_dir = repo_root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    loss_points = build_loss_points(args.loss_start, args.loss_end, args.loss_step)
    summary = {
        "topology_file": str((repo_root / "configs" / "mininet_wifi_complex_12sta" / "topology.json").resolve()),
        "bench_src": args.bench_src,
        "bench_dest_ip": args.bench_dest_ip,
        "bench_data_port": args.bench_data_port,
        "bench_count": args.bench_count,
        "bench_payload_size": args.bench_payload_size,
        "bench_interval_ms": args.bench_interval_ms,
        "bench_report_timeout_sec": args.bench_report_timeout_sec,
        "loss_points": loss_points,
        "results": [],
    }

    for loss_percent in loss_points:
        info(f"\n=== loss sweep case: {loss_percent}% ===\n")
        started_at = time.time()
        try:
            result = run_single_loss_case(repo_root, topology, args, loss_percent)
            result["wall_clock_sec"] = round(time.time() - started_at, 3)
            summary["results"].append(result)
            write_per_loss_result(output_dir, loss_percent, result)
            info(
                "summary "
                f"loss={loss_percent}% path={' -> '.join(result['path'])} "
                f"pdr={result['pdr']} loss_rate={result['loss_rate']} "
                f"goodput_mbps={result['goodput_mbps']}\n"
            )
        except Exception as exc:
            error_result = {
                "loss_percent": loss_percent,
                "status": "error",
                "src_ip": source_ip_of(topology, args.bench_src),
                "dest_ip": args.bench_dest_ip,
                "error": str(exc),
                "wall_clock_sec": round(time.time() - started_at, 3),
            }
            summary["results"].append(error_result)
            write_per_loss_result(output_dir, loss_percent, error_result)
            info(f"case failed loss={loss_percent}% error={exc}\n")
            if not args.continue_on_error:
                write_summary_files(output_dir, summary)
                raise

        write_summary_files(output_dir, summary)

    info("\n=== loss sweep finished ===\n")
    info(f"summary_json={output_dir / 'summary.json'}\n")
    info(f"summary_csv={output_dir / 'summary.csv'}\n")
    return 0


if __name__ == "__main__":
    setLogLevel("info")
    raise SystemExit(main())
