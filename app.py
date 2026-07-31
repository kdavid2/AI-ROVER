import os
import time
import requests
from flask import Flask, render_template_string, request, jsonify
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

app = Flask(__name__)

# ── Ρυθμίσεις ────────────────────────────────────────────────────────────
CONTROL_BASE = os.environ["ROVER_URL"]
CAPTURE_URL = f"{CONTROL_BASE}/capture"          
MODEL = "gemini-robotics-er-2-preview"

CAR_VALS = {"forward": 1, "backward": 2, "left": 3, "right": 4, "stop": 5}
MOVE_PULSE_SECONDS = 0.25     
TURN_PULSE_SECONDS = 0.15     

_session = requests.Session()

class RoverDecision(BaseModel):
    command: str = Field(description="Η εντολή: forward, backward, left, right, stop")
    done: bool = Field(description="True αν ο στόχος ολοκληρώθηκε")
    reason: str = Field(description="Σύντομη αιτιολόγηση στα ελληνικά")

def control(var: str, val: int) -> None:
    url = f"{CONTROL_BASE}/control?var={var}&val={val}"
    try:
        _session.get(url, timeout=3)
    except requests.RequestException:
        pass

def car(command: str) -> None:
    if command in CAR_VALS:
        control("car", CAR_VALS[command])

def get_frame() -> bytes:
    try:
        r = _session.get(CAPTURE_URL, timeout=4)
        r.raise_for_status()
        if r.content.startswith(b"\xff\xd8"):
            return r.content
    except Exception:
        pass
    return b""

# HTML Template για το κινητό και τον browser
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="el">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rover Cloud Control</title>
    <style>
        body { background-color: #121212; color: #fff; font-family: Arial, sans-serif; text-align: center; margin: 0; padding: 10px; }
        h2 { margin: 10px 0; font-size: 1.2rem; }
        .cam-container { margin: 10px auto; max-width: 400px; }
        img { width: 100%; border-radius: 8px; border: 2px solid #444; }
        .controls { display: grid; grid-template-columns: repeat(3, 80px); gap: 10px; justify-content: center; margin: 15px 0; }
        button { background-color: #333; color: white; border: none; padding: 15px; font-size: 1.1rem; border-radius: 8px; cursor: pointer; }
        button:active { background-color: #555; }
        .btn-stop { background-color: #b71c1c; grid-column: span 3; }
        .ai-section { margin: 15px auto; max-width: 400px; text-align: left; background: #1e1e1e; padding: 10px; border-radius: 8px; }
        input[type="text"] { width: calc(100% - 20px); padding: 8px; margin-top: 5px; border-radius: 4px; border: none; }
        .log-box { background: #000; color: #0ff; font-family: monospace; font-size: 0.85rem; height: 120px; overflow-y: auto; padding: 8px; border-radius: 4px; margin-top: 10px; text-align: left; }
    </style>
</head>
<body>
    <h2>🤖 Rover Cloud Control</h2>
    
    <div class="cam-container">
        <img id="camStream" src="/video_feed" alt="Live Camera">
    </div>

    <div class="controls">
        <div></div>
        <button onclick="sendCmd('forward')">▲</button>
        <div></div>
        <button onclick="sendCmd('left')">◄</button>
        <button class="btn-stop" onclick="sendCmd('stop')">STOP</button>
        <button onclick="sendCmd('right')">►</button>
        <div></div>
        <button onclick="sendCmd('backward')">▼</button>
    </div>

    <div class="ai-section">
        <label><b>AI Στόχος (Gemini):</b></label>
        <input type="text" id="aiGoal" value="κάνε περιπολία">
        <button onclick="startAi()" style="margin-top:8px; width:100%; background:#2e7d32;">Εκκίνηση AI Mode</button>
        <div class="log-box" id="logBox">Σύστημα έτοιμο...</div>
    </div>

    <script>
        // Ανανέωση κάμερας κάθε 2 δευτερόλεπτα
        setInterval(() => {
            let img = document.getElementById('camStream');
            img.src = '/video_feed?' + new Date().getTime();
        }, 2000);

        function sendCmd(cmd) {
            fetch('/cmd', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: cmd})
            });
        }

        function startAi() {
            let goal = document.getElementById('aiGoal').value;
            appendLog("Ξεκινάει AI με στόχο: " + goal);
            fetch('/ai_run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({goal: goal})
            })
            .then(res => res.json())
            .then(data => {
                appendLog("🤖 " + data.reason + " [" + data.command.toUpperCase() + "]");
            });
        }

        function appendLog(text) {
            let box = document.getElementById('logBox');
            box.innerHTML += "<br>" + text;
            box.scrollTop = box.scrollHeight;
        }
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/video_feed")
def video_feed():
    frame = get_frame()
    if frame:
        return frame, 200, {'Content-Type': 'image/jpeg'}
    return "", 204

@app.route("/cmd", methods=["POST"])
def manual_cmd():
    data = request.json
    cmd = data.get("command")
    if cmd in ("forward", "backward"):
        car(cmd)
        time.sleep(MOVE_PULSE_SECONDS)
        car("stop")
    elif cmd in ("left", "right"):
        car(cmd)
        time.sleep(TURN_PULSE_SECONDS)
        car("stop")
    else:
        car("stop")
    return jsonify({"status": "ok"})

@app.route("/ai_run", methods=["POST"])
def ai_run():
    data = request.json
    goal = data.get("goal", "περίπολη")
    
    frame = get_frame()
    if not frame:
        return jsonify({"command": "stop", "reason": "Αποτυχία λήψης κάμερας από το σπίτι"})

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"command": "stop", "reason": "Δεν βρέθηκε GEMINI_API_KEY στο Render"})

    client = genai.Client(api_key=api_key)
    
    contents = [
        types.Part.from_bytes(data=frame, mime_type="image/jpeg"),
        f"Στόχος: {goal}"
    ]
    prompt = "Είσαι το ρομπότ rover. Πρόχώρα 'forward' αν είναι ελεύθερα, στρίψε αν υπάρχει εμπόδιο. Αν τελείωσες, done=True."

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=prompt,
                temperature=0.2,
                response_mime_type="application/json",
                response_schema=RoverDecision,
            ),
        )
        result = response.parsed
        if result:
            cmd = result.command
            if cmd in ("forward", "backward"):
                car(cmd)
                time.sleep(MOVE_PULSE_SECONDS)
                car("stop")
            elif cmd in ("left", "right"):
                car(cmd)
                time.sleep(TURN_PULSE_SECONDS)
                car("stop")
            else:
                car("stop")
            return jsonify({"command": cmd, "reason": result.reason})
    except Exception as e:
        return jsonify({"command": "stop", "reason": f"Σφάλμα AI: {str(e)}"})

    return jsonify({"command": "stop", "reason": "Άγνωστο σφάλμα"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)