
from flask import Flask, request, jsonify
import time

app = Flask(__name__)
servers = []  # In-memory server list

@app.route("/add_server", methods=["POST"])
def add_server():
    data = request.get_json()

    required_fields = ["name", "public_ip", "port", "map"]
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required server fields"}), 400

    # Update or add the server in the list
    server_exists = False
    for s in servers:
        if s["public_ip"] == data["public_ip"] and s["port"] == data["port"]:
            s.update(data)
            s["last_seen"] = time.time()
            server_exists = True
            break

    if not server_exists:
        data["last_seen"] = time.time()
        servers.append(data)

    return jsonify({"status": "Server registered/updated"}), 200

@app.route("/servers", methods=["GET"])
def get_servers():
    # Return only servers seen in the last 60 seconds
    now = time.time()
    fresh_servers = [s for s in servers if now - s.get("last_seen", 0) < 60]
    return jsonify(fresh_servers)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=1000)
