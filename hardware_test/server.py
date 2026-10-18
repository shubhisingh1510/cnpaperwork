"""Hardware test helper for the WebRTC campus paper.

Run on the laptop:   python server.py   (macOS/Linux: python3 server.py)
Then open  http://<laptop-address>:8000/  on two devices that are on the Wi-Fi under test.
One device presses "Start as A", the other "Join as B" with the same room number.
Every finished attempt is appended to results.jsonl next to this file.

Uses only the Python standard library. It measures ordinary browser behaviour on
your own network and devices; it does not attack anything.
"""
import datetime
import http.server
import json
import os
import socket
import threading
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results.jsonl")
STORE = {}
LOCK = threading.Lock()

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WebRTC path test</title>
<style>
 body{font-family:system-ui,sans-serif;margin:16px;max-width:640px;line-height:1.4}
 label{display:block;margin-top:10px;font-weight:600}
 input{width:100%;padding:8px;font-size:16px;box-sizing:border-box}
 button{font-size:17px;padding:12px 14px;margin:12px 8px 0 0}
 #out{white-space:pre-wrap;background:#f2f2f2;padding:10px;margin-top:14px;font-family:monospace;font-size:13px}
 #big{font-size:20px;font-weight:700;margin-top:14px}
</style></head><body>
<h2>WebRTC path test</h2>
<label>Room number (same on both devices)</label><input id="room" value="1">
<label>This device and browser (for example: Android Chrome)</label><input id="device">
<label>Network setup (for example: isolation OFF)</label><input id="setup">
<button id="s">Test this device only</button><br><button id="a">Start as A</button><button id="b">Join as B</button>
<div id="big"></div><div id="out">Fill the boxes, then press A on one device and B on the other.</div>
<script>
const $ = id => document.getElementById(id);
const log = m => { $('out').textContent += '\n' + m; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function put(key, val){ await fetch('/api/put?room='+encodeURIComponent($('room').value)+'&key='+key,{method:'POST',body:JSON.stringify(val)}); }
async function get(key, seconds){
  for(let i=0;i<seconds;i++){
    const r = await fetch('/api/get?room='+encodeURIComponent($('room').value)+'&key='+key,{cache:'no-store'});
    if(r.status===200) return await r.json();
    await sleep(1000);
  }
  return null;
}
function gather(pc){ return new Promise(res=>{ if(pc.iceGatheringState==='complete') return res();
  const t=setTimeout(res,6000); pc.addEventListener('icegatheringstatechange',()=>{ if(pc.iceGatheringState==='complete'){clearTimeout(t);res();} }); }); }
function classify(cands){ const k={host_raw:[],host_hidden:0,reflexive:[],relay:0};
  for(const c of cands){ const p=c.split(' '); const typ=p[p.indexOf('typ')+1]; const addr=p[4];
    if(typ==='host'){ if(addr.endsWith('.local')) k.host_hidden++; else k.host_raw.push(addr); }
    else if(typ==='srflx') k.reflexive.push(addr); else if(typ==='relay') k.relay++; }
  return k; }
async function run(role){
  $('a').disabled=$('b').disabled=true; $('out').textContent='Role '+role+' started'; $('big').textContent='';
  const cands=[]; const pc=new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
  pc.onicecandidate=e=>{ if(e.candidate&&e.candidate.candidate) cands.push(e.candidate.candidate); };
  let echo=null, t0=null, tConn=null;
  pc.onconnectionstatechange=()=>{ if(pc.connectionState==='connected'&&tConn===null) tConn=performance.now(); };
  if(role==='A'){
    await fetch('/api/reset?room='+encodeURIComponent($('room').value));
    const dc=pc.createDataChannel('t'); dc.onopen=()=>dc.send('ping'); dc.onmessage=m=>{echo=m.data;};
    await pc.setLocalDescription(await pc.createOffer()); await gather(pc);
    await put('offer', pc.localDescription.sdp); log('Offer sent. Now press "Join as B" on the other device (waiting up to 2 minutes).');
    const ans=await get('answer',120); if(!ans){ log('No answer received. Check the room number on the other device.'); return done(); }
    t0=performance.now(); await pc.setRemoteDescription({type:'answer',sdp:ans});
  } else {
    pc.ondatachannel=e=>{ e.channel.onmessage=m=>{ echo=m.data; e.channel.send('echo:'+m.data); }; };
    log('Waiting for device A (up to 2 minutes).');
    const off=await get('offer',120); if(!off){ log('No offer found. Press "Start as A" on the other device first.'); return done(); }
    await pc.setRemoteDescription({type:'offer',sdp:off}); await pc.setLocalDescription(await pc.createAnswer()); await gather(pc);
    t0=performance.now(); await put('answer', pc.localDescription.sdp);
  }
  log('Connecting (up to 25 seconds).');
  for(let i=0;i<125;i++){ if(['connected','failed'].includes(pc.connectionState)) break; await sleep(200); }
  if(pc.connectionState==='connected') await sleep(1500);
  const stats=await pc.getStats(); const all={}; stats.forEach(s=>all[s.id]=s); let pair=null;
  stats.forEach(s=>{ if(s.type==='transport'&&s.selectedCandidatePairId) pair=all[s.selectedCandidatePairId]; });
  if(!pair) stats.forEach(s=>{ if(s.type==='candidate-pair'&&(s.selected||(s.nominated&&s.state==='succeeded'))) pair=s; });
  const pick=c=>c?{type:c.candidateType,address:c.address||c.ip||null}:null;
  const k=classify(cands);
  const result={role, room:$('room').value, device:$('device').value, setup:$('setup').value, userAgent:navigator.userAgent,
    connected:pc.connectionState==='connected', state:pc.connectionState, echo,
    connect_ms:(tConn!==null&&t0!==null)?Math.round(tConn-t0):null,
    local:pair?pick(all[pair.localCandidateId]):null, remote:pair?pick(all[pair.remoteCandidateId]):null,
    candidates:cands, advertised:k};
  const path=!result.connected?'NO CONNECTION':((result.local&&result.local.type==='relay')||(result.remote&&result.remote.type==='relay'))?'RELAY':
    (result.local&&result.remote&&result.local.type==='host'&&result.remote.type==='host')?'DIRECT (device to device)':'DIRECT via reflexive address';
  result.path=path;
  $('big').textContent='Result: '+path;
  log('This device advertised: '+(k.host_raw.length?'RAW local address '+k.host_raw.join(', '):'no raw local address')+
      (k.host_hidden?'; hidden .local name':'')+(k.reflexive.length?'; outside-visible address '+[...new Set(k.reflexive)].join(', '):''));
  await fetch('/api/result',{method:'POST',body:JSON.stringify(result)}); log('Saved on the laptop. Write this result in your sheet, then reload the page for the next test.');
  done();
}
function addrClass(a){
  if(a.endsWith('.local')) return 'hidden .local name';
  if(a.includes(':')){ const l=a.toLowerCase(); if(l.startsWith('fe80')) return 'IPv6 link-local';
    if(l.startsWith('fc')||l.startsWith('fd')) return 'IPv6 private (ULA)'; return 'IPv6 global'; }
  const o=a.split('.').map(Number);
  if(o[0]===10||(o[0]===172&&o[1]>=16&&o[1]<=31)||(o[0]===192&&o[1]===168)) return 'IPv4 private';
  if(o[0]===100&&o[1]>=64&&o[1]<=127) return 'IPv4 carrier NAT';
  if(o[0]===169&&o[1]===254) return 'IPv4 link-local';
  return 'IPv4 public'; }
async function single(){
  $('a').disabled=$('b').disabled=$('s').disabled=true; $('out').textContent='Checking this device (about 10 seconds)...'; $('big').textContent='';
  const cands=[]; const pc=new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
  pc.onicecandidate=e=>{ if(e.candidate&&e.candidate.candidate) cands.push(e.candidate.candidate); };
  pc.createDataChannel('t'); await pc.setLocalDescription(await pc.createOffer()); await gather(pc); pc.close();
  const host={}, srflx={}; let relay=0;
  for(const c of cands){ const p=c.split(' '); const typ=p[p.indexOf('typ')+1]; const k=addrClass(p[4]);
    if(typ==='host') host[k]=(host[k]||0)+1; else if(typ==='srflx') srflx[k]=(srflx[k]||0)+1; else if(typ==='relay') relay++; }
  const raw=Object.keys(host).filter(k=>k!=='hidden .local name');
  const result={mode:'single', device:$('device').value, setup:$('setup').value, userAgent:navigator.userAgent,
    n_candidates:cands.length, host_classes:host, srflx_classes:srflx, relay,
    reveals_local_address:raw.length>0};
  $('big').textContent='Result: '+(cands.length===0?'NO CANDIDATES':raw.length?'REVEALS local address':'HIDES local address');
  log('Local addresses shown: '+(Object.keys(host).length?Object.entries(host).map(([k,v])=>k+' x'+v).join(', '):'none'));
  log('Outside addresses shown: '+(Object.keys(srflx).length?Object.entries(srflx).map(([k,v])=>k+' x'+v).join(', '):'none'));
  await fetch('/api/result',{method:'POST',body:JSON.stringify(result)});
  log('Saved on the laptop (address types only, no actual addresses). Reload the page to test again.');
  done(); }
function done(){ $('a').disabled=$('b').disabled=$('s').disabled=false; }
$('s').onclick=()=>single().catch(e=>{log('Error: '+e);done();});
$('a').onclick=()=>run('A').catch(e=>{log('Error: '+e);done();});
$('b').onclick=()=>run('B').catch(e=>{log('Error: '+e);done();});
</script></body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _args(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        return url.path, q.get("room", ["1"])[0], q.get("key", [""])[0]

    def do_GET(self):
        path, room, key = self._args()
        if path == "/":
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/get":
            with LOCK:
                val = STORE.get((room, key))
            return self._send(200, json.dumps(val).encode()) if val is not None else self._send(204)
        if path == "/api/reset":
            with LOCK:
                for k in [k for k in STORE if k[0] == room]:
                    del STORE[k]
            return self._send(200, b"{}")
        self._send(404)

    def do_POST(self):
        path, room, key = self._args()
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if path == "/api/put" and key in ("offer", "answer"):
            with LOCK:
                STORE[(room, key)] = json.loads(body)
            return self._send(200, b"{}")
        if path == "/api/result":
            row = json.loads(body)
            row["utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            if row.get("mode") != "single":
                row["seen_from"] = self.client_address[0]
            with LOCK, open(RESULTS, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            print("saved:", row.get("mode", "pair"), "|", row.get("setup"), "|", row.get("device"), "|", row.get("role"), "|", row.get("path"))
            return self._send(200, b"{}")
        self._send(404)

    def log_message(self, *args):
        pass


def addresses():
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    # macOS and Linux often resolve the hostname to 127.0.0.1 only; ask the OS
    # which local address it would use for outside traffic (no packet is sent).
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        found.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


if __name__ == "__main__":
    print("Open one of these on each test device:")
    for a in addresses() or ["<this computer's address>"]:
        print("   http://%s:8000/" % a)
    print("Results are saved to", RESULTS)
    http.server.ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
