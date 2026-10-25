"""Clears a stuck "Data lost" alarm on the Health page.

Use it only when you know your medicines are fine (for example the alarm is left over from a test).
It closes the old data-loss alarm in the log (marked as cleared by you) and resets the expected medicine count.
Stop watchdog.py first (Ctrl+C), run:  python reset_alarm.py   then start python watchdog.py again."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
import db          # noqa: E402
import quarantine  # noqa: E402

conn = db.get_db()
meds = conn.execute("SELECT * FROM medicines").fetchall()
good = sum(1 for m in meds if not quarantine.check(m))
n = conn.execute(
    "UPDATE incidents SET recovered_at = 'never', action = action || ' [alarm cleared by the user with reset_alarm.py]' "
    "WHERE component = 'database' AND recovered_at IS NULL AND diagnosis LIKE 'Data loss%'").rowcount
conn.commit()
conn.close()
db.set_setting("expected_meds", good)
print(f"Healthy medicines found: {good}. Old data-loss alarms closed: {n}. Expected count reset to {good}.")
print("Now start python watchdog.py again.")
