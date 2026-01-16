# 引入你之前上传的 dijkstra 模块
from dijkstra import dijkstra

class RoutingManager:
    def __init__(self, my_ip, neighbor_manager, topology_manager):
        self.my_ip = my_ip
        self.neighbor_manager = neighbor_manager
        self.topology_manager = topology_manager
        
        # 路由表: { dest_ip: next_hop_ip }
        # 或者更详细: { dest_ip: {'next_hop': ip, 'dist': n} }
        self.routing_table = {}

    def recalculate_routing_table(self):
        """
        核心函数：将 OLSR 数据转换为图，计算 Dijkstra，更新路由表
        """
        # --- 第一步：构建图 (Graph Construction) ---
        # 格式: {node: [(neighbor, weight), ...]}
        graph = {}
        
        # 1.1 初始化：确保自己在图中
        graph[self.my_ip] = []

        # 1.2 添加本地对称邻居 (My_IP -> Neighbor)
        # 来源: NeighborManager (只取 SYM_NEIGH)
        for neigh_ip, neigh_tuple in self.neighbor_manager.neighbors.items():
            if neigh_tuple.status == 1: # SYM
                # 添加边: 我 -> 邻居 (权重 1)
                graph[self.my_ip].append((neigh_ip, 1.0))
                # 确保邻居也在 graph 键中，防止 KeyError
                if neigh_ip not in graph:
                    graph[neigh_ip] = []
        

        # =================【新增部分开始】=================
        # 1.4 (新增) 添加二跳邻居信息 (Neighbor -> 2-Hop Node)
        # 来源: NeighborManager (通过 HELLO 消息获得)
        # 只有当中间节点是我们的对称邻居时，这条路径才有效
        sym_neighbors = [ip for ip, n in self.neighbor_manager.neighbors.items() if n.status == 1]
        
        for (neigh_ip, two_hop_ip), two_hop_tuple in self.neighbor_manager.two_hop_set.items():
            # 只有通过对称邻居到达的二跳才算数
            if neigh_ip in sym_neighbors:
                # 确保节点在图中初始化
                if neigh_ip not in graph: graph[neigh_ip] = []
                if two_hop_ip not in graph: graph[two_hop_ip] = []
                
                # 添加边: Neighbor -> 2-Hop (权重 1)
                # 检查是否已存在边，避免重复添加
                if (two_hop_ip, 1.0) not in graph[neigh_ip]:
                    graph[neigh_ip].append((two_hop_ip, 1.0))
        # =================【新增部分结束】=================


        # 1.3 添加全局拓扑链路 (Last_IP -> Dest_IP)
        # 来源: TopologyManager
        for (dest_ip, last_ip), topo_tuple in self.topology_manager.topology_set.items():
            # 确保节点存在于图中
            if last_ip not in graph:
                graph[last_ip] = []
            if dest_ip not in graph:
                graph[dest_ip] = []
            
            # 添加有向边: Last -> Dest
            # 注意：OLSR 拓扑是单向宣告的
            graph[last_ip].append((dest_ip, 1.0))

        # --- 第二步：运行 Dijkstra 算法 ---
        # dist: 到各点的距离
        # parent: 最短路径树 (用于回溯路径)
        dist, parent = dijkstra(graph, self.my_ip)

        # --- 第三步：生成路由表 (Routing Table Generation) ---
        new_routing_table = {}
        
        # 遍历所有可达的目标节点
        for target_node in dist:
            if target_node == self.my_ip or dist[target_node] == float('inf'):
                continue
            
            # 回溯找到下一跳 (Next Hop)
            # 我们需要找到路径: My_IP -> Next_Hop -> ... -> Target_Node
            
            curr = target_node
            prev = None
            
            # 从目标倒推回源头
            while curr != self.my_ip and curr is not None:
                prev = curr
                curr = parent[curr]
            
            # curr 应该是 my_ip, prev 就是 next_hop
            if curr == self.my_ip and prev is not None:
                new_routing_table[target_node] = {
                    'next_hop': prev,
                    'distance': dist[target_node]
                }

        # 更新并打印
        self.routing_table = new_routing_table
        self.print_routing_table()

    def print_routing_table(self):
        print("\n=== 路由表更新 (Routing Table) ===")
        print(f"{'Destination':<16} | {'Next Hop':<16} | {'Distance'}")
        print("-" * 46)
        for dest, info in self.routing_table.items():
            print(f"{dest:<16} | {info['next_hop']:<16} | {info['distance']}")
        print("=" * 46 + "\n")