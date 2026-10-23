from flask import Flask, request, jsonify

app = Flask(__name__)
state = {"up": True}
received = {}  # key -> dose record


@app.route("/sync", methods=["POST"])
def sync():
    if not state["up"]:
        return jsonify(error="network unavailable"), 503
    d = request.get_json(silent=True) or {}
    key = f"{d.get('medicine_name')}|{d.get('day')}|{d.get('time')}"
    received[key] = d  # same dose sent twice just overwrites: no duplicates
    print(f"[CLOUD] received {key}")
    return jsonify(status="stored")


@app.route("/doses")
def doses():
    items = "".join(
        f"<li>{d['day']} {d['time']}: {d['medicine_name']} (taken at {d['taken_at']})</li>"
        for d in received.values()
    )
    return f"<h2>Caregiver view: doses synced</h2><p>{len(received)} total</p><ul>{items}</ul>"


@app.route("/health")
def health():
    if not state["up"]:
        return jsonify(error="network unavailable"), 503
    return jsonify(status="ok")


# Chaos switches
@app.route("/admin/down")
def go_down():
    state["up"] = False
    return "Cloud connection is now DOWN"


@app.route("/admin/up")
def go_up():
    state["up"] = True
    return "Cloud connection is now UP"


if __name__ == "__main__":
    app.run(port=5002)
