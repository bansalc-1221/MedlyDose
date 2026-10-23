import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
from db import get_db, init_db, healing_on  # reuse the app's database code

APP_FILE = ROOT / "app" / "main.py"
HEALTH_URL = "http://127.0.0.1:5000/health"
CHECK_EVERY = 2        # seconds between health checks
FAILS_ALLOWED = 3      # failed checks in a row before we restart

status = {"state": "starting", "detail": "Starting the app"}


def now():
    return datetime.now()


def publish(state=None, detail=None):
    """Write the watchdog's heartbeat and current view of the app to the database."""
    if state:
        status["state"] = state
    if detail:
        status["detail"] = detail
    try:
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO watchdog_status (id, last_beat, state, detail) "
            "VALUES (1, ?, ?, ?)",
            (now().isoformat(timespec="seconds"), status["state"], status["detail"]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # a busy database must never crash the watchdog


def start_app():
    print(f"[watchdog] starting app at {now():%H:%M:%S}")
    return subprocess.Popen([sys.executable, str(APP_FILE)])


def is_healthy():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def wait_until_healthy(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        publish()  # keep the heartbeat alive while waiting
        if is_healthy():
            return True
        time.sleep(0.5)
    return False


def log_incident(detected, diagnosis, action, recovered):
    recovered_at = now() if recovered else None
    seconds = (recovered_at - detected).total_seconds() if recovered else None
    conn = get_db()
    conn.execute(
        "INSERT INTO incidents (component, detected_at, diagnosis, action, "
        "recovered_at, recovery_seconds) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "app",
            detected.isoformat(timespec="milliseconds"),
            diagnosis,
            action,
            recovered_at.isoformat(timespec="milliseconds") if recovered_at else None,
            round(seconds, 2) if seconds is not None else None,
        ),
    )
    conn.commit()
    conn.close()


def main():
    init_db()
    publish("starting", "Starting the app")
    proc = start_app()
    wait_until_healthy()
    publish("healthy", "Responding")
    fails = 0
    first_fail = None

    try:
        while True:
            time.sleep(CHECK_EVERY)
            publish()  # heartbeat

            if not healing_on():
                # Self-healing switched off (for the with-vs-without comparison): watch nothing, fix nothing
                publish("paused", "Self-healing is OFF. Not watching, not restarting.")
                fails = 0
                continue
            if status["state"] == "paused":
                publish("healthy", "Responding")

            if proc.poll() is not None:
                diagnosis = f"App process exited unexpectedly (code {proc.returncode})"
                detected = now()
            elif not is_healthy():
                fails += 1
                if fails == 1:
                    first_fail = now()
                publish("suspect", f"{fails} of {FAILS_ALLOWED} health checks failed")
                if fails < FAILS_ALLOWED:
                    continue
                diagnosis = "App is running but not answering health checks (frozen)"
                detected = first_fail
            else:
                if fails:
                    publish("healthy", "Responding")
                fails = 0
                continue

            # Something is wrong: detect, diagnose, act, recover
            print(f"[watchdog] PROBLEM: {diagnosis}")
            publish("restarting", diagnosis + ". Restarting…")

            if proc.poll() is None:
                proc.kill()
                proc.wait()

            action = "Restarted the app process"
            proc = start_app()
            recovered = wait_until_healthy(30)
            fails = 0

            log_incident(detected, diagnosis, action, recovered)
            if recovered:
                publish("healthy", "Responding")
            else:
                publish("failed", "Restart did not bring the app back")
            print(f"[watchdog] {'RECOVERED' if recovered else 'FAILED TO RECOVER'}")
    except KeyboardInterrupt:
        print("[watchdog] stopping")
    finally:
        if proc.poll() is None:
            proc.kill()


if __name__ == "__main__":
    main()
