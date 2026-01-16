import time
import socket
from constants import *
from pkt_msg_fmt import create_link_code


class LinkTuple: #此类主要用于判断邻居节点对称与否，以及过期与否
    def __init__(self, neighbor_ip):
        # 这个ip根据sender_ip传入，而sender_ip又是发送者ip，通过解析hello消息的msg_header可以获得，注意hello消息不进行转发，从而originator就是sender
        self.neighbor_ip = neighbor_ip 
        self.l_asym_time = 0  # 异步过期时间戳 代表接收链路的有效期
        self.l_sym_time = 0   # 对称过期时间戳 代表双向握手成功的有效期
        self.l_time = 0       # 记录过期时间戳 (通常取上面两者的最大值 + 保持时间)

    def is_symmetric(self):
        """判断当前链路是否对称"""
        return time.time() < self.l_sym_time

    def is_asymmetric(self):
        """判断当前链路是否仅为非对称（我听到他，但他没听到我）""" 
        return (time.time() < self.l_asym_time) and (not self.is_symmetric())

# 类似于邻居相关信息数据库的类来存储链路信息，操作信息的增删  
# 常量定义 (基于 RFC 18.3)
# NEIGHB_HOLD_TIME = 6.0  # 3 * HELLO_INTERVAL

class LinkSet:
    def __init__(self, my_ip=None):
        self.links = {}  # 格式: { '192.168.1.5': LinkTuple对象类, ... }，这里面保存邻居节点的ip信息，是否对称节点
        self.my_ip = my_ip # 请替换为你的真实IP

    def process_hello(self, sender_ip, hello_info, validity_time):# 其中的hello_info就是hello_body解包以后的信息内容，本身是一个字典，这一部分打包解包在hello_msg_fmt文件里面
        """
        核心逻辑：根据收到的 HELLO 处理链路状态
        参考 RFC 3626 Section 7.1.1
        """
        current_time = time.time()
        # validity_time = hello_info['htime_seconds'] * 3  这里不再使用固定值 而是传入
        # 通常 Validity Time = 3 * Htime [cite: 1685, 1710] 对方存在有效时间限制，也就是对方只要存在，我们就认为他存在这个时间，每发一次hello就更新，认为会继续存在这么长时间，这也就是为什么收到hello就更新asym_time:收到说明肯定存在

        # 1. 如果是新邻居，创建记录 [cite: 816-827]
        if sender_ip not in self.links:
            print(f"[LinkSet] 发现新邻居: {sender_ip}")
            new_link = LinkTuple(sender_ip)
            # 新邻居默认为非对称，L_SYM_time 设为过期
            new_link.l_sym_time = current_time - 1 
            self.links[sender_ip] = new_link #ip与对象的键值对构成的字典
        
        link = self.links[sender_ip] #取出sender_ip对应的LinkTuple类的对象，对他进行操作

        # 2. 更新 L_ASYM_time (只要收到 Hello 就更新) [cite: 831-832]
        link.l_asym_time = current_time + validity_time #异步过期时间戳（时刻）
        
        # 3. 检查对方是否听到了我 (链路是否对称?) [cite: 834-835]
        # 遍历 Hello 消息里的所有邻居组
        found_myself = False
        for link_code, ip_list in hello_info['neighbor_groups']:#这里的邻居信息是发送hello消息一方的
            if self.my_ip in ip_list:#如果我在对方的邻居节点中，现在又收到了对方的hello消息
                found_myself = True
                # 检查对方标记的链路类型（最后两位的link_type）
                l_type = link_code & 0x03 
                if l_type == 3: # LOST_LINK [cite: 842]
                    link.l_sym_time = current_time - 1 # 对方说丢失了，我们也标记为非对称
                elif l_type == 1 or l_type == 2: # ASYM_LINK or SYM_LINK [cite: 846]
                    link.l_sym_time = current_time + validity_time # 确认为对称！
                    print(f"[LinkSet] 与 {sender_ip} 建立对称链路！")
                break
        
        # 4. 更新记录总过期时间 L_time [cite: 848-850]
        link.l_time = max(link.l_sym_time, link.l_asym_time)

    def cleanup(self):
        """定期清理过期邻居"""
        current_time = time.time()
        expired_ips = [ip for ip, link in self.links.items() if link.l_time < current_time]
        # 创建一个过期ip构成的列表
        for ip in expired_ips:
            print(f"[LinkSet] 邻居 {ip} 已过期，删除记录。")
            del self.links[ip]

    # 基于链路状态生成hello消息的邻居相关内容，这里自己本身与哪些节点相连的初始化信息应该要么初始设定，要么应该从电台设备爬相关信息，要么是通过hello消息本身去更新过来
    """
    这个部分后续还需要再考虑一下实际的情况来做出裁决
    主要是用于生成neighbor_groups
    """
    def get_hello_groups(self, mpr_set=None):#传入的是linkset类的对象
        """
        根据当前 LinkSet 生成用于发送 HELLO 的 neighbor_groups
        参考 RFC 3626 Section 6.2
        
        生成 HELLO 消息的邻居组列表
        :param mpr_set: 集合, 包含当前被选为 MPR 的邻居 IP
        """
        if mpr_set is None:
            mpr_set = set()
        
        # 分类容器
        mpr_neighbors = []   # 类型 2: MPR_NEIGH
        sym_neighbors = []   # 类型 1: SYM_NEIGH
        asym_neighbors = []  # 类型 0: NOT_NEIGH (但链路是 ASYM)
        
        current_time = time.time()
        
        # 遍历所有邻居，分类
        for link in self.links.values():#这个value()的返回值为linktuple类的对象
            if link.l_time < current_time:
                continue # 已过期忽略
            
            # 1. 处理对称邻居 (Symmetric)
            if link.is_symmetric():
                # 如果该邻居在 MPR 集合中，标记为 MPR_NEIGH [cite: 948]
                if link.neighbor_ip in mpr_set:
                    mpr_neighbors.append(link.neighbor_ip)
                else:
                    sym_neighbors.append(link.neighbor_ip)
            
            # 2. 处理非对称邻居 (Asymmetric)
            elif link.is_asymmetric():
                asym_neighbors.append(link.neighbor_ip)
        
        neighbor_groups = []
        # 组装 Group 1: MPR Neighbors (Link=SYM, Neighbor=MPR)
        if mpr_neighbors:
            # LinkType=2(SYM), NeighType=2(MPR) -> Code 10 (0x0A)
            code = create_link_code(SYM_LINK, MPR_NEIGH)
            neighbor_groups.append((code, mpr_neighbors))

        # 组装 Group 2: Symmetric Neighbors (Link=SYM, Neighbor=SYM)
        if sym_neighbors:
            # LinkType=2(SYM), NeighType=1(SYM) -> Code 6 (0x06)
            code = create_link_code(SYM_LINK, SYM_NEIGH)
            neighbor_groups.append((code, sym_neighbors))
            
        # 组装 Group 3: Asymmetric Neighbors (Link=ASYM, Neighbor=NOT)
        if asym_neighbors:
            code = create_link_code(ASYM_LINK, NOT_NEIGH)
            neighbor_groups.append((code, asym_neighbors))
            
        return neighbor_groups