import os
import time
import threading
from datetime import datetime

import requests
from flask import Flask, render_template, request

import calendar_api
import quarantine
import sync
from breaker import CircuitBreaker
from db import (get_db, init_db, close_orphan_incidents, healing_on, set_healing,
                get_setting, set_setting, mark_chaos)

app = Flask(__name__)
app.register_blueprint(calendar_api.bp)

SMS_URL = "http://127.0.0.1:5001/send"
SMS_HEALTH_URL = "http://127.0.0.1:5001/health"
sms_breaker = CircuitBreaker("sms_service", failure_threshold=3, cooldown=10)

chaos_state = {"freeze_until": 0}
SERVICES = {   # the two helper services MedTracker depends on (simulated stand-ins)
    "sms_service": {"name": "SMS service", "health": "http://127.0.0.1:5001/health", "up": "http://127.0.0.1:5001/admin/up"},
    "cloud_sync": {"name": "Caregiver cloud connection", "health": "http://127.0.0.1:5002/health", "up": "http://127.0.0.1:5002/admin/up"},
}
CHAOS_LINKS = {
    "sms_down": "http://127.0.0.1:5001/admin/down",
    "sms_up": "http://127.0.0.1:5001/admin/up",
    "cloud_down": "http://127.0.0.1:5002/admin/down",
    "cloud_up": "http://127.0.0.1:5002/admin/up",
}
HEAL_AFTER = {"sms_service": 10, "cloud_sync": 8}   # with self-healing on, a service that stays down is reconnected after this many seconds

_svc_cache = {}


def service_up(url, fresh=False):
    """Is a helper service answering? The answer is remembered for a second so busy pages don't flood it."""
    hit = _svc_cache.get(url)
    if hit and not fresh and time.time() - hit[0] < 1:
        return hit[1]
    try:
        ok = requests.get(url, timeout=0.8).status_code == 200
    except Exception:
        ok = False
    _svc_cache[url] = (time.time(), ok)
    return ok


# ---------- "Freeze" simulation ----------

@app.before_request
def freeze_gate():
    """While the chaos panel has 'frozen' the app, every request hangs, like a real freeze.
    The watchdog's health checks time out, kill the process, and start a fresh one."""
    if request.path.startswith("/static"):
        return
    while time.time() < chaos_state["freeze_until"]:
        time.sleep(0.2)


# ---------- Pages (each one is a shell; the data arrives through /api) ----------

@app.route("/")
def home():
    return render_template("hero.html")          # the welcome page; its Get started button opens the Tracker


@app.route("/tracker")
def tracker():
    return render_template("index.html")


@app.route("/medicines")
def medicines_page():
    return render_template("medicines.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/watchdog")
def watchdog_page():
    return render_template("watchdog.html")


@app.route("/chaos")
def chaos_page():
    return render_template("chaos.html")


# ---------- Health endpoints ----------

@app.route("/health")
def health():
    return {"status": "ok"}  # what the watchdog pokes


@app.route("/api/ping")
def api_ping():
    return {"ok": True}  # the browser uses this to show the connection light


# ---------- Medicines and doses ----------

def split_times(times):
    return [t.strip() for t in (times or "").split(",") if t.strip()]


GRACE_MIN = 30   # a dose this many minutes late is "overdue" (and no SMS is sent for it)


def to_min(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


data_lock = threading.Lock()
integrity = {"lost": False, "incident_id": None}


def valid_count():
    conn = get_db()
    n = sum(1 for m in conn.execute("SELECT * FROM medicines").fetchall() if not quarantine.check(m))
    conn.close()
    return n


def update_expected():
    """Remember how many healthy medicines there should be, so a sudden drop can be recognised as data loss."""
    set_setting("expected_meds", valid_count())


def valid_times(times):
    return [t for t in split_times(times) if quarantine.TIME_RE.match(t)]


@app.route("/api/today")
def api_today():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    hm = now.strftime("%H:%M")
    now_min = to_min(hm)

    conn = get_db()
    medicines = conn.execute("SELECT * FROM medicines ORDER BY id").fetchall()
    taken_rows = conn.execute(
        "SELECT medicine_id, time FROM dose_log WHERE day = ?", (today,)
    ).fetchall()
    remind_rows = conn.execute(
        "SELECT medicine_id, time, sms_status, fired_at FROM reminders WHERE day = ?", (today,)
    ).fetchall()
    conn.close()

    taken = {(r["medicine_id"], r["time"]) for r in taken_rows}
    reminded = {(r["medicine_id"], r["time"]): r for r in remind_rows}

    doses, strip = [], []
    for m in medicines:
        for t in valid_times(m["times"]):
            key = (m["id"], t)
            late = now_min - to_min(t)
            if key in taken:
                strip.append({"taken": True, "late": False, "label": f"{t} {m['name']}"})
                continue
            r = reminded.get(key)
            if late < 0:
                state = "scheduled"          # not time yet: the bell can be pressed
            elif late > GRACE_MIN:
                state = "overdue"            # time has struck: the Taken button is live
            else:
                state = "due"
            strip.append({"taken": False, "late": state == "overdue", "label": f"{t} {m['name']}"})
            doses.append({
                "id": m["id"],
                "name": m["name"],
                "dose": m["dose"],
                "time": t,
                "state": state,
                "armed": r is not None,                       # the person set a reminder
                "sms": r["sms_status"] if r and r["fired_at"] else None,
                "late_min": max(0, late),
                "due_in": max(0, -late),
            })
    doses.sort(key=lambda d: (d["time"], d["name"]))
    strip.sort(key=lambda s: s["label"])

    nxt = next((d for d in doses if d["state"] == "scheduled"), None)
    if nxt:
        nxt["next"] = True

    return {
        "today": today,
        "now": hm,
        "doses": doses,                      # only doses still waiting (taken ones leave this list)
        "strip": strip,                      # every dose of the day, for the progress strip
        "done": sum(1 for s in strip if s["taken"]),
        "total": len(strip),
        "breaker": {"state": sms_breaker.state, "retry_in": sms_breaker.retry_in()},
        "pending": sync.pending_count(),
        "online": sync.state["online"],
        "quarantined": quarantine.count(),
    }


@app.route("/api/medicines", methods=["GET"])
def api_medicines():
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    rows = conn.execute("SELECT * FROM medicines ORDER BY id").fetchall()
    last = {
        r["medicine_id"]: r["taken_at"]
        for r in conn.execute(
            "SELECT medicine_id, MAX(taken_at) AS taken_at FROM dose_log GROUP BY medicine_id"
        )
    }
    taken = {(r["medicine_id"], r["time"]) for r in conn.execute(
        "SELECT medicine_id, time FROM dose_log WHERE day = ?", (today,))}
    conn.close()
    out = []
    for r in rows:
        if quarantine.check(r):      # damaged record: never show it, the quarantine loop will set it aside
            continue
        times = split_times(r["times"])
        done = bool(times) and all((r["id"], t) in taken for t in times)
        out.append({"id": r["id"], "name": r["name"], "dose": r["dose"], "times": times,
                    "last_taken": last.get(r["id"]), "done_today": done})
    return {"medicines": out}


@app.route("/api/history")
def api_history():
    """Every dose that was ticked off, newest first. Doses of medicines that were removed stay here."""
    conn = get_db()
    rows = conn.execute(
        "SELECT d.*, m.id AS still_there FROM dose_log d LEFT JOIN medicines m ON m.id = d.medicine_id "
        "ORDER BY d.day DESC, d.taken_at DESC LIMIT 500").fetchall()
    conn.close()
    out = []
    for r in rows:
        delay = None
        try:
            due = datetime.fromisoformat(f"{r['day']}T{r['time']}:00")
            delay = max(0, int((datetime.fromisoformat(r["taken_at"]) - due).total_seconds() // 60))
        except (TypeError, ValueError):
            pass
        out.append({"name": r["name"] or "Removed medicine", "dose": r["dose"] or "", "day": r["day"],
                    "time": r["time"], "taken_at": r["taken_at"], "delay": delay, "removed": r["still_there"] is None})
    return {"history": out}


@app.route("/api/medicines", methods=["POST"])
def api_add_medicine():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    dose = str(data.get("dose", "")).strip()
    times = data.get("times", [])
    if isinstance(times, str):
        times = split_times(times)
    times = sorted({str(t).strip() for t in times if str(t).strip()})
    times_text = ", ".join(times)

    problems = quarantine.check({"name": name, "dose": dose, "times": times_text})
    if problems:
        return {"ok": False, "message": "Not saved: " + "; ".join(problems) + "."}, 400

    conn = get_db()
    conn.execute(
        "INSERT INTO medicines (name, dose, times, created_day) VALUES (?, ?, ?, ?)",
        (name, dose, times_text, datetime.now().strftime("%Y-%m-%d")),
    )
    conn.commit()
    conn.close()
    update_expected()
    return {"ok": True, "message": f"{name} added."}


@app.route("/api/medicines/<int:med_id>", methods=["DELETE"])
def api_delete_medicine(med_id):
    with data_lock:
        conn = get_db()
        row = conn.execute("SELECT name FROM medicines WHERE id = ?", (med_id,)).fetchone()
        if not row:
            conn.close()
            return {"ok": False, "message": "That medicine no longer exists."}, 404
        conn.execute("DELETE FROM medicines WHERE id = ?", (med_id,))
        conn.commit()
        conn.close()
        update_expected()
    return {"ok": True, "message": f"{row['name']} removed."}


@app.route("/api/take", methods=["POST"])
def api_take():
    data = request.get_json(silent=True) or {}
    try:
        med_id = int(data.get("medicine_id"))
    except (TypeError, ValueError):
        return {"ok": False, "message": "Missing medicine."}, 400
    dose_time = str(data.get("time", "")).strip()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    taken_at = now.isoformat(timespec="seconds")

    conn = get_db()
    med = conn.execute("SELECT * FROM medicines WHERE id = ?", (med_id,)).fetchone()
    if not med:
        conn.close()
        return {"ok": False, "message": "That medicine no longer exists."}, 404
    if dose_time not in valid_times(med["times"]):
        conn.close()
        return {"ok": False, "message": "That time isn't on this medicine's schedule."}, 400
    if dose_time > now.strftime("%H:%M"):
        conn.close()
        return {"ok": False, "message": f"{med['name']} isn't due until {dose_time}."}, 400

    cur = conn.execute(
        "INSERT OR IGNORE INTO dose_log (medicine_id, day, time, taken_at, name, dose) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (med_id, today, dose_time, taken_at, med["name"], med["dose"]),
    )
    inserted = cur.rowcount == 1
    conn.commit()
    conn.close()

    if inserted:
        sync.enqueue(med["name"], today, dose_time, taken_at)
        sync.flush()  # try now; if offline, it stays queued

    pending = sync.pending_count()
    message = f"{med['name']} marked as taken."
    if pending:
        message += " Saved on this device; it will sync when the connection returns."
    return {"ok": True, "message": message, "pending": pending}


@app.route("/api/remind", methods=["POST"])
def api_remind():
    """The bell: set a reminder for a dose that isn't due yet. It fires when the time strikes."""
    data = request.get_json(silent=True) or {}
    try:
        med_id = int(data.get("medicine_id"))
    except (TypeError, ValueError):
        return {"ok": False, "message": "Missing medicine."}, 400
    dose_time = str(data.get("time", "")).strip()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    conn = get_db()
    med = conn.execute("SELECT * FROM medicines WHERE id = ?", (med_id,)).fetchone()
    if not med:
        conn.close()
        return {"ok": False, "message": "That medicine no longer exists."}, 404
    if dose_time not in valid_times(med["times"]):
        conn.close()
        return {"ok": False, "message": "That time isn't on this medicine's schedule."}, 400
    if dose_time <= now.strftime("%H:%M"):
        conn.close()
        return {"ok": False, "message": f"{med['name']} is already due. Press Taken when you've taken it."}, 400

    conn.execute(
        "INSERT OR IGNORE INTO reminders (medicine_id, day, time, created_at, sms_status) "
        "VALUES (?, ?, ?, ?, 'armed')",
        (med_id, today, dose_time, now.isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"Reminder set. You'll be alerted at {dose_time}."}


# ---------- Health dashboard and watchdog data ----------

def watchdog_info():
    conn = get_db()
    row = conn.execute("SELECT * FROM watchdog_status WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return {"alive": False, "state": "unknown", "detail": "No heartbeat yet", "age": None}
    age = (datetime.now() - datetime.fromisoformat(row["last_beat"])).total_seconds()
    return {"alive": age < 8, "state": row["state"], "detail": row["detail"], "age": round(age, 1)}


@app.route("/api/watchdog")
def api_watchdog():
    w = watchdog_info()
    conn = get_db()
    history = conn.execute(
        "SELECT * FROM incidents WHERE component = 'app' ORDER BY id DESC LIMIT 10"
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) FROM incidents WHERE component = 'app'"
    ).fetchone()[0]
    conn.close()
    w["restarts"] = total
    w["history"] = [dict(r) for r in history]
    return w


def enrich(rows):
    """Add 'found in' (injection to detection) and 'back to normal in' (injection to recovery) to each incident."""
    conn = get_db()
    marks = conn.execute("SELECT component, injected_at FROM chaos_marks ORDER BY id DESC LIMIT 60").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["detect_seconds"] = d["total_seconds"] = None
        try:
            det = datetime.fromisoformat(d["detected_at"]).timestamp()
        except (TypeError, ValueError):
            det = None
        if det:
            for m in marks:
                if m["component"] == d["component"] and m["injected_at"] <= det + 0.5 and det - m["injected_at"] < 60:
                    d["detect_seconds"] = round(max(0.0, det - m["injected_at"]), 1)
                    if d["recovery_seconds"] is not None:
                        d["total_seconds"] = round(d["detect_seconds"] + d["recovery_seconds"], 1)
                    break
        out.append(d)
    return out


heal_watch = {"was_on": True}


def heal_services():
    """Self-healing has just been switched on: reconnect any helper service that was left down while it was off."""
    for comp, svc in SERVICES.items():
        if service_up(svc["health"], fresh=True):
            continue
        detected = datetime.now()
        try:
            requests.get(svc["up"], timeout=2)
        except Exception:
            continue
        done = datetime.now()
        conn = get_db()
        conn.execute(
            "INSERT INTO incidents (component, detected_at, diagnosis, action, recovered_at, recovery_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (comp, detected.isoformat(timespec="milliseconds"),
             f"{svc['name']} was down while self-healing was off",
             "Self-healing switched on: reconnected the service",
             done.isoformat(timespec="milliseconds"), round((done - detected).total_seconds(), 2)))
        conn.commit()
        conn.close()
    sms_breaker.record_success()      # close the circuit if it was left open
    sync.state["lost"] = 0


@app.route("/api/healing", methods=["GET", "POST"])
def api_healing():
    if request.method == "POST":
        on = bool((request.get_json(silent=True) or {}).get("on"))
        set_healing(on)
        if on and not heal_watch["was_on"]:
            heal_services()
        heal_watch["was_on"] = on
    return {"ok": True, "on": healing_on()}


@app.route("/api/status")
def api_status():
    conn = get_db()
    incidents = conn.execute(
        "SELECT * FROM incidents ORDER BY id DESC LIMIT 12"
    ).fetchall()
    stats = conn.execute(
        "SELECT COUNT(*), AVG(recovery_seconds), MAX(recovery_seconds) "
        "FROM incidents WHERE recovery_seconds IS NOT NULL"
    ).fetchone()
    bad = sum(1 for m in conn.execute("SELECT * FROM medicines").fetchall()
              if quarantine.check(m))
    conn.close()

    # App + Watchdog (what the watchdog itself observes)
    w = watchdog_info()
    if w["alive"] and w["state"] == "paused":
        app_c = ("amber", "Running unguarded. Nothing would restart it")
        wd_c = ("amber", "Paused: self-healing is switched off")
    elif not w["alive"]:
        app_c = ("amber", "Running, but the watchdog is offline, so nothing would restart it")
        wd_c = ("red", "No heartbeat. The app is unguarded")
    else:
        wd_c = ("green", f"Alive. Last check {w['age']}s ago")
        if w["state"] == "healthy":
            app_c = ("green", "Responding")
        elif w["state"] in ("suspect", "starting"):
            app_c = ("amber", w["detail"])
        else:
            app_c = ("red", w["detail"])

    # SMS service (circuit breaker)
    if not healing_on():
        if service_up(SERVICES["sms_service"]["health"]):
            sms = ("green", "Answering. Not protected, because self-healing is off")
        else:
            sms = ("red", "Down. Nothing is protecting reminders, so texts are lost")
    elif sms_breaker.state == "closed":
        sms = ("green", "Normal")
    elif sms_breaker.state == "open":
        sms = ("amber", f"Circuit open. Next test in {sms_breaker.retry_in()}s")
    else:
        sms = ("amber", "Testing the service")

    # Cloud sync (offline queue)
    pending = sync.pending_count()
    if not healing_on() and not service_up(SERVICES["cloud_sync"]["health"]):
        cloud = ("red", "Connection down. Nothing is saving doses for later, so they are lost")
    elif not healing_on() and sync.state["lost"]:
        cloud = ("red", f"{sync.state['lost']} dose(s) never reached the caregiver. Nothing saved them")
    elif not sync.state["online"]:
        cloud = ("amber", f"Offline. {pending} dose(s) queued" if pending else "Offline. Saving doses on this device")
    elif pending:
        cloud = ("green", f"Syncing {pending} dose(s)")
    else:
        cloud = ("green", "Synced")

    # Database (quarantine)
    unhealed = sum(1 for i in incidents if i["recovered_at"] == "never")
    if integrity["lost"]:
        db_ = ("red", "Data lost. Medicines are missing and no backup exists to restore them")
    elif bad and not healing_on():
        db_ = ("red", f"{bad} corrupted record(s) left in the database. Nothing is isolating them")
    elif bad:
        db_ = ("amber", f"{bad} corrupted record(s) found, isolating")
    elif unhealed:
        db_ = ("green", "Working. An earlier data loss could not be repaired (see the log)")
    else:
        isolated = quarantine.count()
        db_ = ("green", f"{isolated} bad record(s) isolated so far" if isolated else "No bad records")

    def card(name, pair):
        return {"name": name, "color": pair[0], "detail": pair[1]}

    return {
        "components": [
            card("App", app_c),
            card("Watchdog", wd_c),
            card("SMS service", sms),
            card("Cloud sync", cloud),
            card("Database", db_),
        ],
        "healing": healing_on(),
        "incidents": enrich(incidents),
        "recoveries": stats[0],
        "avg": round(stats[1], 2) if stats[1] is not None else None,
        "max": stats[2],
    }


# ---------- Chaos ----------

def _test_medicine(with_reminder):
    """A throwaway medicine due right now, used by chaos.py to test the SMS and offline-queue mechanisms."""
    now = datetime.now()
    hm, today = now.strftime("%H:%M"), now.strftime("%Y-%m-%d")
    with data_lock:
        conn = get_db()
        cur = conn.execute("INSERT INTO medicines (name, dose, times, created_day) VALUES ('Chaos test', '1 tablet', ?, ?)", (hm, today))
        med_id = cur.lastrowid
        if with_reminder:
            conn.execute(
                "INSERT INTO reminders (medicine_id, day, time, created_at, sms_status, fired_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (med_id, today, hm, now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
        conn.commit()
        conn.close()
        update_expected()
    return {"ok": True, "message": "Test medicine added.", "medicine_id": med_id, "time": hm}


@app.route("/api/chaos/<action>", methods=["POST"])
def api_chaos(action):
    if action == "crash":
        mark_chaos("app")
        threading.Timer(0.3, lambda: os._exit(1)).start()
        return {"ok": True, "message": "Crashing the app in 0.3 seconds…"}
    if action == "freeze":
        mark_chaos("app")
        chaos_state["freeze_until"] = time.time() + 120
        return {"ok": True, "message": "App frozen. Health checks will start timing out."}
    if action == "corrupt":
        mark_chaos("database")
        conn = get_db()
        conn.execute("INSERT INTO medicines (name, dose, times) VALUES ('', '-5', '25:99')")
        conn.commit()
        conn.close()
        return {"ok": True, "message": "Inserted a corrupted medicine record."}
    if action == "wipe":
        if valid_count() == 0:
            return {"ok": False, "message": "There is nothing to wipe. Add a medicine first."}, 400
        mark_chaos("database")
        with data_lock:
            conn = get_db()
            for table in ("medicines", "dose_log", "reminders"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
            conn.close()
        return {"ok": True, "message": "All medicines and history wiped. There is no backup."}
    if action == "acknowledge":
        with data_lock:
            conn = get_db()
            conn.execute(
                "UPDATE incidents SET recovered_at = 'never', action = action || ' [seen by the user; data not restored]' "
                "WHERE component = 'database' AND recovered_at IS NULL AND diagnosis LIKE 'Data loss%'")
            conn.commit()
            conn.close()
            integrity["lost"] = False
            integrity["incident_id"] = None
            update_expected()
        return {"ok": True, "message": "Marked as seen. The lost data is still gone."}
    if action == "test_sms":
        return _test_medicine(True)
    if action == "test_dose":
        return _test_medicine(False)
    if action == "cleanup":
        with data_lock:
            conn = get_db()
            ids = [r["id"] for r in conn.execute("SELECT id FROM medicines WHERE name = 'Chaos test'")]
            for i in ids:
                conn.execute("DELETE FROM reminders WHERE medicine_id = ?", (i,))
                conn.execute("DELETE FROM dose_log WHERE medicine_id = ?", (i,))
                conn.execute("DELETE FROM medicines WHERE id = ?", (i,))
            conn.execute("DELETE FROM sync_queue WHERE medicine_name = 'Chaos test'")
            conn.commit()
            conn.close()
            update_expected()
        return {"ok": True, "message": "Test data removed."}
    if action in CHAOS_LINKS:
        url = CHAOS_LINKS[action]
        if action == "sms_down":
            mark_chaos("sms_service")
        if action == "cloud_down":
            mark_chaos("cloud_sync")
        try:
            requests.get(url, timeout=2)
        except Exception:
            return {"ok": False, "message": "Could not reach that service. Is it running?"}, 502
        if action in ("sms_down", "cloud_down"):
            what = "SMS service" if action == "sms_down" else "Connection to the caregiver cloud"
            if healing_on():
                msg = (f"{what} is down. Self-healing will reconnect it automatically in about "
                       f"{HEAL_AFTER['sms_service' if action == 'sms_down' else 'cloud_sync']} seconds.")
            else:
                msg = (f"{what} is down. Self-healing is off, so it stays down until you run "
                       "python chaos.py restore, or switch self-healing back on.")
        else:
            msg = "SMS service restored." if action == "sms_up" else "Connection restored."
        return {"ok": True, "message": msg}
    return {"ok": False, "message": "Unknown action."}, 404


# ---------- Background workers ----------

def probe_loop():
    """Checks the SMS service every 2 seconds, even when nobody is sending a text, so an outage is noticed early.
    While the circuit is open it only tests again once the cooldown is over."""
    while True:
        time.sleep(2)
        if not healing_on():
            continue
        if sms_breaker.state == "open" and not sms_breaker.allow():
            continue
        try:
            requests.get(SMS_HEALTH_URL, timeout=2).raise_for_status()
            sms_breaker.record_success()
        except Exception:
            sms_breaker.record_failure()


def sync_loop():
    """Keeps retrying the offline queue until it's empty."""
    while True:
        time.sleep(3)
        if sync.check() and sync.pending_count() > 0:
            sync.flush()


def fire_due_reminders():
    """When the time strikes for a dose the person set a reminder for, fire it: the SMS goes into the queue."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    hm = now.strftime("%H:%M")
    now_min = to_min(hm)

    conn = get_db()
    rows = conn.execute(
        "SELECT r.id, r.time FROM reminders r JOIN medicines m ON m.id = r.medicine_id "
        "WHERE r.day = ? AND r.fired_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM dose_log d WHERE d.medicine_id = r.medicine_id "
        "AND d.day = r.day AND d.time = r.time)", (today,)
    ).fetchall()
    for r in rows:
        if r["time"] > hm:
            continue
        # Far too late to text about (for example the app was off): show it, but don't send an SMS
        status = "pending" if now_min - to_min(r["time"]) <= GRACE_MIN else "skipped"
        conn.execute(
            "UPDATE reminders SET fired_at = ?, sms_status = ? WHERE id = ?",
            (now.isoformat(timespec="seconds"), status, r["id"]),
        )
    conn.commit()
    conn.close()


def deliver_pending_sms():
    """Send the SMS for each new reminder, through the circuit breaker. Failed ones wait and are retried."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    rows = conn.execute(
        "SELECT r.id, r.time, m.name, m.dose FROM reminders r "
        "JOIN medicines m ON m.id = r.medicine_id "
        "WHERE r.day = ? AND r.sms_status = 'pending' AND r.fired_at IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM dose_log d WHERE d.medicine_id = r.medicine_id "
        "AND d.day = r.day AND d.time = r.time) "
        "ORDER BY r.id", (today,)
    ).fetchall()
    conn.close()

    for r in rows:
        if healing_on() and not sms_breaker.allow():
            break  # circuit is open: stop calling the service, try again later
        try:
            resp = requests.post(
                SMS_URL, json={"message": f"Time to take {r['name']} ({r['dose']}) at {r['time']}"}, timeout=2)
            resp.raise_for_status()
            if healing_on():
                sms_breaker.record_success()
        except Exception:
            if healing_on():
                sms_breaker.record_failure()   # the breaker counts it; the text waits and is retried
            else:
                conn = get_db()                # self-healing is off: no retry, the text is simply lost
                conn.execute("UPDATE reminders SET sms_status = 'failed' WHERE id = ?", (r["id"],))
                conn.commit()
                conn.close()
            continue
        conn = get_db()
        conn.execute(
            "UPDATE reminders SET sms_status = 'sent', sms_sent_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), r["id"]),
        )
        conn.commit()
        conn.close()


def reminder_loop():
    """Every 3 seconds: fire reminders whose time has struck, then deliver their SMS."""
    while True:
        time.sleep(3)
        try:
            fire_due_reminders()
            deliver_pending_sms()
        except Exception as e:
            print("[reminders] error:", e)


def check_integrity():
    """Notices a sudden loss of data (healthy medicines that were there are gone). It can see this but cannot undo it."""
    expected = get_setting("expected_meds")
    if expected is None:
        update_expected()
        return
    with data_lock:
        actual = valid_count()
        if actual < int(expected) and not integrity["lost"]:
            now = datetime.now().isoformat(timespec="milliseconds")
            conn = get_db()
            cur = conn.execute(
                "INSERT INTO incidents (component, detected_at, diagnosis, action) VALUES (?, ?, ?, ?)",
                ("database", now, f"Data loss: {expected} medicine(s) expected, {actual} found",
                 "Raised the alarm. There is no backup, so nothing can be restored"))
            integrity["incident_id"] = cur.lastrowid
            integrity["lost"] = True
            conn.commit()
            conn.close()


down_since = {}


def auto_heal():
    """With self-healing on, a helper service that stays down is reconnected automatically after a few seconds."""
    if not healing_on():
        down_since.clear()
        return
    for comp, svc in SERVICES.items():
        if service_up(svc["health"]):
            down_since.pop(comp, None)
            continue
        first = down_since.setdefault(comp, time.time())
        if time.time() - first >= HEAL_AFTER[comp]:
            try:
                requests.get(svc["up"], timeout=2)
            except Exception:
                continue            # the service is not running at all: nothing to reconnect
            down_since.pop(comp, None)
            _svc_cache.pop(svc["health"], None)


def monitor_loop():
    """Every second: data loss, and bringing helper services back when self-healing is switched on."""
    while True:
        time.sleep(1)
        try:
            on = healing_on()
            if on and not heal_watch["was_on"]:     # switched on from the terminal (python chaos.py healing on)
                heal_services()
            heal_watch["was_on"] = on
            auto_heal()
            check_integrity()
        except Exception as e:
            print("[monitor] error:", e)


def quarantine_loop():
    """Scans for corrupted records every 3 seconds."""
    while True:
        time.sleep(3)
        try:
            quarantine.scan_and_repair()
        except Exception as e:
            print("[quarantine] scan error:", e)


if __name__ == "__main__":
    init_db()
    close_orphan_incidents()
    _c = get_db()
    _open = _c.execute("SELECT id FROM incidents WHERE recovered_at IS NULL AND diagnosis LIKE 'Data loss%'").fetchone()
    _c.close()
    if _open:   # a data-loss alarm survives a restart: it was never healed
        integrity["lost"], integrity["incident_id"] = True, _open["id"]
    threading.Thread(target=probe_loop, daemon=True).start()
    threading.Thread(target=sync_loop, daemon=True).start()
    threading.Thread(target=quarantine_loop, daemon=True).start()
    threading.Thread(target=reminder_loop, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    app.run(port=5000, threaded=True)
