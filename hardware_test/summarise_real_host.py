"""Summarise real_host_results.jsonl into counts used in the paper."""
import json
import statistics
import sys
from collections import Counter, defaultdict

rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
by = defaultdict(list)
for r in rows:
    by[r["config"]].append(r)
for cfg, rs in by.items():
    ok = [r for r in rs if not r.get("error")]
    print("==", cfg, "trials", len(rs), "errors", len(rs) - len(ok))
    print(" browser:", Counter(r["browser"].split(") ")[-1] for r in ok).most_common(2))
    print(" host classes:", Counter(json.dumps(r["host_classes"], sort_keys=True) for r in ok))
    print(" srflx classes:", Counter(json.dumps(r["srflx_classes"], sort_keys=True) for r in ok))
    print(" raw interface addr in offer:", sum(1 for r in ok if any(k != "mdns" for k in r["host_classes"])))
    print(" srflx present:", sum(1 for r in ok if r["srflx_classes"]),
          "srflx differs from host:", sum(1 for r in ok if r["srflx_differs_from_host"]))
    print(" relay cands:", sum(r["relay"] for r in ok))
    print(" loopback connected:", sum(r["loopback_connected"] for r in ok))
    print(" selected:", Counter(str((r["selected_local"], r["selected_remote"])) for r in ok))
    g = [r["gather_ms"] for r in ok]
    c = [r["loopback_connect_ms"] for r in ok if r["loopback_connect_ms"] is not None]
    print(" gather ms median %.0f min %d max %d" % (statistics.median(g), min(g), max(g)))
    if c:
        print(" connect ms median %.0f min %d max %d" % (statistics.median(c), min(c), max(c)))
