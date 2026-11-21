# J.A.R.V.I.S  

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-brightgreen.svg)](https://www.python.org/)
[![UI: PyQt6](https://img.shields.io/badge/GUI-PyQt6-darkcyan.svg)](https://riverbankcomputing.com/software/pyqt/)
[![Speech: faster--whisper](https://img.shields.io/badge/STT-faster--whisper-orange.svg)](https://github.com/SYSTRAN/faster-whisper)

A futuristic desktop AI assistant inspired by Tony Stark's J.A.R.V.I.S. Built with Python and PyQt6, featuring an animated Iron Man Arc Reactor HUD, real-time audio visualizer, offline speech recognition with native Hinglish understanding, automated desktop & messaging controls, and dynamic multi-provider LLM support.

---

## 🌟 Key Features

- **Iron Man Sci-Fi HUD Interface**:
  - Animated Arc Reactor core with responsive audio visualizers.
  - Real-time hardware telemetry (CPU %, RAM %, GPU %, Network I/O).
  - 12-hour AM/PM dynamic digital clock and real-time date display.
  - Live activity logging and command response console.

- **Multi-Provider LLM Brain**:
  - Seamless support for **Google Gemini**, **Groq** (`openai/gpt-oss-120b`), **DeepSeek**, **OpenRouter**, **Empero (Free)**, and local backends (**Ollama**, **LM Studio**, **LocalAI**).
  - Provider failover and automatic cooldown routing.

- **In-App API Key & Settings Manager**:
  - Click **`[ ⚙  API KEY / SETTINGS ]`** in the HUD or press **`Ctrl + K`** / **`Ctrl + ,`** to open the settings panel.
  - One-click presets for Gemini, Groq, DeepSeek, OpenRouter, Free, and Custom.
  - Key visibility toggle (`👁`) and instant live reload without restarting.
  - Automatic quota detection: prompts to update keys on 429 / resource exhaustion.

- **Speech & Natural Hinglish Understanding**:
  - **Speech-to-Text (STT)**: Offline `faster-whisper` engine tuned with custom VAD sensitivity and Indian conversational/Hinglish vocabulary.
  - **Text-to-Speech (TTS)**: Microsoft EdgeTTS (`hi-IN-MadhurNeural` / English) for natural, fluid spoken voice feedback.
  - **Instant Interruption**: Interrupt speech at any time by saying *"stop"*, *"chup"*, or *"ruk jao"*, pressing `ESC`, or clicking the HUD button.

- **App & Messaging Automation**:
  - **Automated Messaging**: Send WhatsApp, Telegram, Instagram DM, Discord, or Signal messages directly by voice.
  - **Application Launcher**: Launch any desktop program or website (e.g. WhatsApp, Chrome, VS Code, Spotify, Notepad, Calculator).
  - **System Settings**: Control volume, screen brightness, Wi-Fi, window states, and media playback.

- **Android Phone Voice Controller**:
  - **Screen Unlock & Wake**: Wake phone, swipe up lock screen, and automatically enter PIN by voice (*"Jarvis, mera phone open karo"*, *"Jarvis, phone unlock karo"*).
  - **Mobile App Launcher**: Open phone apps instantly (*"Jarvis, phone par WhatsApp open karo"*, *"phone par YouTube chalao"*, *"phone ka camera open karo"*).
  - **Phone Telemetry**: Real-time battery status by voice (*"Jarvis, phone ki battery kitni hai?"*).
  - **Wireless & USB ADB Automation**: Zero-configuration bundled Google ADB tools with automatic Wi-Fi and phone hotspot gateway auto-discovery on port 5555.

- **Mobile Remote Dashboard**:
  - Built-in secure FastAPI server with AES-256 session encryption.
  - Instant pairing via QR Code or one-time passkey for controlling Jarvis from your smartphone.

- **Silent Background Launcher & Desktop Shortcut**:
  - Launch silently without background terminal windows using `run_silent.vbs`.
  - 1-click **"Create Desktop Shortcut"** directly from the HUD.

---

## 🛠️ Requirements

- **Operating System**: Windows 10/11, macOS, or Linux (Windows recommended for full OS automation)
- **Python Version**: Python 3.10, 3.11, or 3.12 (Python 3.11 recommended)
- **Hardware**: Working microphone and speakers / headphones

---

## 🚀 Quick Start & Installation

### 1. Clone the Repository
```bash
git clone https://github.com/Gourav-jii/Jarvis-Personal-Assistant.git
cd Jarvis-Personal-Assistant
```

### 2. Set Up a Virtual Environment
```bash
python -m venv .venv

# Windows (PowerShell / Command Prompt)
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

---

## ⚙️ Configuration

Jarvis configurations are saved in `config/api_keys.json` (gitignored for your security).

You can easily configure everything using the **In-App Settings UI** on boot, or edit `config/api_keys.json` directly:

```json
{
  "llm_provider": "openai",
  "llm_url": "https://api.groq.com/openai/v1",
  "llm_model": "openai/gpt-oss-120b",
  "llm_api_key": "YOUR_GROQ_API_KEY",
  "vision_model": "gemini-flash-lite-latest",
  "assistant_mode": "text",
  "tts_engine": "edgetts",
  "tts_voice": "hi-IN-MadhurNeural",
  "stt_language": "auto",
  "os_system": "windows",
  "phone_pin": "YOUR_PIN_HERE"
}
```

### Supported Provider Presets

| Provider | Endpoint URL | Recommended Model | Free Tier Available |
|---|---|---|:---:|
| **Groq** | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` | ✅ Yes (Fast) |
| **Google Gemini** | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-flash-lite-latest` | ✅ Yes |
| **DeepSeek** | `https://api.deepseek.com` | `deepseek-chat` | 💳 Paid |
| **OpenRouter** | `https://openrouter.ai/api/v1` | `deepseek/deepseek-chat` | 💳 Varied |
| **Empero (Free)** | `https://free.empero.org/v1` | `glm-5.3-flash` | ✅ Yes (No key needed) |
| **Local / Ollama** | `http://localhost:11434` | `llama3.2` | 💻 Fully Offline |

---

## 💻 Running J.A.R.V.I.S

### Normal Launch:
```bash
python main.py
```
Or simply double-click **`run.bat`** on Windows.

### Silent Launch (No Console Window):
Double-click **`run_silent.vbs`** to launch Jarvis silently in the background with only the futuristic HUD visible.

---

## 🗣️ Voice Commands & Usage Examples

### 1. Messaging & Social:
- *"WhatsApp par Rahul ko message bhejo: Main 5 baje pahunchunga."*
- *"Send a message to Aman on Telegram saying: Project is finished."*
- *"Instagram par Rohit ko message karo."*

### 2. System & Application Controls:
- *"WhatsApp open karo"* / *"Chrome open karo"* / *"Notepad kholo"*
- *"Volume 50% kar do"* / *"Volume mute karo"*
- *"Brightness badhao"* / *"Wi-Fi toggle karo"*

### 3. Android Phone Voice Controls:
- *"Jarvis, phone unlock karo"* (Wakes screen, auto-swipes, and types PIN)
- *"Jarvis, phone par WhatsApp open karo"* (Opens WhatsApp directly on mobile)
- *"Jarvis, phone par YouTube chalao"* (Launches YouTube app on phone)
- *"Jarvis, phone ka camera open karo"* (Triggers native phone camera)
- *"Jarvis, mere phone ki battery kitni hai?"* (Reports real-time phone battery %)
- *"Jarvis, phone lock kar do"* (Locks mobile screen)
- *"Jarvis, phone ke home par jao"* (Presses mobile Home button)

### 4. General Intelligence & Queries:
- *"Jarvis, kya haal hai?"*
- *"Time kya ho raha hai?"* (Replies in standard 12-hour AM/PM format)
- *"Today's weather report in Delhi"*
- *"Flight status from Delhi to Mumbai"*

### 5. Settings & Interruptions:
- Say *"Change API key"* or press **`Ctrl + K`** to open the settings panel.
- Say *"Stop"*, *"Chup"*, *"Ruk jao"*, or tap **`ESC`** to interrupt speech immediately.

---

## 📱 Android Phone Voice Automation Setup

Jarvis natively controls connected Android devices over USB or wirelessly:

### 1. One-Time USB Setup (1 minute):
1. On your phone, go to **Settings > About Phone** and tap **Build Number** 7 times to enable Developer Options.
2. In **Settings > Developer Options**, turn ON **USB Debugging**.
3. Plug your phone into PC with a USB cable. When prompted with *"Allow USB debugging?"*, check **"Always allow from this computer"** and tap **Allow**.

### 2. Wireless & Mobile Hotspot Auto-Discovery:
- **No Third-Party Apps Needed**: Jarvis automatically discovers your phone's Wi-Fi or Hotspot gateway IP and connects over port `5555`.
- **Cable-Free Freedom**: Once connected, you can unplug the USB cable — all voice commands (unlock, app launches, battery checks) continue working wirelessly over the room Wi-Fi or phone hotspot.

### 3. Screen Unlock & PIN Configuration:
- To allow Jarvis to unlock PIN-protected devices, add your lock screen PIN to `config/api_keys.json`:
  ```json
  "phone_pin": "YOUR_PIN_HERE"
  ```
- Jarvis dynamically senses screen dimensions and aspect ratios, waking the screen (`KEYCODE_WAKEUP`), dismissing keyguard, and securely typing your PIN.

---

## 📁 Project Architecture

```text
├── main.py                     # Main coordinator, tool loop & lifecycle
├── ui.py                       # PyQt6 Iron Man HUD, Arc Reactor & Settings overlay
├── run.bat                     # Windows batch starter script
├── run_silent.vbs              # Silent background launcher
├── requirements.txt            # Python dependencies
├── config/
│   ├── api_keys.example.json   # Configuration template
│   └── api_keys.json           # Active credentials & settings (gitignored)
├── core/
│   ├── text_pipeline.py        # Voice processing (VAD -> Whisper -> LLM -> TTS)
│   ├── stt.py                  # faster-whisper STT with Hinglish optimization
│   ├── tts.py                  # Microsoft EdgeTTS voice synthesizer
│   ├── llm_client.py           # Provider-agnostic streaming LLM client
│   └── prompt.txt              # Jarvis persona, Hinglish protocol & instructions
├── actions/                    # Tool handlers & automation actions
│   ├── phone_controller.py     # Android ADB & wireless phone automation & unlock
│   ├── send_message.py         # WhatsApp, Telegram, Instagram DM automation
│   ├── open_app.py             # Cross-platform desktop application launcher
│   ├── computer_settings.py    # Volume, brightness, Wi-Fi & window management
│   ├── web_search.py           # Real-time web & news search
│   ├── screen_processor.py     # Screen capture, OCR & vision analysis
│   ├── system_monitor.py       # Hardware telemetry monitoring & voice alerts
│   └── reminder.py             # Windows task scheduler reminders
├── dashboard/                  # Mobile Remote Web Interface (FastAPI + WebSocket)
│   ├── server.py               # Encrypted mobile server & pairing endpoint
│   └── static/                 # Mobile web dashboard UI (HTML/JS/CSS)
└── memory/
    └── long_term.json          # Persistent user identity, preferences & notes
```

---

## 📄 License

Distributed under the **Apache 2.0 License**. See [LICENSE](LICENSE) for details.
