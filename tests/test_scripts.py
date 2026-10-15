"""Tests for the PS2 ops scripts: success paths and the failure paths that matter in operations
(unreachable service, HTTP errors, timeouts, bad arguments, unreadable files, malformed logs).

Only the Python standard library + pytest are needed. Local HTTP servers are started on free
ports and always shut down in fixture teardown.
"""
import gzip
import http.server
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import app_health_checker as ahc  # noqa: E402
import log_analyzer as la  # noqa: E402

PY = sys.executable


def run(*args, timeout=60, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, **kw)


# ---------------------------------------------------------------- fixtures
class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server naming)
        if self.path.startswith("/slow"):
            time.sleep(3)
        code = {"/ok": 200, "/missing": 404, "/boom": 500}.get(self.path.split("?")[0], 200)
        body = b"hello"
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()
    t.join(5)


@pytest.fixture
def closed_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now -> connection refused
    return f"http://127.0.0.1:{port}"


LOG_LINES = [
    '10.0.0.1 - - [15/Oct/2026:10:00:01 +0000] "GET / HTTP/1.1" 200 512 "-" "curl/8.0"',
    '10.0.0.1 - - [15/Oct/2026:10:00:02 +0000] "GET /missing HTTP/1.1" 404 10 "-" "curl/8.0"',
    '10.0.0.2 - - [15/Oct/2026:10:05:00 +0000] "POST /login HTTP/1.1" 500 - "-" "Mozilla/5.0"',
    '10.0.0.3 - - [15/Oct/2026:11:00:00 +0000] "GET /missing HTTP/1.1" 404 10',      # common format
]


@pytest.fixture
def access_log(tmp_path):
    p = tmp_path / "access.log"
    p.write_text("\n".join(LOG_LINES) + "\n")
    return p


# ---------------------------------------------------------------- app_health_checker.py
def test_health_up(server):
    r = ahc.check_once(server + "/ok", 200, 2)
    assert r["state"] == "UP" and r["status_code"] == 200 and r["latency_ms"] is not None


@pytest.mark.parametrize("path,code", [("/missing", 404), ("/boom", 500)])
def test_health_http_error_is_down(server, path, code):
    r = ahc.check_once(server + path, 200, 2)
    assert r["state"] == "DOWN" and r["status_code"] == code


def test_health_custom_expected_status(server):
    assert ahc.check_once(server + "/missing", 404, 2)["state"] == "UP"


def test_health_connection_refused_is_down(closed_port_url):
    r = ahc.check_once(closed_port_url, 200, 2)
    assert r["state"] == "DOWN" and "connection error" in r["reason"]


def test_health_timeout_is_down(server):
    r = ahc.check_once(server + "/slow", 200, 0.5)
    assert r["state"] == "DOWN" and "timeout" in r["reason"]


def test_health_retries_then_gives_up_without_trailing_sleep(closed_port_url):
    sleeps = []
    r = ahc.check_with_retries(closed_port_url, 200, 1, retries=3, backoff=1, sleep=sleeps.append)
    assert r["state"] == "DOWN" and r["attempts"] == 3
    assert sleeps == [1, 2]          # back-off between attempts, none after the last one


def test_health_cli_json_stdout_is_pure_json(server, closed_port_url):
    p = run(PY, str(SCRIPTS / "app_health_checker.py"), server + "/ok", closed_port_url,
            "--json", "--retries", "1")
    assert p.returncode == 1                                   # one URL is DOWN
    rows = [json.loads(line) for line in p.stdout.splitlines()]  # every stdout line parses
    assert [r["state"] for r in rows] == ["UP", "DOWN"]
    assert "DOWN" in p.stderr                                  # human log went to stderr


def test_health_cli_exit_zero_when_all_up(server, tmp_path):
    log = tmp_path / "h.log"
    p = run(PY, str(SCRIPTS / "app_health_checker.py"), server + "/ok", "--log", str(log))
    assert p.returncode == 0 and "UP" in log.read_text()


@pytest.mark.parametrize("args", [
    ["--retries", "0"], ["--retries", "x"], ["--timeout", "0"], ["--timeout", "-1"],
    ["--expect", "999"], ["--interval", "-5"], ["--log", "/nonexistent-dir/x.log"],
])
def test_health_cli_rejects_bad_arguments(server, args):
    p = run(PY, str(SCRIPTS / "app_health_checker.py"), server + "/ok", *args)
    assert p.returncode == 2 and "error" in p.stderr


@pytest.mark.parametrize("url", ["localhost:8080", "ftp://example.com", "http://"])
def test_health_cli_rejects_bad_urls(url):
    p = run(PY, str(SCRIPTS / "app_health_checker.py"), url)
    assert p.returncode == 2 and "not an http(s) URL" in p.stderr


# ---------------------------------------------------------------- log_analyzer.py
def test_log_counts(access_log):
    s = la.analyze([str(access_log)])
    assert s["total"] == 4 and s["unparsed"] == 0
    assert s["status"]["404"] == 2 and s["not_found_pages"]["/missing"] == 2
    assert s["bytes"] == 512 + 10 + 10 and s["ips"]["10.0.0.1"] == 2


def test_log_malformed_lines_are_counted_not_dropped(tmp_path):
    p = tmp_path / "mixed.log"
    p.write_text(LOG_LINES[0] + "\nthis is not a log line\n" + '1.2.3.4 - - [x] "GET" 200 1\n\n')
    s = la.analyze([str(p)])
    assert s["total"] == 1 and s["unparsed"] == 2   # blank line ignored


def test_log_gzip_and_multiple_files(access_log, tmp_path):
    gz = tmp_path / "access.log.1.gz"
    with gzip.open(gz, "wt") as fh:
        fh.write(LOG_LINES[0] + "\n")
    assert la.analyze([str(access_log), str(gz)])["total"] == 5


def test_log_cli_json_is_valid(access_log):
    p = run(PY, str(SCRIPTS / "log_analyzer.py"), str(access_log), "--json", "--top", "1")
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["total"] == 4 and out["not_found"] == 2
    assert out["top_pages"] == [{"value": "/missing", "count": 2}]


def test_log_cli_text_report(access_log):
    p = run(PY, str(SCRIPTS / "log_analyzer.py"), str(access_log))
    assert p.returncode == 0 and "404 errors     : 2" in p.stdout


def test_log_cli_all_malformed_exits_1(tmp_path):
    p = tmp_path / "junk.log"
    p.write_text("junk\nmore junk\n")
    r = run(PY, str(SCRIPTS / "log_analyzer.py"), str(p))
    assert r.returncode == 1 and "no parseable lines" in r.stderr


def test_log_cli_missing_file_exits_2(tmp_path):
    r = run(PY, str(SCRIPTS / "log_analyzer.py"), str(tmp_path / "nope.log"))
    assert r.returncode == 2 and "cannot read" in r.stderr and "Traceback" not in r.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read chmod-000 files")
def test_log_cli_unreadable_file_exits_2(access_log):
    access_log.chmod(0)
    try:
        r = run(PY, str(SCRIPTS / "log_analyzer.py"), str(access_log))
    finally:
        access_log.chmod(0o644)
    assert r.returncode == 2 and "Permission denied" in r.stderr


def test_log_cli_corrupt_gzip_exits_2(tmp_path):
    bad = tmp_path / "bad.gz"
    bad.write_bytes(b"not gzip at all")
    r = run(PY, str(SCRIPTS / "log_analyzer.py"), str(bad))
    assert r.returncode == 2 and "Traceback" not in r.stderr


def test_log_cli_rejects_bad_top(access_log):
    r = run(PY, str(SCRIPTS / "log_analyzer.py"), str(access_log), "--top", "0")
    assert r.returncode == 2


# ---------------------------------------------------------------- system_health_monitor.sh
MON = str(SCRIPTS / "system_health_monitor.sh")


def test_monitor_json_mode_healthy(tmp_path):
    p = run("bash", MON, "-j", "-c", "100", "-m", "100", "-d", "100", "-p", "1000000", "-l", str(tmp_path / "m.log"))
    assert p.returncode == 0
    d = json.loads(p.stdout)                  # stdout is exactly one JSON object
    assert d["status"] == "OK" and d["alerts"] == []
    assert (tmp_path / "m.log").read_text().count("cpu=") == 1


def test_monitor_alert_exit_1(tmp_path):
    p = run("bash", MON, "-j", "-p", "1", "-l", str(tmp_path / "m.log"))   # >1 process always true
    assert p.returncode == 1
    d = json.loads(p.stdout)
    assert d["status"] == "ALERT" and "processes" in d["alerts"]
    assert "ALERT" in (tmp_path / "m.log").read_text()


@pytest.mark.parametrize("args", [["-c", "abc"], ["-m", "0"], ["-d", "101"], ["-p", "-3"],
                                  ["-i", "1.5"], ["-P", "/no/such/path"], ["-l", "/no/such/dir/x.log"],
                                  ["-z"], ["extra-arg"]])
def test_monitor_rejects_bad_arguments(tmp_path, args):
    p = run("bash", MON, *args, cwd=tmp_path)
    assert p.returncode == 2
