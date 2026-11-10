#!/usr/bin/env bash
# Linux with systemd only (cgroups v2). Used by `make bench` and the heavy tests.
# Run a command inside a capped cgroup and watch the machine while it runs.
# Usage: capped-run.sh LOGFILE command args...
# Limits (override by env): MEM_MAX=3500M, CPU_QUOTA=400% (4 cores), no swap,
# lowest CPU priority (nice 19) and idle IO priority.
# Watchdog: kills the whole run if available RAM drops below MIN_AVAIL_MB (default 2048).
set -u
LOG=$1; shift
MEM_MAX=${MEM_MAX:-3500M}
CPU_QUOTA=${CPU_QUOTA:-400%}
MIN_AVAIL_MB=${MIN_AVAIL_MB:-2048}
UNIT=${UNIT:-scrinium-capped-$$}

systemd-run --user --scope -q --unit "$UNIT" \
  -p MemoryMax="$MEM_MAX" -p MemorySwapMax=0 -p CPUQuota="$CPU_QUOTA" \
  nice -n 19 ionice -c3 "$@" &
RUN=$!

echo "time avail_mb swap_used_mb vram_mb cgroup_mb" > "$LOG"
while kill -0 "$RUN" 2>/dev/null; do
  avail=$(( $(awk '/MemAvailable/{print $2}' /proc/meminfo) / 1024 ))
  swap=$(awk '/SwapTotal/{t=$2}/SwapFree/{f=$2}END{print int((t-f)/1024)}' /proc/meminfo)
  vram=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null || echo -)
  cg=$(systemctl --user show "$UNIT.scope" -p MemoryCurrent --value 2>/dev/null)
  [[ "$cg" =~ ^[0-9]+$ ]] && cg=$((cg / 1048576)) || cg=-
  echo "$(date +%T) $avail $swap $vram $cg" >> "$LOG"
  if (( avail < MIN_AVAIL_MB )); then
    echo "$(date +%T) WATCHDOG: available RAM ${avail} MB < ${MIN_AVAIL_MB} MB, killing $UNIT" >> "$LOG"
    systemctl --user kill -s KILL "$UNIT.scope"
    break
  fi
  sleep 2
done
wait "$RUN"; CODE=$?
echo "exit=$CODE peak_cgroup_mb=$(awk 'NR>1 && $5~/^[0-9]+$/{if($5>m)m=$5}END{print m+0}' "$LOG") min_avail_mb=$(awk 'NR>1 && $2~/^[0-9]+$/{if(!m||$2<m)m=$2}END{print m+0}' "$LOG")" >> "$LOG"
exit $CODE
