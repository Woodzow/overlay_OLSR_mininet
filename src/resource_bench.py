from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

APP_NAME = "resource_bench"
APP_VERSION = 1
DEFAULT_ROLE_PATTERNS: dict[str, tuple[str, ...]] = {
    "olsr": ("olsr_main.py",),
    "video_forwarder": ("video_forwarder.py",),
    "overlay_bench": ("overlay_bench.py daemon", "overlay_bench.py latency", "overlay_bench.py throughput", "overlay_bench.py route"),
}
ROLE_ORDER = ("olsr", "video_forwarder", "overlay_bench")


@dataclass
class ProcessStats:
    role: str
    pid: int
    cpu_percent: float
    mem_percent: float
    rss_kb: int
    vsz_kb: int
    vmhwm_kb: int | None
    etime: str
    cmd: str


@dataclass
class RoleSummary:
    role: str
    process_count: int
    total_cpu_percent: float
    total_mem_percent: float
    total_rss_kb: int
    total_vsz_kb: int
    max_vmhwm_kb: int | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect CPU and memory usage for overlay processes in the current Mininet-WiFi node namespace.")
    parser.add_argument("--role", choices=("all",) + ROLE_ORDER, default="all", help="Limit output to one process role. Default: all.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of the text table.")
    parser.add_argument("--watch-sec", type=float, default=0.0, help="Refresh interval in seconds. Use with --samples for repeated snapshots.")
    parser.add_argument("--samples", type=int, default=1, help="Number of snapshots to capture when --watch-sec > 0. Default: 1.")
    return parser.parse_args()


def read_ps_rows() -> list[str]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,pcpu=,pmem=,rss=,vsz=,etime=,args="],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.rstrip() for line in result.stdout.splitlines() if line.strip()]


def read_proc_status_value(pid: int, key: str) -> int | None:
    status_path = Path("/proc") / str(pid) / "status"
    try:
        for line in status_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.startswith(f"{key}:"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
            return None
    except FileNotFoundError:
        return None
    return None


def resolve_role(command: str) -> str | None:
    for role in ROLE_ORDER:
        patterns = DEFAULT_ROLE_PATTERNS[role]
        if any(pattern in command for pattern in patterns):
            return role
    return None


def collect_process_stats(selected_role: str) -> list[ProcessStats]:
    stats: list[ProcessStats] = []
    for row in read_ps_rows():
        parts = row.split(None, 6)
        if len(parts) != 7:
            continue
        pid_s, cpu_s, mem_s, rss_s, vsz_s, etime, cmd = parts
        role = resolve_role(cmd)
        if role is None:
            continue
        if selected_role != "all" and role != selected_role:
            continue
        try:
            pid = int(pid_s)
            cpu_percent = float(cpu_s)
            mem_percent = float(mem_s)
            rss_kb = int(rss_s)
            vsz_kb = int(vsz_s)
        except ValueError:
            continue
        stats.append(
            ProcessStats(
                role=role,
                pid=pid,
                cpu_percent=cpu_percent,
                mem_percent=mem_percent,
                rss_kb=rss_kb,
                vsz_kb=vsz_kb,
                vmhwm_kb=read_proc_status_value(pid, "VmHWM"),
                etime=etime,
                cmd=cmd,
            )
        )
    stats.sort(key=lambda item: (ROLE_ORDER.index(item.role), item.pid))
    return stats


def summarize(stats: list[ProcessStats], selected_role: str) -> list[RoleSummary]:
    roles = ROLE_ORDER if selected_role == "all" else (selected_role,)
    summaries: list[RoleSummary] = []
    for role in roles:
        role_items = [item for item in stats if item.role == role]
        vmhwm_values = [item.vmhwm_kb for item in role_items if item.vmhwm_kb is not None]
        summaries.append(
            RoleSummary(
                role=role,
                process_count=len(role_items),
                total_cpu_percent=round(sum(item.cpu_percent for item in role_items), 3),
                total_mem_percent=round(sum(item.mem_percent for item in role_items), 3),
                total_rss_kb=sum(item.rss_kb for item in role_items),
                total_vsz_kb=sum(item.vsz_kb for item in role_items),
                max_vmhwm_kb=max(vmhwm_values) if vmhwm_values else None,
            )
        )
    return summaries


def format_kb(value: int | None) -> str:
    if value is None:
        return "-"
    mib = value / 1024.0
    return f"{value} KB ({mib:.2f} MiB)"


def print_text_report(stats: list[ProcessStats], summaries: list[RoleSummary]) -> None:
    print(f"{APP_NAME} v{APP_VERSION}")
    print("")
    print("=== Summary ===")
    print("Role            | Count | Total CPU% | Total MEM% | Total RSS            | Total VSZ            | Max VmHWM")
    print("---------------------------------------------------------------------------------------------------------")
    for summary in summaries:
        print(
            f"{summary.role:<15} | "
            f"{summary.process_count:>5} | "
            f"{summary.total_cpu_percent:>10.3f} | "
            f"{summary.total_mem_percent:>10.3f} | "
            f"{format_kb(summary.total_rss_kb):<20} | "
            f"{format_kb(summary.total_vsz_kb):<20} | "
            f"{format_kb(summary.max_vmhwm_kb)}"
        )

    print("")
    print("=== Processes ===")
    if not stats:
        print("(no matching overlay processes found)")
        return

    print("Role            | PID   | CPU%    | MEM%    | RSS                 | VSZ                 | VmHWM               | Elapsed  | Command")
    print("--------------------------------------------------------------------------------------------------------------------------------------------------")
    for item in stats:
        print(
            f"{item.role:<15} | "
            f"{item.pid:>5} | "
            f"{item.cpu_percent:>7.3f} | "
            f"{item.mem_percent:>7.3f} | "
            f"{format_kb(item.rss_kb):<19} | "
            f"{format_kb(item.vsz_kb):<19} | "
            f"{format_kb(item.vmhwm_kb):<19} | "
            f"{item.etime:<8} | "
            f"{item.cmd}"
        )


def snapshot(selected_role: str) -> dict[str, Any]:
    stats = collect_process_stats(selected_role)
    summaries = summarize(stats, selected_role)
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "timestamp_sec": time.time(),
        "role_filter": selected_role,
        "summary": [asdict(item) for item in summaries],
        "processes": [asdict(item) for item in stats],
    }


def main() -> int:
    args = parse_args()
    samples = max(1, int(args.samples))
    watch_sec = max(0.0, float(args.watch_sec))

    for index in range(samples):
        report = snapshot(args.role)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print_text_report(
                [ProcessStats(**item) for item in report["processes"]],
                [RoleSummary(**item) for item in report["summary"]],
            )
        if index + 1 < samples and watch_sec > 0:
            print("")
            time.sleep(watch_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
