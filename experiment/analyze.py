"""Summarise a trials file into CSV/JSON and write the LaTeX table bodies. No number is typed by hand.

Usage: python3 analyze.py <trials.jsonl> <processed dir> [<paper dir>]
"""
import collections
import csv
import json
import math
import os
import statistics
import sys

SHORT = {
    "chromium-default": "Chromium", "chromium-rawhost": "Chromium raw-host", "firefox-default": "Firefox",
    "firefox-rawhost": "Firefox raw-host", "webkit-default": "WebKit",
    "chromium-default-media": "Chromium +mic", "firefox-default-media": "Firefox +mic",
}


def path_of(row):
    if not row.get("connected"):
        return "none"
    kinds = {(row.get("selected_local") or {}).get("type"), (row.get("selected_remote") or {}).get("type")}
    if "relay" in kinds:
        return "relay"
    if row.get("pkts_c1_to_c2_direct_l2", 0) > 0:
        return "direct"
    if row.get("pkts_c1_to_c2_via_gateway", 0) > 0:
        return "gateway"
    return "other"


def binom_cdf(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def clopper_pearson(k, n, alpha=0.05):
    """Exact two-sided interval for a binomial proportion, by bisection."""
    def solve(f, lo, hi):
        for _ in range(60):
            mid = (lo + hi) / 2
            if f(mid):
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    lower = 0.0 if k == 0 else solve(lambda p: 1 - binom_cdf(k - 1, n, p) < alpha / 2, 0.0, 1.0)
    upper = 1.0 if k == n else solve(lambda p: binom_cdf(k, n, p) > alpha / 2, 0.0, 1.0)
    return round(lower, 3), round(upper, 3)


def code(s):
    parts = []
    for key, letter in (("direct", "D"), ("gateway", "G"), ("relay", "R"), ("other", "O"), ("none", "--")):
        k = s["path_" + key]
        if k:
            parts.append("%s\\,%d" % (letter, k) if letter != "--" else "--\\,%d" % k)
    return " ".join(parts)


def main():
    src, outdir = sys.argv[1], sys.argv[2]
    paper = sys.argv[3] if len(sys.argv) > 3 else None
    os.makedirs(outdir, exist_ok=True)
    rows = [json.loads(line) for line in open(src)]
    variants = list(collections.OrderedDict((r["variant"], 1) for r in rows))
    conds = collections.OrderedDict()
    groups = collections.OrderedDict()
    for r in rows:
        conds.setdefault(r["condition"], r)
        groups.setdefault((r["condition"], r["variant"]), []).append(r)
    summary = []
    for (cond, variant), rs in groups.items():
        paths = collections.Counter(path_of(r) for r in rs)
        times = [r["connect_ms"] for r in rs if r.get("connected") and r.get("connect_ms") is not None]
        n = len(rs)
        top = max(("direct", "gateway", "relay", "other", "none"), key=lambda k: paths[k])
        summary.append({
            "condition": cond, "variant": variant, "n": n,
            "errors": sum(1 for r in rs if r.get("error")),
            "connected": sum(1 for r in rs if r.get("connected")),
            "echo_ok": sum(1 for r in rs if r.get("echo_ok")),
            "path_direct": paths["direct"], "path_gateway": paths["gateway"],
            "path_relay": paths["relay"], "path_other": paths["other"], "path_none": paths["none"],
            "modal_path": top, "modal_ci95": clopper_pearson(paths[top], n),
            "direct_over_ipv6": sum(1 for r in rs if path_of(r) == "direct" and r.get("pkts_direct_ipv6", 0) > 0),
            "v4_iface_addr_disclosed": sum(1 for r in rs if r.get("c1_iface_addr_in_offer")),
            "v4_disclosed_as": "+".join(sorted({t for r in rs for t in r.get("c1_iface_addr_as", [])})),
            "v6_addr_disclosed": sum(1 for r in rs if r.get("c1_v6_addr_in_offer")),
            "v6_disclosed_as": "+".join(sorted({t for r in rs for t in r.get("c1_v6_addr_as", [])})),
            "host_mdns_trials": sum(1 for r in rs if (r.get("c1_candidates") or {}).get("host_mdns", 0) > 0),
            "host_ip_trials": sum(1 for r in rs if (r.get("c1_candidates") or {}).get("host_ip", 0) > 0),
            "media_granted": sum(1 for r in rs if r.get("media") == "granted"),
            "median_connect_ms": round(statistics.median(times), 1) if times else None,
            "browser_version": rs[0].get("browser_version"),
        })
    with open(os.path.join(outdir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)
    meta = {"source": src, "trials": len(rows), "provenance": rows[0].get("provenance"),
            "first_utc": rows[0].get("utc"), "last_utc": rows[-1].get("utc"),
            "errors": sum(1 for r in rows if r.get("error")),
            "browser_versions": sorted({"%s %s" % (r["variant"].split("-")[0], r.get("browser_version")) for r in rows}),
            "summary": summary}
    with open(os.path.join(outdir, "summary.json"), "w") as fh:
        json.dump(meta, fh, indent=1)
    for s in summary:
        print("%-28s %-22s n=%d D=%d G=%d R=%d O=%d none=%d v6direct=%d v4disc=%d(%s) v6disc=%d(%s) mdnsHost=%d mic=%d err=%d" % (
            s["condition"], s["variant"], s["n"], s["path_direct"], s["path_gateway"], s["path_relay"], s["path_other"],
            s["path_none"], s["direct_over_ipv6"], s["v4_iface_addr_disclosed"], s["v4_disclosed_as"],
            s["v6_addr_disclosed"], s["v6_disclosed_as"], s["host_mdns_trials"], s["media_granted"], s["errors"]))
    print("trials", len(rows), "errors", meta["errors"], meta["browser_versions"])
    if not paper:
        return

    by = {(s["condition"], s["variant"]): s for s in summary}
    lines = []
    for name, r in conds.items():
        desc = [
            name.split("-")[0],
            {"nat": "NAT", "public": "public", "dual": "NAT + IPv6"}[r["addressing"]],
            "on" if r["isolation"] else "off",
            "filtered" if r["mdns_filtered"] else "allowed",
            "forwards" if r["gw_forward"] else "drops",
            "yes" if r["turn_offered"] else "no",
        ]
        lines.append(" & ".join(desc + [code(by[(name, v)]) for v in variants]) + r" \\")
    with open(os.path.join(paper, "tab_results_body.tex"), "w", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(os.path.join(paper, "tab_results_head.tex"), "w", newline="\n") as fh:
        fh.write(" & ".join(r"\textbf{%s}" % SHORT.get(v, v) for v in variants) + "\n")

    disc = collections.OrderedDict()
    for r in rows:
        for fam, flag in (("v4", "c1_iface_addr_in_offer"), ("v6", "c1_v6_addr_in_offer")):
            if fam == "v6" and r["addressing"] != "dual":
                continue
            d = disc.setdefault((r["addressing"], fam, r["variant"]), [0, 0])
            d[0] += 1 if r.get(flag) else 0
            d[1] += 1
    lines = []
    for mode, fam, label in (("nat", "v4", "NAT: private IPv4"), ("public", "v4", "Public IPv4"),
                             ("dual", "v4", "Dual: private IPv4"), ("dual", "v6", "Dual: global IPv6")):
        if (mode, fam, variants[0]) not in disc:
            continue
        lines.append(" & ".join([label] + ["%d/%d" % tuple(disc[(mode, fam, v)]) for v in variants]) + r" \\")
    with open(os.path.join(paper, "tab_disclosure_body.tex"), "w", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    for f in ("tab_results_head.tex", "tab_results_body.tex", "tab_disclosure_body.tex"):
        print(open(os.path.join(paper, f)).read())


if __name__ == "__main__":
    main()
