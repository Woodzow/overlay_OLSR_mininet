import time

from dijkstra import dijkstra


class RoutingManager:
    def __init__(self, my_ip, neighbor_manager, topology_manager):
        self.my_ip = my_ip
        self.neighbor_manager = neighbor_manager
        self.topology_manager = topology_manager
        self.routing_table = {}
        self.route_first_seen = {}
        self.last_recalculated_at = None

    def _route_signature(self, route):
        return (
            route["dest"],
            route["next_hop_ip"],
            int(route["hop_count"]),
            bool(route["valid"]),
            route["state"],
        )

    def recalculate_routing_table(self):
        old_routes = self.routing_table
        graph = {self.my_ip: []}

        for neigh_ip, neigh_tuple in self.neighbor_manager.neighbors.items():
            if neigh_tuple.status == 1:
                graph[self.my_ip].append((neigh_ip, 1.0))
                graph.setdefault(neigh_ip, [])

        sym_neighbors = [ip for ip, neigh in self.neighbor_manager.neighbors.items() if neigh.status == 1]
        for (neigh_ip, two_hop_ip), _two_hop_tuple in self.neighbor_manager.two_hop_set.items():
            if neigh_ip not in sym_neighbors:
                continue
            graph.setdefault(neigh_ip, [])
            graph.setdefault(two_hop_ip, [])
            if (two_hop_ip, 1.0) not in graph[neigh_ip]:
                graph[neigh_ip].append((two_hop_ip, 1.0))

        for (dest_ip, last_ip), _topo_tuple in self.topology_manager.topology_set.items():
            graph.setdefault(last_ip, [])
            graph.setdefault(dest_ip, [])
            graph[last_ip].append((dest_ip, 1.0))

        dist, parent = dijkstra(graph, self.my_ip)

        now = time.time()
        new_routing_table = {}
        for target_node in dist:
            if target_node == self.my_ip or dist[target_node] == float("inf"):
                continue

            curr = target_node
            prev = None
            while curr != self.my_ip and curr is not None:
                prev = curr
                curr = parent[curr]

            if curr != self.my_ip or prev is None:
                continue

            if target_node not in self.route_first_seen:
                self.route_first_seen[target_node] = now

            previous = old_routes.get(target_node)
            unchanged = (
                previous is not None
                and previous.get("next_hop_ip") == prev
                and int(previous.get("hop_count", 0)) == int(dist[target_node])
                and previous.get("valid") is True
                and previous.get("state") == "VALID"
            )

            new_routing_table[target_node] = {
                "dest": target_node,
                "next_hop": prev,
                "next_hop_ip": prev,
                "hop_count": int(dist[target_node]),
                "distance": float(dist[target_node]),
                "state": "VALID",
                "valid": True,
                "first_seen_at": self.route_first_seen[target_node],
                "last_updated_at": previous.get("last_updated_at", now) if unchanged else now,
            }

        self.routing_table = new_routing_table
        self.last_recalculated_at = now
        old_signature = {dest: self._route_signature(route) for dest, route in old_routes.items()}
        new_signature = {dest: self._route_signature(route) for dest, route in new_routing_table.items()}
        if old_signature != new_signature:
            self.print_routing_table()

    def get_route(self, dest_ip):
        route = self.routing_table.get(dest_ip)
        if route is None:
            return None
        return dict(route)

    def get_routes(self):
        return {
            dest: dict(route)
            for dest, route in sorted(self.routing_table.items(), key=lambda item: item[0])
        }

    def format_routing_table(self):
        lines = [
            "Destination     | Next Hop        | Hop Count",
            "=============================================",
        ]
        routes = self.get_routes()
        for route in routes.values():
            if not route.get("valid"):
                continue
            lines.append(
                f"{route['dest']:<15} | {route['next_hop_ip']:<15} | {int(route['hop_count'])}"
            )
        if len(lines) == 2:
            lines.append("(empty)")
        return "\n".join(lines)

    def print_routing_table(self):
        print("\n=== Routing Table Updated ===")
        print(self.format_routing_table())
        print("=" * 45 + "\n")
