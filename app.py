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

def control(var: str, val) -> None:
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

# HTML Template με πλήρη υποστήριξη όλων των ρυθμίσεων και λειτουργιών του HTML
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="el">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rover Cloud Control - Full Controls</title>
    <style>
        body { background-color: #121212; color: #fff; font-family: Arial, sans-serif; text-align: center; margin: 0; padding: 10px; }
        h2 { margin: 10px 0; font-size: 1.2rem; }
        .cam-container { margin: 10px auto; max-width: 400px; }
        img { width: 100%; border-radius: 8px; border: 2px solid #444; }
        
        .tabs { list-style: none; padding: 0; margin: 10px auto; width: 90%; display: flex; justify-content: center; }
        .tabs li { flex: 1; border: 1px solid #444; padding: 10px; background: #1e1e1e; cursor: pointer; font-weight: bold; }
        .tabs li:hover { background: #333; }
        
        .hide { display: none !important; }
        
        .controls { 
            display: grid; 
            grid-template-columns: repeat(3, 90px); 
            grid-template-rows: repeat(3, 65px);
            gap: 8px; 
            justify-content: center; 
            margin: 15px 0; 
        }
        button { background-color: #734CA7; color: white; border: none; font-size: 1.1rem; border-radius: 8px; cursor: pointer; }
        button:active { background-color: #555; }
        
        .btn-up { grid-column: 2; grid-row: 1; }
        .btn-left { grid-column: 1; grid-row: 2; }
        .btn-stop { grid-column: 2; grid-row: 2; background-color: #b71c1c; font-weight: bold; }
        .btn-right { grid-column: 3; grid-row: 2; }
        .btn-down { grid-column: 2; grid-row: 3; }
        .btn-tiltup { grid-column: 1; grid-row: 3; background-color: #2e7d32; font-size: 0.9rem; }
        .btn-tiltdown { grid-column: 3; grid-row: 3; background-color: #2e7d32; font-size: 0.9rem; }

        .setting-row { margin: 15px auto; max-width: 400px; text-align: left; background: #1e1e1e; padding: 10px; border-radius: 8px; }
        .setting-row label { display: block; margin-bottom: 5px; font-size: 0.9rem; color: #ccc; }
        .setting-row input[type=range] { width: 100%; }
        .setting-row button { width: 100%; padding: 10px; background: #734CA7; }

        .ai-section { margin: 15px auto; max-width: 400px; text-align: left; background: #1e1e1e; padding: 10px; border-radius: 8px; }
        input[type="text"] { width: calc(100% - 16px); padding: 8px; margin-top: 5px; border-radius: 4px; border: none; }
        .log-box { background: #000; color: #0ff; font-family: monospace; font-size: 0.8rem; height: 110px; overflow-y: auto; padding: 8px; border-radius: 4px; margin-top: 10px; text-align: left; }
    </style>
</head>
<body>
    <h2>🤖 Rover Cloud Control</h2>

    <ul class="tabs">
        <li onclick="switchTab('control')" id="tab-control-btn">Control</li>
        <li onclick="switchTab('settings')" id="tab-settings-btn">Settings</li>
        <li onclick="switchTab('messages')" id="tab-messages-btn">Messages</li>
    </ul>

    <!-- TAB 1: CONTROL -->
    <div class="tab control-tab">
        <div class="cam-container">
            <img id="camStream" src="/video_feed" alt="Live Camera">
        </div>

        <div class="controls">
            <button class="btn-up" onmousedown="sendCmd('forward')" onmouseup="sendCmd('stop')" ontouchstart="sendCmd('forward')" ontouchend="sendCmd('stop')">Forward</button>
            <button class="btn-left" onmousedown="sendCmd('left')" onmouseup="sendCmd('stop')" ontouchstart="sendCmd('left')" ontouchend="sendCmd('stop')">Left</button>
            <button class="btn-stop" onclick="sendCmd('stop')">STOP</button>
            <button class="btn-right" onmousedown="sendCmd('right')" onmouseup="sendCmd('stop')" ontouchstart="sendCmd('right')" ontouchend="sendCmd('stop')">Right</button>
            <button class="btn-down" onmousedown="sendCmd('backward')" onmouseup="sendCmd('stop')" ontouchstart="sendCmd('backward')" ontouchend="sendCmd('stop')">Backward</button>
            <button class="btn-tiltup" onclick="sendControlVar('ltrim', 1)">Tilt Up</button>
            <button class="btn-tiltdown" onclick="sendControlVar('rtrim', 1)">Tilt Down</button>
        </div>

        <div class="ai-section">
            <label><b>AI Στόχος (Gemini):</b></label>
            <input type="text" id="aiGoal" value="κάνε περιπολία">
            <button onclick="toggleAi()" id="aiBtn" style="margin-top:8px; width:100%; padding: 10px; border:none; border-radius:6px; color:#fff; background:#2e7d32; cursor:pointer; font-weight:bold;">Εκκίνηση AI Mode</button>
            <div class="log-box" id="logBox">Σύστημα έτοιμο...</div>
        </div>
    </div>

    <!-- TAB 2: SETTINGS -->
    <div class="tab settings-tab hide">
        <div class="setting-row">
            <label>Reboot</label>
            <button onclick="sendControlVar('reboot', 1)">Reboot Rover</button>
        </div>
        <div class="setting-row">
            <label>Restart Wifi</label>
            <button onclick="sendControlVar('restartwifi', 1)">Restart Wifi</button>
        </div>
        <div class="setting-row">
            <label>Line Tracking</label>
            <button onclick="toggleParam('tracking', this)">Line Tracking: OFF</button>
        </div>
        <div class="setting-row">
            <label>Obstacle Avoidance</label>
            <button onclick="toggleParam('avoid', this)">Obstacle Avoidance: OFF</button>
        </div>
        <div class="setting-row">
            <label>Object Following</label>
            <button onclick="toggleParam('follow', this)">Object Following: OFF</button>
        </div>
        <div class="setting-row">
            <label>Speed (0 - 12)</label>
            <input type="range" id="speedRange" min="0" max="12" value="6" onchange="sendControlVar('speed', this.value)">
        </div>
        <div class="setting-row">
            <label>Gainceiling (0 - 6)</label>
            <input type="range" id="gainceilingRange" min="0" max="6" value="0" onchange="sendControlVar('gainceiling', this.value)">
        </div>
        <div class="setting-row">
            <label>Light / Flash (0 - 255)</label>
            <input type="range" id="flashRange" min="0" max="255" value="0" onchange="sendControlVar('flash', this.value)">
        </div>
        <div class="setting-row">
            <label>Quality (10 - 63)</label>
            <input type="range" id="qualityRange" min="10" max="63" value="10" onchange="sendControlVar('quality', this.value)">
        </div>
        <div class="setting-row">
            <label>Resolution / Framesize (0 - 6)</label>
            <input type="range" id="framesizeRange" min="0" max="6" value="5" onchange="sendControlVar('framesize', this.value)">
        </div>
    </div>

    <!-- TAB 3: MESSAGES -->
    <div class="tab messages-tab hide">
        <div class="setting-row" style="text-align: center;">
            <h3>Καταγραφή Εντολών & Responses</h3>
            <div class="log-box" id="msgBox" style="height: 250px;">Αναμονή εντολών...</div>
        </div>
    </div>

    <script>
        function switchTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.add('hide'));
            document.querySelector('.' + tabName + '-tab').classList.remove('hide');
        }

        // Ανανέωση κάμερας κάθε 2 δευτερόλεπτα
        setInterval(() => {
            let img = document.getElementById('camStream');
            if(img) img.src = '/video_feed?' + new Date().getTime();
        }, 2000);

        // Polling για τα logs από τον server κάθε 1 δευτερόλεπτο
        setInterval(() => {
            fetch('/status')
            .then(res => res.json())
            .then(data => {
                let box = document.getElementById('logBox');
                let msgBox = document.getElementById('msgBox');
                if(box) {
                    box.innerHTML = data.logs.replace(/\\n/g, "<br>");
                    box.scrollTop = box.scrollHeight;
                }
                if(msgBox) {
                    msgBox.innerHTML = data.logs.replace(/\\n/g, "<br>");
                    msgBox.scrollTop = msgBox.scrollHeight;
                }
                
                let btn = document.getElementById('aiBtn');
                if(btn) {
                    if (data.running) {
                        btn.innerText = "Διακοπή AI Mode";
                        btn.style.background = "#b71c1c";
                    } else {
                        btn.innerText = "Εκκίνηση AI Mode";
                        btn.style.background = "#2e7d32";
                    }
                }
            });
        }, 1000);

        function sendCmd(cmd) {
            fetch('/cmd', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: cmd})
            });
        }

        function sendControlVar(varName, val) {
            fetch('/control_var', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({var: varName, val: val})
            }).then(res => res.json()).then(data => {
                console.log("Control set:", data);
            });
        }

        let toggleStates = {tracking: 0, avoid: 0, follow: 0};
        function toggleParam(paramName, btnElem) {
            toggleStates[paramName] = toggleStates[paramName] === 0 ? 1 : 0;
            let val = toggleStates[paramName];
            btnElem.innerText = paramName.toUpperCase() + ": " + (val === 1 ? "ON" : "OFF");
            sendControlVar(paramName, val);
        }

        // Υποστήριξη WASD και Βελάκων από το πληκτρολόγιο
        window.addEventListener('keydown', (e) => {
            let k = e.key.toLowerCase();
            if (k === 'w' || e.key === 'ArrowUp') sendCmd('forward');
            else if (k === 's' || e.key === 'ArrowDown') sendCmd('backward');
            else if (k === 'a' || e.key === 'ArrowLeft') sendCmd('left');
            else if (k === 'd' || e.key === 'ArrowRight') sendCmd('right');
            else if (e.key === ' ') { sendCmd('stop'); e.preventDefault(); }
        });

        window.addEventListener('keyup', (e) => {
            let k = e.key.toLowerCase();
            if (['w','s','a','d','arrowup','arrowdown','arrowleft','arrowright'].includes(k) || ['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)) {
                sendCmd('stop');
            }
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
    if len(log_lines) > 40:
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
            
            time.sleep(1)

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
    elif cmd in ("left", "right"):
        car(cmd)
    else:
        car("stop")
    return jsonify({"status": "ok"})

@app.route("/control_var", methods=["POST"])
def control_var():
    data = request.json
    var_name = data.get("var")
    val = data.get("val")
    control(var_name, val)
    add_log(f"⚙️ Ρύθμιση αποστάλθηκε: var={var_name}&val={val}")
    return jsonify({"status": "success", "var": var_name, "val": val})

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
