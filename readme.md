# OLSR Overlay Mininet-WiFi Test

本仓库补充了基于现有 OLSR 应用层覆盖网络实现的 12 节点 Mininet-WiFi 复杂拓扑、独立视频转发程序，以及路由性能测试程序。

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
  独立于协议本身的视频/文件逐跳转发程序。它不嵌入协议，只查询 OLSR 当前路由表得到下一跳后转发。
- `src/overlay_bench.py`
  用于测试首次路由收敛时间、吞吐量、丢包率，也保留了时延测试能力。
- `src/resource_bench.py`
  查看 OLSR、视频转发、overlay bench 的 CPU/内存占用。

## 运行 Mininet-WiFi 12 节点拓扑

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --cli --skip-file-transfer
```

自动执行一次视频文件传输：

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --video-file data.mp4 --cli
```

自动执行一次路由收敛与吞吐量/丢包率测试：

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer --cli
```

注入链路丢包，例如 5%：

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer --link-loss 5
```

## Mininet-WiFi CLI 常用测试命令

```bash
nodes
```

查看路由表：

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

源节点发送：

```bash
sta1 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.1 --data-port 6200 --send-file data.mp4 --dest-ip 10.0.0.12 --exit-after-send
```

## 路由性能测试命令

### 1. 首次路由收敛时间

这里的“首次路由收敛时间”定义为：

- 从对应 OLSR 节点进程启动开始计时
- 到该节点第一次得到目标路由为止

`overlay_bench.py route` 会同时给出两个字段：

- `startup_route_convergence_sec`
  从协议节点启动到该路由首次建立成功的时间
- `route_setup_sec`
  从你执行 benchmark 命令开始，到它查到路由的时间，仅作辅助值

命令如下：

```bash
sta1 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py route --node-ip 10.0.0.1 --dest-ip 10.0.0.12 --json
```

一键自动跑：

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer
```

### 2. 吞吐量与丢包率

现在的吞吐量/丢包率测量采用“单向统计”方式：

- 源节点发送测试流量和结束报文
- 目的节点在本地统计收到的包数、字节数、重复包数
- 目的节点把统计结果写入 `logs/overlay_bench_results/`
- 源节点轮询该结果文件并给出最终 JSON 结果

并且自动化脚本现在会先从各节点的 OLSR 路由表解析出一条确定的 hop-by-hop 路径，再按这条显式路径转发 benchmark 报文，而不是在每个中继临时重新查下一跳。这样比旧版稳定很多。

推荐优先使用一键脚本：

```bash
cd /home/admin/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer
```

它会自动：

- 清理旧的 `overlay_bench.py daemon`
- 清理旧结果文件
- 重启 relay/destination daemon
- 解析一条显式 overlay 路径
- 打印吞吐量 JSON 结果和结果文件路径

如果你要手工测试，先清掉旧的 daemon：

```bash
pkill -f "overlay_bench.py"
```

然后在除源节点外的其它节点启动 benchmark daemon：

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

源节点执行吞吐量/丢包率测试时，建议带一条显式路径。  
例如如果当前解析出的路径是 `10.0.0.1 -> 10.0.0.4 -> 10.0.0.8 -> 10.0.0.12`，则命令为：

```bash
sta1 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/overlay_bench.py throughput --node-ip 10.0.0.1 --dest-ip 10.0.0.12 --data-port 6300 --count 300 --payload-size 512 --interval-ms 2 --report-timeout-sec 30 --path 10.0.0.1,10.0.0.4,10.0.0.8,10.0.0.12 --json
```

建议先从较保守参数开始，例如上面的 `300` 包、`512` 字节、`2ms` 间隔，跑通后再逐步增大。

结果中的关键字段：

- `goodput_mbps`
  实测吞吐量
- `loss_rate`
  丢包率
- `pdr`
  包投递率
- `received_packets`
  目的节点实际接收包数
- `lost_packets`
  丢失包数
- `result_path`
  目的节点写出的统计结果文件路径

## 资源占用查看

```bash
sta7 cd /home/admin/overlay_OLSR_mininet && PYTHONPATH=src python3 src/resource_bench.py
```
