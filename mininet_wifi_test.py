import time
from pathlib import Path

from mininet.log import info, setLogLevel
from mn_wifi.cli import CLI
from mn_wifi.link import adhoc, wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference


def run():
    root_dir = Path(__file__).resolve().parent
    src_dir = root_dir / "src"
    logs_dir = root_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    info("*** Creating Mininet-WiFi network\n")
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info("*** Creating stations\n")
    sta1 = net.addStation(
        "sta1",
        ip="10.0.0.1/24",
        position="20,50,0",
        range=45,
    )
    sta2 = net.addStation(
        "sta2",
        ip="10.0.0.2/24",
        position="55,50,0",
        range=45,
    )
    sta3 = net.addStation(
        "sta3",
        ip="10.0.0.3/24",
        position="90,50,0",
        range=45,
    )
    sta4 = net.addStation(
        "sta4",
        ip="10.0.0.4/24",
        position="125,50,0",
        range=45,
    )
    stations = [sta1, sta2, sta3, sta4]

    # 控制覆盖范围，让无线拓扑尽量接近 h1-h2-h3-h4 的线性多跳结构。
    net.setPropagationModel(model="logDistance", exp=4)

    info("*** Configuring WiFi nodes\n")
    net.configureWifiNodes()

    info("*** Creating ad-hoc links\n")
    for sta in stations:
        net.addLink(
            sta,
            cls=adhoc,
            intf=f"{sta.name}-wlan0",
            ssid="olsr-adhoc",
            mode="g",
            channel=5,
        )

    info("*** Starting network\n")
    net.build()
    try:
        info("*** Preparing station network state\n")
        for sta in stations:
            wlan = f"{sta.name}-wlan0"
            sta.cmd("sysctl -w net.ipv4.ip_forward=1")
            sta.cmd("sysctl -w net.ipv4.conf.all.rp_filter=0")
            sta.cmd("sysctl -w net.ipv4.conf.default.rp_filter=0")
            sta.cmd("ip route flush table main")
            sta.cmd(f"ip route add 10.0.0.0/24 dev {wlan}")

        info("*** Starting OLSR protocol on all stations\n")
        src_dir_posix = src_dir.as_posix()
        for sta in stations:
            host_ip = sta.IP()
            log_path = (logs_dir / f"{sta.name}.log").as_posix()
            cmd = (
                f"cd {src_dir_posix} && "
                f"python3 -u olsr_main.py {host_ip} "
                f"> {log_path} 2>&1 &"
            )
            info(f"*** {sta.name} executing: {cmd}\n")
            sta.cmd(cmd)

        info("*** Waiting 10 seconds for OLSR convergence\n")
        time.sleep(10)

        info("*** Checking station routes\n")
        print(net["sta1"].cmd("ip route"))
        print(net["sta4"].cmd("ip route"))

        info("*** Running CLI for manual testing\n")
        CLI(net)
    finally:
        info("*** Stopping network\n")
        for sta in stations:
            sta.cmd("pkill -f 'python3 -u olsr_main.py'")
        net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    run()
