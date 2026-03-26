"""WhatsArch Local Agent — runs on user's machine for local compute.

The web UI (whatsarch.com) communicates with this agent for:
- Uploading large files directly from the local filesystem
- Running Whisper transcription locally
- Running Ollama (local LLM) for RAG/Vision
- Checking local hardware capabilities

The agent runs on http://localhost:11470
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS  # needed for cross-origin from whatsarch.com

app = Flask(__name__)
CORS(app, origins=["https://whatsarch-production.up.railway.app", "http://localhost:*"])

# Agent version
VERSION = "1.0.0"

# Data directory
DATA_DIR = Path.home() / "Documents" / "WhatsArch"
CHATS_DIR = DATA_DIR / "chats"
CHATS_DIR.mkdir(parents=True, exist_ok=True)


@app.route("/status")
def status():
    """Health check — web app pings this to detect if agent is running."""
    return jsonify({
        "status": "running",
        "version": VERSION,
        "platform": platform.system(),
        "data_dir": str(DATA_DIR),
        "chats_dir": str(CHATS_DIR),
        "chats": [d.name for d in CHATS_DIR.iterdir() if d.is_dir()],
    })


@app.route("/hardware")
def hardware():
    """Return local hardware info."""
    info = {
        "cpu": platform.processor() or "Unknown",
        "ram_gb": 0,
        "gpu": "Unknown",
        "gpu_dedicated": False,
        "ollama_installed": shutil.which("ollama") is not None,
        "ffmpeg_installed": shutil.which("ffmpeg") is not None,
        "whisper_available": False,
    }

    # RAM
    try:
        import psutil
        info["ram_gb"] = round(psutil.virtual_memory().total / (1024**3), 1)
    except ImportError:
        pass

    # GPU (Windows)
    try:
        result = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "Name,AdapterRAM"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip() and "Name" not in l]
        if lines:
            info["gpu"] = lines[0].split("  ")[0].strip()
            info["gpu_dedicated"] = "nvidia" in info["gpu"].lower() or "radeon" in info["gpu"].lower()
    except Exception:
        pass

    # Whisper
    try:
        from faster_whisper import WhisperModel
        info["whisper_available"] = True
    except ImportError:
        pass

    return jsonify(info)


@app.route("/ollama/status")
def ollama_status():
    """Check if Ollama is running and what models are available."""
    try:
        import urllib.request
        res = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        data = json.loads(res.read())
        models = [m["name"] for m in data.get("models", [])]
        return jsonify({"running": True, "models": models})
    except Exception:
        return jsonify({"running": False, "models": []})


@app.route("/transcribe", methods=["POST"])
def transcribe():
    """Transcribe an audio file using local Whisper."""
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

    file = request.files["file"]

    # Save to temp
    import tempfile
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, file.filename)
    file.save(temp_path)

    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("small", device="auto", compute_type="auto")
        segments, info = model.transcribe(temp_path, beam_size=5, vad_filter=True)
        text = " ".join(seg.text.strip() for seg in segments)
        language = info.language if info and hasattr(info, "language") else ""
        return jsonify({"text": text, "language": language})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.route("/chat", methods=["POST"])
def chat_ollama():
    """Chat with local Ollama model."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing body"}), 400

    model = data.get("model", "qwen2.5:7b")
    messages = data.get("messages", [])

    try:
        import urllib.request
        req_data = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=req_data,
            headers={"Content-Type": "application/json"},
        )
        res = urllib.request.urlopen(req, timeout=300)
        result = json.loads(res.read())
        return jsonify({"response": result.get("message", {}).get("content", "")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/upload/local", methods=["POST"])
def upload_local():
    """Upload a chat from a local file path (no network transfer needed).

    The web UI tells the agent: "process this local file" and the agent
    reads it directly from disk.
    """
    data = request.get_json()
    if not data or not data.get("path"):
        return jsonify({"error": "Missing path"}), 400

    source_path = Path(data["path"])
    if not source_path.exists():
        return jsonify({"error": f"File not found: {source_path}"}), 404

    import zipfile

    if source_path.suffix.lower() == ".zip":
        # Extract ZIP
        chat_name = source_path.stem
        dest = CHATS_DIR / chat_name
        if dest.exists():
            chat_name = chat_name + "_" + str(int(source_path.stat().st_mtime))
            dest = CHATS_DIR / chat_name

        with zipfile.ZipFile(source_path, "r") as zf:
            zf.extractall(dest)

        # Check if extracted to a subdirectory
        entries = list(dest.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            # Move contents up
            sub = entries[0]
            for item in sub.iterdir():
                shutil.move(str(item), str(dest / item.name))
            sub.rmdir()

        return jsonify({"status": "ok", "chat_name": chat_name, "path": str(dest)})

    elif source_path.is_dir():
        # Copy directory
        chat_name = source_path.name
        dest = CHATS_DIR / chat_name
        if dest.exists():
            chat_name = chat_name + "_new"
            dest = CHATS_DIR / chat_name
        shutil.copytree(str(source_path), str(dest))
        return jsonify({"status": "ok", "chat_name": chat_name, "path": str(dest)})

    else:
        return jsonify({"error": "Path must be a ZIP file or directory"}), 400


if __name__ == "__main__":
    print(f"WhatsArch Local Agent v{VERSION}")
    print(f"Data: {DATA_DIR}")
    print(f"Listening on http://localhost:11470")
    app.run(host="127.0.0.1", port=11470, debug=False)
