# J.A.R.V.I.S

A personal desktop voice assistant built with Python and PyQt6, featuring an Iron Man-inspired HUD. It connects to any OpenAI-compatible LLM endpoint (Gemini, DeepSeek, Groq, Ollama), listens via offline Whisper speech-to-text, speaks using EdgeTTS, and can execute system tasks, web searches, and file operations.

---

## Features

- **Futuristic HUD Interface**: Animated PyQt6 UI with live CPU/RAM telemetry, audio visualizer waveform, and activity logs.
- **Provider-Agnostic LLM**: Works with Google Gemini, DeepSeek, Groq, OpenRouter, or local models running on Ollama and LM Studio.
- **Natural Voice & Hinglish Support**: Real-time STT using `faster-whisper` and natural speech output via Microsoft EdgeTTS (`hi-IN-MadhurNeural` / English).
- **System Control**: Launch applications, control system volume, adjust brightness, toggle Wi-Fi, and manage windows by voice or keyboard.
- **Instant Interruption**: Say "stop", "chup", or "ruk jao", click the UI interrupt button, or tap `ESC` to cut off speech instantly.
- **Persistent Memory**: Remembers your name, preferences, and important notes across sessions in `memory/long_term.json`.
- **Vision & Screen Awareness**: Multi-modal queries on active windows, uploaded images, and webcam snapshots.
- **Automated Startup Greeting**: Clean time-aware greeting on boot without delays or unwanted background searches.

---

## Requirements

- **OS**: Windows 10/11, macOS, or Linux
- **Python**: 3.10 to 3.12 (Python 3.11 recommended)
- **Audio**: Working microphone and speaker/headphones

---

## Installation

### 1. Clone the repository
```bash
git clone https://github.com/your-username/jarvis.git
cd jarvis
```

### 2. Set up a virtual environment
```bash
python -m venv .venv

# Windows (Command Prompt / PowerShell)
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

---

## Configuration

Settings are saved in `config/api_keys.json`. You can edit this file directly or configure it through the initial setup prompt.

Here is a standard configuration using Google Gemini:

```json
{
  "llm_provider": "openai",
  "llm_url": "https://generativelanguage.googleapis.com/v1beta/openai",
  "llm_model": "gemini-flash-lite-latest",
  "llm_api_key": "YOUR_GEMINI_API_KEY",
  "vision_model": "gemini-flash-lite-latest",
  "assistant_mode": "text",
  "tts_engine": "edgetts",
  "tts_voice": "hi-IN-MadhurNeural",
  "stt_language": "auto",
  "os_system": "windows"
}
```

### Config Options

| Key | Description |
|---|---|
| `llm_url` | OpenAI-compatible endpoint URL (e.g. Gemini, Groq, DeepSeek, or `http://localhost:11434/v1` for Ollama) |
| `llm_model` | Model identifier (e.g. `gemini-flash-lite-latest`, `deepseek-chat`, `llama-3.3-70b-versatile`) |
| `llm_api_key` | API key for your chosen provider (leave empty for local models) |
| `tts_engine` | Speech engine (`edgetts`, `kokoro`, or `elevenlabs`) |
| `tts_voice` | Voice profile (e.g. `hi-IN-MadhurNeural`, `en-US-GuyNeural`) |
| `stt_language` | Speech recognition language (`auto`, `hi`, `en`) |
| `assistant_mode` | `text` (standard STT -> LLM -> TTS pipeline) or `gemini` (Gemini Live WebSocket) |

---

## Usage

Start the assistant by running:

```bash
python main.py
```

On Windows, you can also run:
```bash
.\run.bat
```

### Interacting with JARVIS
- **Voice**: Speak into your microphone. Say commands like *"Google Chrome open karo"*, *"What is the weather today?"*, or *"Mera naam save kar lo"*.
- **Text**: Type directly into the **Command Input** box on the bottom-right panel and hit Enter.
- **Mute / Stop**:
  - Say *"stop"*, *"chup"*, *"ruk jao"*, or *"chup raho"*.
  - Press the `ESC` key on your keyboard.
  - Click the **INTERRUPT [ESC]** button in the HUD.

---

## Project Structure

```text
├── main.py                  # Application entry point and orchestrator
├── ui.py                    # PyQt6 futuristic HUD and visualization
├── run.bat                  # One-click Windows starter script
├── requirements.txt         # Core dependencies
├── config/
│   ├── api_keys.example.json# Configuration template
│   └── api_keys.json        # Active user credentials (gitignored)
├── core/
│   ├── text_pipeline.py     # Voice loop (VAD -> Whisper -> LLM -> TTS)
│   ├── stt.py               # faster-whisper speech recognition engine
│   ├── tts.py               # EdgeTTS / audio playback
│   ├── llm_client.py        # OpenAI-compatible streaming client
│   └── prompt.txt           # JARVIS personality and instructions
├── actions/                 # Tools and capability handlers
│   ├── computer_settings.py # Volume, brightness, Wi-Fi control
│   ├── open_app.py          # Application launcher
│   ├── web_search.py        # Web search integrations
│   ├── screen_processor.py  # Screen capture and vision analysis
│   └── memory_tool.py       # User memory management
└── memory/
    └── long_term.json       # Persistent user identity & memory
```

---

## Troubleshooting

- **No audio output**: Verify your Windows default playback device. Check if the **VOICE MUTED** button in the HUD was accidentally toggled.
- **Port already in use (`10048`)**: If you see a port binding error on 8000/8001, an older Python process is still running in the background. Close existing instances via Task Manager or terminal (`taskkill /F /IM python.exe`).
- **First run STT download**: The first time you launch, `faster-whisper` will download the offline `base` model (~140 MB). Subsequent runs start completely offline.

---

## License

Distributed under the Apache 2.0 License. See `LICENSE` for details.
