import struct
import socket
import math

# 常量定义 (基于 RFC 3626)
OLSR_C = 1.0 / 16.0  # 缩放因子 C = 0.0625 [cite: 1679]

def encode_mantissa(seconds):
    """
    将时间（秒）编码为 OLSR 的 8-bit 浮点格式 (Vtime/Htime)。
    算法参考 RFC 3626 Section 18.3 [cite: 1732-1737]
    格式: 高4位是 mantissa (a), 低4位是 exponent (b)
    公式: Value = C * (1 + a/16) * 2^bm
    """
    if seconds <= 0: return 0
    value = float(seconds)
    if value < OLSR_C: # 避免除零或极小值错误，设置下限
        return 0 # 最小表示值
    try:
        b = int(math.floor(math.log(value / OLSR_C, 2)))
    except ValueError: b = 0
    # 边界限制: b 必须在 0-15 之间
    if b < 0: b = 0
    if b > 15: b = 15
    a_float = 16 * (value / (OLSR_C * (2 ** b)) - 1)
    a = int(math.ceil(a_float))
    if a >= 16:
        b += 1
        a = 0   
    # 再次检查 b 的边界 (防止溢出)
    if b > 15:
        b = 15
        a = 15 # 最大值
    return (a << 4) | b

def decode_mantissa(encoded):
    """
    将 OLSR 8-bit 浮点格式解码为时间（秒）

    encoded: 0-255 的整数
    返回: 解码后的 seconds(float)
    """
    if encoded <= 0:
        return 0.0

    # 拆分 mantissa 和 exponent
    a = (encoded >> 4) & 0x0F  # 高 4 位
    b = encoded & 0x0F         # 低 4 位

    # 按 RFC 3626 Section 18.3 的公式解码
    value = OLSR_C * (1 + a / 16.0) * (2 ** b)

    return value



def create_message_header(msg_type, vtime_seconds, msg_body_len, originator_ip, ttl, hop_count, msg_seq_num):
    """
    封装 Message Header (12 字节)
    RFC 3626 Section 3.3.2 
    """
    # 1. 计算 Message Size:消息头长度(12字节)+消息体长度 [cite: 306]
    total_msg_size = 12 + msg_body_len
    # 2. 编码 Vtime [cite: 298]
    vtime_byte = encode_mantissa(vtime_seconds)
    # 3. 处理 IP 地址: 将字符串 "192.168.1.5" 转为 4字节二进制 [cite: 307]
    try:
        ip_bytes = socket.inet_aton(originator_ip)
    except OSError:
        print(f"Error: Invalid IP address {originator_ip}")
        return None

    # 4. 打包 (使用大端序 !)
    # B: unsigned char (1 byte) -> Msg Type, Vtime, TTL, Hop Count
    # H: unsigned short (2 bytes) -> Msg Size, Seq Num
    # 4s: char[4] (4 bytes) -> Originator Address
    
    # 结构: Type(1), Vtime(1), Size(2), Originator(4), TTL(1), Hop(1), Seq(2)
    header = struct.pack('!BBH4sBBH', 
                         msg_type,       # Message Type
                         vtime_byte,     # Vtime
                         total_msg_size, # Message Size
                         ip_bytes,       # Originator Address
                         ttl,            # Time To Live
                         hop_count,      # Hop Count
                         msg_seq_num     # Message Sequence Number
                         )
    return header

def create_packet_header(packet_body_len, packet_seq_num):
    """
    封装 Packet Header (4 字节)
    RFC 3626 Section 3.3.1 
    """
    # Packet Length: 包头长度(4字节) + 后面所有消息的长度 [cite: 281]
    total_packet_len = 4 + packet_body_len
    
    # 打包: Length(2), Seq(2)
    header = struct.pack('!HH', total_packet_len, packet_seq_num)
    return header

# 根据具体的linktype和neighbortype生成一个字节长度的link_code

def create_link_code(link_type, neighbor_type):
    """
    生成 HELLO 消息所需的 Link Code 字节。
    
    根据 RFC 3626 Section 6.1.1:
    - Bit 0-1: Link Type
    - Bit 2-3: Neighbor Type
    - Bit 4-7: Reserved (必须为 0)
    
    :param link_type: 链路类型 (0-3)
    :param neighbor_type: 邻居类型 (0-3)
    :return: 组合后的 1 字节整数
    """
    # 简单的边界检查，确保输入值不超过 2 bit (0-3)
    if not (0 <= link_type <= 3):
        raise ValueError(f"Invalid Link Type: {link_type}. Must be 0-3.")
    if not (0 <= neighbor_type <= 3):
        raise ValueError(f"Invalid Neighbor Type: {neighbor_type}. Must be 0-3.")

    # 位操作逻辑：
    # 1. 取 neighbor_type，左移 2 位 (例如 1 -> 100)
    # 2. 与 link_type 进行按位或运算
    return (neighbor_type << 2) | link_type

# ==========================================
# 模拟测试：生成一个完整的 UDP 包
# ==========================================
# if __name__ == "__main__":
#     # 假设配置
#     MY_IP = "192.168.1.100"  # 你的虚拟机 IP
#     MY_MSG_SEQ = 1           # 消息序列号，需维护自增
#     MY_PKT_SEQ = 100         # 包序列号，需维护自增
    
#     # --- 步骤 1: 准备 HELLO 消息体 (暂时为空) ---
#     # HELLO 消息体以后会包含 Link Code 等，现在假设是空的，但通常至少有 Reserved 等字段
#     # 根据 RFC 6.1，HELLO 至少有 Reserved(2字节), Htime(1字节), Willingness(1字节) [cite: 633]
#     # 这里我们先手动构造这最基础的 4 个字节，防止消息体为空
#     hello_body = struct.pack('!HBB', 0, encode_mantissa(2.0), 3) # Reserved=0, Htime=2s, Will=3
    
#     # --- 步骤 2: 生成 Message Header ---
#     # HELLO Message Type = 1 [cite: 1740]
#     # Vtime = 6.0 秒 (NEIGHB_HOLD_TIME) [cite: 1710]
#     # TTL = 1 (只传给直连邻居) [cite: 645]
#     msg_header = create_message_header(
#         msg_type=1, 
#         vtime_seconds=6.0, 
#         msg_body_len=len(hello_body), 
#         originator_ip=MY_IP, 
#         ttl=1, 
#         hop_count=0, 
#         msg_seq_num=MY_MSG_SEQ
#     )
    
#     full_message = msg_header + hello_body
    
#     # --- 步骤 3: 生成 Packet Header ---
#     packet_header = create_packet_header(
#         packet_body_len=len(full_message), 
#         packet_seq_num=MY_PKT_SEQ
#     )
    
#     final_udp_payload = packet_header + full_message
    
#     print(f"生成的 UDP 负载总长度: {len(final_udp_payload)} 字节")
#     print(f"十六进制内容: {final_udp_payload.hex()}")
    
#     vtime_encoded = encode_mantissa(6.0)
#     print(f"Vtime(6.0s) 编码结果: 0x{vtime_encoded:02x} (期望值通常接近 0x86)")