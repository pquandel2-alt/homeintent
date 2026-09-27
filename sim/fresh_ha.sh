#!/usr/bin/env bash
# Reset the test bed to a fresh Home Assistant (no .storage, no recorder
# database, versioned automations.yaml), start it and run bootstrap.py.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
HASS="${HASS:-/home/user/havenv/bin/hass}"
PYTHON="${PYTHON:-$(dirname "$HASS")/python}"
if pid=$(pgrep -f -- "-c $HERE/config$"); then kill $pid; while kill -0 $pid 2>/dev/null; do sleep 1; done; fi
rm -rf "$HERE/config/.storage" "$HERE"/config/home-assistant_v2.db* "$HERE"/config/homeintent_* \
  "$HERE"/config/ha_nlu_* "$HERE/config/.shopping_list.json" "$HERE/config/sim_tokens.json"
git -C "$HERE" checkout -- config/automations.yaml
HASS="$HASS" "$HERE/run_ha.sh"
(cd "$HERE" && "$PYTHON" bootstrap.py)
