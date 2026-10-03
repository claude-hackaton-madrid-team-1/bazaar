#!/bin/sh
# Read-only: fetch every Saturday deployment log of the three live services from Railway.
# Needs a directory linked to the project (`railway link -p <project> -e production`, local config only).
# OUT=<dir> sh fetch_logs.sh
set -e
OUT=${OUT:-./logs}; mkdir -p "$OUT"
for s in bazaar-taker bazaar-maker bazaar-duels; do
  railway deployment list -s $s --limit 300 --json > "$OUT/dep_$s.json"
  python3 - "$s" "$OUT" <<'PY' > "$OUT/ids_$s.txt"
import datetime, json, sys
s, out = sys.argv[1], sys.argv[2]
for x in json.load(open(f"{out}/dep_{s}.json")):
    t = datetime.datetime.fromisoformat(x["createdAt"].replace("Z", "+00:00")) + datetime.timedelta(hours=2)
    if x["status"] != "SKIPPED" and "2026-10-03 08:30" <= t.strftime("%Y-%m-%d %H:%M") <= "2026-10-04 00:30":
        print(t.strftime("%H%M%S"), x["id"])
PY
  while read t id; do
    f="$OUT/$s.$t.${id%%-*}.log"
    [ -s "$f" ] || railway logs -s $s --lines 5000 "$id" > "$f" 2>&1 </dev/null
  done < "$OUT/ids_$s.txt"
done
