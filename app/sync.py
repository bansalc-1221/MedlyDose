import threading
from datetime import datetime

import requests

from db import get_db, healing_on

CLOUD_URL = "http://127.0.0.1:5002/sync"
lock = threading.Lock()  # stops two threads sending the queue at the same time
state = {"online": True, "incident_id": None, "detected": None, "lost": 0}


def enqueue(medicine_name, day, time_, taken_at):
    conn = get_db()
    conn.execute(
        "INSERT INTO sync_queue (medicine_name, day, time, taken_at) VALUES (?, ?, ?, ?)",
        (medicine_name, day, time_, taken_at),
    )
    conn.commit()
    conn.close()


def pending_count():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0]
    conn.close()
    return n


def flush():
    """Send queued doses, oldest first. Stops at the first failure."""
    with lock:
        conn = get_db()
        rows = conn.execute("SELECT * FROM sync_queue ORDER BY id").fetchall()
        for r in rows:
            try:
                resp = requests.post(
                    CLOUD_URL,
                    json={
                        "medicine_name": r["medicine_name"],
                        "day": r["day"],
                        "time": r["time"],
                        "taken_at": r["taken_at"],
                    },
                    timeout=2,
                )
                resp.raise_for_status()
            except Exception:
                if not healing_on():
                    # Self-healing is off: there is no queue to fall back on, so these doses never reach the caregiver
                    lost = conn.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0]
                    conn.execute("DELETE FROM sync_queue")
                    conn.commit()
                    conn.close()
                    state["lost"] += lost
                    return False
                conn.close()
                _went_offline()
                return False
            conn.execute("DELETE FROM sync_queue WHERE id = ?", (r["id"],))
            conn.commit()
        conn.close()
        if not state["online"]:
            _back_online()
        return True


def check():
    """Ping the cloud. Flips the online/offline state even when nothing is waiting to sync."""
    if not healing_on():
        return True    # self-healing is off: nobody is watching the connection
    try:
        requests.get("http://127.0.0.1:5002/health", timeout=2).raise_for_status()
    except Exception:
        _went_offline()
        return False
    if not state["online"] and pending_count() == 0:
        _back_online()
    return True


def _went_offline():
    if state["online"]:
        state["online"] = False
        state["detected"] = datetime.now()
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO incidents (component, detected_at, diagnosis, action) "
            "VALUES (?, ?, ?, ?)",
            (
                "cloud_sync",
                state["detected"].isoformat(timespec="milliseconds"),
                "Cloud sync unreachable (connection lost)",
                "Saved doses to a local queue; retrying every 3s, will sync when connection returns",
            ),
        )
        state["incident_id"] = cur.lastrowid
        conn.commit()
        conn.close()


def _back_online():
    state["online"] = True
    if state["incident_id"]:
        now = datetime.now()
        seconds = (now - state["detected"]).total_seconds()
        conn = get_db()
        conn.execute(
            "UPDATE incidents SET recovered_at = ?, recovery_seconds = ? WHERE id = ?",
            (now.isoformat(timespec="milliseconds"), round(seconds, 2), state["incident_id"]),
        )
        conn.commit()
        conn.close()
        state["incident_id"] = None
