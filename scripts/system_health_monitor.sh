#!/usr/bin/env bash
# System Health Monitoring Script (AccuKnox Problem Statement 2, objective 1).
# Checks CPU, memory, disk and process count against thresholds. Every reading is logged;
# a threshold breach prints an ALERT (console + log file). Reads /proc, so it runs on any Linux.
#
# Usage: system_health_monitor.sh [-c CPU%] [-m MEM%] [-d DISK%] [-p MAX_PROCS] [-i SECONDS]
#                                 [-l LOGFILE] [-P PATH] [-j] [-h]
#   -c/-m/-d  alert thresholds in percent, 1-100   (defaults 80 / 80 / 85)
#   -p        alert when more processes than this  (default 400)
#   -i        repeat every N seconds; 0 = check once (default 0)
#   -l        log file (default ./system_health.log)    -P  filesystem for the disk check (default /)
#   -j        JSON: one object per check on stdout, nothing else on stdout
# Exit codes: 0 = healthy, 1 = at least one alert, 2 = bad arguments or unwritable log file.
set -uo pipefail

CPU_T=80; MEM_T=80; DISK_T=85; PROC_T=400; INTERVAL=0; LOG="system_health.log"; DISK_PATH="/"; JSON=0

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "system_health_monitor: $*" >&2; exit 2; }
is_int() { [[ "$1" =~ ^[0-9]+$ ]]; }

while getopts "c:m:d:p:i:l:P:jh" o; do
  case "$o" in
    c) CPU_T=$OPTARG;; m) MEM_T=$OPTARG;; d) DISK_T=$OPTARG;; p) PROC_T=$OPTARG;;
    i) INTERVAL=$OPTARG;; l) LOG=$OPTARG;; P) DISK_PATH=$OPTARG;; j) JSON=1;;
    h) usage; exit 0;;
    *) usage >&2; exit 2;;
  esac
done
shift $((OPTIND - 1))
[ $# -eq 0 ] || die "unexpected argument: $1"

for pair in "cpu:$CPU_T" "memory:$MEM_T" "disk:$DISK_T"; do
  v=${pair#*:}
  { is_int "$v" && (( 10#$v >= 1 && 10#$v <= 100 )); } || die "${pair%%:*} threshold must be an integer 1-100, got '$v'"
done
{ is_int "$PROC_T" && (( 10#$PROC_T >= 1 )); } || die "process threshold must be a positive integer, got '$PROC_T'"
is_int "$INTERVAL" || die "interval must be a whole number of seconds >= 0, got '$INTERVAL'"
[ -e "$DISK_PATH" ] || die "disk path does not exist: $DISK_PATH"
{ [ -r /proc/stat ] && [ -r /proc/meminfo ]; } || die "/proc is not readable (Linux only)"
{ : >> "$LOG"; } 2>/dev/null || die "cannot write log file: $LOG"

if [ -t 1 ] && [ "$JSON" -eq 0 ]; then RED=$'\e[31m'; GRN=$'\e[32m'; NC=$'\e[0m'; else RED=""; GRN=""; NC=""; fi
ts() { date '+%Y-%m-%d %H:%M:%S'; }
# Human-readable lines go to stdout normally and to stderr in JSON mode, so stdout stays pure JSON.
say() { if [ "$JSON" -eq 1 ]; then echo "$*" >&2; else echo "$*"; fi; }
log() { say "[$(ts)] $*"; echo "[$(ts)] $*" >> "$LOG"; }
alert() { say "${RED}[$(ts)] ALERT $*${NC}"; echo "[$(ts)] ALERT $*" >> "$LOG"; }

cpu_usage() {  # two /proc/stat samples, 1 s apart -> busy percentage
  local u1 n1 s1 i1 w1 q1 sq1 st1 u2 n2 s2 i2 w2 q2 sq2 st2
  read -r _ u1 n1 s1 i1 w1 q1 sq1 st1 _ < /proc/stat
  sleep 1
  read -r _ u2 n2 s2 i2 w2 q2 sq2 st2 _ < /proc/stat
  local idle=$(( (i2 + w2) - (i1 + w1) ))
  local total=$(( (u2 + n2 + s2 + i2 + w2 + q2 + sq2 + st2) - (u1 + n1 + s1 + i1 + w1 + q1 + sq1 + st1) ))
  (( total > 0 )) || { echo 0; return; }
  echo $(( (100 * (total - idle)) / total ))
}
mem_usage() {  # used = MemTotal - MemAvailable
  awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END {if (t > 0) printf "%d", (t - a) * 100 / t; else print 0}' /proc/meminfo
}
disk_usage() { df -P "$DISK_PATH" | awk 'NR==2 {gsub("%", "", $5); print $5}'; }
proc_count() { local n=0 d; for d in /proc/[0-9]*; do [ -d "$d" ] && n=$((n + 1)); done; echo "$n"; }
zombie_count() { awk '$1 == "State:" && $2 == "Z" {z++} END {print z + 0}' /proc/[0-9]*/status 2>/dev/null || echo 0; }
top_procs() { if command -v ps >/dev/null; then ps -eo pid,comm,%cpu,%mem --sort=-%cpu | head -6; fi; }

check() {
  local cpu mem disk procs zombies load problems=0 alerts=()
  cpu=$(cpu_usage); mem=$(mem_usage); disk=$(disk_usage); procs=$(proc_count); zombies=$(zombie_count)
  load=$(cut -d' ' -f1-3 /proc/loadavg)
  [[ "$disk" =~ ^[0-9]+$ ]] || { alert "could not read disk usage for $DISK_PATH"; disk=0; problems=1; alerts+=("disk_unreadable"); }
  log "cpu=${cpu}% mem=${mem}% disk(${DISK_PATH})=${disk}% procs=${procs} zombies=${zombies} load=${load}"
  if (( cpu > CPU_T ));   then alert "CPU usage ${cpu}% > ${CPU_T}%"; problems=1; alerts+=("cpu"); fi
  if (( mem > MEM_T ));   then alert "Memory usage ${mem}% > ${MEM_T}%"; problems=1; alerts+=("memory"); fi
  if (( disk > DISK_T )); then alert "Disk usage ${disk}% > ${DISK_T}% on ${DISK_PATH}"; problems=1; alerts+=("disk"); fi
  if (( procs > PROC_T )); then alert "Running processes ${procs} > ${PROC_T}"; problems=1; alerts+=("processes"); fi
  if (( zombies > 0 ));   then alert "${zombies} zombie process(es) present"; problems=1; alerts+=("zombies"); fi
  if (( problems )); then
    { echo "Top CPU consumers:"; top_procs; } | while IFS= read -r l; do say "$l"; echo "$l" >> "$LOG"; done
  else
    say "${GRN}[$(ts)] OK - all metrics within thresholds${NC}"
  fi
  if (( JSON )); then
    local list="" a
    for a in "${alerts[@]}"; do list+="${list:+,}\"$a\""; done
    printf '{"timestamp":"%s","cpu_pct":%d,"mem_pct":%d,"disk_pct":%d,"disk_path":"%s","processes":%d,"zombies":%d,"load":"%s","thresholds":{"cpu":%d,"mem":%d,"disk":%d,"processes":%d},"status":"%s","alerts":[%s]}\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$cpu" "$mem" "$disk" "${DISK_PATH//\"/\\\"}" "$procs" "$zombies" "$load" \
      "$CPU_T" "$MEM_T" "$DISK_T" "$PROC_T" "$([ "$problems" -eq 0 ] && echo OK || echo ALERT)" "$list"
  fi
  return "$problems"
}

trap 'exit 130' INT
trap 'exit 143' TERM
while true; do
  check; rc=$?
  (( INTERVAL == 0 )) && exit "$rc"
  sleep "$INTERVAL"
done
