import time
from datetime import datetime

from db import get_db


class CircuitBreaker:
    def __init__(self, name, failure_threshold=3, cooldown=20):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.failures = 0
        self.state = "closed"      # closed = normal, open = blocked, half-open = testing
        self.opened_at = None
        self.detected = None
        self.incident_id = None

    def allow(self):
        """Should we call the service right now?"""
        if self.state == "open":
            if time.time() - self.opened_at >= self.cooldown:
                self.state = "half-open"   # cooldown over: allow one test call
                return True
            return False
        return True

    def retry_in(self):
        if self.state != "open":
            return 0
        return max(0, int(self.cooldown - (time.time() - self.opened_at)))

    def record_failure(self):
        self.failures += 1
        if self.state == "half-open":
            self.state = "open"            # test call failed: wait another cooldown
            self.opened_at = time.time()
        elif self.state == "closed" and self.failures >= self.failure_threshold:
            self.state = "open"
            self.opened_at = time.time()
            self._open_incident()

    def record_success(self):
        was_broken = self.state in ("open", "half-open")
        self.failures = 0
        self.state = "closed"
        if was_broken and self.incident_id:
            self._close_incident()

    def _open_incident(self):
        self.detected = datetime.now()
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO incidents (component, detected_at, diagnosis, action) "
            "VALUES (?, ?, ?, ?)",
            (
                self.name,
                self.detected.isoformat(timespec="milliseconds"),
                f"{self.name} failed {self.failures} times in a row",
                f"Circuit opened: stopped calling it, testing again every {self.cooldown}s",
            ),
        )
        self.incident_id = cur.lastrowid
        conn.commit()
        conn.close()

    def _close_incident(self):
        now = datetime.now()
        seconds = (now - self.detected).total_seconds()
        conn = get_db()
        conn.execute(
            "UPDATE incidents SET recovered_at = ?, recovery_seconds = ? WHERE id = ?",
            (now.isoformat(timespec="milliseconds"), round(seconds, 2), self.incident_id),
        )
        conn.commit()
        conn.close()
        self.incident_id = None
