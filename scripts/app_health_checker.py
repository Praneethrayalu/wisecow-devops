#!/usr/bin/env python3
"""
Application Health Checker (AccuKnox Problem Statement 2, objective 4).

Checks whether an HTTP application is UP or DOWN by looking at the HTTP status code
and response time. Exit code 0 = UP, 1 = DOWN, so it works in CI, cron and k8s jobs.

Usage:
  app_health_checker.py URL [URL ...] [--expect 200] [--timeout 5] [--retries 3]
                        [--interval 30] [--log app_health.log] [--json] [--insecure]
"""
import argparse
import json
import logging
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def check_once(url: str, expect: int, timeout: float, insecure: bool = False) -> dict:
    started = time.monotonic()
    result = {"url": url, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wisecow-health/1.0"}, method="GET")
        ctx = ssl._create_unverified_context() if insecure else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read(4096)
            result.update(status_code=resp.status, latency_ms=round((time.monotonic() - started) * 1000, 1),
                          body_bytes=len(body))
            result["state"] = "UP" if resp.status == expect else "DOWN"
            result["reason"] = f"HTTP {resp.status}" + ("" if resp.status == expect else f" (expected {expect})")
    except urllib.error.HTTPError as e:            # server answered, but with an error code
        result.update(status_code=e.code, latency_ms=round((time.monotonic() - started) * 1000, 1),
                      state="UP" if e.code == expect else "DOWN", reason=f"HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:             # DNS failure, connection refused, TLS error ...
        result.update(status_code=None, latency_ms=None, state="DOWN", reason=f"connection error: {e.reason}")
    except socket.timeout:
        result.update(status_code=None, latency_ms=None, state="DOWN", reason=f"timeout after {timeout}s")
    except Exception as e:                         # anything else is still "not functioning correctly"
        result.update(status_code=None, latency_ms=None, state="DOWN", reason=f"{type(e).__name__}: {e}")
    return result


def check_with_retries(url: str, expect: int, timeout: float, retries: int, backoff: float = 1.0, insecure: bool = False) -> dict:
    last = None
    for attempt in range(1, retries + 1):
        last = check_once(url, expect, timeout, insecure)
        last["attempt"] = attempt
        if last["state"] == "UP":
            break
        time.sleep(backoff * attempt)
    return last


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", nargs="+", help="one or more application URLs")
    p.add_argument("--expect", type=int, default=200, help="HTTP status that means UP (default 200)")
    p.add_argument("--timeout", type=float, default=5.0, help="seconds to wait per request")
    p.add_argument("--retries", type=int, default=3, help="attempts before declaring DOWN")
    p.add_argument("--interval", type=float, default=0, help="seconds between rounds; 0 = run once")
    p.add_argument("--log", default="app_health.log", help="log file (console always gets output)")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON lines")
    p.add_argument("--insecure", action="store_true", help="skip TLS certificate verification (self-signed certs)")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(a.log)])
    log = logging.getLogger("health")

    while True:
        all_up = True
        for url in a.urls:
            r = check_with_retries(url, a.expect, a.timeout, a.retries, insecure=a.insecure)
            all_up &= r["state"] == "UP"
            if a.json:
                print(json.dumps(r))
            line = f"{r['state']:<4} {url}  {r['reason']}" + (f"  {r['latency_ms']} ms" if r.get("latency_ms") else "") \
                   + (f"  (attempt {r['attempt']})" if r["attempt"] > 1 else "")
            (log.info if r["state"] == "UP" else log.error)(line)
        if a.interval <= 0:
            return 0 if all_up else 1
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
