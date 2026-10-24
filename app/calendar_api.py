"""Calendar data: for each day, which doses were scheduled and which were taken, missed or still to come."""
import calendar as pycal
from datetime import date, datetime

from flask import Blueprint, request

import quarantine
from db import get_db

bp = Blueprint("calendar_api", __name__)


def _times(text):
    return [t.strip() for t in (text or "").split(",") if quarantine.TIME_RE.match(t.strip())]


def _load(conn, first, last):
    meds = [m for m in conn.execute("SELECT * FROM medicines ORDER BY id") if not quarantine.check(m)]
    first_taken = {r["medicine_id"]: r["d"] for r in conn.execute(
        "SELECT medicine_id, MIN(day) AS d FROM dose_log GROUP BY medicine_id")}
    logs = conn.execute("SELECT * FROM dose_log WHERE day BETWEEN ? AND ? ORDER BY time", (first, last)).fetchall()
    return meds, first_taken, logs


def _day_doses(day, today, now_hm, meds, first_taken, logs):
    """Every dose of one day, with a status: taken, missed, due (time has struck) or scheduled."""
    taken = {(r["medicine_id"], r["time"]): r for r in logs if r["day"] == day}
    out, seen = [], set()
    for m in meds:
        start = m["created_day"] or first_taken.get(m["id"]) or today
        if day < start:
            continue                      # the medicine didn't exist yet
        for t in _times(m["times"]):
            key = (m["id"], t)
            seen.add(key)
            if key in taken:
                status = "taken"
            elif day < today:
                status = "missed"
            elif day == today:
                status = "scheduled" if t > now_hm else "due"
            else:
                status = "scheduled"
            out.append({"name": m["name"], "dose": m["dose"], "time": t, "status": status,
                        "taken_at": taken[key]["taken_at"] if key in taken else None})
    for key, r in taken.items():          # doses of medicines that were later removed stay in the history
        if key not in seen:
            out.append({"name": r["name"] or "Removed medicine", "dose": r["dose"] or "", "time": r["time"],
                        "status": "taken", "taken_at": r["taken_at"], "removed": True})
    out.sort(key=lambda d: (d["time"], d["name"]))
    return out


@bp.route("/api/calendar")
def month():
    now = datetime.now()
    today, now_hm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    ym = request.args.get("month") or now.strftime("%Y-%m")
    try:
        y, mo = int(ym[:4]), int(ym[5:7])
        ndays = pycal.monthrange(y, mo)[1]
    except (ValueError, IndexError, pycal.IllegalMonthError):
        return {"ok": False, "message": "Month must look like 2026-10."}, 400
    first, last = f"{y:04d}-{mo:02d}-01", f"{y:04d}-{mo:02d}-{ndays:02d}"
    conn = get_db()
    meds, first_taken, logs = _load(conn, first, last)
    conn.close()
    days = {}
    for d in range(1, ndays + 1):
        key = f"{y:04d}-{mo:02d}-{d:02d}"
        doses = _day_doses(key, today, now_hm, meds, first_taken, logs)
        days[key] = {"total": len(doses), "taken": sum(1 for x in doses if x["status"] == "taken"),
                     "missed": sum(1 for x in doses if x["status"] == "missed")}
    return {"ok": True, "month": f"{y:04d}-{mo:02d}", "first_weekday": date(y, mo, 1).weekday(),
            "days_in_month": ndays, "today": today, "days": days}


@bp.route("/api/calendar/day")
def one_day():
    now = datetime.now()
    today, now_hm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    day = request.args.get("date", today)
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return {"ok": False, "message": "Date must look like 2026-10-23."}, 400
    conn = get_db()
    meds, first_taken, logs = _load(conn, day, day)
    conn.close()
    return {"ok": True, "date": day, "today": today, "doses": _day_doses(day, today, now_hm, meds, first_taken, logs)}
