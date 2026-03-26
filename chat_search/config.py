"""
Configuration management for WhatsArch.

Manages user preferences for AI provider/model selection (vision, video, RAG),
API keys, hardware detection, and Ollama performance estimation.

Settings are stored in settings.json in the project root directory.
"""

import json
import os
import platform
import re
import subprocess


DEFAULT_SETTINGS = {
    "vision_provider": "gemini",
    "vision_model": "gemini-2.0-flash",
    "video_provider": "gemini",
    "video_model": "gemini-2.0-flash",
    "rag_provider": "gemini",
    "rag_model": "gemini-2.0-flash",
    "ollama_base_url": "http://localhost:11434",
    "ollama_vision_model": "llama3.2-vision",
    "ollama_rag_model": "qwen2.5:14b",
    "sender_aliases": {},  # {"chat_name": {"original_name": "display_name", ...}}
    "user_plans": {},      # {"user@email.com": {mode, cloud_preset, local_vision, local_rag}}
}

# Per-user plan structure
DEFAULT_USER_PLAN = {
    "mode": "cloud",           # "cloud" | "local" | "both"
    "cloud_preset": "budget",  # "budget" | "balanced" | "premium"
    "local_vision": "proxy",   # "proxy" | "own_key" | "ollama"
    "local_rag": "proxy",      # "proxy" | "own_key" | "ollama"
}

VALID_MODES = ("cloud", "local", "both")
VALID_CLOUD_PRESETS = ("budget", "balanced", "premium")
VALID_LOCAL_OPTIONS = ("proxy", "own_key", "ollama")


def normalize_user_plan(plan_value) -> dict:
    """Normalize a user plan value. Handles legacy string format and new dict format."""
    if plan_value is None:
        return dict(DEFAULT_USER_PLAN)
    if isinstance(plan_value, str):
        # Legacy: "budget" -> full dict
        return {
            "mode": "local" if plan_value == "local" else "cloud",
            "cloud_preset": plan_value if plan_value in VALID_CLOUD_PRESETS else "budget",
            "local_vision": "ollama" if plan_value == "local" else "proxy",
            "local_rag": "ollama" if plan_value == "local" else "proxy",
        }
    if isinstance(plan_value, dict):
        result = dict(DEFAULT_USER_PLAN)
        result.update(plan_value)
        return result
    return dict(DEFAULT_USER_PLAN)

PRESETS = {
    "budget": {
        "name_he": "חסכוני",
        "name_en": "Budget",
        "icon": "💰",
        "vision_provider": "gemini", "vision_model": "gemini-2.0-flash",
        "video_provider": "gemini", "video_model": "gemini-2.0-flash",
        "rag_provider": "gemini", "rag_model": "gemini-2.0-flash",
        "description_he": "הכי זול — Gemini Flash לכל דבר. איכות טובה, מחיר מינימלי.",
        "description_en": "Cheapest — Gemini Flash for everything. Good quality, minimal cost.",
    },
    "balanced": {
        "name_he": "מאוזן",
        "name_en": "Balanced",
        "icon": "⚡",
        "vision_provider": "gemini", "vision_model": "gemini-2.0-flash",
        "video_provider": "gemini", "video_model": "gemini-2.0-flash",
        "rag_provider": "openai", "rag_model": "gpt-4o-mini",
        "description_he": "איזון מצוין — Gemini Flash לתמונות, GPT-4o-mini לשאלות. מהיר ואיכותי.",
        "description_en": "Great balance — Gemini Flash for vision, GPT-4o-mini for Q&A. Fast and quality.",
    },
    "premium": {
        "name_he": "פרימיום",
        "name_en": "Premium",
        "icon": "👑",
        "vision_provider": "anthropic", "vision_model": "claude-sonnet-4-20250514",
        "video_provider": "anthropic", "video_model": "claude-sonnet-4-20250514",
        "rag_provider": "anthropic", "rag_model": "claude-opus-4-6",
        "description_he": "הכי חכם — Claude Sonnet לתמונות, Opus לשאלות. הכי מדויק בעברית.",
        "description_en": "Smartest — Claude Sonnet for vision, Opus for Q&A. Best Hebrew accuracy.",
    },
    "local": {
        "name_he": "לוקאלי",
        "name_en": "Local",
        "icon": "🏠",
        "vision_provider": "ollama", "vision_model": "llama3.2-vision",
        "video_provider": "ollama", "video_model": "llama3.2-vision",
        "rag_provider": "ollama", "rag_model": "qwen2.5:14b",
        "description_he": "חינם לגמרי — הכל רץ על המחשב. דורש Ollama + GPU מומלץ.",
        "description_en": "Completely free — runs locally. Requires Ollama + GPU recommended.",
    },
}


def estimate_preset_cost(preset_key: str, image_count: int, video_count: int, question_count: int = 100) -> dict:
    """Estimate cost for a preset given media counts."""
    preset = PRESETS.get(preset_key)
    if not preset:
        return {"total": 0, "vision": 0, "video": 0, "rag": 0}

    # Find cost rates from PROVIDER_MODELS
    vision_cost = 0.0
    video_cost = 0.0
    rag_cost = 0.0

    for m in PROVIDER_MODELS.get("vision", []):
        if m["provider"] == preset["vision_provider"] and m["model"] == preset["vision_model"]:
            vision_cost = m.get("cost_per_image", 0) * image_count
            break

    for m in PROVIDER_MODELS.get("video", []):
        if m["provider"] == preset["video_provider"] and m["model"] == preset["video_model"]:
            video_cost = m.get("cost_per_minute", 0) * video_count
            break

    for m in PROVIDER_MODELS.get("rag", []):
        if m["provider"] == preset["rag_provider"] and m["model"] == preset["rag_model"]:
            rag_cost = m.get("cost_per_query", 0) * question_count
            break

    total = vision_cost + video_cost + rag_cost
    return {
        "total": round(total, 4),
        "vision": round(vision_cost, 4),
        "video": round(video_cost, 4),
        "rag": round(rag_cost, 4),
    }


def recommend_preset(image_count: int, video_count: int, hardware: dict = None) -> str:
    """Recommend a preset based on chat size and hardware."""
    total_media = image_count + video_count

    # If very little media, premium is cheap enough
    if total_media < 50:
        return "premium"

    # If hardware has dedicated GPU, local is viable
    if hardware and hardware.get("gpu_dedicated") and hardware.get("ram_gb", 0) >= 16:
        return "local"

    # Default: balanced for medium, budget for large
    if total_media > 500:
        return "budget"

    return "balanced"


PROVIDER_MODELS = {
    "vision": [
        {"provider": "gemini", "model": "gemini-2.0-flash", "display": "Gemini 2.0 Flash", "cost_per_image": 0.00004, "hebrew_quality": 4, "speed": "fast", "badge": "recommended"},
        {"provider": "openai", "model": "gpt-4.1-nano", "display": "GPT-4.1 nano", "cost_per_image": 0.0001, "hebrew_quality": 3, "speed": "fast"},
        {"provider": "openai", "model": "gpt-4o-mini", "display": "GPT-4o-mini", "cost_per_image": 0.0003, "hebrew_quality": 4, "speed": "fast"},
        {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "display": "Claude Haiku 4.5", "cost_per_image": 0.002, "hebrew_quality": 4, "speed": "fast"},
        {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "display": "Claude Sonnet 4", "cost_per_image": 0.008, "hebrew_quality": 5, "speed": "fast", "badge": "best"},
        {"provider": "ollama", "model": "llama3.2-vision", "display": "llama3.2-vision", "cost_per_image": 0, "hebrew_quality": 3, "speed": "slow", "badge": "free"},
    ],
    "video": [
        {"provider": "gemini", "model": "gemini-2.0-flash", "display": "Gemini 2.0 Flash", "cost_per_minute": 0.002, "hebrew_quality": 4, "speed": "fast", "badge": "recommended"},
        {"provider": "openai", "model": "gpt-4.1-nano", "display": "GPT-4.1 nano", "cost_per_minute": 0.002, "hebrew_quality": 3, "speed": "fast"},
        {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "display": "Claude Haiku 4.5", "cost_per_minute": 0.03, "hebrew_quality": 4, "speed": "fast"},
        {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "display": "Claude Sonnet 4", "cost_per_minute": 0.11, "hebrew_quality": 5, "speed": "fast", "badge": "best"},
        {"provider": "ollama", "model": "llama3.2-vision", "display": "llama3.2-vision", "cost_per_minute": 0, "hebrew_quality": 3, "speed": "slow", "badge": "free"},
    ],
    "rag": [
        {"provider": "gemini", "model": "gemini-2.0-flash", "display": "Gemini 2.0 Flash", "cost_per_query": 0.0005, "hebrew_quality": 4, "speed": "fast"},
        {"provider": "openai", "model": "gpt-4.1-nano", "display": "GPT-4.1 nano", "cost_per_query": 0.0005, "hebrew_quality": 3, "speed": "fast"},
        {"provider": "openai", "model": "gpt-4o-mini", "display": "GPT-4o-mini", "cost_per_query": 0.0008, "hebrew_quality": 4, "speed": "fast"},
        {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "display": "Claude Haiku 4.5", "cost_per_query": 0.004, "hebrew_quality": 4, "speed": "fast"},
        {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "display": "Claude Sonnet 4", "cost_per_query": 0.017, "hebrew_quality": 5, "speed": "fast"},
        {"provider": "anthropic", "model": "claude-opus-4-6", "display": "Claude Opus 4", "cost_per_query": 0.083, "hebrew_quality": 5, "speed": "fast", "badge": "best"},
        {"provider": "ollama", "model": "qwen2.5:14b", "display": "qwen2.5:14b", "cost_per_query": 0, "hebrew_quality": 4, "speed": "slow", "badge": "recommended"},
        {"provider": "ollama", "model": "qwen2.5:7b", "display": "qwen2.5:7b", "cost_per_query": 0, "hebrew_quality": 3, "speed": "slow", "badge": "free"},
    ],
}


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

def get_settings_path(project_root: str) -> str:
    """Return path to settings.json in project root."""
    return os.path.join(project_root, "settings.json")


def load_settings(project_root: str) -> dict:
    """Load settings from file, merging with defaults for any missing keys."""
    settings = dict(DEFAULT_SETTINGS)
    path = get_settings_path(project_root)
    try:
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            settings.update(stored)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return settings


def save_settings(project_root: str, settings: dict) -> None:
    """Save settings to file."""
    path = get_settings_path(project_root)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def get_setting(project_root: str, key: str, default=None):
    """Get a single setting value."""
    settings = load_settings(project_root)
    return settings.get(key, default)


def update_settings(project_root: str, updates: dict) -> dict:
    """Update specific settings and return the full settings dict."""
    settings = load_settings(project_root)
    settings.update(updates)
    save_settings(project_root, settings)
    return settings


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------

_ENV_KEY_MAP = {
    "anthropic_key": "ANTHROPIC_API_KEY",
    "openai_key": "OPENAI_API_KEY",
    "gemini_key": "GEMINI_API_KEY",
}


def get_api_keys() -> dict:
    """Get API key status from environment variables.

    Returns dict with keys: anthropic_key, openai_key, gemini_key.
    Each value is the actual key string or empty string.
    """
    return {
        name: os.environ.get(env_var, "")
        for name, env_var in _ENV_KEY_MAP.items()
    }


def save_api_keys(project_root: str, keys: dict) -> None:
    """Save API keys to .env file and update os.environ.

    ``keys`` dict can have: anthropic_key, openai_key, gemini_key.
    Only updates keys that are present in the dict.
    Reads existing .env, updates/adds/removes keys, writes back.
    """
    env_path = os.path.join(project_root, ".env")

    # Read existing lines
    existing_lines = []
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            existing_lines = f.readlines()
    except (FileNotFoundError, OSError):
        pass

    # Build map of env-var-name -> new value for keys we're updating
    updates = {}
    for friendly_name, value in keys.items():
        env_var = _ENV_KEY_MAP.get(friendly_name)
        if env_var is None:
            continue
        updates[env_var] = value.strip() if value else ""

    # Process existing lines: update or remove matching keys
    updated_vars = set()
    new_lines = []
    for line in existing_lines:
        stripped = line.strip()
        # Keep empty/comment lines as-is
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        # Check if this line sets one of our target vars
        matched_var = None
        for env_var in updates:
            if stripped.startswith(env_var + "=") or stripped.startswith(env_var + " ="):
                matched_var = env_var
                break
        if matched_var is not None:
            updated_vars.add(matched_var)
            new_value = updates[matched_var]
            if new_value:
                new_lines.append(f"{matched_var}={new_value}\n")
            # else: remove the line (don't append)
        else:
            new_lines.append(line)

    # Add any new keys that weren't already in the file
    for env_var, value in updates.items():
        if env_var not in updated_vars and value:
            new_lines.append(f"{env_var}={value}\n")

    # Write back
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Update os.environ so changes take effect immediately
    for env_var, value in updates.items():
        if value:
            os.environ[env_var] = value
        else:
            os.environ.pop(env_var, None)


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

def _run_cmd(cmd, timeout=10):
    """Run a shell command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _get_ram_info():
    """Return (total_ram_gb, available_ram_gb). Falls back to 0.0 on failure."""
    # Try psutil first
    try:
        import psutil
        mem = psutil.virtual_memory()
        return round(mem.total / (1024 ** 3), 1), round(mem.available / (1024 ** 3), 1)
    except ImportError:
        pass

    # Fallback: Windows wmic
    if platform.system() == "Windows":
        total_out = _run_cmd("wmic ComputerSystem get TotalPhysicalMemory /value")
        avail_out = _run_cmd(
            'powershell -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"'
        )
        total_gb = 0.0
        available_gb = 0.0
        match = re.search(r"TotalPhysicalMemory=(\d+)", total_out)
        if match:
            total_gb = round(int(match.group(1)) / (1024 ** 3), 1)
        if avail_out.strip().isdigit():
            # FreePhysicalMemory is in KB
            available_gb = round(int(avail_out.strip()) / (1024 ** 2), 1)
        return total_gb, available_gb

    # Linux fallback
    try:
        with open("/proc/meminfo") as f:
            lines = f.read()
        total_match = re.search(r"MemTotal:\s+(\d+)", lines)
        avail_match = re.search(r"MemAvailable:\s+(\d+)", lines)
        total = round(int(total_match.group(1)) / (1024 ** 2), 1) if total_match else 0.0
        avail = round(int(avail_match.group(1)) / (1024 ** 2), 1) if avail_match else 0.0
        return total, avail
    except Exception:
        return 0.0, 0.0


def _get_cpu_info():
    """Return (cpu_name, core_count)."""
    cores = os.cpu_count() or 0

    if platform.system() == "Windows":
        name = _run_cmd("wmic cpu get Name /value")
        match = re.search(r"Name=(.+)", name)
        if match:
            return match.group(1).strip(), cores
        # Powershell fallback
        name = _run_cmd(
            'powershell -NoProfile -Command "(Get-CimInstance Win32_Processor).Name"'
        )
        if name:
            return name.strip(), cores
    else:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip(), cores
        except Exception:
            pass

    return platform.processor() or "Unknown", cores


def _get_gpu_info_windows():
    """Detect GPUs on Windows. Returns list of {name, dedicated, vram_gb}."""
    gpus = []

    # Try wmic first
    wmic_out = _run_cmd(
        "wmic path win32_VideoController get Name,AdapterRAM,AdapterDACType /value"
    )
    if wmic_out:
        # Split into blocks per GPU (double newline separated)
        blocks = re.split(r"\n\s*\n", wmic_out)
        current = {}
        for block in blocks:
            for line in block.strip().splitlines():
                line = line.strip()
                if line.startswith("Name="):
                    current["name"] = line.split("=", 1)[1].strip()
                elif line.startswith("AdapterRAM="):
                    try:
                        ram_bytes = int(line.split("=", 1)[1].strip())
                        current["vram_gb"] = round(ram_bytes / (1024 ** 3), 1)
                    except (ValueError, IndexError):
                        current["vram_gb"] = 0
            if "name" in current:
                name = current["name"]
                vram = current.get("vram_gb", 0)
                dedicated = any(
                    kw in name.lower()
                    for kw in ("nvidia", "geforce", "rtx", "gtx", "radeon", "rx ")
                )
                gpus.append({
                    "name": name,
                    "dedicated": dedicated,
                    "vram_gb": vram,
                })
                current = {}

    # If wmic gave nothing, try powershell
    if not gpus:
        ps_out = _run_cmd(
            'powershell -NoProfile -Command "'
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name,AdapterRAM | "
            'ForEach-Object { $_.Name + \'|\' + $_.AdapterRAM }"'
        )
        if ps_out:
            for line in ps_out.splitlines():
                parts = line.strip().split("|")
                if len(parts) >= 1 and parts[0]:
                    name = parts[0].strip()
                    vram = 0.0
                    if len(parts) >= 2 and parts[1].strip().isdigit():
                        vram = round(int(parts[1].strip()) / (1024 ** 3), 1)
                    dedicated = any(
                        kw in name.lower()
                        for kw in ("nvidia", "geforce", "rtx", "gtx", "radeon", "rx ")
                    )
                    gpus.append({
                        "name": name,
                        "dedicated": dedicated,
                        "vram_gb": vram,
                    })

    # For NVIDIA GPUs, try nvidia-smi for accurate VRAM (wmic caps at 4GB)
    for gpu in gpus:
        if "nvidia" in gpu["name"].lower() or "geforce" in gpu["name"].lower():
            smi_out = _run_cmd(
                "nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits"
            )
            if smi_out:
                try:
                    vram_mb = int(smi_out.strip().splitlines()[0].strip())
                    gpu["vram_gb"] = round(vram_mb / 1024, 1)
                except (ValueError, IndexError):
                    pass

    return gpus


def _get_device_name():
    """Get computer/device name."""
    if platform.system() == "Windows":
        name = _run_cmd("hostname")
        if name:
            return name
    return platform.node() or "Unknown"


def _get_os_version():
    """Get a human-readable OS version string."""
    system = platform.system()
    if system == "Windows":
        ver = platform.version()
        try:
            build = int(ver.split(".")[-1]) if ver else 0
        except ValueError:
            build = 0
        win_ver = "11" if build >= 22000 else "10"
        return f"Windows {win_ver}"
    elif system == "Darwin":
        mac_ver = platform.mac_ver()[0]
        return f"macOS {mac_ver}" if mac_ver else "macOS"
    else:
        return f"{system} {platform.release()}"


def detect_hardware():
    """Detect system hardware for Ollama performance estimation.

    Returns dict with cpu, cpu_cores, ram_gb, ram_available_gb,
    gpu, gpu_dedicated, gpu_vram_gb, os, device_name.
    """
    cpu_name, cpu_cores = _get_cpu_info()
    ram_gb, ram_available_gb = _get_ram_info()

    gpu_name = "Unknown"
    gpu_dedicated = False
    gpu_vram_gb = 0.0

    if platform.system() == "Windows":
        gpus = _get_gpu_info_windows()
        if gpus:
            # Prefer dedicated GPU
            dedicated_gpus = [g for g in gpus if g["dedicated"]]
            best = dedicated_gpus[0] if dedicated_gpus else gpus[0]
            gpu_name = best["name"]
            gpu_dedicated = best["dedicated"]
            gpu_vram_gb = best["vram_gb"]
    else:
        # Linux: try lspci
        lspci_out = _run_cmd("lspci | grep -i vga")
        if lspci_out:
            gpu_name = lspci_out.split(":")[-1].strip() if ":" in lspci_out else lspci_out
            gpu_dedicated = any(
                kw in gpu_name.lower()
                for kw in ("nvidia", "geforce", "rtx", "gtx", "radeon", "rx ")
            )
        # NVIDIA VRAM via nvidia-smi
        if "nvidia" in gpu_name.lower() or "geforce" in gpu_name.lower():
            smi_out = _run_cmd(
                "nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits"
            )
            if smi_out:
                try:
                    gpu_vram_gb = round(
                        int(smi_out.strip().splitlines()[0].strip()) / 1024, 1
                    )
                    gpu_dedicated = True
                except (ValueError, IndexError):
                    pass

    return {
        "cpu": cpu_name,
        "cpu_cores": cpu_cores,
        "ram_gb": ram_gb,
        "ram_available_gb": ram_available_gb,
        "gpu": gpu_name,
        "gpu_dedicated": gpu_dedicated,
        "gpu_vram_gb": gpu_vram_gb,
        "os": _get_os_version(),
        "device_name": _get_device_name(),
    }


# ---------------------------------------------------------------------------
# Ollama performance estimation
# ---------------------------------------------------------------------------

def estimate_ollama_performance(hardware):
    """Estimate Ollama performance based on hardware.

    Returns dict with feasibility, recommended models, speed estimates,
    RAM usage, overall rating, and Hebrew recommendation text.
    """
    ram = hardware.get("ram_gb", 0)
    ram_avail = hardware.get("ram_available_gb", 0)
    gpu_dedicated = hardware.get("gpu_dedicated", False)
    gpu_vram = hardware.get("gpu_vram_gb", 0)

    # Determine overall tier
    if gpu_dedicated and gpu_vram >= 6:
        tier = "excellent"
    elif ram >= 32:
        tier = "good"
    elif ram >= 16:
        tier = "medium"
    else:
        tier = "low"

    # RAG model recommendation
    if tier in ("excellent", "good"):
        rag_model = "qwen2.5:14b"
        rag_ram = 10
        rag_speed = "10-30 שניות"
        rag_feasible = True
    elif tier == "medium":
        if ram_avail >= 12:
            rag_model = "qwen2.5:14b"
            rag_ram = 10
            rag_speed = "30-90 שניות"
        else:
            rag_model = "qwen2.5:7b"
            rag_ram = 5
            rag_speed = "15-45 שניות"
        rag_feasible = True
    else:
        if ram >= 8:
            rag_model = "qwen2.5:7b"
            rag_ram = 5
            rag_speed = "1-3 דקות"
            rag_feasible = True
        else:
            rag_model = "qwen2.5:7b"
            rag_ram = 5
            rag_speed = "לא מומלץ"
            rag_feasible = False

    # Vision model recommendation
    vision_ram = 8
    if tier == "excellent":
        vision_speed = "10-20 שניות"
        vision_feasible = True
    elif tier == "good":
        vision_speed = "20-40 שניות"
        vision_feasible = True
    elif tier == "medium":
        vision_speed = "30-60 שניות"
        vision_feasible = ram_avail >= 10
    else:
        vision_speed = "1-3 דקות"
        vision_feasible = ram >= 10

    # GPU acceleration adjustments
    gpu_note = ""
    if gpu_dedicated and gpu_vram >= 8:
        gpu_note = " (עם האצת GPU)"
        if tier == "excellent":
            rag_speed = "5-15 שניות"
            vision_speed = "5-10 שניות"

    # Recommendation text in Hebrew
    recommendations = {
        "excellent": (
            "החומרה שלך מצוינת להרצת מודלים מקומיים" + gpu_note + ". "
            "מומלץ להשתמש ב-qwen2.5:14b לצ'אט AI "
            "ו-llama3.2-vision לתיאור תמונות. הביצועים יהיו טובים מאוד."
        ),
        "good": (
            "החומרה שלך מתאימה היטב למודלים מקומיים. "
            "ניתן להריץ מודלים גדולים (14b) בנוחות. "
            "מומלץ לסגור תוכנות כבדות בזמן השימוש ב-Ollama."
        ),
        "medium": (
            "החומרה שלך יכולה להריץ מודלים מקומיים, אבל עם מגבלות. "
            "מומלץ להשתמש במודלים קטנים (7b) לביצועים טובים יותר, "
            "או לנסות מודלים גדולים (14b) כשיש מספיק זיכרון פנוי. "
            "כדאי לסגור תוכנות אחרות בזמן השימוש."
        ),
        "low": (
            "החומרה שלך מוגבלת להרצת מודלים מקומיים. "
            "מומלץ להשתמש בשירותי API (Anthropic, OpenAI, Gemini) "
            "לתוצאות מהירות ואיכותיות יותר. "
            "אם בכל זאת רוצים לנסות, השתמשו במודלים קטנים בלבד (7b)."
        ),
    }

    return {
        "rag_feasible": rag_feasible,
        "rag_model_recommended": rag_model,
        "rag_speed": rag_speed,
        "rag_ram_usage_gb": rag_ram,
        "vision_feasible": vision_feasible,
        "vision_speed_per_image": vision_speed,
        "vision_ram_usage_gb": vision_ram,
        "overall_rating": tier,
        "recommendation_text": recommendations[tier],
    }
