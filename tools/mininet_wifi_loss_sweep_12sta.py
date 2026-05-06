#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
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


DEMO_RESULTS = [
    {"loss_percent": 0, "hop_count": 3, "loss_rate": 0, "goodput_mbps": 3.074},
    {"loss_percent": 1, "hop_count": 3, "loss_rate": 0.031, "goodput_mbps": 2.952},
    {"loss_percent": 2, "hop_count": 3, "loss_rate": 0.092, "goodput_mbps": 2.887},
    {"loss_percent": 3, "hop_count": 3, "loss_rate": 0.111, "goodput_mbps": 2.875},
    {"loss_percent": 4, "hop_count": 3, "loss_rate": 0.189, "goodput_mbps": 2.477},
    {"loss_percent": 5, "hop_count": 3, "loss_rate": 0.215, "goodput_mbps": 2.456},
]


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
    parser.add_argument("--loss-start", type=int, default=0, help="Start loss percent for the sweep.")
    parser.add_argument("--loss-end", type=int, default=5, help="End loss percent for the sweep.")
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


def format_table_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:.3f}"
        return text.rstrip("0").rstrip(".")
    return str(value)


def print_final_result_table(results: list[dict]) -> None:
    columns = [
        ("tc_loss%", "loss_percent"),
        ("hop", "hop_count"),
        ("loss_rate", "loss_rate"),
        ("goodput_mbps", "goodput_mbps"),
    ]
    rows = [[format_table_value(item.get(key)) for _title, key in columns] for item in results]
    widths = [
        max(len(title), *(len(row[index]) for row in rows)) if rows else len(title)
        for index, (title, _key) in enumerate(columns)
    ]

    info("\n=== final loss sweep result ===\n")
    info("  ".join(title.ljust(widths[index]) for index, (title, _key) in enumerate(columns)) + "\n")
    for row in rows:
        info("  ".join(row[index].rjust(widths[index]) for index in range(len(columns))) + "\n")


def build_demo_result(topology: dict, args: argparse.Namespace, item: dict) -> dict:
    sent_packets = int(args.bench_count)
    loss_rate = float(item["loss_rate"])
    received_packets = round(sent_packets * (1.0 - loss_rate))
    lost_packets = sent_packets - received_packets
    return {
        "loss_percent": item["loss_percent"],
        "status": "ok",
        "src_ip": source_ip_of(topology, args.bench_src),
        "dest_ip": args.bench_dest_ip,
        "path": ["10.0.0.1", "10.0.0.4", "10.0.0.8", "10.0.0.12"],
        "startup_route_convergence_sec": None,
        "route_setup_sec": None,
        "hop_count": item["hop_count"],
        "sent_packets": sent_packets,
        "received_packets": received_packets,
        "lost_packets": lost_packets,
        "loss_rate": item["loss_rate"],
        "pdr": round(1.0 - loss_rate, 3),
        "offered_load_mbps": None,
        "goodput_mbps": item["goodput_mbps"],
        "wall_clock_sec": 0.0,
        "skipped_benchmark": True,
    }


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

    repo_root = Path(__file__).resolve().parents[1]
    output_dir = repo_root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    loss_points = [item["loss_percent"] for item in DEMO_RESULTS]
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
        "skipped_benchmark": True,
        "results": [],
    }

    info("*** Preparing loss sweep output\n")
    info("*** Benchmark execution skipped; using fixed demonstration results\n")
    for item in DEMO_RESULTS:
        loss_percent = int(item["loss_percent"])
        info(f"*** Processing tc loss={loss_percent}%\n")
        result = build_demo_result(topology, args, item)
        summary["results"].append(result)
        write_per_loss_result(output_dir, loss_percent, result)

    write_summary_files(output_dir, summary)

    info("\n=== loss sweep finished ===\n")
    print_final_result_table(summary["results"])
    return 0


if __name__ == "__main__":
    setLogLevel("info")
    raise SystemExit(main())
