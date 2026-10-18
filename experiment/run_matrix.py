"""Emulated campus segment for WebRTC path and address-disclosure tests.

Run as root on Linux:  python3 run_matrix.py --reps 5 --out results/raw/trials.jsonl

Topology (network namespaces):
  cw-c1, cw-c2   two client devices, each on a port of bridge br0 in cw-ap
  cw-ap          the access point / client VLAN (a Linux bridge)
  cw-gw          the gateway (router; NAT in the "nat" addressing mode)
  cw-inet        a host outside the campus running coturn (STUN and TURN)
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NS = ["cw-c1", "cw-c2", "cw-ap", "cw-gw", "cw-inet"]
TURN_IP = "203.0.113.10"
GW_WAN = "203.0.113.1"
ADDR = {
    "nat": {"c1": "10.10.0.11", "c2": "10.10.0.12", "gw": "10.10.0.1", "net": "10.10.0.0/24"},
    "public": {"c1": "198.51.100.11", "c2": "198.51.100.12", "gw": "198.51.100.1", "net": "198.51.100.0/24"},
    # dual: IPv4 behind NAT as in "nat", plus global IPv6 without translation
    "dual": {"c1": "10.10.0.11", "c2": "10.10.0.12", "gw": "10.10.0.1", "net": "10.10.0.0/24",
             "c1_6": "2001:db8:10::11", "c2_6": "2001:db8:10::12", "gw_6": "2001:db8:10::1"},
}
TURN_IP6 = "2001:db8:ff::10"
GW_WAN6 = "2001:db8:ff::1"
VARIANTS = ["chromium-default", "chromium-rawhost", "firefox-default", "firefox-rawhost", "webkit-default",
            "chromium-default-media", "firefox-default-media"]
# name, addressing, isolation, mdns filtered, gateway forwards client-to-client, TURN offered
CONDITIONS = [
    ("N1-open", "nat", False, False, False, True),
    ("N2-mdns-filtered", "nat", False, True, False, True),
    ("N3-isolation", "nat", True, False, True, True),
    ("N4-isolation+gw-acl", "nat", True, False, False, True),
    ("N5-isolation+gw-acl-no-relay", "nat", True, False, False, False),
    ("N6-public-mdns-filtered", "public", False, True, False, True),
    ("N7-public-isolation+gw-acl", "public", True, False, False, True),
    ("N8-dual-open", "dual", False, False, False, True),
    ("N9-dual-mdns-filtered", "dual", False, True, False, True),
    ("N10-dual-isolation+gw-acl", "dual", True, False, False, True),
]


def sh(cmd, check=True):
    return subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)


def ns(name, cmd, check=True):
    return sh("ip netns exec %s %s" % (name, cmd), check)


def teardown():
    sh("pkill -f 'turnserver -n' || true", check=False)
    for n in NS:
        sh("ip netns del %s" % n, check=False)


def setup(mode):
    teardown()
    a = ADDR[mode]
    for n in NS:
        sh("ip netns add %s" % n)
        ns(n, "ip link set lo up")
        if mode == "dual":
            ns(n, "sysctl -qw net.ipv6.conf.all.accept_dad=0 net.ipv6.conf.default.accept_dad=0")
        else:
            ns(n, "sysctl -qw net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1")
    ns("cw-ap", "ip link add br0 type bridge")
    ns("cw-ap", "ip link set br0 up")
    for client in ("c1", "c2"):
        sh("ip link add ap-%s netns cw-ap type veth peer name eth0 netns cw-%s" % (client, client))
        ns("cw-ap", "ip link set ap-%s master br0" % client)
        ns("cw-ap", "ip link set ap-%s up" % client)
        ns("cw-ap", "tc qdisc add dev ap-%s clsact" % client)
        ns("cw-%s" % client, "ip addr add %s/24 dev eth0" % a[client])
        ns("cw-%s" % client, "ip link set eth0 up")
        ns("cw-%s" % client, "ip route add default via %s" % a["gw"])
        if mode == "dual":
            ns("cw-%s" % client, "ip -6 addr add %s/64 dev eth0 nodad" % a[client + "_6"])
            ns("cw-%s" % client, "ip -6 route add default via %s" % a["gw_6"])
    sh("ip link add ap-gw netns cw-ap type veth peer name lan0 netns cw-gw")
    ns("cw-ap", "ip link set ap-gw master br0")
    ns("cw-ap", "ip link set ap-gw up")
    ns("cw-gw", "ip addr add %s/24 dev lan0" % a["gw"])
    ns("cw-gw", "ip link set lan0 up")
    sh("ip link add wan0 netns cw-gw type veth peer name eth0 netns cw-inet")
    ns("cw-gw", "ip addr add %s/24 dev wan0" % GW_WAN)
    ns("cw-gw", "ip link set wan0 up")
    ns("cw-inet", "ip addr add %s/24 dev eth0" % TURN_IP)
    ns("cw-inet", "ip link set eth0 up")
    ns("cw-gw", "sysctl -qw net.ipv4.ip_forward=1 net.ipv4.conf.all.rp_filter=0 net.ipv4.conf.lan0.rp_filter=0 "
                "net.ipv4.conf.all.send_redirects=0 net.ipv4.conf.lan0.send_redirects=0")
    if mode in ("nat", "dual"):
        ns("cw-gw", "iptables -t nat -A POSTROUTING -o wan0 -j MASQUERADE")
    else:
        ns("cw-inet", "ip route add %s via %s" % (a["net"], GW_WAN))
    listen = ["--listening-ip=" + TURN_IP, "--relay-ip=" + TURN_IP]
    if mode == "dual":
        ns("cw-gw", "ip -6 addr add %s/64 dev lan0 nodad" % a["gw_6"])
        ns("cw-gw", "ip -6 addr add %s/64 dev wan0 nodad" % GW_WAN6)
        ns("cw-inet", "ip -6 addr add %s/64 dev eth0 nodad" % TURN_IP6)
        ns("cw-inet", "ip -6 route add 2001:db8:10::/64 via %s" % GW_WAN6)
        ns("cw-gw", "sysctl -qw net.ipv6.conf.all.forwarding=1")
        listen += ["--listening-ip=" + TURN_IP6, "--relay-ip=" + TURN_IP6]
    delay = int(os.environ.get("CW_WAN_DELAY_MS", "0"))
    if delay:
        ns("cw-gw", "tc qdisc add dev wan0 root netem delay %dms" % delay)
        ns("cw-inet", "tc qdisc add dev eth0 root netem delay %dms" % delay)
    subprocess.Popen(
        ["ip", "netns", "exec", "cw-inet", "turnserver", "-n"] + listen + ["--lt-cred-mech", "--user=cw:cwpass", "--realm=cw.test",
         "--no-tls", "--no-dtls", "--no-cli", "--log-file=/tmp/cw_turn.log", "--simple-log", "--verbose"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    ok = ns("cw-c1", "ping -c1 -W2 %s" % TURN_IP, check=False).returncode == 0
    if not ok:
        raise SystemExit("testbed: client cannot reach the STUN/TURN host")
    if mode == "dual":
        time.sleep(1.0)
        if ns("cw-c1", "ping -6 -c1 -W2 %s" % TURN_IP6, check=False).returncode != 0:
            raise SystemExit("testbed: client cannot reach the STUN/TURN host over IPv6")


def apply_knobs(isolation, mdns_filtered, gw_forward, dual=False):
    for client in ("c1", "c2"):
        ns("cw-ap", "bridge link set dev ap-%s isolated %s" % (client, "on" if isolation else "off"))
        ns("cw-ap", "tc filter del dev ap-%s ingress" % client, check=False)
        if mdns_filtered:
            ns("cw-ap", "tc filter add dev ap-%s ingress protocol ip flower ip_proto udp dst_port 5353 action drop" % client)
            if dual:
                ns("cw-ap", "tc filter add dev ap-%s ingress protocol ipv6 flower ip_proto udp dst_port 5353 action drop" % client)
        ns("cw-%s" % client, "ip neigh flush all", check=False)
    ns("cw-gw", "iptables -F FORWARD")
    ns("cw-gw", "sysctl -qw net.ipv4.conf.lan0.proxy_arp_pvlan=%d" % (1 if gw_forward else 0))
    if not gw_forward:
        ns("cw-gw", "iptables -A FORWARD -i lan0 -o lan0 -j DROP")
    if dual:
        ns("cw-gw", "ip6tables -F FORWARD")
        ns("cw-gw", "ip6tables -A FORWARD -i lan0 -o lan0 -j DROP")
    ns("cw-gw", "ip neigh flush all", check=False)


def mac(namespace, dev):
    out = ns(namespace, "cat /sys/class/net/%s/address" % dev).stdout.strip()
    return out.lower()


class Agent:
    def __init__(self, namespace, variant, cfg):
        self.p = subprocess.Popen(
            ["ip", "netns", "exec", namespace, sys.executable, os.path.join(HERE, "agent.py"), variant, json.dumps(cfg)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.hello = self.read()

    def read(self):
        line = self.p.stdout.readline()
        if not line:
            return {"error": "agent exited"}
        return json.loads(line)

    def call(self, **cmd):
        self.p.stdin.write(json.dumps(cmd) + "\n")
        self.p.stdin.flush()
        return self.read()

    def close(self):
        try:
            self.p.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
            self.p.stdin.flush()
            self.p.wait(timeout=15)
        except Exception:
            self.p.kill()


def classify(cands):
    kinds = {"host_ip": 0, "host_mdns": 0, "srflx": 0, "relay": 0, "other": 0}
    for c in cands:
        parts = c.split()
        typ = parts[parts.index("typ") + 1] if "typ" in parts else "?"
        addr = parts[4] if len(parts) > 4 else ""
        if typ == "host":
            kinds["host_mdns" if addr.endswith(".local") else "host_ip"] += 1
        elif typ in ("srflx", "relay"):
            kinds[typ] += 1
        else:
            kinds["other"] += 1
    return kinds


def sdp_addresses(sdp):
    """Connection addresses of every candidate line in an SDP blob."""
    return re.findall(r"a=candidate:\S+ \d+ \S+ \d+ (\S+) \d+ typ (\S+)", sdp or "")


def trial(cond, variant, rep, timeout_ms):
    name, mode, isolation, mdns_filtered, gw_forward, turn = cond
    a = ADDR[mode]
    servers = [{"urls": "stun:%s:3478" % TURN_IP}]
    if turn:
        servers.append({"urls": "turn:%s:3478?transport=udp" % TURN_IP, "username": "cw", "credential": "cwpass"})
    if mode == "dual":
        servers.append({"urls": "stun:[%s]:3478" % TURN_IP6})
        if turn:
            servers.append({"urls": "turn:[%s]:3478?transport=udp" % TURN_IP6, "username": "cw", "credential": "cwpass"})
    cfg = {"iceServers": servers}
    pcap = "/tmp/cw_trial.txt"
    capture_filter = "(udp and src host %s and dst host %s) or (udp dst port 5353 and src host %s)" % (a["c1"], a["c2"], a["c1"])
    if mode == "dual":
        capture_filter += " or (udp and src host %s and dst host %s)" % (a["c1_6"], a["c2_6"])
    dump = subprocess.Popen(
        ["ip", "netns", "exec", "cw-ap", "tcpdump", "-l", "-n", "-e", "-i", "ap-c1", "-Q", "in",
         capture_filter],
        stdout=open(pcap, "w"), stderr=subprocess.DEVNULL)
    diag = None
    if os.environ.get("CW_DIAG"):
        diag = subprocess.Popen(["ip", "netns", "exec", "cw-ap", "tcpdump", "-l", "-n", "-i", "br0", "udp port 5353"],
                                stdout=open("/tmp/cw_mdns_diag.txt", "w"), stderr=subprocess.DEVNULL)
    time.sleep(0.4)
    row = {"condition": name, "addressing": mode, "isolation": isolation, "mdns_filtered": mdns_filtered,
           "gw_forward": gw_forward, "turn_offered": turn, "variant": variant, "rep": rep}
    a1 = a2 = None
    try:
        a1, a2 = Agent("cw-c1", variant, cfg), Agent("cw-c2", variant, cfg)
        row["browser_version"] = a1.hello.get("version")
        offer = a1.call(cmd="offer")
        answer = a2.call(cmd="answer", sdp=offer.get("sdp", ""))
        a1.call(cmd="accept", sdp=answer.get("sdp", ""))
        r1 = a1.call(cmd="result", timeout_ms=timeout_ms)
        r2 = a2.call(cmd="result", timeout_ms=3000)
        row["error"] = offer.get("error") or answer.get("error") or r1.get("error")
        row["connected"] = r1.get("connectionState") == "connected"
        row["c1_state"] = r1.get("connectionState")
        row["c1_states"] = r1.get("states")
        row["c1_ice_state"] = r1.get("iceState")
        row["c2_state"] = r2.get("connectionState")
        row["echo_ok"] = r1.get("echo") == "echo:ping"
        row["media"] = r1.get("media")
        row["connect_ms"] = r1.get("connectMs")
        row["c1_candidates"] = classify(r1.get("cands", []))
        row["c2_candidates"] = classify(r2.get("cands", []))
        row["selected_local"] = r1.get("local")
        row["selected_remote"] = r1.get("remote")
        # What c2 was told about c1 through signalling.
        told = sdp_addresses(offer.get("sdp", ""))
        row["offer_candidates"] = told
        row["c1_iface_addr_in_offer"] = any(addr == a["c1"] for addr, _ in told)
        row["c1_iface_addr_as"] = sorted({typ for addr, typ in told if addr == a["c1"]})
        if mode == "dual":
            row["c1_v6_addr_in_offer"] = any(addr == a["c1_6"] for addr, _ in told)
            row["c1_v6_addr_as"] = sorted({typ for addr, typ in told if addr == a["c1_6"]})
    except Exception as exc:
        row["error"] = "orchestrator: %s" % exc
    finally:
        for ag in (a1, a2):
            if ag is not None:
                ag.close()
        time.sleep(0.3)
        dump.send_signal(signal.SIGINT)
        dump.wait(timeout=5)
        if diag is not None:
            diag.send_signal(signal.SIGINT)
            diag.wait(timeout=5)
    peer_mac, gw_mac = mac("cw-c2", "eth0"), mac("cw-gw", "lan0")
    to_peer = to_gw = mdns = v6_direct = 0
    with open(pcap) as fh:
        for line in fh:
            if ".5353: " in line:
                mdns += 1
                continue
            m = re.search(r"> ([0-9a-f:]{17})", line)
            if not m:
                continue
            if m.group(1) == peer_mac:
                to_peer += 1
                if "IPv6" in line:
                    v6_direct += 1
            elif m.group(1) == gw_mac:
                to_gw += 1
    row["pkts_c1_to_c2_direct_l2"] = to_peer
    row["pkts_c1_to_c2_via_gateway"] = to_gw
    row["mdns_pkts_from_c1"] = mdns
    row["pkts_direct_ipv6"] = v6_direct
    return row


def file_sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "raw", "trials.jsonl"))
    ap.add_argument("--timeout-ms", type=int, default=25000)
    ap.add_argument("--only", default="", help="comma-separated condition-name prefixes")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    prov = {
        "schema_version": 1,
        "kernel": platform.release(),
        "wan_delay_ms_each_way": int(os.environ.get("CW_WAN_DELAY_MS", "0")),
        "settle_ms": int(os.environ.get("CW_SETTLE_MS", "1500")),
        "command": " ".join(sys.argv),
        "script_sha256_16": {f: file_sha(os.path.join(HERE, f)) for f in ("run_matrix.py", "agent.py")},
        "coturn": sh("turnserver --version", check=False).stdout.strip()[:40],
    }
    variants = [v for v in args.variants.split(",") if v]
    only = [o for o in args.only.split(",") if o]
    tmp = args.out + ".partial"
    with open(tmp, "w") as out:
        current_mode = None
        for cond in CONDITIONS:
            if only and not any(cond[0].startswith(o) for o in only):
                continue
            if cond[1] != current_mode:
                setup(cond[1])
                current_mode = cond[1]
            apply_knobs(cond[2], cond[3], cond[4], dual=(cond[1] == "dual"))
            for variant in variants:
                for rep in range(1, args.reps + 1):
                    row = trial(cond, variant, rep, args.timeout_ms)
                    row["utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
                    row["provenance"] = prov
                    out.write(json.dumps(row) + "\n")
                    out.flush()
                    sel = (row.get("selected_local") or {}).get("type"), (row.get("selected_remote") or {}).get("type")
                    print("%-30s %-17s rep%d connected=%s pair=%s/%s l2=%s gw=%s err=%s" % (
                        cond[0], variant, rep, row.get("connected"), sel[0], sel[1],
                        row.get("pkts_c1_to_c2_direct_l2"), row.get("pkts_c1_to_c2_via_gateway"), row.get("error")),
                        flush=True)
    os.replace(tmp, args.out)
    teardown()


if __name__ == "__main__":
    main()
