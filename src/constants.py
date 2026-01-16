#本项目中可能会用到的常量设置
#
#
#
#


#编码vtime时用到的缩放常量参数
OLSR_C = 0.0625

# 发射间隔 (Emission Intervals):
HELLO_INTERVAL   = 2            # —— 发送HELLO消息的默认间隔 
REFRESH_INTERVAL = 2            # —— 邻居信息的刷新间隔 
TC_INTERVAL      = 5            # —— 发送TC（拓扑控制）消息的默认间隔 
MID_INTERVAL     = TC_INTERVAL  # 等同于 TC_INTERVAL (5秒) 
HNA_INTERVAL     = TC_INTERVAL  # 等同于 TC_INTERVAL (5秒) 

# 保持时间 (Holding Times):
NEIGHB_HOLD_TIME = 3 * REFRESH_INTERVAL # 邻居记录的有效期
TOP_HOLD_TIME    = 3 * TC_INTERVAL      # 拓扑信息的有效期
DUP_HOLD_TIME    = 30                   # 重复消息记录（防止广播风暴）的保持时间
MID_HOLD_TIME    = 3 * MID_INTERVAL     # MID记录的有效期
HNA_HOLD_TIME    = 3 * HNA_INTERVAL     # HNA记录的有效期

# msg_type 
HELLO_MESSAGE = 1
TC_MESSAGE    = 2
MID_MESSAGE   = 3
HNA_MESSAGE   = 4
DATA_MESSAGE  = 5  # 新增数据消息类型

# Link Types
UNSPEC_LINK = 0
ASYM_LINK   = 1  # 我听到了它，不知道它听到我没
SYM_LINK    = 2  # 双向连通
LOST_LINK   = 3  # 链路丢失

# Neighbor Types
NOT_NEIGH   = 0
SYM_NEIGH   = 1  # 对称邻居
MPR_NEIGH   = 2  # 我选它当 MPR


#定义节点愿意作为MPR中继节点的意愿等级常量，这5个权重值与OLSR协议RFC3626中定义的保持一致

WILL_NEVER   = 0 # 永不
WILL_LOW     = 1 # 低
WILL_DEFAULT = 3 # 默认
WILL_HIGH    = 6 # 高
WILL_ALWAYS  = 7 # 总是

#链路迟滞阈值
HYST_THRESHOLD_HIGH = 0.8   # (链路建立阈值)
HYST_THRESHOLD_LOW  = 0.3   # (链路断开阈值)
HYST_SCALING        = 0.5   # (初始质量估算值)



