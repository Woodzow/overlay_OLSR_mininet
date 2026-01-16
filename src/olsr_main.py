'''
本文件为运行olsr应用层覆盖网络的主程序入口
'''
import time
import socket
import struct
import threading
import random

# --- 引入各个功能模块 ---
from link_sensing import LinkSet
from neigh_manager import NeighborManager  # 确保文件名匹配
from topology_manager import TopologyManager
from routing_manager import RoutingManager
from flooding_mpp import DuplicateSet

# --- 引入消息格式处理 ---
from pkt_msg_fmt import create_packet_header, create_message_header, decode_mantissa
from hello_msg_body import create_hello_body, parse_hello_body
from tc_msg_body import create_tc_body, parse_tc_body
from constants import *

class OLSRNode:
    def __init__(self, my_ip, port=5005):
        self.my_ip = my_ip
        self.port = port
        self.running = True
        
        # --- 1. 初始化网络接口 ---
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(('0.0.0.0', self.port))
        
        # --- 2. 初始化各个管理器 ---
        self.link_set = LinkSet()
        self.link_set.my_ip = my_ip
        
        self.neighbor_manager = NeighborManager(my_ip)
        
        self.topology_manager = TopologyManager(my_ip)
        
        self.routing_manager = RoutingManager(
            my_ip, 
            self.neighbor_manager, 
            self.topology_manager
        )
        
        self.duplicate_set = DuplicateSet()

        # ===【新增】初始化全局锁 ===
        self.lock = threading.Lock()

        # --- 3. 状态变量 ---
        self.pkt_seq_num = 0    # 包序列号
        self.msg_seq_num = 0    # 消息序列号
        self.ansn = 0           # TC 序列号 (Advertised Neighbor Sequence Number)

    def start(self):
        """启动 OLSR 节点"""
        print(f"[*] OLSR Node {self.my_ip} started on port {self.port}")
        
        # 启动后台线程
        threading.Thread(target=self.loop_hello, daemon=True).start()
        threading.Thread(target=self.loop_tc, daemon=True).start()
        threading.Thread(target=self.loop_cleanup, daemon=True).start()
        
        # 主线程进入接收循环
        self.receive_loop()

    # ==========================
    # 接收与分发 (Receive & Dispatch)
    # ==========================
    def receive_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                sender_ip = addr[0]
                if sender_ip == self.my_ip: continue # 忽略自己
                
                self.process_packet(data, sender_ip)
            except Exception as e:
                print(f"[Error] Receive: {e}")

    def process_packet(self, data, sender_ip):
        """解析 UDP 包并分发消息"""
        if len(data) < 4: return
        
        # 1. 解析包头
        pkt_len, pkt_seq = struct.unpack('!HH', data[:4])
        cursor = 4
        

        # 2. 遍历消息
        with self.lock:
            while cursor < len(data):
                if len(data) - cursor < 12: break
                
                # 解析消息头
                msg_head = data[cursor:cursor+12]
                msg_type, vtime, msg_size, orig_bytes, ttl, hop, msg_seq = \
                    struct.unpack('!BBH4sBBH', msg_head)
                
                orig_ip = socket.inet_ntoa(orig_bytes) #将字节流的发送者ip转换为字符串形式，这里便是直接获取我自己有哪些可以收到hello消息邻居的
                
                # =================【修改点：解码 vtime】=================
                validity_time = decode_mantissa(vtime)

                # 提取消息体
                body_start = cursor + 12
                body_end = cursor + msg_size
                if body_end > len(data): break
                msg_body_bytes = data[body_start:body_end]
                
                # --- 去重检查 ---
                if not self.duplicate_set.is_duplicate(orig_ip, msg_seq):
                    self.duplicate_set.record_message(orig_ip, msg_seq, time.time())
                    
                    # --- 分发处理 ---
                    if msg_type == HELLO_MESSAGE: # Type 1
                        # 解析为字典 hello_info
                        hello_info = parse_hello_body(msg_body_bytes)
                        if hello_info:
                            self.process_hello(sender_ip, hello_info, validity_time)
                            
                    elif msg_type == TC_MESSAGE: # Type 2
                        tc_info = parse_tc_body(msg_body_bytes)
                        if tc_info:
                            self.process_tc(orig_ip, tc_info, validity_time)

                # --- 转发检查 (MPR Flooding) ---
                # 即使处理过内容，如果之前没转发过且我是MPR，仍需转发
                if self.check_forwarding_condition(sender_ip, orig_ip, msg_seq, ttl):
                    # 传入完整的单条消息数据 (Header + Body) 进行转发处理
                    full_msg_data = data[cursor:body_end]
                    self.forward_message(full_msg_data, ttl, hop)

                cursor += msg_size

    # ==========================
    # 逻辑处理 (Logic Processing)
    # ==========================
    def process_hello(self, sender_ip, hello_info, validity_time):
        """处理 HELLO 消息字典"""
        current_time = time.time()
        
        # 1. 链路感知
        self.link_set.process_hello(sender_ip, hello_info, validity_time)
        
        link = self.link_set.links.get(sender_ip)
        is_sym = link.is_symmetric() if link else False
        
        # 2. 邻居维护
        self.neighbor_manager.update_neighbor_status(
            sender_ip, hello_info['willingness'], is_sym
        )
        
        if is_sym:
            # 3. 二跳与 MPR Selector
            self.neighbor_manager.process_2hop_neighbors(sender_ip, hello_info, validity_time, current_time)
            self.neighbor_manager.process_mpr_selector(sender_ip, hello_info, validity_time, current_time)
            
            # 4. 触发重算
            self.neighbor_manager.recalculate_mpr()
            # 拓扑变动，重算路由
            self.routing_manager.recalculate_routing_table()

    def process_tc(self, originator_ip, tc_info, validity_time):
        """处理 TC 消息字典"""
        self.topology_manager.process_tc_message(originator_ip, tc_info, validity_time, time.time())
        # 拓扑变动，重算路由
        self.routing_manager.recalculate_routing_table()

    # ==========================
    # 发送与转发 (Sending & Forwarding)
    # ==========================
    def generate_and_send_hello(self):
        """生成并发送 HELLO"""
        # 1. 获取邻居组 (带 MPR 标记)
        mpr_set = self.neighbor_manager.current_mpr_set
        groups = self.link_set.get_hello_groups(mpr_set)
        
        # 2. 构建消息体字典
        # 构造 hello_info 字典，而不是传散参数
        hello_info = {
            "htime_seconds": HELLO_INTERVAL,
            "willingness": WILL_DEFAULT,
            "neighbor_groups": groups
        }
        
        # 调用 create_hello_body，传入字典
        hello_body = create_hello_body(hello_info)
        
        # 3. 构建消息头 (TTL=1)
        header = create_message_header(
            HELLO_MESSAGE, NEIGHB_HOLD_TIME, len(hello_body),
            self.my_ip, 1, 0, self.get_next_msg_seq()
        )
        
        self.send_packet(header + hello_body)
        print(f"[Send] HELLO ({len(groups)} groups)")

    def generate_and_send_tc(self):
        """生成并发送 TC (仅当我是 MPR)"""
        selectors = list(self.neighbor_manager.mpr_selectors.keys())
        if not selectors: return # 没人选我，不发
        
        self.ansn = (self.ansn + 1) % 65535
        
        # 1. 构建消息体
        tc_body = create_tc_body(self.ansn, selectors)
        
        # 2. 构建消息头 (TTL=255)
        header = create_message_header(
            TC_MESSAGE, TOP_HOLD_TIME, len(tc_body),
            self.my_ip, 255, 0, self.get_next_msg_seq()
        )
        
        self.send_packet(header + tc_body)
        print(f"[Send] TC (Selectors: {selectors})")

    def check_forwarding_condition(self, sender_ip, orig_ip, seq, ttl):
        """判断是否转发 (RFC 3.4.1)"""
        if ttl <= 1: return False
        if orig_ip == self.my_ip: return False
        
        # 检查是否已转发过
        if self.duplicate_set.is_duplicate(orig_ip, seq):
            if self.duplicate_set.entries[(orig_ip, seq)].retransmitted:
                return False

        # MPR 规则：Sender 必须选我做了 MPR
        return sender_ip in self.neighbor_manager.mpr_selectors

    def forward_message(self, msg_data, old_ttl, old_hop):
        """修改 TTL/Hop 并转发"""
        # 解包头部前12字节修改
        fmt = '!BBH4sBBH'
        lst = list(struct.unpack(fmt, msg_data[:12]))
        lst[4] -= 1 # TTL - 1
        lst[5] += 1 # Hop + 1
        new_head = struct.pack(fmt, *lst)
        
        # 标记为已转发
        # (解析 Originator 和 Seq)
        orig_ip = socket.inet_ntoa(lst[3])
        seq = lst[6]
        self.duplicate_set.mark_retransmitted(orig_ip, seq)
        
        print(f"[Forward] 转发来自 {orig_ip} 的消息")
        self.send_packet(new_head + msg_data[12:])

    def send_packet(self, msg_bytes):
        """封装包头并广播"""
        pkt_head = create_packet_header(len(msg_bytes), self.get_next_pkt_seq())
        self.sock.sendto(pkt_head + msg_bytes, ('<broadcast>', self.port))

    # ==========================
    # 辅助与循环
    # ==========================
    def get_next_msg_seq(self):
        self.msg_seq_num = (self.msg_seq_num + 1) % 65535
        return self.msg_seq_num

    def get_next_pkt_seq(self):
        self.pkt_seq_num = (self.pkt_seq_num + 1) % 65535
        return self.pkt_seq_num

    def loop_hello(self):
        while self.running:
            try:
                # 获取锁，保护 generate_and_send_hello 读取数据
                with self.lock:
                    self.generate_and_send_hello()
                time.sleep(HELLO_INTERVAL - 0.5 + random.random())
            except Exception as e:
                print(f"[Error] Hello Loop: {e}")

    def loop_tc(self):
        while self.running:
            try:
                with self.lock:
                    self.generate_and_send_tc()
                time.sleep(TC_INTERVAL - 0.5 + random.random())
            except Exception as e:
                print(f"[Error] TC Loop: {e}")

    def loop_cleanup(self):
        while self.running:
            time.sleep(2.0)
            
            # 获取锁，保护清理过程中的删除操作
            with self.lock:
                self.link_set.cleanup()
                self.neighbor_manager.cleanup()
                self.topology_manager.cleanup()
                self.duplicate_set.cleanup()

if __name__ == "__main__":
    import sys
    ip = "192.168.3.5"
    if len(sys.argv) > 1: ip = sys.argv[1]
    node = OLSRNode(ip)
    node.start()