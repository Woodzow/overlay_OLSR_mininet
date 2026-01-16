# src/flooding_mpp.py 
"""
DuplicateTuple 结构：
D_addr: 消息的源头地址 (Originator Address)。
D_seq_num: 消息序列号 (Message Sequence Number)。
D_retransmitted: 布尔值，标记这条消息我是否已经转发过。
D_time: 过期时间
"""
# 这个模块实现了用于跟踪已接收消息的重复检测机制，防止消息的重复处理和转发。主要的两个类也就是命名为duplicate


import time
from constants import DUP_HOLD_TIME # 通常是 30秒

class DuplicateTuple:
    def __init__(self, originator_ip, msg_seq_num, current_time):
        self.originator_ip = originator_ip
        self.msg_seq_num = msg_seq_num
        self.retransmitted = False
        self.expiration_time = current_time + DUP_HOLD_TIME

class DuplicateSet:
    def __init__(self):
        # 格式: { (originator_ip, seq_num): DuplicateTuple }
        self.entries = {}

    def is_duplicate(self, originator_ip, msg_seq_num):
        """检查消息是否已存在"""
        return (originator_ip, msg_seq_num) in self.entries

    def record_message(self, originator_ip, msg_seq_num, current_time):
        """记录新消息"""
        key = (originator_ip, msg_seq_num)
        if key not in self.entries:
            self.entries[key] = DuplicateTuple(originator_ip, msg_seq_num, current_time)
        return self.entries[key]

    def mark_retransmitted(self, originator_ip, msg_seq_num):
        """标记消息已被转发"""
        key = (originator_ip, msg_seq_num)
        if key in self.entries:
            self.entries[key].retransmitted = True

    def cleanup(self):
        """清理过期记录"""
        now = time.time()
        keys_to_remove = [k for k, v in self.entries.items() if v.expiration_time < now]
        for k in keys_to_remove:
            del self.entries[k]