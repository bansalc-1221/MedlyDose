"""Traffic protection (load shedding): one client may make RATE requests a second, with a small burst.
Anything beyond that is refused straight away with 429, so one noisy client cannot starve everyone else.
A client is identified by its address, or by an X-Client-Id header (this is how the chaos flood tells itself apart from you)."""
import threading
import time
from collections import deque
from datetime import datetime

from db import get_db

RATE = 15       # requests per second per client
BURST = 30      # a short burst above that is fine
CALM_AFTER = 2  # seconds without refusing anyone before the incident is closed

lock = threading.Lock()
buckets = {}
recent = deque()   # timestamps of all requests in the last second, used for the dashboard
state = {"shed_total": 0, "last_shed": 0.0, "episode_start": None,
         "episode_shed": 0, "incident_id": None, "detected": None}


def allow(key):
    now = time.time()
    with lock:
        recent.append(now)
        while recent and now - recent[0] > 1:
            recent.popleft()
        b = buckets.setdefault(key, [BURST, now])
        b[0] = min(BURST, b[0] + (now - b[1]) * RATE)
        b[1] = now
        if b[0] >= 1:
            b[0] -= 1
            return True
        if now - state["last_shed"] > CALM_AFTER or state["episode_start"] is None:
            state["episode_start"] = now
            state["episode_shed"] = 0
        state["shed_total"] += 1
        state["episode_shed"] += 1
        state["last_shed"] = now
        return False


def requests_per_second():
    now = time.time()
    with lock:
        while recent and now - recent[0] > 1:
            recent.popleft()
        return len(recent)


def shedding():
    return time.time() - state["last_shed"] < CALM_AFTER


def monitor():
    """Called every second: opens an incident when load shedding starts, closes it when things calm down."""
    now = time.time()
    if shedding() and state["incident_id"] is None:
        state["detected"] = datetime.fromtimestamp(state["episode_start"])
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO incidents (component, detected_at, diagnosis, action) VALUES (?, ?, ?, ?)",
            ("traffic", state["detected"].isoformat(timespec="milliseconds"),
             f"Traffic spike: one client is sending more than {RATE} requests a second",
             "Load shedding: refusing that client's excess requests (429) so everyone else is served normally"))
        state["incident_id"] = cur.lastrowid
        conn.commit()
        conn.close()
    elif not shedding() and state["incident_id"] is not None:
        done = datetime.fromtimestamp(state["last_shed"])
        secs = (done - state["detected"]).total_seconds()
        conn = get_db()
        conn.execute(
            "UPDATE incidents SET recovered_at = ?, recovery_seconds = ?, action = action || ? WHERE id = ?",
            (datetime.now().isoformat(timespec="milliseconds"), round(secs, 2),
             f". Spike lasted {secs:.0f}s, {state['episode_shed']} requests refused, other users never noticed", state["incident_id"]))
        conn.commit()
        conn.close()
        state["incident_id"] = None
