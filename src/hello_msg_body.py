import struct
import socket
from pkt_msg_fmt import encode_mantissa,decode_mantissa

"""
本文件主要设计hello_body的打包和解包,也就是hello_info和hello_body的相互转换
hello_info的格式如下
{
        "htime_seconds": 134,       # 原始编码的 Htime
        "willingness": 3,       # 节点的意愿值
        "neighbor_groups": [          # 解析出的所有邻居列表
            (link_code, ["0.0.0.1", "0.0.0.2"]),
            (link_code, [ip_list]),
            ...
        ]
}
hello_body的格式则比较复杂  会在笔记中用图来表示
"""


#打包 HELLO msg body
def create_hello_body(hello_info):
    """
    构造 HELLO 消息体
    :param neighbor_groups: 列表，每个元素是一个元组 (link_code, [ip_list])
    """
    # 从字典中“解包”，变量名保持不变
    htime_seconds = hello_info["htime_seconds"]
    willingness = hello_info["willingness"]
    neighbor_groups = hello_info.get("neighbor_groups", []) 
    #value = some_dict.get(key, default) #如果key在some_dict中，返回value，否则返回default


    # 1. 固定头部 (4字节)
    # Reserved (2B) + Htime (1B) + Willingness (1B)
    # RFC 6.1: Reserved must be 0
    htime_byte = encode_mantissa(htime_seconds)
    fixed_part = struct.pack('!HBB', 0, htime_byte, willingness)
    
    link_messages_part = b''
    
    # 2. 遍历邻居组，打包每个 Link Message
    for link_code, ip_list_strings in neighbor_groups:
        # 计算当前 Link Message 的大小
        # Link Code(1) + Reserved(1) + Size(2) + N * IP(4)
        # linkcode = 0000+neighbortype+linktype 包含邻居节点类型的信息和链路类型的信息
        link_msg_size = 4 + len(ip_list_strings) * 4
        
        # 打包 Link Message Header
        # RFC 6.1: Link Code(1), Reserved(1), Link Message Size(2)
        lm_header = struct.pack('!BBH', link_code, 0, link_msg_size)#这里的link_code是一个整型，直接打包会自动转换
        
        # 打包所有 IP
        ips_bytes = b''
        for ip_str in ip_list_strings:
            ips_bytes += socket.inet_aton(ip_str)
            
        link_messages_part += lm_header + ips_bytes

    return fixed_part + link_messages_part #也就是hello_body

# 解包 HELLO msg body


def parse_hello_body(hello_body):
    """
    解析 HELLO 消息体，并将信息存储在结构化数据中返回。
    
    返回结构示例:
    {
        "htime_seconds": 134,       # 原始编码的 Htime
        "willingness": 3,       # 节点的意愿值
        "neighbor_groups": [          # 解析出的所有邻居列表
            (link_code, ["0.0.0.1", "0.0.0.2"]),
            (link_code, [ip_list]),
            ...
        ]
    }
    """
    # 检查长度：至少要有 Reserved(2) + Htime(1) + Willingness(1) = 4字节
    if len(hello_body) < 4:
        print("Error: HELLO body too short")
        return None
        
    # --- 1. 解析固定头部 ---
    # 使用 hello_body 而不是 body_data
    reserved, htime_byte, willingness = struct.unpack('!HBB', hello_body[:4])
    
    # 还原时间
    htime_seconds = decode_mantissa(htime_byte)
    
    # 初始化结果字典
    hello_info = {
        "htime_seconds": htime_seconds,
        "willingness": willingness,
        "neighbor_groups": []  # 存放 (link_code, [ip_list])
    }
    
    cursor = 4
    
    # --- 2. 循环解析 Link Messages ---
    while cursor < len(hello_body):
        # 确保剩余长度足够读取 Link Message Header (4字节)
        if len(hello_body) - cursor < 4:
            break
            
        # 读取 Link Message Header
        # Link Code(1) + Reserved(1) + Size(2)
        link_code, lm_reserved, lm_size = struct.unpack('!BBH', hello_body[cursor:cursor+4])
        
        # 边界检查
        if lm_size < 4:
            break

        # --- 3. 提取该组内的 IP 列表 ---
        current_ip_list = []
        
        # 计算当前 Link Message 的结束位置
        end_of_lm = cursor + lm_size
        ip_cursor = cursor + 4
        
        while ip_cursor + 4 <= end_of_lm:
            # 提取 IP
            ip_bytes = hello_body[ip_cursor : ip_cursor+4]
            ip_str = socket.inet_ntoa(ip_bytes)
            current_ip_list.append(ip_str)
            ip_cursor += 4
            
        # 保持元组结构 (link_code, [ip_list]) 并存入
        group_tuple = (link_code, current_ip_list)
        hello_info["neighbor_groups"].append(group_tuple)
            
        # 跳到下一个 Link Message
        cursor += lm_size
        
    return hello_info
