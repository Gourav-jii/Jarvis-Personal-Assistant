"""
Background Wake Listener for JARVIS (MARK XLVIII)
Continuously listens for wake-up phrases like:
  - "jarvis uth jao"
  - "jarvis wake up"
  - "wake up jarvis"
  - "uth jao jarvis"
When detected, it automatically launches JARVIS.
"""

import os
import sys
import time
import subprocess
from pathlib import Path
import numpy as np

# Suppress warnings
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR = get_base_dir()
RUN_BAT = BASE_DIR / "run.bat"
PYTHON_EXE = BASE_DIR / ".venv" / "Scripts" / "python.exe"
MAIN_PY = BASE_DIR / "main.py"
LOG_FILE = BASE_DIR / "wake_listener.log"

WAKE_PHRASES = [
    "jarvis uth jao",
    "uth jao jarvis",
    "jarvis utho",
    "wake up jarvis",
    "jarvis wake up",
    "hey jarvis",
    "ok jarvis",
    "hello jarvis",
    "uth jao",
    "wake up",
]

def log(msg: str):
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{ts} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def is_jarvis_running() -> bool:
    """Fast psutil check to see if main.py is already running."""
    try:
        import psutil
        for p in psutil.process_iter(['name', 'cmdline']):
            try:
                cmd = " ".join(p.info['cmdline'] or []).lower()
                if "main.py" in cmd:
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False

def launch_jarvis():
    """Launch JARVIS UI process."""
    if is_jarvis_running():
        log("⚡ JARVIS is already running!")
        return

    log("🚀 WAKE WORD DETECTED! Launching JARVIS...")
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                ["cmd.exe", "/c", str(RUN_BAT)],
                cwd=str(BASE_DIR),
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            subprocess.Popen([str(PYTHON_EXE), str(MAIN_PY)], cwd=str(BASE_DIR))
    except Exception as e:
        log(f"Error launching run.bat: {e}")
        try:
            subprocess.Popen([str(PYTHON_EXE), str(MAIN_PY)], cwd=str(BASE_DIR))
        except Exception as e2:
            log(f"Error launching main.py directly: {e2}")

def start_listening():
    import sounddevice as sd
    from faster_whisper import WhisperModel

    SAMPLE_RATE = 16000
    VAD_THRESHOLD = 0.005  # More sensitive to catch natural speech easily

    log("🎧 Loading lightweight Whisper model for wake detection...")
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    log("✅ Background Wake Listener is active and listening!")
    log(f"Wake phrases supported: {', '.join(WAKE_PHRASES[:4])}...")

    audio_buffer = []

    def audio_callback(indata, frames, time_info, status):
        audio_buffer.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=audio_callback):
        while True:
            time.sleep(1.0)
            if not audio_buffer:
                continue

            # If JARVIS is currently open/running, don't interfere
            if is_jarvis_running():
                audio_buffer.clear()
                time.sleep(2.0)
                continue

            # Flatten buffered audio
            data = np.concatenate(audio_buffer, axis=0).flatten()
            audio_buffer.clear()

            # Energy check (skip pure room silence)
            rms = np.sqrt(np.mean(data ** 2))
            if rms < VAD_THRESHOLD:
                continue

            try:
                segments, _ = model.transcribe(
                    data,
                    language=None,  # auto-detect Hindi / English
                    initial_prompt="Jarvis uth jao. Wake up Jarvis. Hey Jarvis.",
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False
                )
                text = " ".join(seg.text for seg in segments).strip().lower()
                if not text:
                    continue

                log(f"🎙️ Heard: \"{text}\" (RMS: {rms:.4f})")

                # Match wake words
                matched = any(phrase in text for phrase in WAKE_PHRASES)
                if not matched and "jarvis" in text:
                    if any(w in text for w in ("uth", "wake", "on", "shuru", "open", "kholo", "aao", "start", "sun", "sunna")):
                        matched = True

                if matched:
                    launch_jarvis()
                    time.sleep(8.0)

            except Exception as ex:
                log(f"Audio decode error: {ex}")

if __name__ == "__main__":
    start_listening()
