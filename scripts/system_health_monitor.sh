#!/usr/bin/env bash
# System Health Monitoring Script (AccuKnox Problem Statement 2, objective 1).
# Checks CPU, memory, disk and process count against thresholds and alerts to the console
# and to a log file when a threshold is exceeded. Works on any Linux with /proc + coreutils.
#
# Usage: system_health_monitor.sh [-c CPU%] [-m MEM%] [-d DISK%] [-p MAX_PROCS] [-i SECONDS] [-l LOGFILE] [-P PATH]
set -uo pipefail

CPU_T=80; MEM_T=80; DISK_T=85; PROC_T=400; INTERVAL=0; LOG="system_health.log"; DISK_PATH="/"
while getopts "c:m:d:p:i:l:P:h" o; do
  case "$o" in
    c) CPU_T=$OPTARG;; m) MEM_T=$OPTARG;; d) DISK_T=$OPTARG;; p) PROC_T=$OPTARG;;
    i) INTERVAL=$OPTARG;; l) LOG=$OPTARG;; P) DISK_PATH=$OPTARG;;
    h) sed -n '2,7p' "$0"; exit 0;;
    *) echo "bad option"; exit 2;;
  esac
done

RED=$'\e[31m'; GRN=$'\e[32m'; NC=$'\e[0m'
ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }
alert() { echo "${RED}[$(ts)] ALERT $*${NC}"; echo "[$(ts)] ALERT $*" >> "$LOG"; }

cpu_usage() {  # two /proc/stat samples, 1 s apart -> busy percentage
  read -r _ u1 n1 s1 i1 w1 q1 sq1 st1 _ < /proc/stat
  sleep 1
  read -r _ u2 n2 s2 i2 w2 q2 sq2 st2 _ < /proc/stat
  local idle=$(( (i2+w2) - (i1+w1) ))
  local total=$(( (u2+n2+s2+i2+w2+q2+sq2+st2) - (u1+n1+s1+i1+w1+q1+sq1+st1) ))
  (( total == 0 )) && { echo 0; return; }
  echo $(( (100 * (total - idle)) / total ))
}
mem_usage() { free | awk '/^Mem:/ {printf "%d", ($2-$7)*100/$2}'; }     # used = total - available
disk_usage() { df -P "$DISK_PATH" | awk 'NR==2 {gsub("%","",$5); print $5}'; }
proc_count() { ps -e --no-headers | wc -l; }
top_procs() { ps -eo pid,comm,%cpu,%mem --sort=-%cpu | head -6; }

check() {
  local cpu mem disk procs problems=0
  cpu=$(cpu_usage); mem=$(mem_usage); disk=$(disk_usage); procs=$(proc_count)
  log "cpu=${cpu}% mem=${mem}% disk(${DISK_PATH})=${disk}% procs=${procs}  load=$(cut -d' ' -f1-3 /proc/loadavg)"
  (( cpu  > CPU_T  )) && { alert "CPU usage ${cpu}% > ${CPU_T}%";    problems=1; }
  (( mem  > MEM_T  )) && { alert "Memory usage ${mem}% > ${MEM_T}%"; problems=1; }
  (( disk > DISK_T )) && { alert "Disk usage ${disk}% > ${DISK_T}% on ${DISK_PATH}"; problems=1; }
  (( procs > PROC_T )) && { alert "Running processes ${procs} > ${PROC_T}"; problems=1; }
  # zombie processes are a health signal too
  local z; z=$(ps -eo stat --no-headers | grep -c '^Z' || true)
  (( z > 0 )) && { alert "${z} zombie process(es) present"; problems=1; }
  if (( problems )); then
    { echo "Top CPU consumers:"; top_procs; } | tee -a "$LOG"
    return 1
  fi
  echo "${GRN}[$(ts)] OK - all metrics within thresholds${NC}"
  return 0
}

while true; do
  check; rc=$?
  (( INTERVAL <= 0 )) && exit $rc
  sleep "$INTERVAL"
done
