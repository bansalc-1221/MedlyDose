import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "medtracker.db"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row  # lets us read columns by name
    return conn


def init_db():
    conn = get_db()
    # WAL lets the watchdog, the app and its background threads share the file smoothly
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS medicines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        dose TEXT NOT NULL,
        times TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS dose_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medicine_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        time TEXT NOT NULL,
        taken_at TEXT NOT NULL,
        name TEXT,
        dose TEXT,
        UNIQUE(medicine_id, day, time)
    );
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medicine_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        time TEXT NOT NULL,
        created_at TEXT NOT NULL,       -- when the person pressed the bell
        sms_status TEXT NOT NULL,       -- armed | pending | sent | skipped
        sms_sent_at TEXT,
        fired_at TEXT,                  -- when the due time struck and the alert went out
        UNIQUE(medicine_id, day, time)
    );
    CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        component TEXT NOT NULL,
        detected_at TEXT NOT NULL,
        diagnosis TEXT NOT NULL,
        action TEXT NOT NULL,
        recovered_at TEXT,
        recovery_seconds REAL
    );
    CREATE TABLE IF NOT EXISTS sync_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medicine_name TEXT NOT NULL,
        day TEXT NOT NULL,
        time TEXT NOT NULL,
        taken_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS quarantine (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_id INTEGER,
        name TEXT,
        dose TEXT,
        times TEXT,
        reason TEXT NOT NULL,
        quarantined_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS chaos_marks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        component TEXT NOT NULL,
        injected_at REAL NOT NULL        -- when a failure was injected (seconds since 1970)
    );
    CREATE TABLE IF NOT EXISTS watchdog_status (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_beat TEXT NOT NULL,
        state TEXT NOT NULL,
        detail TEXT NOT NULL
    );
    """)

    # Upgrade an older database: the history now keeps each dose's own name and dose text,
    # so it stays readable even after a medicine is removed.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(dose_log)")}
    if "name" not in cols:
        conn.execute("ALTER TABLE dose_log ADD COLUMN name TEXT")
    if "dose" not in cols:
        conn.execute("ALTER TABLE dose_log ADD COLUMN dose TEXT")
    # Reminders used to be created automatically at the due time; those rows count as already fired.
    rcols = {r["name"] for r in conn.execute("PRAGMA table_info(reminders)")}
    if "fired_at" not in rcols:
        conn.execute("ALTER TABLE reminders ADD COLUMN fired_at TEXT")
        conn.execute("UPDATE reminders SET fired_at = created_at WHERE fired_at IS NULL")
    conn.execute(
        "UPDATE dose_log SET "
        "name = (SELECT name FROM medicines WHERE medicines.id = dose_log.medicine_id), "
        "dose = (SELECT dose FROM medicines WHERE medicines.id = dose_log.medicine_id) "
        "WHERE name IS NULL"
    )
    conn.commit()
    conn.close()


def close_orphan_incidents():
    """If the app restarted mid-incident, close the leftover record so it doesn't look open forever."""
    conn = get_db()
    conn.execute(
        "UPDATE incidents SET recovered_at = ?, action = action || ' [state reset by app restart]' "
        "WHERE recovered_at IS NULL AND diagnosis NOT LIKE 'Data loss%'",
        (datetime.now().isoformat(timespec="seconds"),),
    )
    conn.commit()
    conn.close()


# ---------- Settings (shared by the app, the watchdog and chaos.py) ----------

def get_setting(key, default=None):
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default


def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


_heal_cache = {"at": 0.0, "on": True}


def healing_on():
    """Is self-healing switched on? Cached for 1 second so busy code paths don't hit the database each time."""
    if time.time() - _heal_cache["at"] > 1:
        _heal_cache["on"] = get_setting("healing", "on") != "off"
        _heal_cache["at"] = time.time()
    return _heal_cache["on"]


def set_healing(on):
    set_setting("healing", "on" if on else "off")
    _heal_cache["at"] = 0.0


def mark_chaos(component):
    """Remember the exact moment a failure was injected, so 'time to detect' can be measured."""
    conn = get_db()
    conn.execute("INSERT INTO chaos_marks (component, injected_at) VALUES (?, ?)", (component, time.time()))
    conn.commit()
    conn.close()
