"""Simple local sign-in. Accounts live in the same database file; the session is a signed cookie.
The secret that signs the cookie is stored in the database, so signing in survives an automatic restart."""
import re
import time
from datetime import datetime

from flask import Blueprint, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db

bp = Blueprint("auth", __name__)
NAME_RE = re.compile(r"^[A-Za-z0-9_.\- ]{3,30}$")


def _body():
    return request.get_json(silent=True) or {}


@bp.route("/api/auth/me")
def me():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return {"signed_in": bool(session.get("user")), "user": session.get("user"), "has_users": n > 0}


@bp.route("/api/auth/signup", methods=["POST"])
def signup():
    d = _body()
    name = str(d.get("username", "")).strip()
    pw = str(d.get("password", ""))
    if not NAME_RE.match(name):
        return {"ok": False, "message": "Choose a name of 3 to 30 letters, numbers, spaces, dots or dashes."}, 400
    if len(pw) < 6:
        return {"ok": False, "message": "Choose a password of at least 6 characters."}, 400
    conn = get_db()
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
        conn.close()
        return {"ok": False, "message": "That name is taken. Choose another, or sign in."}, 409
    conn.execute("INSERT INTO users (username, pw_hash, created_at) VALUES (?, ?, ?)",
                 (name, generate_password_hash(pw), datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()
    session.clear()
    session["user"] = name
    session.permanent = True
    return {"ok": True, "message": f"Welcome, {name}."}


@bp.route("/api/auth/signin", methods=["POST"])
def signin():
    d = _body()
    name = str(d.get("username", "")).strip()
    pw = str(d.get("password", ""))
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
    conn.close()
    if not row or not check_password_hash(row["pw_hash"], pw):
        time.sleep(0.4)   # slows down password guessing
        return {"ok": False, "message": "That name and password don't match. Check them, or create an account."}, 401
    session.clear()
    session["user"] = row["username"]
    session.permanent = True
    return {"ok": True, "message": f"Welcome back, {row['username']}."}


@bp.route("/api/auth/signout", methods=["POST"])
def signout():
    session.clear()
    return {"ok": True, "message": "Signed out."}
