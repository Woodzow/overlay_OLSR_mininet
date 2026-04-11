from __future__ import annotations

import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from olsr_main import OLSRNode


def _is_valid_ipv4(text: str) -> bool:
    try:
        socket.inet_aton(text)
    except OSError:
        return False
    return True


def _show_route_detail(node: "OLSRNode", dest_ip: str) -> str:
    route = node.routing_manager.get_route(dest_ip)
    if route is None:
        return f"未找到路由：{dest_ip}"
    return (
        f"dest={route['dest']}\n"
        f"next_hop={route['next_hop']}\n"
        f"next_hop_ip={route['next_hop_ip']}\n"
        f"hop_count={route['hop_count']}\n"
        f"distance={route['distance']}\n"
        f"state={route['state']}\n"
        f"valid={route['valid']}\n"
        f"first_seen_at={route['first_seen_at']:.6f}\n"
        f"last_updated_at={route['last_updated_at']:.6f}\n"
        f"protocol_started_at={node.started_at:.6f}"
    )


def _show_neighbors(node: "OLSRNode") -> str:
    lines = [
        "Neighbor         | Symmetric | Willingness | SelectedMe",
        "=======================================================",
    ]
    neighbors = sorted(node.neighbor_manager.neighbors.items(), key=lambda item: item[0])
    for neighbor_ip, neighbor in neighbors:
        lines.append(
            f"{neighbor_ip:<16} | "
            f"{('yes' if neighbor.status == 1 else 'no'):<9} | "
            f"{neighbor.willingness:<11} | "
            f"{('yes' if neighbor_ip in node.neighbor_manager.mpr_selectors else 'no')}"
        )
    if len(lines) == 2:
        lines.append("(empty)")
    return "\n".join(lines)


def process_control_command(node: "OLSRNode", command_text: str) -> str:
    parts = command_text.strip().split(":", 1)
    op = parts[0].upper() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if op == "DISCOVER_ROUTE":
        if not _is_valid_ipv4(arg):
            return f"非法地址：{arg}"
        route = node.routing_manager.get_route(arg)
        if route is not None:
            return f"已有有效路由：dest={arg} next_hop={route['next_hop_ip']} hop_count={route['hop_count']}"
        node.routing_manager.recalculate_routing_table()
        route = node.routing_manager.get_route(arg)
        if route is not None:
            return f"路由已建立：dest={arg} next_hop={route['next_hop_ip']} hop_count={route['hop_count']}"
        return f"已触发 OLSR 路由检查：dest={arg}"

    if op == "SHOW_ROUTE":
        return node.routing_manager.format_routing_table()

    if op == "SHOW_ROUTE_DETAIL":
        if not _is_valid_ipv4(arg):
            return f"非法地址：{arg}"
        return _show_route_detail(node, arg)

    if op == "SHOW_NEIGHBORS":
        return _show_neighbors(node)

    if op == "SHOW_STATUS":
        last_recalculated_at = node.routing_manager.last_recalculated_at
        last_text = f"{last_recalculated_at:.6f}" if last_recalculated_at is not None else ""
        return (
            f"my_ip={node.my_ip}\n"
            f"udp_port={node.port}\n"
            f"control_port={node.control_port}\n"
            f"protocol_started_at={node.started_at:.6f}\n"
            f"route_count={len(node.routing_manager.routing_table)}\n"
            f"last_recalculated_at={last_text}"
        )

    if op == "HELP":
        return "支持命令: DISCOVER_ROUTE:<dest> | SHOW_ROUTE | SHOW_ROUTE_DETAIL:<dest> | SHOW_NEIGHBORS | SHOW_STATUS"

    return "未知命令"
