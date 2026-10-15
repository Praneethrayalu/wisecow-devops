#!/usr/bin/env python3
"""
Log File Analyzer (AccuKnox Problem Statement 2, objective 3).

Parses Apache/Nginx "common" or "combined" access logs and reports: total requests,
status-code breakdown, 404 count, top pages, top client IPs, top user agents,
methods and requests per hour. Lines that do not match the format are counted as
"unparsed" (never silently dropped). Plain and .gz files are supported; "-" reads stdin.

Exit codes: 0 = report produced, 1 = input readable but no line could be parsed,
            2 = bad arguments or an input file cannot be read.

Usage: log_analyzer.py access.log [access.log.1.gz ...] [--top 10] [--json]
"""
import argparse
import gzip
import json
import re
import sys
from collections import Counter

LINE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+)(?: (?P<proto>[^"]*))?" '
    r'(?P<status>\d{3}) (?P<size>\d+|-)(?: "(?P<referer>[^"]*)" "(?P<agent>[^"]*)")?\s*$')

COUNTERS = ("status", "pages", "ips", "agents", "hours", "methods", "not_found_pages")


def new_stats() -> dict:
    stats = {"files": [], "total": 0, "unparsed": 0, "bytes": 0}
    stats.update({name: Counter() for name in COUNTERS})
    return stats


def analyze_lines(lines, stats: dict) -> dict:
    for line in lines:
        if not line.strip():
            continue
        m = LINE.match(line)
        if not m:
            stats["unparsed"] += 1
            continue
        d = m.groupdict()
        stats["total"] += 1
        stats["status"][d["status"]] += 1
        stats["pages"][d["path"]] += 1
        stats["ips"][d["ip"]] += 1
        stats["methods"][d["method"]] += 1
        stats["agents"][d["agent"] or "-"] += 1
        stats["hours"][d["time"][:14]] += 1          # dd/Mon/yyyy:HH
        if d["size"] != "-":
            stats["bytes"] += int(d["size"])
        if d["status"] == "404":
            stats["not_found_pages"][d["path"]] += 1
    return stats


def open_log(path: str):
    if path == "-":
        return sys.stdin
    if path.endswith(".gz"):
        return gzip.open(path, "rt", errors="replace")
    return open(path, errors="replace")


def analyze(paths) -> dict:
    stats = new_stats()
    for path in paths:
        fh = open_log(path)          # OSError propagates to main() -> exit 2
        try:
            analyze_lines(fh, stats)
        finally:
            if fh is not sys.stdin:
                fh.close()
        stats["files"].append(path)
    return stats


def to_json(stats: dict, top: int) -> dict:
    out = {k: v for k, v in stats.items() if k not in COUNTERS}
    out["not_found"] = stats["status"].get("404", 0)
    out["status"] = dict(sorted(stats["status"].items()))
    out["methods"] = dict(stats["methods"].most_common())
    out["hours"] = dict(sorted(stats["hours"].items()))
    for name in ("pages", "ips", "agents", "not_found_pages"):
        out[f"top_{name}"] = [{"value": k, "count": n} for k, n in stats[name].most_common(top)]
    return out


def print_report(stats: dict, top: int) -> None:
    print("=" * 60)
    print("WEB SERVER LOG REPORT")
    print("=" * 60)
    print(f"Files          : {', '.join(stats['files'])}")
    print(f"Total requests : {stats['total']}   (unparsed lines: {stats['unparsed']})")
    print(f"Bytes served   : {stats['bytes']:,}")
    print(f"404 errors     : {stats['status'].get('404', 0)}")
    print("\nStatus codes:")
    for code, n in sorted(stats["status"].items()):
        print(f"  {code}: {n}")
    print("\nMethods:", ", ".join(f"{m}={n}" for m, n in stats["methods"].most_common()) or "-")
    for title, name in (("requested pages", "pages"), ("pages returning 404", "not_found_pages"),
                        ("client IPs", "ips"), ("user agents", "agents")):
        print(f"\nTop {top} {title}:")
        for key, n in stats[name].most_common(top):
            print(f"  {n:>7}  {key}")
    print("\nRequests per hour:")
    for hour, n in sorted(stats["hours"].items()):
        print(f"  {hour}  {n}")


def positive_int(value: str) -> int:
    try:
        i = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}")
    if i < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1: {value!r}")
    return i


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("logs", nargs="+", help="access log files (.gz ok, '-' = stdin)")
    p.add_argument("--top", type=positive_int, default=10, help="how many entries per top-N list (default 10)")
    p.add_argument("--json", action="store_true", help="print the report as one JSON document on stdout")
    a = p.parse_args(argv)
    try:
        stats = analyze(a.logs)
    except (OSError, EOFError) as e:      # missing/unreadable file, corrupt .gz
        print(f"log_analyzer: cannot read {getattr(e, 'filename', None) or 'input'}: "
              f"{getattr(e, 'strerror', None) or e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(to_json(stats, a.top), indent=2))
    else:
        print_report(stats, a.top)
    if stats["total"] == 0:
        print(f"log_analyzer: no parseable lines ({stats['unparsed']} unparsed)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
