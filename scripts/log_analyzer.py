#!/usr/bin/env python3
"""
Log File Analyzer (AccuKnox Problem Statement 2, objective 3) - bonus third script.

Parses Apache/Nginx combined-format access logs and prints a summary report:
total requests, status-code breakdown, number of 404s, top requested pages,
top client IPs, top user agents and requests per hour.

Usage: log_analyzer.py access.log [access.log.1 ...] [--top 10] [--json]
"""
import argparse, gzip, json, re, sys
from collections import Counter

LINE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>\S+)\s?(?P<path>\S*)\s?(?P<proto>[^"]*)" '
    r'(?P<status>\d{3}) (?P<size>\S+)(?: "(?P<referer>[^"]*)" "(?P<agent>[^"]*)")?')


def open_any(path):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") else open(path, errors="replace")


def analyze(paths):
    c = dict(total=0, unparsed=0, status=Counter(), pages=Counter(), ips=Counter(), agents=Counter(),
             hours=Counter(), methods=Counter(), bytes=0, not_found_pages=Counter())
    for path in paths:
        with open_any(path) as fh:
            for line in fh:
                m = LINE.match(line)
                if not m:
                    c["unparsed"] += 1
                    continue
                d = m.groupdict()
                c["total"] += 1
                c["status"][d["status"]] += 1
                c["pages"][d["path"]] += 1
                c["ips"][d["ip"]] += 1
                c["methods"][d["method"]] += 1
                c["agents"][d["agent"] or "-"] += 1
                c["hours"][d["time"][:14]] += 1            # dd/Mon/yyyy:HH
                if d["size"].isdigit():
                    c["bytes"] += int(d["size"])
                if d["status"] == "404":
                    c["not_found_pages"][d["path"]] += 1
    return c


def report(c, top):
    print("=" * 60); print("WEB SERVER LOG REPORT"); print("=" * 60)
    print(f"Total requests : {c['total']}   (unparsed lines: {c['unparsed']})")
    print(f"Bytes served   : {c['bytes']:,}")
    print(f"404 errors     : {c['status'].get('404', 0)}")
    print("\nStatus codes:")
    for s, n in sorted(c["status"].items()):
        print(f"  {s}: {n}")
    print("\nMethods:", ", ".join(f"{m}={n}" for m, n in c["methods"].most_common()))
    def block(title, counter):
        print(f"\nTop {top} {title}:")
        for k, n in counter.most_common(top):
            print(f"  {n:>7}  {k}")
    block("requested pages", c["pages"])
    block("pages returning 404", c["not_found_pages"])
    block("client IPs", c["ips"])
    block("user agents", c["agents"])
    print("\nRequests per hour:")
    for h, n in sorted(c["hours"].items()):
        print(f"  {h}  {n}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("logs", nargs="+"); p.add_argument("--top", type=int, default=10); p.add_argument("--json", action="store_true")
    a = p.parse_args()
    c = analyze(a.logs)
    if a.json:
        out = {k: (dict(v.most_common(a.top)) if isinstance(v, Counter) else v) for k, v in c.items()}
        print(json.dumps(out, indent=2))
    else:
        report(c, a.top)


if __name__ == "__main__":
    sys.exit(main())
