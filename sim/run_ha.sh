#!/usr/bin/env bash
# Start (or restart) the test-bed Home Assistant in the background.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
HASS="${HASS:-/home/user/havenv/bin/hass}"
LOG="${HA_LOG:-$HERE/config/home-assistant.run.log}"
if pid=$(pgrep -f -- "-c $HERE/config$"); then kill $pid; while kill -0 $pid 2>/dev/null; do sleep 1; done; fi
nohup "$HASS" -c "$HERE/config" >"$LOG" 2>&1 &
for _ in $(seq 1 120); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8123/manifest.json || true)
  [ "$code" = "200" ] && break
  sleep 1
done
sleep 8
echo "Home Assistant up (log: $LOG)"
