"""One WebRTC endpoint. Run inside a client network namespace.

Usage: agent.py <variant> <RTCConfiguration as JSON>
Reads one JSON command per line on stdin and writes one JSON reply per line on stdout.
Signalling is carried by the orchestrator over these pipes, not over the emulated network.
"""
import http.server
import json
import os
import sys
import threading

from playwright.sync_api import sync_playwright

PAGE_JS = r"""
window.cw = {cands: [], states: [], echo: null, t0: null};
window.cwStart = async (cfg, media) => {
  const pc = new RTCPeerConnection(cfg);
  window.pc = pc;
  cw.media = null;
  if (media) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      stream.getTracks().forEach(t => pc.addTrack(t, stream));
      cw.media = 'granted';
    } catch (e) { cw.media = 'error:' + e.name; }
  }
  pc.onicecandidate = e => { if (e.candidate && e.candidate.candidate) cw.cands.push(e.candidate.candidate); };
  pc.onconnectionstatechange = () => cw.states.push([performance.now(), pc.connectionState]);
  pc.ondatachannel = e => { e.channel.onmessage = m => e.channel.send('echo:' + m.data); };
  return true;
};
window.cwGather = () => new Promise(res => {
  if (pc.iceGatheringState === 'complete') return res();
  const t = setTimeout(res, 10000);
  pc.addEventListener('icegatheringstatechange', () => {
    if (pc.iceGatheringState === 'complete') { clearTimeout(t); res(); }
  });
});
window.cwOffer = async () => {
  const dc = pc.createDataChannel('t');
  dc.onopen = () => dc.send('ping');
  dc.onmessage = m => { cw.echo = m.data; };
  await pc.setLocalDescription(await pc.createOffer());
  await cwGather();
  return pc.localDescription.sdp;
};
window.cwAnswer = async (sdp) => {
  await pc.setRemoteDescription({type: 'offer', sdp});
  await pc.setLocalDescription(await pc.createAnswer());
  await cwGather();
  return pc.localDescription.sdp;
};
window.cwAccept = async (sdp) => {
  cw.t0 = performance.now();
  await pc.setRemoteDescription({type: 'answer', sdp});
  return true;
};
window.cwResult = async (timeoutMs) => {
  const start = performance.now();
  while (performance.now() - start < timeoutMs) {
    const s = pc.connectionState;
    if (s === 'connected' || s === 'failed') break;
    await new Promise(r => setTimeout(r, 200));
  }
  if (pc.connectionState === 'connected') await new Promise(r => setTimeout(r, window.cwSettleMs || 1500));
  const stats = await pc.getStats();
  const all = {};
  stats.forEach(s => { all[s.id] = s; });
  let pair = null;
  stats.forEach(s => { if (s.type === 'transport' && s.selectedCandidatePairId) pair = all[s.selectedCandidatePairId]; });
  if (!pair) stats.forEach(s => {
    if (s.type === 'candidate-pair' && (s.selected || (s.nominated && s.state === 'succeeded'))) pair = s;
  });
  const pick = c => c ? {type: c.candidateType, address: c.address || c.ip || null, port: c.port || null,
                         protocol: c.protocol || null, relayProtocol: c.relayProtocol || null} : null;
  let connectedAt = null;
  for (const [t, s] of cw.states) if (s === 'connected' && connectedAt === null) connectedAt = t;
  return {
    connectionState: pc.connectionState, iceState: pc.iceConnectionState, cands: cw.cands, echo: cw.echo, media: cw.media,
    local: pair ? pick(all[pair.localCandidateId]) : null,
    remote: pair ? pick(all[pair.remoteCandidateId]) : null,
    connectMs: (connectedAt !== null && cw.t0 !== null) ? connectedAt - cw.t0 : null,
    states: cw.states.map(([t, s]) => [Math.round(t - (cw.t0 || 0)), s]),
  };
};
"""


class Quiet(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<!doctype html><title>cw</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def reply(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    variant, cfg = sys.argv[1], json.loads(sys.argv[2])
    server = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    with sync_playwright() as p:
        media = variant.endswith("-media")
        if variant.startswith("chromium"):
            args = ["--no-sandbox"]
            if variant == "chromium-rawhost":
                args.append("--disable-features=WebRtcHideLocalIpsWithMdns")
            if media:
                args += ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
            browser = p.chromium.launch(headless=True, args=args)
        elif variant.startswith("webkit"):
            browser = p.webkit.launch(headless=True)
        else:
            prefs = {}
            if variant == "firefox-rawhost":
                prefs["media.peerconnection.ice.obfuscate_host_addresses"] = False
            if media:
                prefs["media.navigator.streams.fake"] = True
                prefs["media.navigator.permission.disabled"] = True
            browser = p.firefox.launch(headless=True, firefox_user_prefs=prefs)
        page = browser.new_page()
        page.goto(url)
        page.add_script_tag(content=PAGE_JS)
        page.evaluate("ms => { window.cwSettleMs = ms; }", int(os.environ.get("CW_SETTLE_MS", "1500")))
        page.evaluate("([cfg, media]) => cwStart(cfg, media)", [cfg, media])
        reply({"ready": True, "version": browser.version})
        for line in sys.stdin:
            cmd = json.loads(line)
            try:
                if cmd["cmd"] == "offer":
                    reply({"sdp": page.evaluate("() => cwOffer()")})
                elif cmd["cmd"] == "answer":
                    reply({"sdp": page.evaluate("sdp => cwAnswer(sdp)", cmd["sdp"])})
                elif cmd["cmd"] == "accept":
                    reply({"ok": page.evaluate("sdp => cwAccept(sdp)", cmd["sdp"])})
                elif cmd["cmd"] == "result":
                    reply(page.evaluate("ms => cwResult(ms)", cmd["timeout_ms"]))
                elif cmd["cmd"] == "quit":
                    break
            except Exception as exc:  # report and keep the protocol in step
                reply({"error": str(exc)[:500]})
        browser.close()


if __name__ == "__main__":
    main()
