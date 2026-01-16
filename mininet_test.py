import os
from mininet.net import Mininet
from mininet.topo import Topo
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink

# --- 1. 定义一个简单的多跳拓扑 ---
class MeshTopo(Topo):
    def build(self):
        # 创建4个节点：h1, h2, h3, h4
        h1 = self.addHost('h1', ip='10.0.0.1/24')
        h2 = self.addHost('h2', ip='10.0.0.2/24')
        h3 = self.addHost('h3', ip='10.0.0.3/24')
        h4 = self.addHost('h4', ip='10.0.0.4/24')

        # 创建链路，模拟线性/多跳结构：h1-h2-h3-h4
        # 这样 h1 ping h4 必须经过路由
        self.addLink(h1, h2, bw=10, delay='5ms', loss=0)
        self.addLink(h2, h3, bw=10, delay='5ms', loss=0)
        self.addLink(h3, h4, bw=10, delay='5ms', loss=0)
        
        # 如果你想测试更复杂的网状（Mesh），可以加一条斜线：
        # self.addLink(h2, h4) 

def run():
    # 初始化 Mininet
    topo = MeshTopo()
    # 使用 TCLink 支持设置带宽和延迟，controller=None 表示我们不需要 OpenFlow 控制器
    # 纯自组网协议通常只涉及三层路由，不需要 SDN 控制器
    net = Mininet(topo=topo, link=TCLink, controller=None)
    
    # *** 关键：关闭自动路由和ARP ***
    # Mininet 默认会帮主机设置好路由，这会干扰你的协议验证。
    # 我们希望路由表完全由你的 OLSR 程序来写入。
    net.start()
    for h in net.hosts:
        h.cmd('sysctl -w net.ipv4.ip_forward=1') # 开启转发功能（充当路由器）
        # 清除默认路由，迫使必须通过 OLSR 发现路由
        h.cmd('ip route flush table main')
        # 重新把直连路由加回去（否则连邻居都找不到）
        h.cmd('ip route add 10.0.0.0/24 dev %s-eth0' % h.name)

    # --- 2. 启动你的 OLSR 程序 ---
    project_path = "/home/user/olsr_project" # 修改为你的实际路径
    olsr_script = "olsr_main.py"
    
    info('*** Starting OLSR protocol on all hosts...\n')
    for h in net.hosts:
        # 获取接口名 (如 h1-eth0)
        intf = h.defaultIntf().name
        
        # 构造命令：
        # 1. 切换到项目目录
        # 2. 运行 Python 脚本
        # 3. 传入接口参数 (假设你的程序接受 --intf)
        # 4. 重定向日志 (非常重要！)
        # 5. 后台运行 (&)
        cmd = (
            f"cd {project_path} && "
            f"python3 {olsr_script} --intf {intf} "
            f"> {project_path}/logs/{h.name}.log 2>&1 &"
        )
        
        info(f'*** {h.name} executing: {cmd}\n')
        h.cmd(cmd)

    info('*** Waiting 10 seconds for OLSR convergence...\n')
    import time
    time.sleep(10)

    # --- 3. 验证环节 ---
    info('*** Checking Routing Tables on h1 and h4...\n')
    # 查看 h1 是否学习到了 h4 的路由
    print(net.get('h1').cmd('ip route'))
    print(net.get('h4').cmd('ip route'))

    info('*** Running CLI for manual testing...\n')
    CLI(net)

    info('*** Stopping network...\n')
    # 杀掉后台的 python 进程，防止僵尸进程
    for h in net.hosts:
        h.cmd('killall python3') 
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run()