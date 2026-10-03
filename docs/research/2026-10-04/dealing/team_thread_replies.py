"""Did other teams answer our team threads? Reads the body of ONE keyed GET /api/me/threads
(fetched with keyed_get.py at 00:21 Madrid, logged in keyed_requests.log; the body stays outside the repo
because it holds other teams' words). The endpoint returns only our last 50 threads (ticks 1289-1441).

    uv run python docs/research/2026-10-04/dealing/team_thread_replies.py /path/to/me_threads.json
"""

import json
import sys
from collections import Counter

with open(sys.argv[1]) as f:
    threads = json.load(f)["threads"]
team = [t for t in threads if t["kind"] == "team"]
ours = [t for t in team if t["team"] == "t01"]
inbound = [t for t in team if t["team"] != "t01"]
c = Counter("replied" if any(m["sender"] != "t01" for m in t["messages"]) else "silent" for t in ours)
print(f"window: ticks {min(t['created_tick'] for t in threads)}-{max(t['created_tick'] for t in threads)}")
print(f"threads we opened: {len(ours)}  {dict(c)}")
print(f"threads other teams opened with us: {len(inbound)}")
for t in inbound:
    first = next(m for m in t["messages"] if m["sender"] != "t01")
    offer = first.get("offer") or {}
    print(
        f"  {t['id']} from {t['team']} tick {t['created_tick']} offer_attached={bool(offer)} "
        f"we_answered={any(m['sender'] == 't01' for m in t['messages'])}"
    )
