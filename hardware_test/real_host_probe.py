"""Real-host candidate disclosure probe (single laptop, real Wi-Fi NIC).

Opens Microsoft Edge (installed system browser) through Playwright and, in each
trial, gathers ICE candidates for one RTCPeerConnection against a public STUN
server, then connects two peer connections inside the same page (loopback) and
records the selected pair. It measures only what this machine's own browser
advertises; it sends nothing to other devices on the network.

Addresses are never written to disk: each candidate is reduced to a class
(mdns, private_v4, link_local_v6, global_v6, cgnat_v4, public_v4, ...).

Usage: python real_host_probe.py <trials_per_config> <out.jsonl> [channel]
  channel: msedge (default, Windows) or chrome (macOS/Linux with Google Chrome)
"""
import ipaddress
import json
import sys
import time
import datetime

from playwright.sync_api import sync_playwright

CONFIGS = {
    "edge_default": [],
    "edge_mdns_off": ["--disable-features=WebRtcHideLocalIpsWithMdns"],
}

JS = r"""
async () => {
  const cfg = {iceServers: [{urls: 'stun:stun.l.google.com:19302'}]};
  const gather = pc => new Promise(res => {
    const c = []; const t = setTimeout(() => res(c), 8000);
    pc.onicecandidate = e => { if (e.candidate && e.candidate.candidate) c.push(e.candidate.candidate);
                               else if (!e.candidate) { clearTimeout(t); res(c); } };
  });
  const p = new RTCPeerConnection(cfg); p.createDataChannel('x');
  const g = gather(p); const t0 = performance.now();
  await p.setLocalDescription(await p.createOffer());
  const cands = await g; const gather_ms = Math.round(performance.now() - t0); p.close();

  const a = new RTCPeerConnection(cfg), b = new RTCPeerConnection(cfg);
  a.onicecandidate = e => e.candidate && b.addIceCandidate(e.candidate).catch(()=>{});
  b.onicecandidate = e => e.candidate && a.addIceCandidate(e.candidate).catch(()=>{});
  const dc = a.createDataChannel('t'); let open = false; dc.onopen = () => { open = true; };
  const t1 = performance.now();
  await a.setLocalDescription(await a.createOffer()); await b.setRemoteDescription(a.localDescription);
  await b.setLocalDescription(await b.createAnswer()); await a.setRemoteDescription(b.localDescription);
  for (let i = 0; i < 100 && !open; i++) await new Promise(r => setTimeout(r, 100));
  const connect_ms = open ? Math.round(performance.now() - t1) : null;
  let local = null, remote = null;
  const st = await a.getStats(); const all = {}; st.forEach(s => all[s.id] = s);
  st.forEach(s => { if (s.type === 'transport' && s.selectedCandidatePairId) {
    const pr = all[s.selectedCandidatePairId];
    local = all[pr.localCandidateId]; remote = all[pr.remoteCandidateId]; } });
  const sel = local ? {local_type: local.candidateType, local_addr: local.address || local.ip,
                       remote_type: remote.candidateType, remote_addr: remote.address || remote.ip} : null;
  a.close(); b.close();
  return {cands, gather_ms, open, connect_ms, sel, ua: navigator.userAgent};
}
"""


def addr_class(addr):
    if addr is None:
        return None
    if addr == "":
        return "redacted"
    if addr.endswith(".local"):
        return "mdns"
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return "other"
    if ip.version == 4:
        if ip in ipaddress.ip_network("100.64.0.0/10"):
            return "cgnat_v4"
        if ip.is_private:
            return "private_v4"
        if ip.is_link_local:
            return "link_local_v4"
        return "public_v4"
    if ip.is_link_local:
        return "link_local_v6"
    if ip in ipaddress.ip_network("fc00::/7"):
        return "ula_v6"
    if ip.is_global:
        return "global_v6"
    return "other_v6"


def summarise(raw):
    host, srflx, relay = {}, {}, 0
    host_addrs, srflx_addrs = set(), set()
    for c in raw["cands"]:
        p = c.split()
        typ = p[p.index("typ") + 1]
        cls = addr_class(p[4])
        if typ == "host":
            host[cls] = host.get(cls, 0) + 1
            host_addrs.add(p[4])
        elif typ == "srflx":
            srflx[cls] = srflx.get(cls, 0) + 1
            srflx_addrs.add(p[4])
        elif typ == "relay":
            relay += 1
    sel = raw["sel"]
    return {
        "n_candidates": len(raw["cands"]),
        "host_classes": host,
        "srflx_classes": srflx,
        "relay": relay,
        "distinct_host_addrs": len(host_addrs),
        "srflx_differs_from_host": bool(srflx_addrs) and not (srflx_addrs & host_addrs),
        "gather_ms": raw["gather_ms"],
        "loopback_connected": raw["open"],
        "loopback_connect_ms": raw["connect_ms"],
        "selected_local": None if not sel else [sel["local_type"], addr_class(sel["local_addr"])],
        "selected_remote": None if not sel else [sel["remote_type"], addr_class(sel["remote_addr"])],
        "browser": raw["ua"],
    }


def main():
    n, out = int(sys.argv[1]), sys.argv[2]
    channel = sys.argv[3] if len(sys.argv) > 3 else "msedge"
    name_prefix = "edge" if channel == "msedge" else channel
    with sync_playwright() as pw, open(out, "a", encoding="utf-8") as fh:
        for name, args in CONFIGS.items():
            for i in range(n):
                br = pw.chromium.launch(channel=channel, headless=True, args=args)
                page = br.new_page()
                page.goto("about:blank")
                page.set_content("<title>probe</title>")
                try:
                    row = summarise(page.evaluate(JS))
                    row["error"] = None
                except Exception as e:  # keep failed trials visible
                    row = {"error": str(e)[:200]}
                br.close()
                row.update(config=name.replace("edge", name_prefix, 1), trial=i,
                           utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                print(name, i, row.get("host_classes"), row.get("srflx_classes"),
                      row.get("loopback_connected"), row.get("selected_local"), row.get("error"))
                time.sleep(0.5)


if __name__ == "__main__":
    main()
