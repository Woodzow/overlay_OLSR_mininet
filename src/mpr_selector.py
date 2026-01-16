from constants import WILL_ALWAYS, WILL_NEVER

def select_mpr(candidates, coverage_map):
    """
    执行 RFC 3626 Section 8.3.1 的 MPR 选择算法
    
    :param candidates: 字典 {neighbor_ip: willingness}, 包含所有候选的对称 1跳邻居
    :param coverage_map: 字典 {neighbor_ip: set(2hop_ips)}, 每个邻居能覆盖的严格 2跳邻居集合
    :return: 集合 set(mpr_ips), 被选为 MPR 的节点 IP
    """
    mpr_set = set()
    
    # 1. 构建需要覆盖的目标集合 (Strict 2-Hop Neighbors) 
    # 这个集合其实在类neighbormanager里面也有，后续可以考虑能不能优化，这里不直接传这个集合还是因为不太方便操作，我们需要多次用到的是coverage_map
    #    它是所有邻居能覆盖的 2跳节点的并集
    strict_2hop_set = set()
    for covered_nodes in coverage_map.values():
        strict_2hop_set.update(covered_nodes)
        
    # 如果没有 2跳节点需要覆盖，直接返回空 (或者只返回 WILL_ALWAYS)
    if not strict_2hop_set:
        # 即使没有 2跳，WILL_ALWAYS 的邻居通常也建议保留在 MPR 中 (视具体实现而定，RFC 建议加上)，这里加上了的；
        for ip, will in candidates.items():
            if will == WILL_ALWAYS:
                mpr_set.add(ip)
        return mpr_set

    # 计算 D(y): 每个邻居在初始状态下的覆盖度 (Degree) 也就是一个邻居对应的二跳邻居有多少个
    # 用于后续的 Tie-breaking (打平时的判断依据)
    degree_map = {ip: len(covered) for ip, covered in coverage_map.items()}

    # --- 步骤 1: 必须选 Willingness = WILL_ALWAYS 的节点 ---
    for ip, will in candidates.items():
        if will == WILL_ALWAYS:
            mpr_set.add(ip)
            # 既然选了它，它覆盖的节点就标记为“已解决”
            strict_2hop_set -= coverage_map.get(ip, set())

    # --- 步骤 2: 选择“唯一路径”提供者 ---
    # 如果某个 2跳节点只能由某一个 1跳邻居覆盖，那这个 1跳邻居必须选
    # 注意：需循环处理，因为选入一个 MPR 后可能会影响后续判断，但在 RFC 基础版中通常一次扫描即可
    
    # 构建反向映射: 2hop_ip -> [能覆盖它的 1hop_ips]
    def build_reverse_map(current_targets):
        rev_map = {target: [] for target in current_targets}
        for n_ip, covered_nodes in coverage_map.items():
            # 只考虑还没被选为 MPR 的 candidates (优化)
            # 或者全部考虑也可以，反正已经选入的再选一次也没关系
            for target in current_targets:
                if target in covered_nodes:
                    rev_map[target].append(n_ip)
        return rev_map

    reverse_map = build_reverse_map(strict_2hop_set)
    
    # 遍历反向map字典
    for target_2hop, provider_list in reverse_map.items():
        if len(provider_list) == 1:
            sole_provider = provider_list[0]
            if sole_provider not in mpr_set:
                mpr_set.add(sole_provider)
                # 删除必须选的邻居节点对应的二跳邻居
                strict_2hop_set -= coverage_map.get(sole_provider, set())

    # --- 步骤 3: 贪婪算法 (覆盖剩余节点) ---
    while strict_2hop_set:
        best_candidate = None
        max_reachability = -1
        
        for n_ip, willingness in candidates.items():
            if n_ip in mpr_set:
                continue # 已经在集合里了，跳过
            
            if willingness == WILL_NEVER:
                continue # 永远不选
                
            # 计算 Reachability: 能覆盖多少 *目前尚未覆盖* 的节点
            # 这是一个动态值
            # python中的 & 运算就是取集合的交集， 这里记录了目前遍历到的ip节点所拥有的严格二跳邻居的个数
            current_reachability = len(coverage_map[n_ip] & strict_2hop_set)
            
            if current_reachability == 0:
                continue
                
            # 比较逻辑 (RFC 8.3.1)
            # 1. 覆盖数 (Reachability) 越大越好
            # 2. Willingness 越大越好
            # 3. 初始度数 (Degree) 越大越好
            
            is_better = False
            if best_candidate is None:
                is_better = True
            elif current_reachability > max_reachability:
                is_better = True
            elif current_reachability == max_reachability:
                # 打平，比 Willingness
                if willingness > candidates[best_candidate]:
                    is_better = True
                elif willingness == candidates[best_candidate]:
                    # 还打平，比 Degree
                    if degree_map[n_ip] > degree_map[best_candidate]:
                        is_better = True
            
            if is_better:
                max_reachability = current_reachability
                best_candidate = n_ip
        
        if best_candidate is not None:
            mpr_set.add(best_candidate)
            strict_2hop_set -= coverage_map[best_candidate]
        else:
            # 异常情况：还有节点没覆盖，但没有候选人能覆盖它了 (比如那个候选人是 WILL_NEVER)
            # 此时只能退出循环
            break
            
    # (可选) 步骤 4: 优化 (Optimization)
    # 尝试移除多余的 MPR (如果移除它，覆盖集依然不变)
    # RFC 建议按 Willingness 升序检查
    # 这里为了代码简洁暂略，基础版本不加也完全可以工作。
    
    # 这部分可以适当优化

    return mpr_set