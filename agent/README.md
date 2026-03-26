# WhatsArch Local Agent

A lightweight background service that runs on your computer, enabling the WhatsArch web app to use your local hardware for processing.

## What it does
- Transcribes voice messages using Whisper (locally, free)
- Runs Ollama for AI chat (locally, free)
- Handles large chat file uploads directly from disk
- Reports hardware capabilities to the web app

## Install
1. Make sure Python 3.10+ is installed
2. Run `install.bat`
3. The agent starts automatically on login

## Manual start
```
python agent.py
```

## API
The agent listens on `http://localhost:11470`

| Endpoint | Description |
|----------|-------------|
| GET /status | Health check |
| GET /hardware | Local hardware info |
| GET /ollama/status | Ollama availability + models |
| POST /transcribe | Transcribe audio file |
| POST /chat | Chat with local Ollama |
| POST /upload/local | Process local file/directory |
