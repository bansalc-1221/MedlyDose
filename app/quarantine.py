import re
from datetime import datetime

from db import get_db, healing_on

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def check(m):
    """Return a list of problems with a medicine record (empty list = healthy)."""
    problems = []
    name = (m["name"] or "").strip()
    dose = (m["dose"] or "").strip()
    times = (m["times"] or "").strip()

    if not name:
        problems.append("blank name")
    if not dose or dose.startswith("-") or not any(c.isdigit() for c in dose):
        problems.append(f"invalid dose '{dose}'")
    parts = [t.strip() for t in times.split(",")]
    if not times or not all(TIME_RE.match(t) for t in parts):
        problems.append(f"invalid times '{times}'")
    return problems


def scan_and_repair():
    """Move every corrupted medicine into quarantine. Returns how many were isolated."""
    if not healing_on():
        return 0   # self-healing is switched off: bad records are left where they are
    conn = get_db()
    medicines = conn.execute("SELECT * FROM medicines").fetchall()
    isolated = 0
    for m in medicines:
        problems = check(m)
        if not problems:
            continue
        detected = datetime.now()
        reason = "; ".join(problems)
        conn.execute(
            "INSERT INTO quarantine (original_id, name, dose, times, reason, quarantined_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (m["id"], m["name"], m["dose"], m["times"], reason,
             detected.isoformat(timespec="milliseconds")),
        )
        conn.execute("DELETE FROM medicines WHERE id = ?", (m["id"],))
        recovered = datetime.now()
        conn.execute(
            "INSERT INTO incidents (component, detected_at, diagnosis, action, "
            "recovered_at, recovery_seconds) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "database",
                detected.isoformat(timespec="milliseconds"),
                f"Corrupted medicine record #{m['id']}: {reason}",
                "Moved the record to quarantine; the rest of the data keeps working",
                recovered.isoformat(timespec="milliseconds"),
                round((recovered - detected).total_seconds(), 2),
            ),
        )
        isolated += 1
    conn.commit()
    conn.close()
    return isolated


def count():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
    conn.close()
    return n
