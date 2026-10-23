import threading

from flask import Flask, request, jsonify

app = Flask(__name__)
state = {"up": True}


@app.route("/send", methods=["POST"])
def send():
    if not state["up"]:
        return jsonify(error="service unavailable"), 503
    data = request.get_json(silent=True) or {}
    print(f"[SMS SENT] {data.get('message')}")
    return jsonify(status="sent")


@app.route("/health")
def health():
    if not state["up"]:
        return jsonify(status="down"), 503
    return jsonify(status="ok")


# Chaos switches: visit these URLs to break or repair the service
@app.route("/admin/down")
def go_down():
    """/admin/down stays down until /admin/up. /admin/down?seconds=12 comes back by itself, like a real outage."""
    state["up"] = False
    secs = request.args.get("seconds", type=float)
    if secs:
        t = threading.Timer(secs, lambda: state.update(up=True))
        t.daemon = True
        t.start()
        return f"SMS service is DOWN for {secs:g} seconds"
    return "SMS service is now DOWN"


@app.route("/admin/up")
def go_up():
    state["up"] = True
    return "SMS service is now UP"


if __name__ == "__main__":
    app.run(port=5001)
