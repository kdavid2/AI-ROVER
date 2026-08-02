import os
import time
import threading
import requests
from flask import Flask, render_template_string, request, jsonify
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

app = Flask(__name__)

# --- ΕΚΔΟΣΗ ΕΦΑΡΜΟΓΗΣ ---
APP_VERSION = "v1.4-full-tools"

CONTROL_BASE = os.environ.get("ROVER_URL", "http://192.168.1.100")
CAPTURE_URL = f"{CONTROL_BASE}/capture"          
MODEL = "gemini-robotics-er-2-preview"

CAR_VALS = {"forward": 1, "backward": 2, "left": 3, "right": 4, "stop": 5}

current_speed = 6
_session = requests.Session()

ai_thread = None
ai_running = False
ai_status_log = "Σύστημα έτοιμο..."

# Ιστορικό κινήσεων για την αποφυγή λούπας
action_history = []

class RoverDecision(BaseModel):
    command: str = Field(description="Η εντολή: forward, backward, left, right, stop, tiltup, tiltdown")
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

def get_pulse_durations():
    """Πιο επιθετική εκθετική κλίμακα για εξαιρετικά μικρές και γρήγορες μικροκινήσεις"""
    global current_speed
    spd = max(0, min(12, current_speed))
    
    if spd == 0:
        factor = 0.08 
    else:
        factor = (spd / 6.0) ** 2.0
        
    move_time = 0.20 * factor
    turn_time = 0.12 * factor
    return move_time, turn_time

def execute_pulse(cmd: str):
    move_time, turn_time = get_pulse_durations()
    
    if cmd in ("forward", "backward"):
        car(cmd)
        time.sleep(move_time)
        car("stop")
    elif cmd in ("left", "right"):
        car(cmd)
        time.sleep(turn_time)
        car("stop")
    else:
        car("stop")

def execute_rover_action(cmd: str):
    """Εκτελεί όλες τις διαθέσιμες ενέργειες (κίνηση + tilt κάμερας)"""
    if cmd in ("forward", "backward", "left", "right", "stop"):
        execute_pulse(cmd)
    elif cmd == "tiltup":
        control("ltrim", 1)
        add_log("📷 Κλίση Κάμερας: ΠΑΝΩ (tiltup)")
        time.sleep(0.3)
    elif cmd == "tiltdown":
        control("rtrim", 1)
        add_log("📷 Κλίση Κάμερας: ΚΑΤΩ (tiltdown)")
        time.sleep(0.3)

def get_frame() -> bytes:
    try:
        r = _session.get(CAPTURE_URL, timeout=4)
        r.raise_for_status()
        if r.content.startswith(b"\xff\xd8"):
            return r.content
    except Exception:
        pass
    return b""

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="el">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rover Cloud Control - Full Tools Mode</title>
    <style>
        body { background-color: #121212; color: #fff; font-family: Arial, sans-serif; text-align: center; margin: 0; padding: 10px; }
        h2 { margin: 10px 0; font-size: 1.2rem; }
        .version-badge { background: #734CA7; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; margin-left: 5px; }
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
        .input-group { display: flex; gap: 5px; margin-top: 5px; }
        input[type="text"] { flex: 1; padding: 8px; border-radius: 4px; border: none; }
        .mic-btn { padding: 8px 12px; background: #e65100; font-size: 0.9rem; font-weight: bold; border-radius: 4px; border: none; color: white; cursor: pointer; }
        .log-box { background: #000; color: #0ff; font-family: monospace; font-size: 0.8rem; height: 110px; overflow-y: auto; padding: 8px; border-radius: 4px; margin-top: 10px; text-align: left; }
    </style>
</head>
<body>
    <h2>🤖 Rover Cloud Control <span class="version-badge" id="appVersion">...</span></h2>

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
            <button class="btn-up" onclick="sendCmd('forward')">Forward</button>
            <button class="btn-left" onclick="sendCmd('left')">Left</button>
            <button class="btn-stop" onclick="sendCmd('stop')">STOP</button>
            <button class="btn-right" onclick="sendCmd('right')">Right</button>
            <button class="btn-down" onclick="sendCmd('backward')">Backward</button>
            <button class="btn-tiltup" onclick="sendControlVar('ltrim', 1)">Tilt Up</button>
            <button class="btn-tiltdown" onclick="sendControlVar('rtrim', 1)">Tilt Down</button>
        </div>

        <div class="ai-section">
            <label><b>AI Στόχος (Gemini Live Voice/Text):</b></label>
            <div class="input-group">
                <input type="text" id="aiGoal" value="κάνε περιπολία">
                <button class="mic-btn" id="micBtn" onclick="toggleLiveVoice()">🎤 Live: OFF</button>
            </div>
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
        let isLiveMicActive = false;
        let recognition = null;

        function switchTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.add('hide'));
            document.querySelector('.' + tabName + '-tab').classList.remove('hide');
        }

        fetch('/version').then(res => res.json()).then(data => {
            let badge = document.getElementById('appVersion');
            if(badge) badge.innerText = data.version;
        });

        setInterval(() => {
            let img = document.getElementById('camStream');
            if(img) img.src = '/video_feed?' + new Date().getTime();
        }, 2000);

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

        // --- LIVE MIC ENGINE (Continuous Speech Recognition) ---
        function toggleLiveVoice() {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {
                alert("Ο browser σου δεν υποστηρίζει φωνητική αναγνώριση. Χρησιμοποίησε Chrome στο κινητό.");
                return;
            }

            const micBtn = document.getElementById('micBtn');

            if (isLiveMicActive) {
                isLiveMicActive = false;
                if (recognition) recognition.stop();
                micBtn.style.background = "#e65100";
                micBtn.innerText = "🎤 Live: OFF";
                return;
            }

            isLiveMicActive = true;
            micBtn.style.background = "#b71c1c";
            micBtn.innerText = "🔴 Live: ON";

            recognition = new SpeechRecognition();
            recognition.lang = 'el-GR';
            recognition.continuous = true;
            recognition.interimResults = false;

            recognition.onresult = function(event) {
                const lastIndex = event.results.length - 1;
                const transcript = event.results[lastIndex][0].transcript.trim();
                
                if (transcript.length > 0) {
                    document.getElementById('aiGoal').value = transcript;
                    
                    fetch('/ai_toggle', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({goal: transcript})
                    });
                }
            };

            recognition.onend = function() {
                if (isLiveMicActive) {
                    try { recognition.start(); } catch(e) {}
                }
            };

            recognition.onerror = function(event) {
                console.log("Mic error: ", event.error);
            };

            recognition.start();
        }

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
            });
        }

        let toggleStates = {tracking: 0, avoid: 0, follow: 0};
        function toggleParam(paramName, btnElem) {
            toggleStates[paramName] = toggleStates[paramName] === 0 ? 1 : 0;
            let val = toggleStates[paramName];
            btnElem.innerText = paramName.toUpperCase() + ": " + (val === 1 ? "ON" : "OFF");
            sendControlVar(paramName, val);
        }

        window.addEventListener('keydown', (e) => {
            if (e.repeat) return;
            let k = e.key.toLowerCase();
            if (k === 'w' || e.key === 'ArrowUp') sendCmd('forward');
            else if (k === 's' || e.key === 'ArrowDown') sendCmd('backward');
            else if (k === 'a' || e.key === 'ArrowLeft') sendCmd('left');
            else if (k === 'd' || e.key === 'ArrowRight') sendCmd('right');
            else if (e.key === ' ') { sendCmd('stop'); e.preventDefault(); }
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

log_lines = [f"Σύστημα έτοιμο ({APP_VERSION})..."]

def add_log(msg):
    global log_lines
    print(msg)
    log_lines.append(msg)
    if len(log_lines) > 40:
        log_lines.pop(0)

def check_and_break_loop(cmd):
    """Ελέγχει αν υπάρχει ατέρμονη λούπα και επεμβαίνει"""
    global action_history
    action_history.append(cmd)
    if len(action_history) > 6:
        action_history.pop(0)
        
    if len(action_history) >= 4:
        last_4 = action_history[-4:]
        if last_4 == ['left', 'right', 'left', 'right'] or last_4 == ['right', 'left', 'right', 'left']:
            add_log("🔄 Εντοπίστηκε λούπα αριστερά/δεξιά! Εκτελείται αυτόματη διαφυγή.")
            action_history.clear()
            return 'backward'
            
        if last_4 == ['forward', 'backward', 'forward', 'backward'] or last_4 == ['backward', 'forward', 'backward', 'forward']:
            add_log("🔄 Εντοπίστηκε λούπα μπρος/πίσω! Εκτελείται αυτόματη διαφυγή.")
            action_history.clear()
            return 'left'
            
    return cmd

def ai_worker(goal):
    global ai_running
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        add_log("❌ Σφάλμα: Δεν βρέθηκε GEMINI_API_KEY")
        ai_running = False
        return

    client = genai.Client(api_key=api_key)
    
    # --- ΕΝΙΣΧΥΜΕΝΟ SYSTEM PROMPT ΜΕ ΠΛΗΡΗ ΧΡΗΣΗ ΕΡΓΑΛΕΙΩΝ CAM TILT ---
    system_prompt = (
        "Είσαι το αυτόνομο ρομπότ rover. Η ΠΡΩΤΗ ΣΟΥ ΠΡΟΤΕΡΑΙΟΤΗΤΑ ΕΙΝΑΙ Η ΑΣΦΑΛΕΙΑ ΚΑΙ Η ΣΩΣΤΗ ΕΠΙΣΚΟΠΗΣΗ.\n\n"
        "ΔΙΑΘΕΣΙΜΕΣ ΕΝΤΟΛΕΣ (command):\n"
        "- 'forward': Προχώρα μπροστά (ΜΟΝΟ αν βλέπεις καθαρά ελεύθερο πάτωμα/διάδρομο).\n"
        "- 'backward': Πίσω (αν είσαι πολύ κοντά σε εμπόδιο ή κόλλησες).\n"
        "- 'left' / 'right': Στροφή.\n"
        "- 'stop': Σταμάτημα.\n"
        "- 'tiltdown': Γείρε την κάμερα ΚΑΤΩ για να ελέγξεις το πάτωμα και χαμηλά εμπόδια.\n"
        "- 'tiltup': Γείρε την κάμερα ΠΑΝΩ για να δεις τον υπόλοιπο χώρο.\n\n"
        "ΚΑΝΟΝΕΣ ΠΛΟΗΓΗΣΗΣ:\n"
        "1. Αν δεν βλέπεις καθαρά το πάτωμα μπροστά σου (ή αν αμφιβάλεις), χρησιμοποίησε 'tiltdown' για να ελέγξεις πριν δώσεις 'forward'.\n"
        "2. ΑΠΑΓΟΡΕΥΕΤΑΙ ΑΥΣΤΗΡΑ το 'forward' αν βλέπεις εμπόδιο, τοίχο, έπιπλο, ή αν η εικόνα είναι μπλοκαρισμένη/σκοτεινή.\n"
        "3. Αν ο δρόμος είναι μπλοκαρισμένος, χρησιμοποίησε 'tiltdown' / 'tiltup' για να αξιολογήσεις ή 'left'/'right'/'backward' για να ελευθερωθείς.\n"
        "4. Δώσε μια σύντομη αιτιολογία στα ελληνικά στο 'reason'. Αν ο στόχος ολοκληρώθηκε, βάλε done=True."
    )

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
        add_log(f"🚀 Ξεκίνησε AI Mode ({APP_VERSION}) με στόχο: {goal}")
        
        while ai_running:
            frame = get_frame()
            if not frame:
                add_log("⚠️ Αποτυχία λήψης κάμερας, αναμονή...")
                time.sleep(2)
                continue

            contents = [
                types.Part.from_bytes(data=frame, mime_type="image/jpeg"),
                f"Στόχος: {goal}. Αξιολόγησε την εικόνα, έλεγξε το πάτωμα με tiltdown αν χρειάζεται και αποφάσισε."
            ]

            response = chat.send_message(contents)
            result = response.parsed

            if result:
                cmd = result.command
                cmd = check_and_break_loop(cmd)
                
                add_log(f"🤖 {result.reason} [{cmd.upper()}]")
                execute_rover_action(cmd)

                if result.done:
                    add_log("✨ Ο στόχος ολοκληρώθηκε!")
                    break
            
            time.sleep(0.8)

    except Exception as e:
        add_log(f"⚠️ Σφάλμα AI: {str(e)}")
    
    ai_running = False
    add_log("AI Mode σταμάτησε.")

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/version")
def version():
    return jsonify({"version": APP_VERSION})

@app.route("/video_feed")
def video_feed():
    frame = get_frame()
    if frame:
        return frame, 200, {'Content-Type': 'image/jpeg'}
    return "", 204

@app.route("/status")
def status():
    global ai_running, log_lines
    return jsonify({
        "running": ai_running, 
        "logs": "<br>".join(log_lines)
    })

@app.route("/cmd", methods=["POST"])
def manual_cmd():
    data = request.json
    cmd = data.get("command")
    execute_rover_action(cmd)
    return jsonify({"status": "ok"})

@app.route("/control_var", methods=["POST"])
def control_var():
    global current_speed
    data = request.json
    var_name = data.get("var")
    val = data.get("val")
    
    if var_name == "speed":
        try:
            current_speed = int(val)
        except ValueError:
            pass

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
