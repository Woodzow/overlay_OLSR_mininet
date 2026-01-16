import heapq #导入堆排序包，用于实现优先队列
from typing import Dict, List, Tuple, Any #导入提示变量类型的包，主要还是为了方便阅读代码
import networkx as nx
import matplotlib.pyplot as plt
INF = float('inf')
def dijkstra(graph: Dict[Any, List[Tuple[Any, float]]], source: Any):  #创建dijkstra算法求解函数，输入为图和源节点；其中图的表示方法为邻接表
    dist = {node: INF for node in graph} #利用字典推导式的方法初始化到各个节点的距离字典值权重为无穷大
    parent = {node: None for node in graph} #利用字典推导式的方法初始化各个节点的父节点为None，也就是没有父节点
    dist[source] = 0 #源节点到自己的距离为0
    heap = [(0, source)] #初始化堆，堆中存储的是距离和节点的元组，堆中的元组会自动按照第一个元素从小到大满足堆属性（父节点小于等于子节点）的前提下进行排序，但毫无疑问的是第一个节点一定是最小的
    while heap:
        d, u = heapq.heappop(heap) #弹出/移除堆中距离最小的节点
        if d != dist[u]:
            continue #如果弹出的距离不是当前节点的最短距离，说明该节点已经被更新过，跳过该节点
        for v, w in graph[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                parent[v] = u
                heapq.heappush(heap, (nd, v)) #将更新后的距离和（当前处理节点的邻居）节点重新加入堆中
    return dist, parent
def reconstruct_path(parent: Dict[Any, Any], source: Any, target: Any) -> List[Any]: #重建最短路径的函数，输入为父节点字典，源节点和目标节点，返回值的是列表，从尾到头重建路径
    path = []
    cur = target
    while cur is not None:
        path.append(cur) #从列表末尾添加元素
        if cur == source:
            break #中止条件，添加到了源节点
        cur = parent[cur] #更新当前节点为其父节点，依次从尾到头添加节点
    path.reverse() #调转列表
    return path
def draw_graph(graph, path=None): #绘制图的函数，输入为图和路径
    G = nx.DiGraph()
    plt.figure(figsize=(10, 8))
    plt.title("Graph with Shortest Path Highlighted")
    # Add edges with weights
    for u in graph:
        for v, w in graph[u]:
            G.add_edge(u, v, weight=w) #遍历邻接表，添加有向边和权重
    pos = nx.circular_layout(G, center=(0,0))  # layout for positioning 这里使用默认使用spring layout布局（seed=42），也可以使用circular_layout(环形，节点少的时候效果不错)、shell_layout、kamada_kawai_layout、Spectral Layout等布局
    # Draw nodes and edges
    nx.draw(G, pos, with_labels=True, node_color='skyblue', node_size=1500, font_size=12, arrowsize=20)
    # Draw edge weights
    edge_labels = nx.get_edge_attributes(G, 'weight')
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, font_color='black')
    # Highlight shortest path
    if path and len(path) > 1:
        path_edges = list(zip(path, path[1:]))
        nx.draw_networkx_edges(G, pos, edgelist=path_edges, edge_color='red', width=2.5)
    plt.show()
# if __name__ == "__main__":
#     graph = {
#         'uav1': [('uav2', 2), ('uav3', 5)],
#         'uav2': [('uav3', 3), ('uav4', 4)],
#         'uav3': [('uav4', 8)],
#         'uav4': [('uav5', 3)],
#         'uav5': []
#     }
#     src = 'uav1'
#     target = 'uav5'
#     dist, parent = dijkstra(graph, src)
#     path = reconstruct_path(parent, src, target)
#     print("Shortest distances from", src)
#     for node in sorted(graph):
#         print(f"  {node}: {dist[node]}")
#     if path:
#         print(f"\nShortest path {src} -> {target}: {' -> '.join(path)} (cost {dist[target]})")
#     else:
#         print(f"No path from {src} to {target}")
#     # Draw the graph and highlight shortest path
#     draw_graph(graph, path)