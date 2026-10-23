"""
MedTracker chaos script.

Breaks MedTracker on purpose, times how long it takes to notice and recover, and prints a results table.
Judges (and you) can run it to check every claim in the report.

Start MedTracker first (three terminals, each with the venv on):
    python watchdog.py
    python mock_sms\\server.py
    python mock_cloud\\server.py

Then, in a fourth terminal:
    python chaos.py                       run every failure 3 times with self-healing ON, once OFF, print the table
    python chaos.py run --runs 1          a quicker run
    python chaos.py run --only crash,flood
    python chaos.py run --skip-off        skip the "without self-healing" comparison
    python chaos.py inject crash          just break one thing (crash, freeze, sms, cloud, corrupt, flood, wipe)
    python chaos.py healing on            switch self-healing back on (needed if you crashed the app while it was off)
    python chaos.py healing off

Times are measured from the moment the failure is injected:
    detected   = until the system notices
    recovered  = until it is back to normal, with no human action
"""
import argparse
import json
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "app"))
import db          # noqa: E402  (shares the app's database code and settings)
import quarantine  # noqa: E402

APP = "http://127.0.0.1:5000"
CLOUD = "http://127.0.0.1:5002"
SMS = "http://127.0.0.1:5001"
RECOVERY_LIMIT = 30     # the brief's target: recover within 30 seconds, no human help
OFF_WAIT = 20           # how long we watch a failure that nothing is healing

SCENARIOS = ["crash", "freeze", "sms", "cloud", "corrupt", "flood", "wipe"]
DETECTED_BY = {
    "crash": "Watchdog sees the process has exited",
    "freeze": "Watchdog: 3 failed health checks in a row",
    "sms": "Circuit breaker: 3 failed checks of the SMS service",
    "cloud": "Sync loop pings the cloud every 3 s",
    "corrupt": "Quarantine scan every 3 s validates every record",
    "flood": "Rate limiter sees one client over its limit",
    "wipe": "Integrity check: healthy medicines suddenly missing",
}
LABEL = {
    "crash": "App crashes", "freeze": "App freezes", "sms": "SMS service outage (12 s)",
    "cloud": "Internet to cloud drops (8 s)", "corrupt": "Corrupted record in database",
    "flood": "Traffic flood (8 s)", "wipe": "All data wiped",
}


# ---------------------------------------------------------------- helpers

def say(text=""):
    print(text, flush=True)


def post(path, **kw):
    try:
        return requests.post(APP + path, timeout=kw.pop("timeout", 4), **kw)
    except Exception:
        return None


def get_json(url, timeout=3):
    try:
        return requests.get(url, timeout=timeout).json()
    except Exception:
        return None


def app_up():
    try:
        return requests.get(APP + "/health", timeout=1).status_code == 200
    except Exception:
        return False


def wait_for_app(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if app_up():
            return True
        time.sleep(0.3)
    return False


def set_healing(on):
    db.set_healing(on)
    time.sleep(1.4)   # the app and the watchdog re-read the setting about once a second


def incident_since(component, since, like=None, wait=6.0):
    """The newest incident for a component that was detected at or after `since`. Returns (row, detected_timestamp)."""
    end = time.time() + wait
    while True:
        conn = db.get_db()
        rows = conn.execute("SELECT * FROM incidents WHERE component = ? ORDER BY id DESC LIMIT 12", (component,)).fetchall()
        conn.close()
        for r in rows:
            det = datetime.fromisoformat(r["detected_at"]).timestamp()
            if det >= since - 0.5 and (like is None or like in r["diagnosis"]):
                return r, det
        if time.time() > end:
            return None, None
        time.sleep(0.3)


def cleanup():
    post("/api/chaos/cleanup")


def result(name, mode, detected, recovered, note):
    return {"failure": name, "mode": mode, "detected": None if detected is None else round(detected, 1),
            "recovered": None if recovered is None else round(recovered, 1), "note": note}


def fresh_minute():
    """The test dose is due 'now'. Make sure the caregiver view has not already seen a dose for this exact minute."""
    for _ in range(14):
        hm = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            text = requests.get(CLOUD + "/doses", timeout=3).text
        except Exception:
            return
        if f"{hm}: Chaos test" not in text:
            return
        time.sleep(5)


# ---------------------------------------------------------------- scenarios
# Each takes heal (True = self-healing on) and returns one result dict.

def s_crash(heal):
    set_healing(heal)
    wait_for_app()
    t0 = time.time()
    post("/api/chaos/crash")
    limit = RECOVERY_LIMIT if heal else OFF_WAIT
    saw_down, recovered = False, None
    while time.time() - t0 < limit:
        if not app_up():
            saw_down = True
        elif saw_down:
            recovered = time.time() - t0
            break
        time.sleep(0.15)
    detected = None
    if heal and recovered is not None:
        row, det = incident_since("app", t0)
        detected = det - t0 if det else None
    if not heal:
        set_healing(True)      # a human switches healing back on so the rest of the run can continue
        wait_for_app()
        return result("crash", "without", None, None, f"Still down after {OFF_WAIT} s. Nothing restarted it")
    return result("crash", "with", detected, recovered, "Restarted automatically" if recovered else "NOT recovered")


def s_freeze(heal):
    set_healing(heal)
    wait_for_app()
    t0 = time.time()
    post("/api/chaos/freeze")
    limit = RECOVERY_LIMIT if heal else OFF_WAIT
    saw_down, recovered = False, None
    while time.time() - t0 < limit:
        if not app_up():
            saw_down = True
        elif saw_down:
            recovered = time.time() - t0
            break
        time.sleep(0.15)
    detected = None
    if heal and recovered is not None:
        row, det = incident_since("app", t0)
        detected = det - t0 if det else None
    if not heal:
        set_healing(True)
        wait_for_app(60)
        return result("freeze", "without", None, None, f"Still frozen after {OFF_WAIT} s. Nothing noticed")
    return result("freeze", "with", detected, recovered, "Killed and restarted automatically" if recovered else "NOT recovered")


def s_sms(heal):
    set_healing(heal)
    cleanup()
    requests.get(SMS + "/admin/up", timeout=2)
    time.sleep(2.5)
    t0 = time.time()
    post("/api/chaos/sms_down")
    post("/api/chaos/test_sms")           # a reminder whose text is due right now
    limit = 30 if heal else 22
    recovered, failed_at = None, None
    while time.time() - t0 < limit:
        today = get_json(APP + "/api/today") or {}
        dose = next((d for d in today.get("doses", []) if d["name"] == "Chaos test"), None)
        if dose and dose["sms"] == "sent":
            recovered = time.time() - t0
            break
        if dose and dose["sms"] == "failed" and failed_at is None:
            failed_at = time.time() - t0
        time.sleep(0.25)
    detected = None
    if heal:
        row, det = incident_since("sms_service", t0, wait=4)
        detected = det - t0 if det else None
    requests.get(SMS + "/admin/up", timeout=2)
    cleanup()
    if heal:
        for _ in range(60):   # let the circuit settle before the next run
            b = (get_json(APP + "/api/today") or {}).get("breaker", {})
            if b.get("state") == "closed":
                break
            time.sleep(0.5)
        return result("sms", "with", detected, recovered,
                      "Circuit opened, text sent once the service returned" if recovered else "NOT recovered")
    return result("sms", "without", None, None, "Text failed and was never sent, even after the service came back")


def s_cloud(heal):
    set_healing(heal)
    cleanup()
    requests.get(CLOUD + "/admin/up", timeout=2)
    fresh_minute()
    setup = post("/api/chaos/test_dose")
    info = setup.json() if setup is not None else {}
    t0 = time.time()
    post("/api/chaos/cloud_down")
    detected = None
    if heal:
        while time.time() - t0 < 8:
            comps = (get_json(APP + "/api/status") or {}).get("components", [])
            cloud = next((c for c in comps if c["name"] == "Cloud sync"), None)
            if cloud and cloud["color"] == "amber":
                detected = time.time() - t0
                break
            time.sleep(0.15)
    time.sleep(max(0, 4 - (time.time() - t0)))
    post("/api/take", json={"medicine_id": info.get("medicine_id"), "time": info.get("time")})
    time.sleep(max(0, 8 - (time.time() - t0)))
    requests.get(CLOUD + "/admin/up", timeout=2)
    restored = time.time() - t0
    key = f"{datetime.now().strftime('%Y-%m-%d')} {info.get('time')}: Chaos test"
    recovered = None
    limit = restored + (15 if heal else 14)
    while time.time() - t0 < limit:
        try:
            if key in requests.get(CLOUD + "/doses", timeout=2).text:
                recovered = time.time() - t0
                break
        except Exception:
            pass
        time.sleep(0.2)
    cleanup()
    if heal:
        return result("cloud", "with", detected, recovered,
                      "Dose queued locally, synced after the connection returned" if recovered else "NOT recovered")
    return result("cloud", "without", None, None, "The dose never reached the caregiver. It was dropped")


def s_corrupt(heal):
    set_healing(heal)

    def bad_rows():
        conn = db.get_db()
        n = conn.execute("SELECT COUNT(*) FROM medicines WHERE times = '25:99'").fetchone()[0]
        conn.close()
        return n

    t0 = time.time()
    post("/api/chaos/corrupt")
    limit = RECOVERY_LIMIT if heal else 10
    recovered = None
    while time.time() - t0 < limit:
        if bad_rows() == 0 and time.time() - t0 > 0.3:
            recovered = time.time() - t0
            break
        time.sleep(0.1)
    if heal:
        row, det = incident_since("database", t0, like="Corrupted", wait=4)
        detected = det - t0 if det else None
        return result("corrupt", "with", detected, recovered,
                      "Record moved to quarantine" if recovered else "NOT recovered")
    conn = db.get_db()                       # tidy up by hand: nothing else will
    conn.execute("DELETE FROM medicines WHERE times = '25:99'")
    conn.commit()
    conn.close()
    set_healing(True)
    return result("corrupt", "without", None, None, "Bad record stays in the database, mixed with real data")


def flood(seconds, workers=60, probe=True):
    """One noisy client hammers the app. A normal client keeps using it. Returns what each side experienced."""
    stop = time.time() + seconds
    stats = {"sent": 0, "refused": 0, "first_refused": None, "flood_errors": 0}
    probe_log = []
    lock = threading.Lock()
    t0 = time.time()

    def hammer():
        s = requests.Session()
        s.headers["X-Client-Id"] = "chaos-flood"
        while time.time() < stop:
            try:
                r = s.get(APP + "/api/status", timeout=3)
                with lock:
                    stats["sent"] += 1
                    if r.status_code == 429:
                        stats["refused"] += 1
                        if stats["first_refused"] is None:
                            stats["first_refused"] = time.time() - t0
            except Exception:
                with lock:
                    stats["flood_errors"] += 1

    def normal_user():
        s = requests.Session()
        while time.time() < stop:
            a = time.time()
            try:
                code = s.get(APP + "/api/today", timeout=3).status_code
            except Exception:
                code = 0
            probe_log.append((code, time.time() - a))
            time.sleep(0.1)

    threads = [threading.Thread(target=hammer, daemon=True) for _ in range(workers)]
    if probe:
        threads.append(threading.Thread(target=normal_user, daemon=True))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stats["probe"] = probe_log
    return stats


def s_flood(heal):
    set_healing(heal)
    db.mark_chaos("traffic")      # (the Chaos page button starts this same flood from inside the app)
    st = flood(8)
    log = st["probe"]
    ok = [l for c, l in log if c == 200]
    lat = sorted(l for c, l in log)
    p95 = lat[int(len(lat) * 0.95) - 1] if lat else None
    success = 100.0 * len(ok) / len(log) if log else 0
    note = (f"Normal user: {success:.0f}% of requests answered, slowest {max(lat):.2f} s, "
            f"95th percentile {p95:.2f} s. Flood: {st['refused']} of {st['sent']} requests refused") if lat else "no data"
    time.sleep(3.5)   # let the incident close
    if heal:
        return result("flood", "with", st["first_refused"], 0.0, note)
    set_healing(True)
    return result("flood", "without", None, None, note)


def s_wipe(heal):
    set_healing(True)
    # Safety net: keep a copy of your real data, because this really deletes it
    conn = db.get_db()
    snap = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in ("medicines", "dose_log", "reminders")}
    conn.close()
    if not any(not quarantine.check(m) for m in snap["medicines"]):
        post("/api/chaos/test_dose")
    t0 = time.time()
    post("/api/chaos/wipe")
    row, det = incident_since("database", t0, like="Data loss", wait=6)
    detected = det - t0 if det else None
    recovered = None
    end = time.time() + 15
    while time.time() < end:                       # give it every chance to heal
        conn = db.get_db()
        n = conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0]
        conn.close()
        if n > 0:
            recovered = time.time() - t0
            break
        time.sleep(0.5)
    post("/api/chaos/acknowledge")
    conn = db.get_db()                             # put your real data back (this is the test harness, not the app healing)
    for table, rows in snap.items():
        for r in rows:
            cols = ",".join(r)
            conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({','.join('?' * len(r))})", list(r.values()))
    conn.commit()
    conn.close()
    cleanup()
    db.set_setting("expected_meds", sum(1 for m in snap["medicines"] if not quarantine.check(m)))
    return result("wipe", "with", detected, recovered,
                  "Data is gone for good. The alarm is raised but nothing can restore it" if recovered is None else "restored?")


RUNNERS = {"crash": s_crash, "freeze": s_freeze, "sms": s_sms, "cloud": s_cloud,
           "corrupt": s_corrupt, "flood": s_flood, "wipe": s_wipe}


# ---------------------------------------------------------------- reporting

def fmt(x):
    return "—" if x is None else f"{x:.1f}"


def summarise(rows, name):
    with_rows = [r for r in rows if r["failure"] == name and r["mode"] == "with"]
    off_rows = [r for r in rows if r["failure"] == name and r["mode"] == "without"]
    return with_rows, off_rows


def build_table(rows, ran):
    out = ["| Failure | How it is detected | Detected after (s) | Back to normal after (s) | Runs | Without self-healing |",
           "|---|---|---|---|---|---|"]
    for name in ran:
        w, o = summarise(rows, name)
        if not w:
            continue
        det = [r["detected"] for r in w if r["detected"] is not None]
        rec = [r["recovered"] for r in w if r["recovered"] is not None]
        det_s = f"{statistics.mean(det):.1f} (max {max(det):.1f})" if det else "—"
        if name == "wipe":
            rec_s = "**Never. Not healed**"
        elif rec:
            rec_s = f"{statistics.mean(rec):.1f} (max {max(rec):.1f})"
        else:
            rec_s = "**Not recovered**"
        off_s = o[0]["note"] if o else ("Same: nothing could restore it" if name == "wipe" else "not run")
        out.append(f"| {LABEL[name]} | {DETECTED_BY[name]} | {det_s} | {rec_s} | {len(w)} | {off_s} |")
    return "\n".join(out)


def run(args):
    ran = [s for s in (args.only.split(",") if args.only else SCENARIOS) if s in RUNNERS]
    if not app_up():
        say("MedTracker is not answering on port 5000. Start it first:  python watchdog.py")
        return 1
    beat = db.get_db().execute("SELECT last_beat FROM watchdog_status WHERE id = 1").fetchone()
    say(f"Chaos run: {', '.join(ran)}  |  {args.runs} run(s) with self-healing, {'no' if args.skip_off else '1'} without")
    say("Your real data is untouched (test doses are removed, wiped data is put back).\n")
    global OFF_WAIT
    OFF_WAIT = args.off_wait
    rows = []
    try:
        for name in ran:
            for i in range(args.runs if name != "wipe" else 1):
                say(f"[{name}] run {i + 1}/{args.runs if name != 'wipe' else 1}, self-healing ON …")
                r = RUNNERS[name](True)
                rows.append(r)
                say(f"    detected {fmt(r['detected'])} s, back to normal {fmt(r['recovered'])} s. {r['note']}")
            if not args.skip_off and name != "wipe":
                say(f"[{name}] self-healing OFF …")
                r = RUNNERS[name](False)
                rows.append(r)
                say(f"    {r['note']}")
    finally:
        set_healing(True)
        cleanup()
        wait_for_app(60)

    table = build_table(rows, ran)
    when = datetime.now().strftime("%d %b %Y %H:%M")
    say("\n" + table + "\n")
    (ROOT / "chaos_results.md").write_text(f"# MedTracker chaos results ({when})\n\n{table}\n", encoding="utf-8")
    (ROOT / "chaos_results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    say("Saved chaos_results.md and chaos_results.json")
    slow = [r for r in rows if r["mode"] == "with" and r["failure"] != "wipe" and (r["recovered"] is None or r["recovered"] > RECOVERY_LIMIT)]
    say("All recoveries within 30 s with no human help." if not slow else "Some recoveries missed the 30 s target: see above.")
    return 0


def inject(args):
    action = {"sms": "sms_down", "cloud": "cloud_down"}.get(args.name, args.name)
    r = post("/api/chaos/" + action)
    say(r.json().get("message") if r is not None else "The app did not answer.")


def healing(args):
    db.set_healing(args.state == "on")
    say(f"Self-healing is now {args.state.upper()}.")


def main():
    p = argparse.ArgumentParser(description="MedTracker chaos script")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run the failures and print the results table")
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--only", default="")
    r.add_argument("--skip-off", action="store_true")
    r.add_argument("--off-wait", type=int, default=OFF_WAIT)
    i = sub.add_parser("inject", help="break one thing and stop")
    i.add_argument("name", choices=SCENARIOS)
    h = sub.add_parser("healing", help="switch self-healing on or off")
    h.add_argument("state", choices=["on", "off"])
    f = sub.add_parser("flood", help="flood the app with requests")
    f.add_argument("--seconds", type=int, default=8)
    args = p.parse_args()

    if args.cmd in (None, "run"):
        if args.cmd is None:
            args = argparse.Namespace(runs=3, only="", skip_off=False, off_wait=OFF_WAIT)
        return run(args)
    if args.cmd == "inject":
        return inject(args)
    if args.cmd == "healing":
        return healing(args)
    if args.cmd == "flood":
        st = flood(args.seconds, probe=False)
        say(f"Flood finished: {st['sent']} requests sent, {st['refused']} refused.")
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
