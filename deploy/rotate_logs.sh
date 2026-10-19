#!/bin/sh
# Daily log rotation for ~/Library/Logs/maxie (run by dev.maxie.logrotate at 03:30).
# newsyslog needs root, so this is copy-truncate: launchd keeps its append-mode file handle, we copy
# the contents to <log>.1.gz (keeping 7) and truncate the live file. A log is rotated when it is
# over MAX_KB, or always with -F.
set -eu
LOGS="${LOGS:-$HOME/Library/Logs/maxie}"
MAX_KB=5120
KEEP=7
force="${1:-}"
for log in "$LOGS"/*.log; do
  [ -f "$log" ] || continue
  kb=$(( $(stat -f %z "$log") / 1024 ))
  if [ "$force" != "-F" ] && [ "$kb" -le "$MAX_KB" ]; then continue; fi
  i=$KEEP
  while [ "$i" -gt 1 ]; do
    prev=$((i - 1))
    [ -f "$log.$prev.gz" ] && mv "$log.$prev.gz" "$log.$i.gz"
    i=$prev
  done
  cp "$log" "$log.1" && : > "$log" && gzip -f "$log.1"
  echo "rotated $log (${kb} KB)"
done
