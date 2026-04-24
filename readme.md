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

拓扑配置文件：`/home/woodzow/overlay_OLSR_mininet/configs/mininet_wifi_complex_12sta/topology.json`

主测试脚本：`/home/woodzow/overlay_OLSR_mininet/tools/mininet_wifi_complex_12sta.py`

## 新增组件

- `src/olsr_main.py`
  增加本地控制面 UDP 端口 `127.0.0.1:5100`，支持 `SHOW_ROUTE`、`SHOW_ROUTE_DETAIL:<ip>`、`SHOW_NEIGHBORS`、`DISCOVER_ROUTE:<ip>`。
- `src/video_forwarder.py`
  独立于协议本身的视频/文件逐跳转发程序。它不嵌入协议，只查询 OLSR 当前路由表得到下一跳后转发。
- `src/overlay_bench.py`
  用于一键测试首次路由收敛时间、端到端时延、吞吐量、丢包率。
- `src/resource_bench.py`
  查看 OLSR、视频转发、overlay bench 的 CPU/内存占用。

## 运行 Mininet-WiFi 12 节点拓扑

```bash
cd /home/woodzow/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --cli --skip-file-transfer
```

自动执行一次视频文件传输：

```bash
cd /home/woodzow/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --video-file data.mp4 --cli
```

自动执行一次路由收敛、端到端时延与吞吐量/丢包率测试：

```bash
cd /home/woodzow/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer --cli
```

注入链路丢包，例如 5%：

```bash
cd /home/woodzow/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer --link-loss 5
```

一键扫描底层 `tc` 丢包率 1%-10%，统计 `sta1 -> sta12` 的吞吐量和端到端丢包率变化：

```bash
cd /home/woodzow/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_loss_sweep_12sta.py
```

结果会写到：

- `/home/woodzow/overlay_OLSR_mininet/logs/loss_sweep_12sta/summary.json`
- `/home/woodzow/overlay_OLSR_mininet/logs/loss_sweep_12sta/summary.csv`

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
sta2 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.2 --data-port 6200 &
sta3 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.3 --data-port 6200 &
sta4 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.4 --data-port 6200 &
sta5 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.5 --data-port 6200 &
sta6 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.6 --data-port 6200 &
sta7 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.7 --data-port 6200 &
sta8 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.8 --data-port 6200 &
sta9 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.9 --data-port 6200 &
sta10 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.10 --data-port 6200 &
sta11 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.11 --data-port 6200 &
sta12 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.12 --data-port 6200 --output-dir logs/received_videos &
```

源节点发送：

```bash
sta1 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/video_forwarder.py --node-ip 10.0.0.1 --data-port 6200 --send-file data.mp4 --dest-ip 10.0.0.12 --exit-after-send
```

## 路由性能测试命令

推荐直接使用一键性能测试命令：

```bash
cd /home/woodzow/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer
```

这条命令会自动完成完整流程：

- 清理旧的 `overlay_bench.py daemon`
- 清理旧结果文件
- 在除源节点外的 relay/destination 节点自动启动 benchmark daemon
- 从 OLSR 路由表解析一条确定的 hop-by-hop overlay 路径
- 测试首次路由收敛时间
- 测试端到端 RTT、估算单向时延、时延丢包率
- 测试吞吐量、PDR、丢包率
- 打印各项 JSON 结果和吞吐量结果文件路径

默认测试路径是 `sta1 -> sta12`。如需调整测试强度，可以直接在同一行命令后追加参数：

```bash
cd /home/woodzow/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_complex_12sta.py --run-bench --skip-file-transfer --bench-latency-count 50 --bench-count 1000 --bench-payload-size 1000 --bench-interval-ms 1
```

首次路由收敛时间字段：

- `startup_route_convergence_sec`
  从协议节点启动到该路由首次建立成功的时间
- `route_setup_sec`
  从 benchmark 命令开始，到它查到路由的时间，仅作辅助值

端到端时延字段：

- `rtt_min_ms`
  最小 RTT
- `rtt_avg_ms`
  平均 RTT
- `rtt_p95_ms`
  P95 RTT
- `rtt_max_ms`
  最大 RTT
- `one_way_estimated_ms`
  按 RTT/2 估算的单向时延
- `loss_rate`
  时延探测包丢包率

吞吐量与丢包率字段：

- `goodput_mbps`
  实测吞吐量
- `loss_rate`
  吞吐量测试的丢包率
- `pdr`
  包投递率
- `received_packets`
  目的节点实际接收包数
- `lost_packets`
  丢失包数
- `result_path`
  目的节点写出的统计结果文件路径

### 3. 12 点复杂拓扑下的 1%-10% 丢包扫频

这个脚本会对每个丢包率点独立完成一轮完整流程：

- 重新构建 12 节点 Mininet-WiFi 拓扑
- 在所有 `sta*-wlan0` 接口注入对应的 `tc netem loss`
- 启动当前这套 OLSR 应用层协议
- 自动解析当前 OLSR 路由表得到 `sta1 -> sta12` 的显式逐跳路径
- 测量该路径下的吞吐量、`PDR`、`loss_rate`
- 把每个 loss 点结果分别写成单独 JSON，并汇总为总表

一键运行：

```bash
cd /home/woodzow/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_loss_sweep_12sta.py
```

如果你要修改测试强度，例如每个点发 1000 个包、每包 1000 字节、包间隔 1ms：

```bash
cd /home/woodzow/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_loss_sweep_12sta.py --bench-count 1000 --bench-payload-size 1000 --bench-interval-ms 1
```

如果某个 loss 点失败但你仍然希望整轮继续跑完，可以加：

```bash
cd /home/woodzow/overlay_OLSR_mininet
sudo python3 tools/mininet_wifi_loss_sweep_12sta.py --continue-on-error
```

汇总结果文件：

- `logs/loss_sweep_12sta/summary.json`
- `logs/loss_sweep_12sta/summary.csv`

`summary.csv` 里会直接给出这些字段，便于画图：

- `loss_percent`
- `path`
- `startup_route_convergence_sec`
- `route_setup_sec`
- `goodput_mbps`
- `loss_rate`
- `pdr`
- `sent_packets`
- `received_packets`
- `lost_packets`

## 资源占用查看

```bash
sta7 cd /home/woodzow/overlay_OLSR_mininet && PYTHONPATH=src python3 src/resource_bench.py
```
