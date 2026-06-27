#!/usr/bin/env bash
# tl — tiny CLI to drive the traffic light from any script.
#   tl red                 tl green                 tl off
#   tl yellow --flash      tl red --label "Build failed"
#   tl yellow --flash --label "Waiting on you"
#   TL_PORT=7654 tl green   (override port)
set -euo pipefail
PORT="${TL_PORT:-7654}"
STATE="${1:-}"; shift || true
[ -z "$STATE" ] && { echo "usage: tl {red|yellow|green|off} [--flash] [--label TEXT]"; exit 1; }

QS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --flash) QS="${QS}flash=1&";;
    --label) shift; QS="${QS}label=$(printf %s "${1:-}" | sed 's/ /+/g')&";;
    *) echo "unknown option: $1" >&2; exit 1;;
  esac
  shift
done

curl -s -m 1 "localhost:${PORT}/${STATE}?${QS}" >/dev/null 2>&1 || {
  echo "traffic light not reachable on port ${PORT} (is it running?)" >&2; exit 1; }
