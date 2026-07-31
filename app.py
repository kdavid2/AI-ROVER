import os
import time
import threading
import requests
from flask import Flask, render_template_string, request, jsonify
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

app = Flask(__name__)

CONTROL_BASE = os.environ["ROVER_URL"]
CAPTURE_URL = f"{CONTROL_BASE}/capture"          
MODEL = "gemini-robotics-er-2-preview"

CAR_VALS = {"forward": 1, "backward": 2, "left": 3, "right": 4, "stop": 5}
MOVE_PULSE_SECONDS = 0.25     
TURN_PULSE_SECONDS = 0.15     

_session = requests.Session()

# Μεταβλητές κατάστασης για το AI Thread
ai_thread = None
ai_running = False
ai_status_log = "Σύστημα έτοιμο..."

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

# HTML Template με ζωντανό log polling
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
        
        .controls { 
            display: grid; 
            grid-template-columns: repeat(3, 80px); 
            grid-template-rows: repeat(3, 60px);
            gap: 8px; 
            justify-content: center; 
            margin: 15px 0; 
        }
        button { background-color: #333; color: white; border: none; font-size: 1.2rem; border-radius: 8px; cursor: pointer; }
        button:active { background-color: #555; }
        
        .btn-up { grid-column: 2; grid-row: 1; }
        .btn-left { grid-column: 1; grid-row: 2; }
        .btn-stop { grid-column: 2; grid-row: 2; background-color: #b71c1c; font-size: 1rem; font-weight: bold; }
        .btn-right { grid-column: 3; grid-row: 2; }
        .btn-down { grid-column: 2; grid-row: 3; }

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
        <button class="btn-up" onclick="sendCmd('forward')">▲</button>
        <button class="btn-left" onclick="sendCmd('left')">◄</button>
        <button class="btn-stop" onclick="sendCmd('stop')">STOP</button>
        <button class="btn-right" onclick="sendCmd('right')">►</button>
        <button class="btn-down" onclick="sendCmd('backward')">▼</button>
    </div>

    <div class="ai-section">
        <label><b>AI Στόχος (Gemini):</b></label>
        <input type="text" id="aiGoal" value="κάνε περιπολία">
        <button onclick="toggleAi()" id="aiBtn" style="margin-top:8px; width:100%; padding: 10px; border:none; border-radius:6px; color:#fff; background:#2e7d32; cursor:pointer; font-weight:bold;">Εκκίνηση AI Mode</button>
        <div class="log-box" id="logBox">Σύστημα έτοιμο...</div>
    </div>

    <script>
        // Ανανέωση κάμερας κάθε 2 δευτερόλεπτα
        setInterval(() => {
            let img = document.getElementById('camStream');
            img.src = '/video_feed?' + new Date().getTime();
        }, 2000);

        // Polling για τα logs από τον server κάθε 1 δευτερόλεπτο
        setInterval(() => {
            fetch('/status')
            .then(res => res.json())
            .then(data => {
                let box = document.getElementById('logBox');
                box.innerHTML = data.logs.replace(/\\n/g, "<br>");
                box.scrollTop = box.scrollHeight;
                
                let btn = document.getElementById('aiBtn');
                if (data.running) {
                    btn.innerText = "Διακοπή AI Mode";
                    btn.style.background = "#b71c1c";
                } else {
                    btn.innerText = "Εκκίνηση AI Mode";
                    btn.style.background = "#2e7d32";
                }
            });
        }, 1000);

        function sendCmd(cmd) {
            if(cmd === 'stop') {
                fetch('/ai_stop', {method: 'POST'});
            }
            fetch('/cmd', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: cmd})
            });
        }

        // Υποστήριξη WASD και Βελάκων
        window.addEventListener('keydown', (e) => {
            let k = e.key.toLowerCase();
            if (k === 'w' || e.key === 'ArrowUp') sendCmd('forward');
            else if (k === 's' || e.key === 'ArrowDown') sendCmd('backward');
            else if (k === 'a' || e.key === 'ArrowLeft') sendCmd('left');
            else if (k === 'd' || e.key === 'ArrowRight') sendCmd('right');
            else if (k === ' ') { sendCmd('stop'); e.preventDefault(); }
        });

        function toggleAi() {
            let goal = document.getElementById('aiGoal').value;
            fetch('/ai_toggle', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({goal: goal})
            });
        }
    </script>
</body>
</html>
"""

# Global buffer για τα logs
log_lines = ["Σύστημα έτοιμο..."]

def add_log(msg):
    global log_lines
    print(msg)
    log_lines.append(msg)
    if len(log_lines) > 30:
        log_lines.pop(0)

def ai_worker(goal):
    global ai_running
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        add_log("❌ Σφάλμα: Δεν βρέθηκε GEMINI_API_KEY")
        ai_running = False
        return

    client = genai.Client(api_key=api_key)
    system_prompt = "Είσαι το ρομπότ rover. Πρόχώρα 'forward' αν είναι ελεύθερα, στρίψε αν υπάρχει εμπόδιο. Θυμήσου τις προηγούμενες κινήσεις σου για να αποφεύγεις επαναλαμβανόμενα λάθη. Αν τελείωσες, done=True."

    try:
        # Δημιουργία Chat Session με μνήμη
        chat = client.chats.create(
            model=MODEL,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2,
                response_mime_type="application/json",
                response_schema=RoverDecision,
            )
        )
        add_log(f"🚀 Ξεκίνησε AI με στόχο: {goal}")
        
        while ai_running:
            frame = get_frame()
            if not frame:
                add_log("⚠️ Αποτυχία λήψης κάμερας, αναμονή...")
                time.sleep(2)
                continue

            contents = [
                types.Part.from_bytes(data=frame, mime_type="image/jpeg"),
                f"Στόχος: {goal}. Τρέχουσα εικόνα από την κάμερα."
            ]

            response = chat.send_message(contents)
            result = response.parsed

            if result:
                cmd = result.command
                add_log(f"🤖 {result.reason} [{cmd.upper()}]")

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

                if result.done:
                    add_log("✨ Ο στόχος ολοκληρώθηκε!")
                    break
            
            time.sleep(1) # Μικρή παύση ανάμεσα στους κύκλους

    except Exception as e:
        add_log(f"⚠️ Σφάλμα AI: {str(e)}")
    
    ai_running = False
    add_log("AI Mode σταμάτησε.")

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/video_feed")
def video_feed():
    frame = get_frame()
    if frame:
        return frame, 200, {'Content-Type': 'image/jpeg'}
    return "", 204

@app.route("/status")
def status():
    global ai_running, log_lines
    return jsonify({"running": ai_running, "logs": "<br>".join(log_lines)})

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

@app.route("/ai_toggle", methods=["POST"])
def ai_toggle():
    global ai_running, ai_thread
    if ai_running:
        ai_running = False
        add_log("Σήμα διακοπής AI...")
    else:
        data = request.json
        goal = data.get("goal", "περίπολη")
        ai_running = True
        ai_thread = threading.Thread(target=ai_worker, args=(goal,), daemon=True)
        ai_thread.start()
    return jsonify({"running": ai_running})

@app.route("/ai_stop", methods=["POST"])
def ai_stop():
    global ai_running
    ai_running = False
    car("stop")
    return jsonify({"status": "stopped"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
