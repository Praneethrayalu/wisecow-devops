#!/usr/bin/env python3
"""
Application Health Checker (AccuKnox Problem Statement 2, objective 4).

Decides whether an HTTP(S) application is UP or DOWN from its HTTP status code.
A check is retried (with a short back-off) before a URL is declared DOWN.

Exit codes: 0 = every URL is UP, 1 = at least one URL is DOWN, 2 = bad arguments
            (or the log file cannot be opened), 130 = interrupted with Ctrl+C.

Examples:
  app_health_checker.py http://localhost:8080
  app_health_checker.py https://wisecow.local --cacert ca.crt --retries 3 --timeout 5
  app_health_checker.py http://a http://b --json            # one JSON object per URL on stdout
  app_health_checker.py http://a --interval 30 --log health.log   # keep checking every 30 s
"""
import argparse
import http.client
import json
import logging
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

LOG = logging.getLogger("app_health")


def _ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def check_once(url: str, expect: int, timeout: float, ssl_context=None) -> dict:
    """One HTTP GET. Never raises: every failure becomes state DOWN with a reason."""
    started = time.monotonic()
    result = {"url": url, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "status_code": None, "latency_ms": None}
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        result.update(state="DOWN", reason="only http:// and https:// URLs are checked")
        return result
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wisecow-health/1.1"}, method="GET")
        # scheme restricted to http/https just above, so file:// etc. can never be opened
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:  # nosec B310
            resp.read(65536)
            status = resp.status
    except urllib.error.HTTPError as e:            # the server answered with 4xx/5xx
        status = e.code
    except urllib.error.URLError as e:             # DNS failure, refused, TLS error, connect timeout
        reason = e.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            result.update(state="DOWN", reason=f"timeout after {timeout}s")
        else:
            result.update(state="DOWN", reason=f"connection error: {reason}")
        return result
    except (socket.timeout, TimeoutError):          # read timeout after the connection was made
        result.update(state="DOWN", reason=f"timeout after {timeout}s")
        return result
    except (OSError, ValueError, http.client.HTTPException) as e:
        result.update(state="DOWN", reason=f"{type(e).__name__}: {e}")
        return result
    result.update(status_code=status, latency_ms=_ms(started))
    result["state"] = "UP" if status == expect else "DOWN"
    result["reason"] = f"HTTP {status}" + ("" if status == expect else f" (expected {expect})")
    return result


def check_with_retries(url, expect, timeout, retries, backoff=1.0, ssl_context=None, sleep=time.sleep) -> dict:
    """Try up to `retries` times; wait backoff*attempt seconds between attempts (not after the last)."""
    result = {}
    for attempt in range(1, retries + 1):
        result = check_once(url, expect, timeout, ssl_context)
        result["attempts"] = attempt
        if result["state"] == "UP":
            break
        if attempt < retries:
            LOG.debug("attempt %d for %s failed: %s", attempt, url, result["reason"])
            sleep(backoff * attempt)
    return result


def valid_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise argparse.ArgumentTypeError(f"not an http(s) URL: {value!r}")
    return value


def positive_float(value: str) -> float:
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}")
    if not f > 0:
        raise argparse.ArgumentTypeError(f"must be > 0: {value!r}")
    return f


def non_negative_float(value: str) -> float:
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}")
    if f < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0: {value!r}")
    return f


def bounded_int(lo: int, hi: int):
    def parse(value: str) -> int:
        try:
            i = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not an integer: {value!r}")
        if not lo <= i <= hi:
            raise argparse.ArgumentTypeError(f"must be between {lo} and {hi}: {value!r}")
        return i
    return parse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", nargs="+", type=valid_url, help="one or more http(s) URLs")
    p.add_argument("--expect", type=bounded_int(100, 599), default=200, help="HTTP status that means UP (default 200)")
    p.add_argument("--timeout", type=positive_float, default=5.0, help="seconds per request (default 5)")
    p.add_argument("--retries", type=bounded_int(1, 20), default=3, help="attempts before DOWN (default 3)")
    p.add_argument("--backoff", type=non_negative_float, default=1.0, help="back-off base in seconds (default 1)")
    p.add_argument("--interval", type=non_negative_float, default=0, help="seconds between rounds; 0 = run once")
    p.add_argument("--log", help="also append log lines to this file")
    p.add_argument("--json", action="store_true", help="print one JSON object per URL on stdout (logs go to stderr)")
    tls = p.add_mutually_exclusive_group()
    tls.add_argument("--cacert", help="trust this CA/self-signed certificate file (keeps hostname checks)")
    tls.add_argument("--insecure", action="store_true", help="skip TLS verification entirely (debug only)")
    p.add_argument("-v", "--verbose", action="store_true", help="log every failed attempt")
    return p


def main(argv=None) -> int:
    p = build_parser()
    a = p.parse_args(argv)

    # Human-readable log lines go to stderr in --json mode so stdout stays pure JSON.
    handlers = [logging.StreamHandler(sys.stderr if a.json else sys.stdout)]
    if a.log:
        try:
            handlers.append(logging.FileHandler(a.log))
        except OSError as e:
            p.error(f"cannot open log file {a.log!r}: {e.strerror}")
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers, force=True)

    ctx = None
    if a.cacert:
        try:
            ctx = ssl.create_default_context(cafile=a.cacert)
        except (OSError, ssl.SSLError) as e:
            p.error(f"cannot load --cacert {a.cacert!r}: {e}")
    elif a.insecure:
        LOG.warning("--insecure: TLS certificates are NOT verified")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        while True:
            all_up = True
            for url in a.urls:
                r = check_with_retries(url, a.expect, a.timeout, a.retries, a.backoff, ctx)
                all_up = all_up and r["state"] == "UP"
                if a.json:
                    print(json.dumps(r), flush=True)
                line = f"{r['state']:<4} {url}  {r['reason']}"
                if r["latency_ms"] is not None:
                    line += f"  {r['latency_ms']} ms"
                if r["attempts"] > 1:
                    line += f"  (attempt {r['attempts']}/{a.retries})"
                (LOG.info if r["state"] == "UP" else LOG.error)(line)
            if a.interval <= 0:
                return 0 if all_up else 1
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
