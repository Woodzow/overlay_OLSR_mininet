# OLSR Overlay Mininet-WiFi Test

本仓库现在补充了基于现有 OLSR 应用层覆盖网络实现的 12 节点 Mininet-WiFi 复杂拓扑、独立视频转发程序，以及路由性能测试程序。

## 拓扑

12 节点邻接关系如下：

```text
1: 2, 4, 5
2: 1, 3, 5
3: 2, 6
4: 1, 8
5: 1, 2, 7
6: 3, 7
7: 5, 6, 9, 10
8: 4, 12
9: 7, 12
10: 7, 11
11: 10, 12
12: 8, 9, 11
```

拓扑配置文件：`/home/admin/overlay_OLSR_mininet/configs/mininet_wifi_complex_12sta/topology.json`

主测试脚本：`/home/admin/overlay_OLSR_mininet/tools/mininet_wifi_complex_12sta.py`

## 新增组件

- `src/olsr_main.py`
  增加本地控制面 UDP 端口 `127.0.0.1:5100`，支持 `SHOW_ROUTE`、`SHOW_ROUTE_DETAIL:<ip>`、`SHOW_NEIGHBORS`、`DISCOVER_ROUTE:<ip>`。
- `src/video_forwarder.py`
  独立于协议本身的视频/文件逐跳转发程序。它不会嵌入协议，只查询 OLSR 当前路由表得到下一跳后转发。
- `src/overlay_bench.py`
  用于测试首次路由收敛时间、吞吐量、丢包率，也保留了时延测试能力。
- `src/resource_bench.py`
  查看 OLSR、视频转发、overlay bench 的 CPU/内存占用。

## 运行 Mininet-WiFi 12 节点拓扑

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --cli --skip-file-transfer
```

如果要自动执行一次视频文件传输：

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --video-file data.mp4 --cli
```

如果要在启动后自动执行一次路由收敛与吞吐量/丢包率测试：

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer --cli
```

如果要人为注入链路丢包，例如 5%：

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer --link-loss 5
```

## Mininet-WiFi CLI 常用测试命令

进入 CLI 后，可先查看站点：

```bash
nodes
```

查看某个节点当前 OLSR 路由表：

```bash
sta1 python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(3); s.sendto(b'SHOW_ROUTE',('127.0.0.1',5100)); print(s.recvfrom(8192)[0].decode())"
```

查看某个目标的详细路由：

```bash
sta1 python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(3); s.sendto(b'SHOW_ROUTE_DETAIL:10.0.0.12',('127.0.0.1',5100)); print(s.recvfrom(8192)[0].decode())"
```

查看邻居表：

```bash
sta7 python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(3); s.sendto(b'SHOW_NEIGHBORS',('127.0.0.1',5100)); print(s.recvfrom(8192)[0].decode())"
```

## 视频传输命令

先在除源节点外的中继/接收节点启动 `video_forwarder.py`。例如从 `sta1` 发到 `sta12`：

```bash
sta2 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.2 --data-port 6200 &
sta3 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.3 --data-port 6200 &
sta4 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.4 --data-port 6200 &
sta5 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.5 --data-port 6200 &
sta6 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.6 --data-port 6200 &
sta7 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.7 --data-port 6200 &
sta8 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.8 --data-port 6200 &
sta9 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.9 --data-port 6200 &
sta10 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.10 --data-port 6200 &
sta11 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.11 --data-port 6200 &
sta12 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.12 --data-port 6200 --output-dir logs/received_videos &
```

然后在源节点发送：

```bash
sta1 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.1 --data-port 6200 --send-file data.mp4 --dest-ip 10.0.0.12 --exit-after-send
```

## 路由性能测试命令

### 1. 首次路由收敛时间

这里的“首次路由收敛时间”定义为：

- 从对应 OLSR 节点进程启动开始计时
- 到该节点第一次得到目标路由为止

因此，不再建议用“尽快手工敲命令”的方式近似测量。现在 `overlay_bench.py route` 会同时给出两个字段：

- `startup_route_convergence_sec`
  这是你真正要的指标，表示从协议节点启动到该路由首次建立成功的时间
- `route_setup_sec`
  这是从你执行这条 benchmark 命令开始，到它查到路由的时间，只是辅助值

推荐做法：

1. 先启动 OLSR 节点
2. 等网络稳定后再执行下面命令读取结果
3. 以返回结果中的 `startup_route_convergence_sec` 为准

命令如下：

```bash
sta1 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py route --node-ip 10.0.0.1 --dest-ip 10.0.0.12 --json
```

如果你使用一键脚本自动跑：

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer
```

### 2. 吞吐量与丢包率

先在除源节点外的其它节点启动 benchmark daemon：

```bash
sta2 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.2 --data-port 6300 &
sta3 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.3 --data-port 6300 &
sta4 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.4 --data-port 6300 &
sta5 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.5 --data-port 6300 &
sta6 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.6 --data-port 6300 &
sta7 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.7 --data-port 6300 &
sta8 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.8 --data-port 6300 &
sta9 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.9 --data-port 6300 &
sta10 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.10 --data-port 6300 &
sta11 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.11 --data-port 6300 &
sta12 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py daemon --node-ip 10.0.0.12 --data-port 6300 &
```

然后在源节点执行吞吐量/丢包率测试：

```bash
sta1 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py throughput --node-ip 10.0.0.1 --dest-ip 10.0.0.12 --data-port 6300 --count 1000 --payload-size 1000 --json
```

返回结果中的关键字段：

- `startup_route_convergence_sec`
  若命令中带出了对应路由状态，则表示启动后首次路由建立时间
- `goodput_mbps`
  实测吞吐量
- `loss_rate`
  丢包率
- `pdr`
  包投递率

## 资源占用查看

```bash
sta7 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/resource_bench.py
```
