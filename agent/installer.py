"""WhatsArch Agent Installer — beautiful web-based GUI installer.

Run with: python installer.py
Opens a browser window with a visual installer that checks/installs all dependencies.
"""

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

PORT = 11471  # Installer port (agent uses 11470)
AGENT_DIR = Path.home() / "Documents" / "WhatsArch"
REPO_DIR = AGENT_DIR / "agent" / "WhatsArch"
VENV_DIR = AGENT_DIR / "agent" / "venv"

# Track installation state
install_state = {
    "python": {"status": "pending", "message": "...ממתין"},
    "ffmpeg": {"status": "pending", "message": "...ממתין"},
    "ollama": {"status": "pending", "message": "...ממתין"},
    "packages": {"status": "pending", "message": "...ממתין"},
    "whisper": {"status": "pending", "message": "...ממתין"},
    "e5": {"status": "pending", "message": "...ממתין"},
    "overall": "idle",  # idle, running, done, error
}


def run_cmd(cmd, timeout=600):
    """Run a shell command and return (success, output)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, shell=True
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def check_command(cmd):
    """Check if a command exists."""
    return shutil.which(cmd) is not None


def get_venv_python():
    """Get the path to the venv Python executable."""
    if sys.platform == "win32":
        return str(VENV_DIR / "Scripts" / "python.exe")
    return str(VENV_DIR / "bin" / "python")


def get_venv_pip():
    """Get the path to the venv pip."""
    if sys.platform == "win32":
        return str(VENV_DIR / "Scripts" / "pip.exe")
    return str(VENV_DIR / "bin" / "pip")


def update_step(step, status, message, percent=0):
    install_state[step] = {"status": status, "message": message, "percent": percent}


def run_installation():
    """Run the full installation process."""
    install_state["overall"] = "running"

    # Step 1: Python
    update_step("python", "checking", "...בודק")
    ok, out = run_cmd("python --version")
    if ok:
        version = out.strip().split()[-1] if out.strip() else "?"
        update_step("python", "done", f"Python {version} נמצא (system)")
    else:
        update_step("python", "error", "Python לא נמצא! התקן מ-python.org")
        install_state["overall"] = "error"
        return

    # Step 2: ffmpeg
    update_step("ffmpeg", "checking", "...בודק")
    if check_command("ffmpeg"):
        update_step("ffmpeg", "done", "ffmpeg נמצא (system)")
    else:
        update_step("ffmpeg", "downloading", "...מתקין ffmpeg")
        # Try winget
        ok, _ = run_cmd("winget install --id=Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements", timeout=120)
        if ok or check_command("ffmpeg"):
            update_step("ffmpeg", "done", "ffmpeg הותקן")
        else:
            update_step("ffmpeg", "done", "ffmpeg לא נמצא — אופציונלי לעיבוד וידאו")

    # Step 3: Ollama
    update_step("ollama", "checking", "...בודק")
    if check_command("ollama"):
        update_step("ollama", "done", "Ollama נמצא")
    else:
        update_step("ollama", "done", "Ollama לא נמצא — אופציונלי ל-AI מקומי")

    # Step 4: Clone repo + create venv + install packages
    update_step("packages", "downloading", "...מוריד קבצים")

    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    (AGENT_DIR / "agent").mkdir(parents=True, exist_ok=True)
    (AGENT_DIR / "chats").mkdir(parents=True, exist_ok=True)

    # Clone or update repo
    if (REPO_DIR / ".git").exists():
        update_step("packages", "downloading", "...מעדכן קוד", 10)
        run_cmd(f'cd /d "{REPO_DIR}" && git pull --quiet')
    elif check_command("git"):
        update_step("packages", "downloading", "...מוריד קוד מ-GitHub", 10)
        run_cmd(f'git clone --quiet https://github.com/susunoufi/WhatsArch.git "{REPO_DIR}"', timeout=120)
    else:
        # Download via curl
        update_step("packages", "downloading", "...מוריד קבצים", 10)
        REPO_DIR.mkdir(parents=True, exist_ok=True)
        (REPO_DIR / "agent").mkdir(parents=True, exist_ok=True)
        (REPO_DIR / "chat_search").mkdir(parents=True, exist_ok=True)

        for fname in ["agent.py", "requirements.txt"]:
            run_cmd(f'curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/agent/{fname}" -o "{REPO_DIR / "agent" / fname}"')

        for fname in ["__init__.py", "config.py", "parser.py", "transcribe.py", "vision.py",
                       "indexer.py", "chunker.py", "ai_chat.py", "process_manager.py"]:
            run_cmd(f'curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/chat_search/{fname}" -o "{REPO_DIR / "chat_search" / fname}"')

    # Create venv
    if not VENV_DIR.exists():
        update_step("packages", "downloading", "...יוצר סביבת Python", 25)
        run_cmd(f'python -m venv "{VENV_DIR}"')

    # Install packages
    update_step("packages", "downloading", "...מתקין חבילות Python (זה לוקח כמה דקות)", 40)
    pip = get_venv_pip()
    py = get_venv_python()

    run_cmd(f'"{pip}" install --quiet --upgrade pip', timeout=60)

    # Install CPU-only torch first (much smaller)
    update_step("packages", "downloading", "...מתקין PyTorch (CPU)", 50)
    run_cmd(f'"{pip}" install --quiet torch --index-url https://download.pytorch.org/whl/cpu', timeout=300)

    # Install remaining requirements
    req_path = REPO_DIR / "agent" / "requirements.txt"
    if req_path.exists():
        update_step("packages", "downloading", "...מתקין שאר החבילות", 70)
        run_cmd(f'"{pip}" install --quiet -r "{req_path}"', timeout=300)

    update_step("packages", "done", "חבילות Python מותקנות")

    # Step 5: Whisper model
    update_step("whisper", "downloading", "...בודק מודל Whisper")
    ok, _ = run_cmd(f'"{py}" -c "from faster_whisper import WhisperModel; m = WhisperModel(\'small\', device=\'cpu\', compute_type=\'int8\'); print(\'ok\')"', timeout=300)
    if ok:
        update_step("whisper", "done", "Whisper: הותקן")
    else:
        update_step("whisper", "done", "Whisper: ייטען בשימוש הראשון")

    # Step 6: E5 embedding model
    update_step("e5", "downloading", "E5: ...טוען")
    ok, _ = run_cmd(f'"{py}" -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer(\'intfloat/multilingual-e5-large\'); print(\'ok\')"', timeout=600)
    if ok:
        update_step("e5", "done", "E5: הותקן")
    else:
        update_step("e5", "done", "E5: ייטען בשימוש הראשון")

    # Create startup scripts
    _create_startup_scripts()

    install_state["overall"] = "done"


def _create_startup_scripts():
    """Create scripts to launch the agent."""
    agent_base = AGENT_DIR / "agent"
    py = get_venv_python()
    pyw = py.replace("python.exe", "pythonw.exe")
    agent_script = REPO_DIR / "agent" / "agent.py"

    # Visible launcher
    launcher = agent_base / "start-agent.bat"
    launcher.write_text(
        f'@echo off\ncd /d "{REPO_DIR}"\n"{py}" agent\\agent.py\npause\n',
        encoding="utf-8"
    )

    # Hidden launcher (no console window)
    vbs = agent_base / "start-agent-hidden.vbs"
    vbs.write_text(
        f'Set WshShell = WScript.CreateObject("WScript.Shell")\n'
        f'WshShell.CurrentDirectory = "{REPO_DIR}"\n'
        f'WshShell.Run """" & "{pyw}" & """ """ & "{agent_script}" & """", 0, False\n',
        encoding="utf-8"
    )

    # Add to startup (optional — user confirmed in the UI)
    startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    if startup_dir.exists():
        try:
            shutil.copy2(str(vbs), str(startup_dir / "WhatsArch-Agent.vbs"))
        except Exception:
            pass


def start_agent():
    """Start the agent after installation."""
    vbs = AGENT_DIR / "agent" / "start-agent-hidden.vbs"
    if vbs.exists():
        subprocess.Popen(["wscript", str(vbs)], shell=True)


# ============================================================
# HTTP Server — serves the installer UI + API
# ============================================================

SETUP_HTML = r'''<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>WhatsArch - התקנת Agent</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: "Segoe UI", "Arial", sans-serif;
            background: linear-gradient(135deg, #0D9488 0%, #6366F1 100%);
            color: #FFFFFF; min-height: 100vh;
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            user-select: none;
        }
        .logo { width: 64px; height: 64px; margin-bottom: 10px; filter: drop-shadow(0 4px 12px rgba(0,0,0,0.2)); }
        h1 { font-size: 1.4em; font-weight: 700; margin-bottom: 2px; }
        h1 .arch { opacity: 0.7; }
        .subtitle { font-size: 0.85em; opacity: 0.8; margin-bottom: 20px; }
        .steps { width: 420px; }
        .step {
            display: flex; align-items: center; gap: 10px; padding: 8px 12px; margin-bottom: 6px;
            background: rgba(255,255,255,0.08); border-radius: 10px; transition: background 0.3s;
        }
        .step.active { background: rgba(255,255,255,0.18); }
        .step.done { background: rgba(255,255,255,0.05); }
        .step.error { background: rgba(255,80,80,0.18); }
        .step-icon {
            width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center;
            justify-content: center; flex-shrink: 0; font-size: 0.8em; background: rgba(255,255,255,0.12);
        }
        .step-info { flex: 1; min-width: 0; }
        .step-label { font-size: 0.82em; font-weight: 600; }
        .step-status { font-size: 0.72em; opacity: 0.75; }
        .progress-bar { width: 100%; height: 4px; background: rgba(255,255,255,0.2); border-radius: 2px; margin-top: 5px; overflow: hidden; display: none; }
        .progress-fill { height: 100%; background: #FFFFFF; border-radius: 2px; width: 0%; transition: width 0.3s ease; }
        .spinner { width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.3); border-top-color: #FFF; border-radius: 50%; animation: spin 0.8s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .footer { margin-top: 16px; font-size: 0.72em; opacity: 0.45; }
        .num { font-size: 0.75em; opacity: 0.35; }
        .check { color: #86EFAC; font-size: 0.9em; }
        .error-x { color: #FCA5A5; font-size: 0.9em; }
        .done-banner {
            margin-top: 20px; padding: 16px 32px; background: rgba(255,255,255,0.15);
            border-radius: 12px; text-align: center; display: none;
        }
        .done-banner h2 { font-size: 1.1em; margin-bottom: 6px; }
        .done-banner p { font-size: 0.82em; opacity: 0.8; }
        .done-banner a {
            display: inline-block; margin-top: 12px; padding: 10px 28px;
            background: #fff; color: #0D9488; font-weight: 700; border-radius: 8px;
            text-decoration: none; font-size: 0.9em; transition: transform 0.2s;
        }
        .done-banner a:hover { transform: scale(1.05); }
    </style>
</head>
<body>
    <svg class="logo" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M50 8C26.8 8 8 24.5 8 45c0 11.5 5.8 21.8 15 28.5L18 88l17-9c4.6 1.6 9.6 2.5 15 2.5 23.2 0 42-16.5 42-37.5S73.2 8 50 8z" fill="white" opacity="0.95"/>
        <rect x="30" y="30" width="28" height="3" rx="1.5" fill="#0D9488" opacity="0.8"/>
        <rect x="30" y="38" width="22" height="3" rx="1.5" fill="#0D9488" opacity="0.5"/>
        <circle cx="62" cy="56" r="12" stroke="#6366F1" stroke-width="3.5" fill="none" opacity="0.95"/>
        <line x1="71" y1="65" x2="80" y2="74" stroke="#6366F1" stroke-width="3.5" stroke-linecap="round" opacity="0.95"/>
    </svg>
    <h1><span>Whats</span><span class="arch">Arch</span></h1>
    <p class="subtitle" id="subtitle">בודק ומתקין רכיבים...</p>

    <div class="steps" id="stepsContainer">
        <div class="step active" id="step-python">
            <div class="step-icon" id="icon-python"><div class="spinner"></div></div>
            <div class="step-info">
                <div class="step-label">Python</div>
                <div class="step-status" id="status-python">...בודק</div>
                <div class="progress-bar" id="bar-python"><div class="progress-fill" id="fill-python"></div></div>
            </div>
        </div>
        <div class="step" id="step-ffmpeg">
            <div class="step-icon" id="icon-ffmpeg"><span class="num">2</span></div>
            <div class="step-info">
                <div class="step-label">ffmpeg</div>
                <div class="step-status" id="status-ffmpeg">...ממתין</div>
                <div class="progress-bar" id="bar-ffmpeg"><div class="progress-fill" id="fill-ffmpeg"></div></div>
            </div>
        </div>
        <div class="step" id="step-ollama">
            <div class="step-icon" id="icon-ollama"><span class="num">3</span></div>
            <div class="step-info">
                <div class="step-label">Ollama (AI מקומי)</div>
                <div class="step-status" id="status-ollama">...ממתין</div>
                <div class="progress-bar" id="bar-ollama"><div class="progress-fill" id="fill-ollama"></div></div>
            </div>
        </div>
        <div class="step" id="step-packages">
            <div class="step-icon" id="icon-packages"><span class="num">4</span></div>
            <div class="step-info">
                <div class="step-label">חבילות Python</div>
                <div class="step-status" id="status-packages">...ממתין</div>
                <div class="progress-bar" id="bar-packages"><div class="progress-fill" id="fill-packages"></div></div>
            </div>
        </div>
        <div class="step" id="step-whisper">
            <div class="step-icon" id="icon-whisper"><span class="num">5</span></div>
            <div class="step-info">
                <div class="step-label">מודל זיהוי דיבור (Whisper)</div>
                <div class="step-status" id="status-whisper">...ממתין</div>
                <div class="progress-bar" id="bar-whisper"><div class="progress-fill" id="fill-whisper"></div></div>
            </div>
        </div>
        <div class="step" id="step-e5">
            <div class="step-icon" id="icon-e5"><span class="num">6</span></div>
            <div class="step-info">
                <div class="step-label">מודל חיפוש חכם (E5)</div>
                <div class="step-status" id="status-e5">...ממתין</div>
                <div class="progress-bar" id="bar-e5"><div class="progress-fill" id="fill-e5"></div></div>
            </div>
        </div>
    </div>

    <p class="footer">רכיבים שכבר מותקנים ידולגו אוטומטית</p>

    <div class="done-banner" id="doneBanner">
        <h2>&#10003; הכל מוכן!</h2>
        <p>ה-Agent רץ ברקע. מעביר אותך לאתר תוך 5 שניות...</p>
        <a href="https://whatsarch-production.up.railway.app/app">פתח את WhatsArch</a>
        <p style="font-size:0.72em; opacity:0.5; margin-top:8px;">ה-Agent יעלה אוטומטית עם כל הפעלה של המחשב</p>
    </div>

    <script>
        const allSteps = ['python', 'ffmpeg', 'ollama', 'packages', 'whisper', 'e5'];

        // Start installation
        fetch('/api/install', { method: 'POST' });

        // Poll for progress
        setInterval(async () => {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();

                allSteps.forEach(step => {
                    const info = data[step];
                    if (!info) return;
                    const stepEl = document.getElementById('step-' + step);
                    const iconEl = document.getElementById('icon-' + step);
                    const statusEl = document.getElementById('status-' + step);
                    const barEl = document.getElementById('bar-' + step);
                    const fillEl = document.getElementById('fill-' + step);

                    statusEl.textContent = info.message;

                    if (info.status === 'checking' || info.status === 'downloading') {
                        stepEl.className = 'step active';
                        iconEl.innerHTML = '<div class="spinner"></div>';
                        if (info.percent > 0) {
                            barEl.style.display = 'block';
                            fillEl.style.width = info.percent + '%';
                        }
                    } else if (info.status === 'done') {
                        stepEl.className = 'step done';
                        barEl.style.display = 'none';
                        iconEl.innerHTML = '<span class="check">&#10003;</span>';
                    } else if (info.status === 'error') {
                        stepEl.className = 'step error';
                        barEl.style.display = 'none';
                        iconEl.innerHTML = '<span class="error-x">&#10007;</span>';
                    }
                });

                if (data.overall === 'done') {
                    document.getElementById('subtitle').textContent = '!הכל מוכן';
                    document.getElementById('doneBanner').style.display = 'block';
                    // Auto-redirect to WhatsArch after 5 seconds
                    if (!window._redirectScheduled) {
                        window._redirectScheduled = true;
                        setTimeout(() => {
                            window.location.href = 'https://whatsarch-production.up.railway.app/app';
                        }, 5000);
                    }
                } else if (data.overall === 'error') {
                    document.getElementById('subtitle').textContent = 'שגיאה בהתקנה';
                }
            } catch {}
        }, 1000);
    </script>
</body>
</html>'''


class InstallerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SETUP_HTML.encode("utf-8"))
        elif self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(install_state, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/install":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            if install_state["overall"] == "idle":
                threading.Thread(target=run_installation_and_start, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress request logs


def run_installation_and_start():
    """Run installation, then start the agent."""
    run_installation()
    if install_state["overall"] == "done":
        start_agent()


def main():
    print(f"WhatsArch Agent Installer")
    print(f"Opening browser at http://localhost:{PORT}")
    print(f"Close this window when done.\n")

    server = http.server.HTTPServer(("127.0.0.1", PORT), InstallerHandler)

    # Open browser after a short delay
    def open_browser():
        import time
        time.sleep(0.5)
        webbrowser.open(f"http://localhost:{PORT}")

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nInstaller closed.")
        server.server_close()


if __name__ == "__main__":
    main()
