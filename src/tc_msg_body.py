import struct
import socket

def create_tc_body(ansn, advertised_neighbors):
    """
    构造 TC 消息体 (Pack)
    :param ansn: Advertised Neighbor Sequence Number (int, 0-65535)
    :param advertised_neighbors: list of neighbor IP strings
    """
    # 1. 固定头部: ANSN (2B) + Reserved (2B)
    # !HH 代表两个 unsigned short (大端序)
    fixed_part = struct.pack('!HH', ansn, 0)
    
    # 2. 邻居列表部分
    neigh_part = b''
    for ip_str in advertised_neighbors:
        try:
            # 将字符串 IP 转为 4 字节二进制
            neigh_part += socket.inet_aton(ip_str)
        except OSError:
            print(f"[TC Pack Error] Invalid IP: {ip_str}")
            
    return fixed_part + neigh_part

def parse_tc_body(tc_body_data):
    """
    解析 TC 消息体 (Unpack)
    :param tc_body_data: 接收到的二进制数据 (去除 Message Header 后的部分)
    :return: 字典 {'ansn': int, 'advertised_neighbors': [ip_str, ...]}
    """
    if len(tc_body_data) < 4:
        return None # 数据太短，连头部都不够

    # 1. 解析固定头部
    ansn, reserved = struct.unpack('!HH', tc_body_data[:4])
    
    # 2. 解析邻居列表
    advertised_neighbors = []
    cursor = 4
    
    # 循环读取剩余的所有 4 字节块
    while cursor + 4 <= len(tc_body_data):
        ip_bytes = tc_body_data[cursor : cursor+4]
        try:
            ip_str = socket.inet_ntoa(ip_bytes)
            advertised_neighbors.append(ip_str)
        except OSError:
            pass # 忽略非法 IP
        cursor += 4
        
    return {
        'ansn': ansn,
        'advertised_neighbors': advertised_neighbors
    }