#!/usr/bin/env bash
# Black-box smoke test for a running wisecow (docker run, port-forward or ingress).
# Usage: tests/smoke_test.sh [BASE_URL]   (default http://127.0.0.1:4499)
#        CURL_OPTS="--cacert ca.crt" tests/smoke_test.sh https://wisecow.local
# Exit code 0 = every check passed, 1 = at least one check failed.
# pass/fail always return 0, so `test && pass || fail` is safe here:
# shellcheck disable=SC2015
set -uo pipefail
URL=${1:-http://127.0.0.1:4499}
URL=${URL%/}
read -r -a EXTRA <<< "${CURL_OPTS:-}"
fails=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }
code() { curl -s -m 10 -o /dev/null -w '%{http_code}' "${EXTRA[@]}" "$@"; }

# 1. GET / -> 200, text/html, a cow inside <pre>
hdrs=$(mktemp); body=$(mktemp); trap 'rm -f "$hdrs" "$body"' EXIT
status=$(curl -s -m 10 -D "$hdrs" -o "$body" -w '%{http_code}' "${EXTRA[@]}" "$URL/")
[ "$status" = 200 ] && pass "GET / -> 200" || fail "GET / -> $status (want 200)"
grep -qi '^content-type: text/html' "$hdrs" && pass "Content-Type text/html" || fail "missing Content-Type text/html"
grep -q '<pre>' "$body" && grep -q '(oo)' "$body" && pass "body has <pre> and the cow" || fail "body has no cow"

# 2. methods and paths
[ "$(code -I "$URL/")" = 200 ] && pass "HEAD / -> 200" || fail "HEAD / not 200"
[ "$(code -X POST -d x=1 "$URL/")" = 405 ] && pass "POST / -> 405" || fail "POST / not 405"
[ "$(code "$URL/no-such-page")" = 404 ] && pass "GET /no-such-page -> 404" || fail "unknown path not 404"

# 3. back-to-back (the original nc loop reset these) and concurrent requests
bad=0; for _ in $(seq 20); do [ "$(code "$URL/")" = 200 ] || bad=$((bad + 1)); done
[ "$bad" -eq 0 ] && pass "20 back-to-back requests -> 20 x 200" || fail "$bad of 20 back-to-back requests failed"
results=$(seq 30 | xargs -P 30 -I{} curl -s -m 20 -o /dev/null -w '%{http_code}\n' "${EXTRA[@]}" "$URL/")
ok=$(grep -c '^200$' <<< "$results")
[ "$ok" -eq 30 ] && pass "30 concurrent requests -> 30 x 200" || fail "only $ok of 30 concurrent requests got 200"

echo "----"
if [ "$fails" -eq 0 ]; then echo "smoke test: all checks passed against $URL"; else echo "smoke test: $fails check(s) failed against $URL"; fi
[ "$fails" -eq 0 ]
