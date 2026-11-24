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
]

def is_jarvis_running() -> bool:
    """Check if main.py is already running."""
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "Get-Process | Where-Object { $_.MainWindowTitle -match 'JARVIS' -or $_.CommandLine -match 'main.py' } | Measure-Object | Select-Object -ExpandProperty Count"],
            text=True,
            timeout=4
        )
        return int(output.strip() or 0) > 0
    except Exception:
        return False

def launch_jarvis():
    """Launch JARVIS UI process."""
    if is_jarvis_running():
        print("[WakeListener] ⚡ JARVIS is already running!")
        return

    print("[WakeListener] 🚀 WAKE WORD DETECTED! Launching JARVIS...")
    try:
        # Launch run.bat cleanly detached
        subprocess.Popen(
            [str(RUN_BAT)],
            cwd=str(BASE_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        )
    except Exception as e:
        print(f"[WakeListener] Error launching run.bat: {e}")
        try:
            subprocess.Popen([str(PYTHON_EXE), str(MAIN_PY)], cwd=str(BASE_DIR))
        except Exception as e2:
            print(f"[WakeListener] Error launching main.py directly: {e2}")

def start_listening():
    import sounddevice as sd
    from faster_whisper import WhisperModel

    SAMPLE_RATE = 16000
    CHUNK_DURATION = 2.5  # seconds
    CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION)
    VAD_THRESHOLD = 0.012

    print("[WakeListener] 🎧 Loading lightweight Whisper model for wake detection...")
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    print("[WakeListener] ✅ Background Wake Listener is active!")
    print(f"[WakeListener] Say any wake phrase: {', '.join(WAKE_PHRASES[:4])}...")

    audio_buffer = []

    def audio_callback(indata, frames, time_info, status):
        audio_buffer.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=audio_callback):
        while True:
            time.sleep(1.2)
            if not audio_buffer:
                continue

            # Check if jarvis is already running
            if is_jarvis_running():
                audio_buffer.clear()
                time.sleep(3.0)
                continue

            # Flatten buffered audio
            data = np.concatenate(audio_buffer, axis=0).flatten()
            audio_buffer.clear()

            # Energy check (skip pure silence)
            rms = np.sqrt(np.mean(data ** 2))
            if rms < VAD_THRESHOLD:
                continue

            # Transcribe short clip
            try:
                segments, _ = model.transcribe(
                    data,
                    language="hi",
                    initial_prompt="Jarvis uth jao. Wake up Jarvis. Jarvis utho.",
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False
                )
                text = " ".join(seg.text for seg in segments).strip().lower()
                if not text:
                    continue

                print(f"[WakeListener] 🎙️ Heard: \"{text}\"")

                # Match wake words
                matched = any(phrase in text for phrase in WAKE_PHRASES)
                # Also check individual word combinations (e.g. 'jarvis' and 'uth')
                if not matched and "jarvis" in text:
                    if any(w in text for w in ("uth", "wake", "on", "shuru", "open", "kholo", "aao")):
                        matched = True

                if matched:
                    launch_jarvis()
                    # Cooldown while jarvis starts up
                    time.sleep(10.0)

            except Exception as ex:
                print(f"[WakeListener] Audio decode error: {ex}")

if __name__ == "__main__":
    start_listening()
