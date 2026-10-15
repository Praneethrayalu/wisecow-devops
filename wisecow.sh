#!/usr/bin/env bash
# Wisecow: serves a random fortune, drawn by cowsay, over HTTP.
#
# How it works
#   socat listens on $SRVPORT and forks one short-lived copy of this script per TCP
#   connection ("wisecow.sh --handle"). Each copy reads exactly one HTTP request from
#   stdin, writes one complete HTTP/1.1 response to stdout and exits. Because the
#   listener never stops listening, back-to-back and concurrent requests are not
#   refused (the original `nc -l` loop accepted one connection, exited and restarted,
#   so requests arriving in that gap were reset).
#
# Routes:  GET|HEAD /  -> 200 text/html, <pre>fortune | cowsay</pre>
#          other path  -> 404      other method -> 405 (Allow: GET, HEAD)
#          bad request line -> 400 slow/silent client -> 408 (after READ_TIMEOUT s)
#
# Environment: SRVPORT (default 4499), MAX_CONN (parallel handlers, default 32),
#              READ_TIMEOUT (seconds to wait for the request, default 5)
set -uo pipefail
export LC_ALL=C   # byte-oriented string lengths => correct Content-Length

SRVPORT=${SRVPORT:-4499}
MAX_CONN=${MAX_CONN:-32}
READ_TIMEOUT=${READ_TIMEOUT:-5}
MAX_HEADERS=100

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

# send_response STATUS REASON BODY [SEND_BODY=1] [EXTRA_HEADER]
send_response() {
	local status=$1 reason=$2 body=$3 send_body=${4:-1} extra=${5:-}
	printf 'HTTP/1.1 %s %s\r\n' "$status" "$reason"
	printf 'Content-Type: text/html; charset=utf-8\r\n'
	printf 'Content-Length: %d\r\n' "${#body}"
	printf 'Cache-Control: no-store\r\n'
	printf 'X-Content-Type-Options: nosniff\r\n'
	[ -n "$extra" ] && printf '%s\r\n' "$extra"
	printf 'Connection: close\r\n\r\n'
	[ "$send_body" = 1 ] && printf '%s' "$body"
	return 0
}

html_escape() {
	local s=$1
	# replacements are quoted: in bash 5.2 an unquoted "&" in the replacement means "the match"
	s=${s//&/"&amp;"}; s=${s//</"&lt;"}; s=${s//>/"&gt;"}
	printf '%s' "$s"
}

handle_request() {
	local line method target version path hdr n=0 cow body_len=0
	local -a parts
	# 1) request line, e.g. "GET / HTTP/1.1"
	if ! IFS= read -r -t "$READ_TIMEOUT" line; then
		# read status > 128 means timeout; otherwise the client closed without sending
		[ -n "${line:-}" ] || { log "client sent nothing (timeout or disconnect)"; send_response 408 "Request Timeout" "Request Timeout"$'\n'; return 0; }
	fi
	line=${line%$'\r'}
	# split on whitespace without globbing (no here-string: works on a read-only filesystem)
	set -f
	# shellcheck disable=SC2206
	parts=($line)
	set +f
	method=${parts[0]:-}; target=${parts[1]:-}; version=${parts[2]:-}
	if [ "${#parts[@]}" -ne 3 ] \
		|| [[ ! "${version:-}" =~ ^HTTP/1\.[01]$ ]] || [[ ! "$method" =~ ^[A-Z]+$ ]] || [[ "$target" != /* ]]; then
		log "400 malformed request line"
		send_response 400 "Bad Request" "Bad Request"$'\n'
		return 0
	fi
	# 2) drain the headers (until the blank line) so the client is not reset mid-send
	while IFS= read -r -t "$READ_TIMEOUT" hdr; do
		hdr=${hdr%$'\r'}
		[ -z "$hdr" ] && break
		n=$((n + 1))
		shopt -s nocasematch
		[[ "$hdr" =~ ^content-length:[[:space:]]*([0-9]+)[[:space:]]*$ ]] && body_len=${BASH_REMATCH[1]}
		shopt -u nocasematch
		if [ "$n" -gt "$MAX_HEADERS" ]; then
			log "431 too many headers"
			send_response 431 "Request Header Fields Too Large" "Too many headers"$'\n'
			return 0
		fi
	done
	# 3) discard a small request body (e.g. POST) so closing the socket does not reset the client
	if [ "$body_len" -gt 1048576 ]; then
		log "413 body of $body_len bytes"
		send_response 413 "Content Too Large" "Content Too Large"$'\n'
		return 0
	fi
	[ "$body_len" -gt 0 ] && IFS= read -r -N "$body_len" -t "$READ_TIMEOUT" _
	path=${target%%\?*}
	# 4) route
	if [ "$method" != GET ] && [ "$method" != HEAD ]; then
		log "405 $method $path"
		send_response 405 "Method Not Allowed" "Method Not Allowed"$'\n' 1 "Allow: GET, HEAD"
		return 0
	fi
	if [ "$path" != / ]; then
		log "404 $method $path"
		send_response 404 "Not Found" "Not Found"$'\n' "$([ "$method" = HEAD ] && echo 0 || echo 1)"
		return 0
	fi
	if ! cow=$(fortune | cowsay); then
		log "500 fortune/cowsay failed"
		send_response 500 "Internal Server Error" "Internal Server Error"$'\n'
		return 0
	fi
	log "200 $method $path"
	send_response 200 OK "<pre>$(html_escape "$cow")</pre>"$'\n' "$([ "$method" = HEAD ] && echo 0 || echo 1)"
}

prerequisites() {
	local cmd
	for cmd in cowsay fortune socat; do
		command -v "$cmd" >/dev/null 2>&1 || { echo "Install prerequisites (missing: $cmd)." >&2; exit 1; }
	done
}

main() {
	if [ "${1:-}" = "--handle" ]; then
		handle_request
		exit 0
	fi
	prerequisites
	echo "Wisdom served on port=$SRVPORT..."
	# exec: socat becomes the main process and receives SIGTERM directly (clean pod shutdown).
	exec socat -T 30 "TCP-LISTEN:${SRVPORT},reuseaddr,fork,backlog=128,max-children=${MAX_CONN}" \
		"EXEC:$(realpath "$0") --handle"
}

main "$@"
