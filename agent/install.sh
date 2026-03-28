#!/usr/bin/env bash
# WhatsArch Local Agent - Mac/Linux Installer
# Usage: curl -sL https://whatsarch.app/install.sh | bash
set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║    WhatsArch Local Agent Installer       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="$HOME/Documents/WhatsArch"
VENV_DIR="$INSTALL_DIR/agent/venv"
AGENT_DIR="$INSTALL_DIR/agent"

# Check Python 3.10+
check_python() {
    echo -e "${CYAN}[1/6] Checking Python...${NC}"
    if command -v python3 &>/dev/null; then
        PY=python3
    elif command -v python &>/dev/null; then
        PY=python
    else
        echo -e "${RED}Python not found. Please install Python 3.10+${NC}"
        echo "  macOS: brew install python3"
        echo "  Ubuntu: sudo apt install python3 python3-venv python3-pip"
        exit 1
    fi

    VERSION=$($PY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    MAJOR=$($PY -c 'import sys; print(sys.version_info.major)')
    MINOR=$($PY -c 'import sys; print(sys.version_info.minor)')

    if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]); then
        echo -e "${RED}Python $VERSION found, but 3.10+ required${NC}"
        exit 1
    fi
    echo -e "${GREEN}  Python $VERSION ✓${NC}"
}

# Check/install ffmpeg
check_ffmpeg() {
    echo -e "${CYAN}[2/6] Checking ffmpeg...${NC}"
    if command -v ffmpeg &>/dev/null; then
        echo -e "${GREEN}  ffmpeg ✓${NC}"
    else
        echo -e "${YELLOW}  ffmpeg not found. Installing...${NC}"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            if command -v brew &>/dev/null; then
                brew install ffmpeg
            else
                echo -e "${RED}  Please install Homebrew first: https://brew.sh${NC}"
                echo "  Then run: brew install ffmpeg"
            fi
        elif command -v apt-get &>/dev/null; then
            sudo apt-get update && sudo apt-get install -y ffmpeg
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y ffmpeg
        elif command -v pacman &>/dev/null; then
            sudo pacman -S ffmpeg
        else
            echo -e "${RED}  Could not auto-install ffmpeg. Please install manually.${NC}"
        fi
    fi
}

# Check Ollama (optional)
check_ollama() {
    echo -e "${CYAN}[3/6] Checking Ollama (optional, for local AI)...${NC}"
    if command -v ollama &>/dev/null; then
        echo -e "${GREEN}  Ollama ✓${NC}"
    else
        echo -e "${YELLOW}  Ollama not found. Optional for local AI.${NC}"
        echo "  Install from: https://ollama.com/download"
    fi
}

# Setup directories and clone/download agent
setup_agent() {
    echo -e "${CYAN}[4/6] Setting up WhatsArch agent...${NC}"
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$AGENT_DIR"

    # Try git clone first
    if command -v git &>/dev/null; then
        if [ -d "$AGENT_DIR/WhatsArch/.git" ]; then
            echo "  Updating existing repo..."
            cd "$AGENT_DIR/WhatsArch" && git pull --quiet
        else
            echo "  Cloning repository..."
            git clone --quiet --depth 1 https://github.com/susunoufi/WhatsArch.git "$AGENT_DIR/WhatsArch" 2>/dev/null || true
        fi
    else
        echo "  Downloading agent files..."
        curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/agent/agent.py" -o "$AGENT_DIR/agent.py"
        curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/agent/requirements.txt" -o "$AGENT_DIR/requirements.txt"
        # Also download chat_search module
        mkdir -p "$AGENT_DIR/chat_search"
        for f in __init__.py config.py parser.py transcribe.py vision.py indexer.py chunker.py ai_chat.py process_manager.py usage_tracker.py storage.py; do
            curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/chat_search/$f" -o "$AGENT_DIR/chat_search/$f" 2>/dev/null || true
        done
    fi
    echo -e "${GREEN}  Agent files ready ✓${NC}"
}

# Create virtual environment and install dependencies
install_deps() {
    echo -e "${CYAN}[5/6] Installing Python dependencies...${NC}"

    # Create venv
    if [ ! -d "$VENV_DIR" ]; then
        $PY -m venv "$VENV_DIR"
    fi

    # Activate and install
    source "$VENV_DIR/bin/activate"

    # Install PyTorch (CPU only)
    pip install --quiet --upgrade pip
    pip install --quiet torch --index-url https://download.pytorch.org/whl/cpu 2>/dev/null || \
        pip install --quiet torch

    # Install requirements
    REQ_FILE="$AGENT_DIR/requirements.txt"
    if [ -d "$AGENT_DIR/WhatsArch" ]; then
        REQ_FILE="$AGENT_DIR/WhatsArch/agent/requirements.txt"
    fi
    pip install --quiet -r "$REQ_FILE"

    # Pre-download embedding model
    echo "  Downloading embedding model (first time only)..."
    python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-large')" 2>/dev/null || true

    deactivate
    echo -e "${GREEN}  Dependencies installed ✓${NC}"
}

# Create launch script
create_launcher() {
    echo -e "${CYAN}[6/6] Creating launcher...${NC}"

    # Determine agent.py location
    if [ -d "$AGENT_DIR/WhatsArch" ]; then
        AGENT_PY="$AGENT_DIR/WhatsArch/agent/agent.py"
    else
        AGENT_PY="$AGENT_DIR/agent.py"
    fi

    # Create launch script
    cat > "$INSTALL_DIR/start-agent.sh" << LAUNCH
#!/usr/bin/env bash
source "$VENV_DIR/bin/activate"
cd "$INSTALL_DIR"
python "$AGENT_PY"
LAUNCH
    chmod +x "$INSTALL_DIR/start-agent.sh"

    # macOS: create LaunchAgent for auto-start
    if [[ "$OSTYPE" == "darwin"* ]]; then
        PLIST_DIR="$HOME/Library/LaunchAgents"
        mkdir -p "$PLIST_DIR"
        cat > "$PLIST_DIR/com.whatsarch.agent.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.whatsarch.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/start-agent.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
PLIST
        echo -e "  ${GREEN}Auto-start configured (LaunchAgent) ✓${NC}"
    fi

    # Linux: create systemd user service
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        SYSTEMD_DIR="$HOME/.config/systemd/user"
        mkdir -p "$SYSTEMD_DIR"
        cat > "$SYSTEMD_DIR/whatsarch-agent.service" << SERVICE
[Unit]
Description=WhatsArch Local Agent
After=network.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/start-agent.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
SERVICE
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user enable whatsarch-agent.service 2>/dev/null || true
        echo -e "  ${GREEN}Auto-start configured (systemd) ✓${NC}"
    fi

    echo -e "${GREEN}  Launcher created ✓${NC}"
}

# Run all steps
check_python
check_ffmpeg
check_ollama
setup_agent
install_deps
create_launcher

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Installation complete!                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Start agent:  ${CYAN}$INSTALL_DIR/start-agent.sh${NC}"
echo -e "  Agent URL:    ${CYAN}http://localhost:11470${NC}"
echo ""
echo -e "  Open WhatsArch in your browser and the agent"
echo -e "  will be detected automatically."
echo ""
